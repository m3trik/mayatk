# !/usr/bin/python
# coding=utf-8
"""Scene diagnostics and repair helpers."""
from __future__ import annotations

import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Any, Tuple, Callable
import math
import pythontk as ptk
from mayatk.ui_utils._ui_utils import UiUtils

try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)


class SceneDiagnostics:
    """Operations for inspecting and fixing common scene issues."""

    @staticmethod
    def fix_ocio(
        dry_run: bool = False,
        verbose: bool = True,
        prefer_env_ocio: bool = True,
        prefer_aces: bool = True,
    ) -> dict:
        """Repair Maya OCIO/Color Management preferences.

        Goals:
        - Ensure Maya color management is enabled.
        - Ensure the configured OCIO config file path points to a real, loadable config.
        - Prefer a user-provided OCIO config (OCIO env var) when valid.
        - Otherwise, discover a reasonable default OCIO config shipped with the installed Maya.

        This is intentionally conservative: it only changes config path + enable flag.
        It does not force view/display/rendering spaces, which are pipeline-specific.

        Args:
            dry_run: If True, report intended changes only.
            verbose: If True, print a summary of actions.
            prefer_env_ocio: Prefer a valid OCIO config from the OCIO environment variable.
            prefer_aces: Prefer an ACES config when multiple configs are found.

        Returns:
            dict with keys: changed(bool), previous_config(str|None), new_config(str|None),
            enabled_before(bool|None), enabled_after(bool|None), notes(list[str]).
        """

        notes: list[str] = []

        def _cm_prefs(*args, **kwargs):
            return pm.colorManagementPrefs(*args, **kwargs)

        def _try_cm_query(flag: str):
            try:
                return _cm_prefs(q=True, **{flag: True})
            except Exception:
                return None

        def _try_cm_set(**kwargs) -> bool:
            try:
                _cm_prefs(e=True, **kwargs)
                return True
            except Exception:
                return False

        def _get_maya_location() -> Path | None:
            loc = os.environ.get("MAYA_LOCATION")
            if loc:
                p = Path(loc)
                if p.exists():
                    return p
            # Fallback: try to derive from maya module path
            try:
                import maya  # type: ignore

                mp = Path(maya.__file__).resolve()
                # .../Maya2025/Python/Lib/site-packages/maya/__init__.py
                # Walk up a bit and hope to land on MAYA_LOCATION.
                for parent in mp.parents:
                    if (parent / "bin").exists() and (parent / "resources").exists():
                        return parent
            except Exception:
                return None
            return None

        def _walk_configs(root: Path, max_depth: int = 6) -> list[Path]:
            configs: list[Path] = []
            if not root.exists():
                return configs
            root = root.resolve()
            for dirpath, dirnames, filenames in os.walk(root):
                rel_depth = len(Path(dirpath).resolve().parts) - len(root.parts)
                if rel_depth > max_depth:
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    if fn.lower().endswith(".ocio"):
                        configs.append(Path(dirpath) / fn)
            return configs

        def _is_valid_ocio_config(path: Path) -> bool:
            if not path.exists() or not path.is_file():
                return False
            # Try using OCIO if available
            try:
                import PyOpenColorIO as ocio  # type: ignore

                ocio.Config.CreateFromFile(str(path))
                return True
            except Exception:
                # Fallback heuristic (fast, but not authoritative)
                try:
                    head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
                except Exception:
                    return False
                return (
                    "ocio_profile_version" in head
                    or "roles:" in head
                    or "displays:" in head
                )

        def _rank_config(path: Path) -> tuple:
            p = str(path).lower()
            score = 0
            if path.name.lower() == "config.ocio":
                score += 5
            if "resources" in p:
                score += 2
            if "ocio" in p:
                score += 1
            if prefer_aces and "aces" in p:
                score += 4
            # Prefer configs that are not clearly "test" or "example"
            if "example" in p or "test" in p:
                score -= 3
            return (score, len(p))

        previous_config = (
            _try_cm_query("configFilePath")
            or _try_cm_query("cmConfigFilePath")
            or _try_cm_query("ocioConfigFilePath")
            or _try_cm_query("cmConfigFile")
            or _try_cm_query("config")
        )
        if isinstance(previous_config, (list, tuple)) and previous_config:
            previous_config = previous_config[0]

        enabled_before = _try_cm_query("cmEnabled")
        if enabled_before is None:
            enabled_before = _try_cm_query("cmEnable")
        if enabled_before is None:
            enabled_before = _try_cm_query("enabled")
        if isinstance(enabled_before, (list, tuple)) and enabled_before:
            enabled_before = enabled_before[0]

        chosen: Path | None = None

        # 1) Prefer OCIO env var
        if prefer_env_ocio:
            ocio_env = os.environ.get("OCIO")
            if ocio_env:
                env_path = Path(ocio_env)
                if _is_valid_ocio_config(env_path):
                    chosen = env_path
                    notes.append(f"Using OCIO from env var: {env_path}")
                else:
                    notes.append(f"OCIO env var set but invalid/missing: {env_path}")

        # 2) If current Maya pref config is valid, keep it
        if chosen is None and previous_config:
            try:
                prev_path = Path(str(previous_config))
                if _is_valid_ocio_config(prev_path):
                    chosen = prev_path
                    notes.append(f"Current Maya OCIO config is valid: {prev_path}")
            except Exception:
                pass

        # 3) Discover shipped configs from Maya install
        if chosen is None:
            maya_loc = _get_maya_location()
            if maya_loc:
                search_roots = [
                    maya_loc / "resources" / "OCIO",
                    maya_loc / "resources" / "ColorManagement",
                    maya_loc / "resources" / "colorManagement",
                    maya_loc / "resources",
                ]
                found: list[Path] = []
                for r in search_roots:
                    found.extend(_walk_configs(r, max_depth=6))
                found = list(dict.fromkeys([p.resolve() for p in found if p.exists()]))
                found_valid = [p for p in found if _is_valid_ocio_config(p)]
                if found_valid:
                    found_valid.sort(key=_rank_config, reverse=True)
                    chosen = found_valid[0]
                    notes.append(f"Discovered Maya OCIO config: {chosen}")
                else:
                    notes.append(
                        "No valid OCIO configs discovered under MAYA_LOCATION/resources."
                    )
            else:
                notes.append(
                    "Cannot determine MAYA_LOCATION; config discovery skipped."
                )

        enabled_after = bool(enabled_before) if enabled_before is not None else None
        changed = False

        if chosen is None:
            if verbose:
                pm.warning("OCIO repair: no valid config found; no changes made.")
            return {
                "changed": False,
                "previous_config": str(previous_config) if previous_config else None,
                "new_config": None,
                "enabled_before": enabled_before,
                "enabled_after": enabled_before,
                "notes": notes,
            }

        # Apply changes
        target_config = str(chosen)

        if enabled_before is not True:
            enabled_after = True
            if dry_run:
                notes.append("Would enable color management")
            else:
                ok = (
                    _try_cm_set(cmEnabled=True)
                    or _try_cm_set(cmEnable=True)
                    or _try_cm_set(enabled=True)
                )
                if ok:
                    changed = True
                    notes.append("Enabled color management")
                else:
                    notes.append("Failed to enable color management (unsupported flag)")

        if not previous_config or str(previous_config) != target_config:
            if dry_run:
                notes.append(f"Would set OCIO config file: {target_config}")
            else:
                ok = (
                    _try_cm_set(configFilePath=target_config)
                    or _try_cm_set(cmConfigFilePath=target_config)
                    or _try_cm_set(ocioConfigFilePath=target_config)
                    or _try_cm_set(cmConfigFile=target_config)
                    or _try_cm_set(config=target_config)
                )
                if ok:
                    changed = True
                    notes.append(f"Set OCIO config file: {target_config}")
                else:
                    notes.append("Failed to set OCIO config file (unsupported flag)")

        if verbose:
            header = "OCIO repair (dry-run)" if dry_run else "OCIO repair"
            print(header)
            print(f"  Enabled: {enabled_before} -> {enabled_after}")
            print(f"  Config : {previous_config} -> {target_config}")
            for n in notes:
                print(f"  - {n}")

        return {
            "changed": changed,
            "previous_config": str(previous_config) if previous_config else None,
            "new_config": target_config,
            "enabled_before": enabled_before,
            "enabled_after": enabled_after,
            "notes": notes,
        }

    @staticmethod
    def fix_unknown_plugins(dry_run=False, verbose=True):
        """
        Fixes the 'Unable to Save Scene' issue by removing unknown nodes and plugins.
        Ref: https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Unable-to-Save-Scene-as-MA-file-or-Maya-crashes-when-saving-as-MB-file.html

        Args:
            dry_run (bool): If True, only lists what would be removed without taking action.
            verbose (bool): If True, logs details about the operations.
        """
        # Delete unknown nodes
        try:
            unknown_nodes = pm.ls(type="unknown")
            if unknown_nodes:
                if verbose:
                    action = "Would delete" if dry_run else "Deleting"
                    print(f"{action} {len(unknown_nodes)} unknown nodes:")
                    for node in unknown_nodes:
                        print(f"  - {node}")

                if not dry_run:
                    pm.delete(unknown_nodes)
        except Exception as e:
            pm.warning(f"Error deleting unknown nodes: {e}")

        # Remove unknown plugins
        try:
            unknown_plugins = pm.unknownPlugin(query=True, list=True)
            if unknown_plugins:
                if verbose:
                    action = "Would remove" if dry_run else "Removing"
                    print(f"{action} {len(unknown_plugins)} unknown plugins:")
                    for plugin in unknown_plugins:
                        print(f"  - {plugin}")

                if not dry_run:
                    for plugin in unknown_plugins:
                        try:
                            pm.unknownPlugin(plugin, remove=True)
                        except Exception as e:
                            if verbose:
                                pm.warning(
                                    f"Failed to remove unknown plugin '{plugin}': {e}"
                                )
        except Exception as e:
            pm.warning(f"Error querying unknown plugins: {e}")

        if (
            verbose
            and not ("unknown_nodes" in locals() and unknown_nodes)
            and not ("unknown_plugins" in locals() and unknown_plugins)
        ):
            print("No unknown nodes or plugins found.")

    @staticmethod
    def remove_xgen_expressions(quiet: bool = False) -> int:
        """
        Remove legacy XGen expressions that cause 'Cannot find procedure xgmPreview' errors.

        Returns:
            int: Number of nodes deleted.
        """
        nodes_to_delete = []

        # Common XGen expression that causes issues when plugin is missing
        if pm.objExists("xgmRefreshPreview"):
            nodes_to_delete.append("xgmRefreshPreview")

        if not nodes_to_delete:
            if not quiet:
                print("No XGen preview expressions found.")
            return 0

        count = len(nodes_to_delete)
        try:
            pm.delete(nodes_to_delete)
            if not quiet:
                print(
                    f"Deleted {count} XGen expression nodes: {', '.join(nodes_to_delete)}"
                )
        except Exception as e:
            if not quiet:
                pm.warning(f"Failed to delete XGen nodes: {e}")
            return 0

        return count

    @classmethod
    def cleanup_scene(cls, quiet: bool = False) -> None:
        """
        Run all scene cleanup operations:
        - Remove unknown nodes and plugins
        - Remove legacy XGen expressions
        """
        if not quiet:
            print("Starting scene cleanup...")

        cls.fix_unknown_plugins(dry_run=False, verbose=not quiet)
        cls.remove_xgen_expressions(quiet=quiet)

        if not quiet:
            print("Scene cleanup complete.")


@dataclass
class AuditProfile:
    """Thresholds for scene analysis."""

    max_tris: int = 20000
    max_slots: int = 4
    max_tex_res: int = 4096
    max_uvs: int = 2
    name: str = "Standard"
    texture_compression: str = "BC7"  # BC7, ASTC, None
    adaptive_tris: bool = False
    reference_diag: float = 200.0  # Size in units where max_tris applies
    min_tris: int = 500  # Floor for adaptive budget


@dataclass
class MeshRecord:
    """Compact record for mesh statistics."""

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
    vertex_byte_breakdown: str = ""


@dataclass
class MaterialRecord:
    """Compact record for material usage."""

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
    unique_paths_scene: int = 0
    unique_paths_local: int = 0
    max_res_is_unique: bool = False


@dataclass
class AssetRecord:
    """Combined record for an analyzed asset."""

    transform: str
    mesh: MeshRecord
    material: MaterialRecord
    score: float = 0.0
    perf_score: float = 0.0
    risk_score: float = 0.0
    findings: List[str] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    instance_count: int = 0
    tri_percent: float = 0.0
    delta_summary: str = ""
    fix_plan: List[str] = field(default_factory=list)
    target_tris: int = 0


@dataclass
class SceneOverview:
    """High-level overview of the scene analysis."""

    total_meshes: int
    total_tris: int
    total_verts: int
    avg_slots: float
    max_slots: int
    multi_slot_meshes: int
    transparent_meshes: int
    total_texture_mb: float
    est_gpu_mb: float
    est_gpu_mb_compressed: float
    max_texture_res: int
    large_texture_count: int
    unique_texture_paths: int
    top_offenders: List[AssetRecord]
    top_by_tris: List[AssetRecord]
    top_by_slots: List[AssetRecord]
    top_by_max_res: List[AssetRecord]
    top_by_risk: List[AssetRecord]
    top_by_transparency: List[AssetRecord]
    top_multi_slot_density: List[AssetRecord]
    fix_plan: List[str]
    fix_first_items: List[str] = field(default_factory=list)
    texture_dim_histogram: Dict[str, int] = field(default_factory=dict)
    pipeline_integrity: List[str] = field(default_factory=list)
    scope: str = "Selection"
    profile: AuditProfile = field(default_factory=AuditProfile)
    total_slots: int = 0
    meshes_over_slot_threshold: int = 0
    meshes_over_tri_threshold: int = 0
    total_target_tris: int = 0
    total_slots_over_budget: int = 0
    non_manifold_count: int = 0
    lamina_count: int = 0
    ngon_count: int = 0
    high_poly_count: int = 0
    top_repeated_offenders: List[AssetRecord] = field(default_factory=list)
    top_by_effective_score: List[AssetRecord] = field(default_factory=list)
    top_materials: List[Tuple[str, int]] = field(default_factory=list)
    top_savings_draw_calls: List[AssetRecord] = field(default_factory=list)
    top_savings_tris: List[AssetRecord] = field(default_factory=list)
    missing_textures: List[Tuple[str, int, List[str]]] = field(
        default_factory=list
    )  # (path, count, [materials])
    missing_textures_project: List[Tuple[str, int, List[str]]] = field(
        default_factory=list
    )
    missing_textures_presets: List[Tuple[str, int, List[str]]] = field(
        default_factory=list
    )
    heaviest_textures: List[
        Tuple[str, float, Tuple[int, int], int, List[str], int, int]
    ] = field(
        default_factory=list
    )  # (path, size_mb, res, mat_count, [materials], mesh_count, instance_count)
    texture_type_breakdown: Dict[str, float] = field(
        default_factory=dict
    )  # type -> size_mb
    savings_draw_calls_total: int = 0
    savings_tris_total: int = 0
    savings_draw_calls_budget: int = 0
    savings_tris_budget: int = 0
    instance_stats: Dict[str, int] = field(default_factory=dict)
    budget_compliance_dist: Dict[str, Dict[str, int]] = field(default_factory=dict)
    scene_compliance: Dict[str, float] = field(default_factory=dict)  # % over budget

    # New fields for improved reporting
    effective_total_tris: int = 0
    effective_total_slots: int = 0
    raw_total_tris: int = 0
    raw_total_verts: int = 0
    raw_total_slots: int = 0
    pareto_tris: List[str] = field(default_factory=list)
    pareto_slots: List[str] = field(default_factory=list)
    top_wins_by_type: Dict[str, List[str]] = field(default_factory=dict)
    scene_health_flags: List[str] = field(default_factory=list)
    meshes_with_transparency: int = 0
    meshes_with_extra_uvs: int = 0
    meshes_with_high_slots: int = 0
    materials_causing_splits: List[Tuple[str, int, int, float]] = field(
        default_factory=list
    )  # (mat_name, unique_count, over_budget_count, avg_slots)

    # ROI Improvements
    slot_stats: Dict[str, float] = field(default_factory=dict)  # avg, median, p90, max
    slot_budget_delta: Dict[str, int] = field(
        default_factory=dict
    )  # excess, opportunity
    pareto_texture_mb: List[str] = field(default_factory=list)
    shared_4k_textures: List[Tuple[str, int]] = field(
        default_factory=list
    )  # path, count
    single_use_4k_count: int = 0
    shared_4k_count: int = 0
    missing_texture_impact: Dict[str, Any] = field(default_factory=dict)
    selection_coverage: Dict[str, int] = field(default_factory=dict)
    texture_class_estimates: Dict[str, float] = field(
        default_factory=dict
    )  # Class -> MB


class SceneAnalyzer(ptk.LoggingMixin):
    """
    Analyzes scene objects for performance expectations in game engines.
    Focuses on Mesh and Material metrics with a scalable, bulk-collection workflow.
    """

    def __init__(self):
        super().__init__()
        self.logger.hide_logger_name(True)
        self._shading_map: Dict[str, Set[str]] = (
            {}
        )  # shape_name -> {shading_engine_names}
        self._material_map: Dict[str, str] = {}  # shading_engine -> material_node
        self._material_flags: Dict[str, Dict[str, Any]] = {}  # material_node -> {flags}
        self._global_texture_usage: Dict[str, Dict[str, Any]] = (
            {}
        )  # path -> {count, meshes, instances}
        self.scope = "Selection"
        self.profile = "Generic"

    @classmethod
    def run_audit(cls, adaptive: bool = False, verbose: bool = True) -> None:
        """
        Run a full scene audit and print the report.

        Args:
            adaptive: If True, use adaptive budgeting based on object size.
            verbose: If True, print the report to the script editor.
        """
        profile = AuditProfile(adaptive_tris=adaptive)
        if adaptive:
            profile.name = "Adaptive (Game Ready)"

        analyzer = cls()
        records = analyzer.analyze(profile=profile)
        report = analyzer.generate_report(records)

        if verbose:
            analyzer.print_report(report)

    def analyze(
        self,
        objects: List[Any] = None,
        fast_mode: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        profile: AuditProfile = None,
    ) -> List[AssetRecord]:
        """
        Main entry point for analysis.

        Args:
            objects: List of objects to analyze. If None, uses selection.
            fast_mode: If True, skips deep checks (not implemented yet, but reserved for future).
            progress_callback: Optional callback(current, total, message) for progress updates.
            profile: Target profile settings.

        Returns:
            List of AssetRecord objects sorted by score (descending).
        """
        if profile is None:
            profile = AuditProfile()
        self.profile = profile
        self.scope = "Selection" if objects is None else "Custom"

        # Phase A: Resolve targets
        if progress_callback:
            progress_callback(0, 100, "Resolving targets...")

        # shape -> list of transform names
        shape_map = self._resolve_targets(objects)
        if not shape_map:
            return []

        shapes = list(shape_map.keys())

        # Phase B: Bulk collect material data
        if progress_callback:
            progress_callback(10, 100, "Collecting material data...")
        self._build_material_caches(shape_map)

        # Phase C: Analyze and Score
        records = []
        total_shapes = len(shapes)
        for i, shape in enumerate(shapes):
            if progress_callback:
                # Map 20-100% to shape analysis
                pct = 20 + int((i / total_shapes) * 80)
                progress_callback(pct, 100, f"Analyzing {shape.name()}")

            mesh_rec = self._analyze_mesh(shape)
            mat_rec = self._analyze_material(shape)

            # Calculate score and findings
            (
                score,
                perf_score,
                risk_score,
                findings,
                breakdown,
                delta_summary,
                fix_plan,
                target_tris,
            ) = self._calculate_score(mesh_rec, mat_rec)

            # Get transforms and instance count
            transforms = shape_map[shape]
            instance_count = len(transforms)
            # Use the first transform as the representative name
            transform_name = transforms[0] if transforms else "Unknown"

            records.append(
                AssetRecord(
                    transform=transform_name,
                    mesh=mesh_rec,
                    material=mat_rec,
                    score=score,
                    perf_score=perf_score,
                    risk_score=risk_score,
                    findings=findings,
                    score_breakdown=breakdown,
                    instance_count=instance_count,
                    delta_summary=delta_summary,
                    fix_plan=fix_plan,
                    target_tris=target_tris,
                )
            )

        # Post-process: Calculate tri_percent
        total_tris = sum(r.mesh.tris * r.instance_count for r in records)
        if total_tris > 0:
            for r in records:
                r.tri_percent = ((r.mesh.tris * r.instance_count) / total_tris) * 100.0

        # Sort by score descending
        records.sort(key=lambda x: x.score, reverse=True)
        return records

    def generate_report(self, records: List[AssetRecord]) -> SceneOverview:
        """Generates a high-level overview from analysis records."""
        if not records:
            return SceneOverview(
                total_meshes=0,
                total_tris=0,
                total_verts=0,
                avg_slots=0.0,
                max_slots=0,
                multi_slot_meshes=0,
                transparent_meshes=0,
                total_texture_mb=0.0,
                est_gpu_mb=0.0,
                est_gpu_mb_compressed=0.0,
                max_texture_res=0,
                large_texture_count=0,
                unique_texture_paths=0,
                top_offenders=[],
                top_by_tris=[],
                top_by_slots=[],
                top_by_max_res=[],
                top_by_risk=[],
                top_by_transparency=[],
                top_multi_slot_density=[],
                fix_plan=[],
                total_target_tris=0,
                instance_stats={"total_instances": 0, "instanced_shapes": 0},
            )

        total_meshes = len(records)
        # Note: records are per unique shape. We must multiply by instance_count for scene totals.
        total_tris = sum(r.mesh.tris * r.instance_count for r in records)
        total_verts = sum(r.mesh.verts * r.instance_count for r in records)
        total_slots = sum(r.material.slot_count * r.instance_count for r in records)
        max_slots = max((r.material.slot_count for r in records), default=0)
        avg_slots = (
            total_slots / sum(r.instance_count for r in records)
            if total_meshes > 0
            else 0
        )

        multi_slot_meshes = sum(1 for r in records if r.material.slot_count > 1)
        meshes_over_slot_threshold = sum(
            1 for r in records if r.material.slot_count > self.profile.max_slots
        )
        meshes_over_tri_threshold = sum(
            1 for r in records if r.mesh.tris > r.target_tris
        )
        total_slots_over_budget = sum(
            max(0, r.material.slot_count - self.profile.max_slots) * r.instance_count
            for r in records
        )

        transparent_meshes = sum(1 for r in records if r.material.uses_transparency)

        non_manifold_count = sum(1 for r in records if r.mesh.non_manifold_edges > 0)
        lamina_count = sum(1 for r in records if r.mesh.lamina_faces > 0)
        ngon_count = sum(1 for r in records if r.mesh.ngons > 0)
        high_poly_count = sum(1 for r in records if r.mesh.tris > r.target_tris)

        # Texture stats
        # total_tex_size_mb is unique disk size per material.
        # Summing it across all records might double count if materials are shared across shapes.
        # But MaterialRecord is per shape.
        # We should use global texture usage for scene total.
        # But we don't have file sizes in global usage map easily available here unless we iterate.
        # Let's approximate by summing unique textures in records, but we need to be careful.
        # Actually, we can just iterate over _global_texture_usage keys and get size?
        # No, we don't have size in that map.
        # Let's use the sum of records but acknowledge it might be slightly off if materials are shared across shapes.
        # Wait, MaterialRecord calculates `total_tex_size_mb` based on unique textures for that shape.
        # If Shape A and Shape B use Mat X, they both count the textures.
        # So summing them is wrong for "Unique Scene Total".
        # We need a set of all unique paths encountered in records.
        # But we don't have paths in MaterialRecord, only stats.
        # We can't accurately calculate "Unique Scene Texture Size" from records alone without paths.
        # However, we can use `est_gpu_mb` which is per-instance (resident).
        # Actually, GPU memory is also unique (resources are shared).
        # So we really need to know the set of all textures in the scene.
        # We can rebuild it from `_material_flags` since we have `_material_map`.

        unique_paths = set()
        total_texture_mb = 0.0
        est_gpu_mb = 0.0
        est_gpu_mb_compressed = 0.0
        texture_type_breakdown = {}
        texture_class_estimates = {}  # Class -> Compressed MB
        texture_dim_histogram = {"4k+": 0, "2k": 0, "1k": 0, "512": 0, "<512": 0}
        shared_4k_textures = []
        single_use_4k_count = 0
        shared_4k_count = 0

        # Re-scan materials used by these records
        used_materials = set()
        for r in records:
            used_materials.update(r.material.materials)

        # Use global texture usage for accurate scene-wide stats
        # But filter by what's actually used in the analyzed records if scope is Selection
        # If scope is Selection, we only care about textures used by selected objects.
        # _global_texture_usage contains everything found during _build_material_caches.
        # _build_material_caches was built from shape_map (selection).
        # So _global_texture_usage should be correct for the scope.

        for path, usage_data in self._global_texture_usage.items():
            unique_paths.add(path)

        processed_paths = set()

        for mat_name in used_materials:
            flags = self._material_flags.get(mat_name, {})
            textures = flags.get("textures", [])
            for t in textures:
                path = t["path"]
                if path not in processed_paths:
                    processed_paths.add(path)

                    size_mb = t["size_mb"]
                    total_texture_mb += size_mb

                    w, h = t["res"]
                    # Uncompressed: RGBA8 (4 bytes) + Mips (1.33x)
                    est_gpu_mb += (w * h * 4 * 1.33) / (1024 * 1024)

                    # Compressed Estimate per Class
                    tex_type = t.get("type", "Other")
                    has_alpha = t.get("has_alpha", False)

                    bpp = 1.0  # Default BC7/BC5
                    if tex_type == "BaseColor":
                        bpp = 1.0 if has_alpha else 0.5  # BC7 vs BC1
                    elif tex_type == "Normal":
                        bpp = 1.0  # BC5
                    elif tex_type == "Masks":
                        bpp = 0.5  # BC1/BC4
                    elif tex_type == "Emissive":
                        bpp = 0.5  # BC1

                    comp_size = (w * h * bpp * 1.33) / (1024 * 1024)
                    est_gpu_mb_compressed += comp_size

                    texture_class_estimates[tex_type] = (
                        texture_class_estimates.get(tex_type, 0.0) + comp_size
                    )

                    texture_type_breakdown[tex_type] = (
                        texture_type_breakdown.get(tex_type, 0.0) + size_mb
                    )

                    # Histogram & 4K Analysis
                    max_dim = max(w, h)
                    if max_dim >= 4096:
                        texture_dim_histogram["4k+"] += 1
                        # Check usage
                        usage = self._global_texture_usage.get(path, {})
                        count = usage.get("count", 0) if isinstance(usage, dict) else 0
                        # Note: count in global_texture_usage is "used by X materials * objects"
                        # We want "used by X meshes" or "used by X materials"
                        # usage["meshes"] is a set of mesh names
                        mesh_count = len(usage.get("meshes", []))

                        if mesh_count > 1:
                            shared_4k_count += 1
                            shared_4k_textures.append((path, mesh_count))
                        else:
                            single_use_4k_count += 1

                    elif max_dim >= 2048:
                        texture_dim_histogram["2k"] += 1
                    elif max_dim >= 1024:
                        texture_dim_histogram["1k"] += 1
                    elif max_dim >= 512:
                        texture_dim_histogram["512"] += 1
                    else:
                        texture_dim_histogram["<512"] += 1

        # Sort shared 4K textures
        shared_4k_textures.sort(key=lambda x: x[1], reverse=True)
        shared_4k_textures = shared_4k_textures[:5]  # Top 5

        max_texture_res = max((r.material.max_res for r in records), default=0)
        large_texture_count = sum(1 for r in records if r.material.max_res > 2048)
        unique_texture_paths = len(unique_paths)

        # Top offenders (already sorted by score in analyze)
        top_offenders = records[:20]

        # Category offenders
        top_by_tris = sorted(records, key=lambda x: x.mesh.tris, reverse=True)[:10]
        top_by_slots = sorted(
            records, key=lambda x: x.material.slot_count, reverse=True
        )[:10]
        top_by_max_res = sorted(
            records, key=lambda x: x.material.max_res, reverse=True
        )[:10]
        top_by_risk = sorted(records, key=lambda x: x.risk_score, reverse=True)[:10]

        # Heaviest Textures (Files)
        heaviest_textures_list = []
        for path in processed_paths:
            # Find details again (inefficient but safe)
            # We could have cached it above.
            found = False
            for mat_name in used_materials:
                flags = self._material_flags.get(mat_name, {})
                textures = flags.get("textures", [])
                for t in textures:
                    if t["path"] == path:
                        usage = self._global_texture_usage.get(
                            path, {"count": 0, "meshes": set(), "instances": 0}
                        )
                        # Find materials using this texture
                        # We don't have a reverse map path->materials easily.
                        # But we can iterate used_materials.
                        mats_using = []
                        for m in used_materials:
                            m_flags = self._material_flags.get(m, {})
                            if any(
                                tx["path"] == path for tx in m_flags.get("textures", [])
                            ):
                                mats_using.append(m)

                        heaviest_textures_list.append(
                            (
                                path,
                                t["size_mb"],
                                t["res"],
                                len(mats_using),
                                mats_using,
                                len(usage["meshes"]),
                                usage["instances"],
                            )
                        )
                        found = True
                        break
                if found:
                    break

        # Sort by size descending
        heaviest_textures_list.sort(key=lambda x: x[1], reverse=True)

        top_multi_slot_density = []  # Placeholder

        # Transparency
        transparent_recs = [r for r in records if r.material.uses_transparency]
        top_by_transparency = sorted(
            transparent_recs, key=lambda x: x.score, reverse=True
        )[:10]

        # Top Repeated Offenders (Effective Cost = instances * score)
        top_repeated_offenders = sorted(
            records, key=lambda x: x.score * max(1, x.instance_count), reverse=True
        )[:10]

        # Top Effective Score (Same as repeated offenders but explicitly named for dual ranking)
        top_by_effective_score = top_repeated_offenders

        # Top Materials
        mat_usage = {}
        for r in records:
            for mat in r.material.materials:
                mat_usage[mat] = mat_usage.get(mat, 0) + max(1, r.instance_count)
        top_materials = sorted(mat_usage.items(), key=lambda x: x[1], reverse=True)[:10]

        # Top Savings
        # Draw Calls: (slots - 1) * instances
        savings_draw_calls_candidates = [
            r for r in records if r.material.slot_count > 1
        ]
        top_savings_draw_calls = sorted(
            savings_draw_calls_candidates,
            key=lambda x: (x.material.slot_count - 1) * x.instance_count,
            reverse=True,
        )[:5]
        savings_draw_calls_total = sum(
            (r.material.slot_count - 1) * r.instance_count
            for r in savings_draw_calls_candidates
        )

        # Savings to Budget (slots - budget) * instances
        savings_draw_calls_budget = sum(
            max(0, r.material.slot_count - self.profile.max_slots) * r.instance_count
            for r in records
        )

        # Tris: (tris - budget) * instances
        savings_tris_candidates = [r for r in records if r.mesh.tris > r.target_tris]
        top_savings_tris = sorted(
            savings_tris_candidates,
            key=lambda x: (x.mesh.tris - x.target_tris) * x.instance_count,
            reverse=True,
        )[:5]
        savings_tris_total = sum(
            (r.mesh.tris - r.target_tris) * r.instance_count
            for r in savings_tris_candidates
        )
        savings_tris_budget = (
            savings_tris_total  # Same logic as total since budget is the baseline
        )

        # Missing Textures
        missing_map = {}  # path -> set(mat_names)
        for mat_name in used_materials:
            flags = self._material_flags.get(mat_name, {})
            paths = flags.get("missing_paths", [])
            for p in paths:
                if p not in missing_map:
                    missing_map[p] = set()
                missing_map[p].add(mat_name)

        missing_textures_list = []
        missing_textures_project = []
        missing_textures_presets = []

        for p, mats in missing_map.items():
            entry = (p, len(mats), list(mats))
            missing_textures_list.append(entry)

            # Categorize
            lower_p = p.lower().replace("\\", "/")
            if "program files" in lower_p or "maya" in lower_p and "presets" in lower_p:
                missing_textures_presets.append(entry)
            else:
                missing_textures_project.append(entry)

        missing_textures_list.sort(key=lambda x: x[1], reverse=True)
        missing_textures_project.sort(key=lambda x: x[1], reverse=True)
        missing_textures_presets.sort(key=lambda x: x[1], reverse=True)

        # Instance Stats
        instance_stats = {
            "unique_meshes": len(records),
            "instanced_shapes": sum(1 for r in records if r.instance_count > 1),
            "total_instances": sum(r.instance_count for r in records),
        }

        # Budget Compliance Distribution
        budget_compliance_dist = {
            "tris": {"0-10%": 0, "10-50%": 0, "50%+": 0},
            "slots": {"1-2": 0, "3-5": 0, "6+": 0},
        }

        for r in records:
            # Tris
            if r.mesh.tris > r.target_tris:
                overage = (r.mesh.tris - r.target_tris) / r.target_tris
                if overage <= 0.1:
                    budget_compliance_dist["tris"]["0-10%"] += 1
                elif overage <= 0.5:
                    budget_compliance_dist["tris"]["10-50%"] += 1
                else:
                    budget_compliance_dist["tris"]["50%+"] += 1

            # Slots
            slots = r.material.slot_count
            if slots > self.profile.max_slots:
                overage = slots - self.profile.max_slots
                if overage <= 2:
                    budget_compliance_dist["slots"]["1-2"] += 1
                elif overage <= 5:
                    budget_compliance_dist["slots"]["3-5"] += 1
                else:
                    budget_compliance_dist["slots"]["6+"] += 1

        # Scene Compliance
        # For adaptive, we sum the individual targets
        total_target_tris = sum(r.target_tris * r.instance_count for r in records)
        scene_compliance = {
            "tris": (
                (total_tris / total_target_tris * 100.0)
                if total_target_tris > 0
                else 0.0
            ),
            "slots": (
                (total_slots / (total_meshes * self.profile.max_slots) * 100.0)
                if total_meshes > 0
                else 0.0
            ),
        }

        # Generate Fix Plan
        fix_plan = []
        if non_manifold_count > 0:
            offender = max(records, key=lambda r: r.mesh.non_manifold_edges)
            fix_plan.append(
                f"Fix non-manifold geometry: {offender.transform} ({offender.mesh.non_manifold_edges} edges), ... (total {non_manifold_count} meshes)"
            )
        if lamina_count > 0:
            fix_plan.append(
                f"Remove lamina faces (Mesh Cleanup) on {lamina_count} meshes."
            )
        if missing_textures_list:
            fix_plan.append(
                f"Relink {len(missing_textures_list)} missing textures (File Path Editor)."
            )
        if any(r.material.slot_count > 1 for r in records):
            offender = max(records, key=lambda r: r.material.slot_count)
            saved = (offender.material.slot_count - 1) * max(1, offender.instance_count)
            fix_plan.append(
                f"Reduce slots: {offender.transform} ({offender.material.slot_count} slots x {max(1, offender.instance_count)} instances) -> target 1 slot (est -{saved} slots effective)"
            )
        if any(r.material.max_res > self.profile.max_tex_res for r in records):
            offender = max(records, key=lambda r: r.material.max_res)
            fix_plan.append(
                f"Downscale textures: {offender.transform} uses {offender.material.max_res}px textures."
            )
        if high_poly_count > 0:
            fix_plan.append(
                f"Decimate or retopologize {high_poly_count} high-poly meshes (>{self.profile.max_tris} tris)."
            )
        if any(r.material.unpacked_pbr for r in records):
            fix_plan.append("Pack PBR textures (ORM/ARM) to reduce sampler count.")

        # Fix First Items (Top 3-7 actionable items)
        fix_first_items = []

        # 1. Top Offenders by Effective Score
        effective_offenders = sorted(
            records, key=lambda x: x.score * max(1, x.instance_count), reverse=True
        )
        for r in effective_offenders[:3]:
            if r.score > 10:
                reason = r.findings[0] if r.findings else "General Issues"
                # Simplify reason for summary
                if "High Poly" in reason:
                    reason = "High Poly"
                elif "Draw Call" in reason:
                    reason = "High Slots"
                elif "Texture" in reason:
                    reason = "Heavy Textures"

                fix_first_items.append(
                    f"Fix {r.transform}: {reason} (Score {r.score:.0f} x {r.instance_count} instances)"
                )

        # 2. Missing Textures
        if missing_textures_project:
            fix_first_items.append(
                f"Relink {len(missing_textures_project)} missing project textures"
            )

        # 3. Slot Reduction
        if savings_draw_calls_budget > 0:
            fix_first_items.append(
                f"Reduce material slots by {savings_draw_calls_budget} to reach budget"
            )

        # 4. High Poly
        high_poly_overage = sum(max(0, r.mesh.tris - r.target_tris) for r in records)
        if high_poly_overage > 100000:
            fix_first_items.append(
                f"Decimate meshes to save {high_poly_overage:,} triangles total"
            )

        # Pipeline Integrity
        pipeline_integrity = []
        if missing_textures_project:
            pipeline_integrity.append(
                f"{len(missing_textures_project)} missing project files"
            )
        if missing_textures_presets:
            pipeline_integrity.append(
                f"{len(missing_textures_presets)} missing preset files (low priority)"
            )

        # --- New Calculations ---
        raw_total_tris = sum(r.mesh.tris for r in records)
        raw_total_verts = sum(r.mesh.verts for r in records)
        raw_total_slots = sum(r.material.slot_count for r in records)

        # Slot Stats (Per Unique Mesh)
        slot_counts = sorted([r.material.slot_count for r in records])
        if slot_counts:
            import math

            median_slots = slot_counts[len(slot_counts) // 2]
            p90_index = int(len(slot_counts) * 0.9)
            p90_slots = slot_counts[p90_index]
        else:
            median_slots = 0
            p90_slots = 0

        slot_stats = {
            "avg": avg_slots,  # This is weighted avg in current code? No, avg_slots = total_slots / total_instances.
            # Wait, avg_slots calculation in current code:
            # avg_slots = total_slots / sum(r.instance_count)
            # This is "Avg Slots per Instance".
            # User asked for "Material Slots (per-mesh)".
            # Let's provide per-unique-mesh stats for the breakdown.
            "avg_unique": sum(slot_counts) / len(slot_counts) if slot_counts else 0,
            "median": median_slots,
            "p90": p90_slots,
            "max": max_slots,
        }

        # Slot Budget Delta
        # Excess: Slots above budget (Compliance)
        # Opportunity: Slots above 1 (Consolidation)
        opportunity_slots = sum(
            (r.material.slot_count - 1) * r.instance_count for r in records
        )
        slot_budget_delta = {
            "excess": total_slots_over_budget,
            "opportunity": opportunity_slots,
        }

        # Pareto View (Tris)
        sorted_by_eff_tris = sorted(
            records, key=lambda r: r.mesh.tris * r.instance_count, reverse=True
        )
        pareto_tris = []
        running_tris = 0
        for i, r in enumerate(sorted_by_eff_tris[:10]):
            eff_tris = r.mesh.tris * r.instance_count
            running_tris += eff_tris
            pct = (running_tris / total_tris * 100.0) if total_tris > 0 else 0
            pareto_tris.append(f"{i+1}. {r.transform}: {eff_tris:,} ({pct:.1f}% cum)")

        # Pareto View (Slots)
        sorted_by_eff_slots = sorted(
            records,
            key=lambda r: r.material.slot_count * r.instance_count,
            reverse=True,
        )
        pareto_slots = []
        running_slots = 0
        for i, r in enumerate(sorted_by_eff_slots[:10]):
            eff_slots = r.material.slot_count * r.instance_count
            running_slots += eff_slots
            pct = (running_slots / total_slots * 100.0) if total_slots > 0 else 0
            pareto_slots.append(f"{i+1}. {r.transform}: {eff_slots} ({pct:.1f}% cum)")

        # Pareto View (Texture MB - Compressed Estimate)
        # We need to attribute texture size to meshes.
        # This is hard because textures are shared.
        # But we can list the heaviest textures directly as requested.
        # "texture MB (compressed estimate) (optional since you already list heavy textures)"
        # Let's skip per-mesh texture pareto and just rely on the "Heaviest Textures" list but formatted better.
        # Or we can list the top textures by size * usage?
        # User said: "slots, texture MB (compressed estimate)".
        # Let's list the top textures by compressed size.
        # We have `heaviest_textures_list` which is sorted by disk size.
        # Let's create a list of top compressed textures.
        # We need to re-calculate compressed size for the list.
        pareto_texture_mb = []
        # We can reuse processed_paths logic if we stored it.
        # We didn't store the compressed size per texture in a list.
        # Let's just use heaviest_textures_list (disk size) as a proxy or re-sort it?
        # User specifically asked for "compressed estimate".
        # Let's try to do it right.

        tex_compressed_list = []
        for path in processed_paths:
            # Find t again... inefficient but safe
            for mat_name in used_materials:
                flags = self._material_flags.get(mat_name, {})
                textures = flags.get("textures", [])
                found = False
                for t in textures:
                    if t["path"] == path:
                        w, h = t["res"]
                        tex_type = t.get("type", "Other")
                        has_alpha = t.get("has_alpha", False)
                        bpp = 1.0
                        if tex_type == "BaseColor":
                            bpp = 1.0 if has_alpha else 0.5
                        elif tex_type == "Normal":
                            bpp = 1.0
                        elif tex_type == "Masks":
                            bpp = 0.5
                        elif tex_type == "Emissive":
                            bpp = 0.5

                        comp_size = (w * h * bpp * 1.33) / (1024 * 1024)
                        tex_compressed_list.append((path, comp_size, tex_type))
                        found = True
                        break
                if found:
                    break

        tex_compressed_list.sort(key=lambda x: x[1], reverse=True)
        total_comp_mb = est_gpu_mb_compressed
        running_comp = 0
        for i, (path, size, t_type) in enumerate(tex_compressed_list[:10]):
            running_comp += size
            pct = (running_comp / total_comp_mb * 100.0) if total_comp_mb > 0 else 0
            name = os.path.basename(path)
            pareto_texture_mb.append(
                f"{i+1}. {name} ({t_type}): {size:.1f} MB ({pct:.1f}% cum)"
            )

        # Top Wins
        top_wins_by_type = {}
        if savings_draw_calls_total > 0:
            top_wins_by_type["Reduce Slots"] = [
                f"Save {savings_draw_calls_total} draw calls by combining slots on {len(savings_draw_calls_candidates)} meshes."
            ]
        if savings_tris_total > 0:
            top_wins_by_type["Decimate Meshes"] = [
                f"Save {savings_tris_total:,} tris by optimizing {len(savings_tris_candidates)} high-poly meshes."
            ]
        oversized_tex_count = sum(
            1 for r in records if r.material.max_res > self.profile.max_tex_res
        )
        if oversized_tex_count > 0:
            top_wins_by_type["Resize Textures"] = [
                f"Downscale textures on {oversized_tex_count} meshes to save memory."
            ]

        # Scene Health Flags
        scene_health_flags = []
        if total_tris > 10000000:
            scene_health_flags.append("Extreme Poly Count (>10M)")
        if total_slots > 5000:
            scene_health_flags.append("High Draw Call Count (>5k)")
        if unique_texture_paths > 500:
            scene_health_flags.append("Many Unique Textures (>500)")

        # Materials Causing Splits
        mat_mesh_counts = {}
        for r in records:
            for m in r.material.materials:
                if m not in mat_mesh_counts:
                    mat_mesh_counts[m] = {"unique": 0, "over_budget": 0, "slots": []}
                mat_mesh_counts[m]["unique"] += 1
                mat_mesh_counts[m]["slots"].append(r.material.slot_count)
                if r.material.slot_count > self.profile.max_slots:
                    mat_mesh_counts[m]["over_budget"] += 1

        materials_causing_splits = []
        for m, data in mat_mesh_counts.items():
            avg_slots = sum(data["slots"]) / len(data["slots"])
            # Filter: avg_slots >= 4 or significant over-budget meshes
            if avg_slots >= 4 or data["over_budget"] > 5:
                materials_causing_splits.append(
                    (m, data["unique"], data["over_budget"], avg_slots)
                )

        materials_causing_splits.sort(key=lambda x: x[1], reverse=True)
        materials_causing_splits = materials_causing_splits[:5]

        # Missing Texture Impact
        missing_texture_impact = {
            "materials": set(),
            "meshes": set(),
            "top_offenders": [],
        }
        if missing_textures_list:
            missing_paths_set = set(p[0] for p in missing_textures_list)
            affected_meshes = []
            for r in records:
                # Check if this record uses any missing texture
                # We need to check its materials
                rec_missing = False
                for m in r.material.materials:
                    flags = self._material_flags.get(m, {})
                    m_missing = flags.get("missing_paths", [])
                    if any(p in missing_paths_set for p in m_missing):
                        rec_missing = True
                        missing_texture_impact["materials"].add(m)

                if rec_missing:
                    missing_texture_impact["meshes"].add(r.transform)
                    affected_meshes.append(r.transform)

            missing_texture_impact["top_offenders"] = affected_meshes[:5]

        meshes_with_transparency = sum(
            1 for r in records if r.material.uses_transparency
        )
        meshes_with_extra_uvs = sum(1 for r in records if r.mesh.uv_sets > 1)
        meshes_with_high_slots = sum(1 for r in records if r.material.slot_count > 1)

        selection_coverage = {
            "transforms": sum(r.instance_count for r in records),
            "shapes": len(records),
        }

        return SceneOverview(
            total_meshes=total_meshes,
            total_tris=total_tris,
            total_verts=total_verts,
            avg_slots=avg_slots,
            max_slots=max_slots,
            multi_slot_meshes=multi_slot_meshes,
            transparent_meshes=transparent_meshes,
            total_texture_mb=total_texture_mb,
            est_gpu_mb=est_gpu_mb,
            est_gpu_mb_compressed=est_gpu_mb_compressed,
            max_texture_res=max_texture_res,
            large_texture_count=large_texture_count,
            unique_texture_paths=unique_texture_paths,
            top_offenders=top_offenders,
            top_by_tris=top_by_tris,
            top_by_slots=top_by_slots,
            top_by_max_res=top_by_max_res,
            top_by_risk=top_by_risk,
            top_by_transparency=top_by_transparency,
            top_multi_slot_density=top_multi_slot_density,
            fix_plan=fix_plan,
            fix_first_items=fix_first_items,
            texture_dim_histogram=texture_dim_histogram,
            pipeline_integrity=pipeline_integrity,
            scope="Selection",
            profile=self.profile,
            total_slots=total_slots,
            meshes_over_slot_threshold=meshes_over_slot_threshold,
            meshes_over_tri_threshold=meshes_over_tri_threshold,
            total_target_tris=total_target_tris,
            total_slots_over_budget=total_slots_over_budget,
            non_manifold_count=non_manifold_count,
            lamina_count=lamina_count,
            ngon_count=ngon_count,
            high_poly_count=high_poly_count,
            top_repeated_offenders=top_repeated_offenders,
            top_by_effective_score=top_by_effective_score,
            top_materials=top_materials,
            top_savings_draw_calls=top_savings_draw_calls,
            top_savings_tris=top_savings_tris,
            missing_textures=missing_textures_list,
            missing_textures_project=missing_textures_project,
            missing_textures_presets=missing_textures_presets,
            heaviest_textures=heaviest_textures_list,
            texture_type_breakdown=texture_type_breakdown,
            savings_draw_calls_total=savings_draw_calls_total,
            savings_tris_total=savings_tris_total,
            savings_draw_calls_budget=savings_draw_calls_budget,
            savings_tris_budget=savings_tris_budget,
            instance_stats=instance_stats,
            budget_compliance_dist=budget_compliance_dist,
            scene_compliance=scene_compliance,
            effective_total_tris=total_tris,
            effective_total_slots=total_slots,
            raw_total_tris=raw_total_tris,
            raw_total_verts=raw_total_verts,
            raw_total_slots=raw_total_slots,
            pareto_tris=pareto_tris,
            top_wins_by_type=top_wins_by_type,
            scene_health_flags=scene_health_flags,
            materials_causing_splits=materials_causing_splits,
            meshes_with_transparency=meshes_with_transparency,
            meshes_with_extra_uvs=meshes_with_extra_uvs,
            meshes_with_high_slots=meshes_with_high_slots,
            slot_stats=slot_stats,
            slot_budget_delta=slot_budget_delta,
            pareto_slots=pareto_slots,
            pareto_texture_mb=pareto_texture_mb,
            shared_4k_textures=shared_4k_textures,
            single_use_4k_count=single_use_4k_count,
            shared_4k_count=shared_4k_count,
            missing_texture_impact=missing_texture_impact,
            selection_coverage=selection_coverage,
            texture_class_estimates=texture_class_estimates,
        )

    def _resolve_targets(
        self, objects: Optional[List[Any]]
    ) -> Dict[pm.nt.Mesh, List[str]]:
        """Resolves inputs to a map of {MeshShape: [TransformNames]}."""
        if objects is None:
            objects = pm.selected()
            if not objects:
                return {}

        shape_map = {}  # shape -> list of transform names

        def add_shape(shape, transform_node):
            if shape.intermediateObject.get():
                return
            if shape not in shape_map:
                shape_map[shape] = []
            shape_map[shape].append(transform_node.name())

        for obj in objects:
            if isinstance(obj, str):
                try:
                    obj = pm.PyNode(obj)
                except pm.MayaNodeError:
                    continue

            if isinstance(obj, pm.nt.Transform):
                # Get shapes
                s = obj.getShape()
                if s and isinstance(s, pm.nt.Mesh):
                    add_shape(s, obj)

                # Also check children if it's a group?
                # The prompt says "Accept: transforms, groups, sets".
                # Let's do a recursive check or listRelatives.
                # Note: listRelatives returns transforms or shapes depending on flags.
                # We want all descendant meshes.
                # But we need their transforms to count instances correctly.
                # listRelatives(allDescendents=True, type="mesh") returns shapes.
                # From shape, we can get parent transform.

                # If the user selected a group, we want all meshes inside.
                # If the user selected a mesh transform, we handled it above.
                # If it's a group (transform with no shape or non-mesh shape), recurse.
                if not s:
                    descendants = obj.listRelatives(allDescendents=True, type="mesh")
                    for ds in descendants:
                        parent = ds.getParent()
                        if parent:
                            add_shape(ds, parent)

            elif isinstance(obj, pm.nt.Mesh):
                parent = obj.getParent()
                if parent:
                    add_shape(obj, parent)
            elif isinstance(obj, pm.nt.ObjectSet):
                # Handle sets
                members = obj.flatten()
                for m in members:
                    if isinstance(m, pm.nt.Transform):
                        s = m.getShape()
                        if s and isinstance(s, pm.nt.Mesh):
                            add_shape(s, m)
                    elif isinstance(m, pm.nt.Mesh):
                        parent = m.getParent()
                        if parent:
                            add_shape(m, parent)

        return shape_map

    def _build_material_caches(self, shape_map: Dict[pm.nt.Mesh, List[str]]):
        """
        Builds shared caches for material lookups to avoid per-object graph walks.
        Inverts the relationship: Iterates Shading Engines -> Members.
        """
        self._shading_map.clear()
        self._material_map.clear()
        self._material_flags.clear()
        self._global_texture_usage.clear()

        shading_engines = pm.ls(type="shadingEngine")

        target_shapes = set(shape_map.keys())
        target_names = {s.name() for s in target_shapes}

        for se in shading_engines:
            # Get members
            # pm.sets(se, q=True) returns list of objects/components
            members = pm.sets(se, q=True)
            if not members:
                continue

            # Resolve members to shapes
            # Members can be transforms, shapes, or faces (mesh.f[0:10])
            se_name = se.name()

            # Find the surface shader
            surface_shader = se.surfaceShader.inputs()
            mat_name = (
                surface_shader[0].name() if surface_shader else "lambert1"
            )  # Default fallback
            self._material_map[se_name] = mat_name

            # Cache material flags if not done
            if mat_name not in self._material_flags:
                self._material_flags[mat_name] = self._analyze_material_node(
                    surface_shader[0] if surface_shader else None
                )

            # Get textures for this material
            mat_textures = self._material_flags[mat_name].get("textures", [])

            # Track unique objects using this SE
            se_objects = set()
            se_instance_count = 0

            for member in members:
                # Handle component assignments (mesh.f[*])
                node = member.node() if hasattr(member, "node") else member

                # If it's a transform, get shape
                if isinstance(node, pm.nt.Transform):
                    shape = node.getShape()
                    if shape:
                        node = shape

                # If node is not a mesh (e.g. nurbs), skip if we only care about meshes
                if not isinstance(node, pm.nt.Mesh):
                    continue

                node_name = node.name()
                se_objects.add(node_name)

                # Count instances if this is one of our target shapes
                # If it's not in our target list, we might still want to count it for global usage?
                # Yes, global usage should reflect the whole scene if possible, or at least the scope.
                # But shape_map only contains the scope.
                # For "Used by X meshes", we probably want the scope.

                # Find the shape object to look up instances
                # We can't easily look up by name in shape_map keys (objects).
                # But we can check if node is in target_shapes.
                # Since 'node' is a PyNode, equality check works.

                instances = 1
                if node in shape_map:
                    instances = len(shape_map[node])
                else:
                    # Fallback for non-scope objects
                    p = node.getParent()
                    if p:
                        # This is rough, assumes 1 if not in scope map
                        instances = 1

                se_instance_count += instances

                if node_name in target_names:
                    if node_name not in self._shading_map:
                        self._shading_map[node_name] = set()
                    self._shading_map[node_name].add(se_name)

            # Update global texture usage
            obj_count = len(se_objects)
            for tex in mat_textures:
                path = tex["path"]
                if path not in self._global_texture_usage:
                    self._global_texture_usage[path] = {
                        "count": 0,
                        "meshes": set(),
                        "instances": 0,
                    }

                self._global_texture_usage[path][
                    "count"
                ] += obj_count  # This is actually "used by X materials * objects" which is weird.
                # Wait, "Used by X mats" is one metric. "Used by Y meshes" is another.
                # Here we are iterating SEs. One SE = One Material (usually).
                # So for this SE, we add the objects to the set.
                self._global_texture_usage[path]["meshes"].update(se_objects)
                self._global_texture_usage[path]["instances"] += se_instance_count

    def _analyze_material_node(self, mat_node: Optional[pm.PyNode]) -> Dict[str, Any]:
        """Analyzes a single material node for flags (transparency, etc)."""
        flags = {"transparent": False, "type": "Unknown"}
        if not mat_node:
            return flags

        flags["type"] = mat_node.type()

        is_transparent = False

        # Group 1: 0 = Opaque, >0 = Transparent
        # transparency (Color), transmission (Float)
        transparency_attrs = ["transparency", "transmission"]

        for attr in transparency_attrs:
            if mat_node.hasAttr(attr):
                if mat_node.attr(attr).inputs():
                    is_transparent = True
                    break
                val = mat_node.attr(attr).get()
                if isinstance(val, (float, int)):
                    if val > 0.001:
                        is_transparent = True
                        break
                elif isinstance(val, (tuple, list, pm.dt.Vector, pm.dt.Color)):
                    if any(c > 0.001 for c in val):
                        is_transparent = True
                        break

        if not is_transparent:
            # Group 2: 1 = Opaque, <1 = Transparent
            # opacity (Color), cutout_opacity (Float)
            opacity_attrs = ["opacity", "cutout_opacity"]

            for attr in opacity_attrs:
                if mat_node.hasAttr(attr):
                    if mat_node.attr(attr).inputs():
                        is_transparent = True
                        break
                    val = mat_node.attr(attr).get()
                    if isinstance(val, (float, int)):
                        if val < 0.999:
                            is_transparent = True
                            break
                    elif isinstance(val, (tuple, list, pm.dt.Vector, pm.dt.Color)):
                        # If any channel is < 0.999, it's transparent
                        if any(c < 0.999 for c in val):
                            is_transparent = True
                            break

        flags["transparent"] = is_transparent

        # PBR Packing Check
        # Check if Metallic and Roughness are fed by different file nodes (Inefficient)
        # or if they are fed by the same file node (Efficient/Packed)
        unpacked_pbr = False
        if mat_node:
            try:
                # Map attributes to source file nodes
                pbr_sources = {}
                # Common PBR attributes to check for packing
                # (StandardSurface, StingrayPBS, aiStandardSurface)
                check_attrs = {
                    "metallic": ["metalness", "metallic"],
                    "roughness": ["specularRoughness", "roughness"],
                    "ao": ["ambientOcclusion", "ao"],
                }

                for key, attrs in check_attrs.items():
                    for attr in attrs:
                        if mat_node.hasAttr(attr):
                            inputs = mat_node.attr(attr).inputs()
                            if inputs:
                                # Trace back to file node
                                # Simple check: is input a file node?
                                src = inputs[0]
                                if src.type() == "file":
                                    pbr_sources[key] = src.name()
                                # If it's a reverse/luminance/etc, we could trace further,
                                # but for now let's stick to direct connections or simple chains.
                                # If we can't find a file, we ignore it.
                            break

                # If we have both Metallic and Roughness, check if they are different
                if "metallic" in pbr_sources and "roughness" in pbr_sources:
                    if pbr_sources["metallic"] != pbr_sources["roughness"]:
                        unpacked_pbr = True

                # If we have AO and it's different from Metallic or Roughness
                if "ao" in pbr_sources:
                    if (
                        "metallic" in pbr_sources
                        and pbr_sources["ao"] != pbr_sources["metallic"]
                    ):
                        unpacked_pbr = True
                    elif (
                        "roughness" in pbr_sources
                        and pbr_sources["ao"] != pbr_sources["roughness"]
                    ):
                        unpacked_pbr = True

            except Exception:
                pass

        flags["unpacked_pbr"] = unpacked_pbr

        # Find textures
        textures = []
        missing_count = 0
        missing_paths = []
        if mat_node:
            # Use future=False to only look upstream
            try:
                file_nodes = mat_node.listHistory(type="file")
                for fn in file_nodes:
                    path = fn.fileTextureName.get()
                    if path:
                        # Resolve path (handles project relative paths)
                        resolved_path = pm.workspace.expandName(path)

                        # Get resolution
                        # fn.outSize.get() returns [w, h]
                        res = fn.outSize.get()
                        # Get file size
                        size_mb = 0.0
                        if os.path.exists(resolved_path):
                            size_mb = os.path.getsize(resolved_path) / (1024 * 1024)
                        else:
                            missing_count += 1
                            missing_paths.append(resolved_path)

                        # Guess Type
                        tex_type = "Unknown"
                        has_alpha = False

                        # Check for alpha connection
                        if (
                            fn.hasAttr("outTransparency")
                            and fn.outTransparency.inputs()
                        ):
                            has_alpha = True
                        elif fn.hasAttr("outAlpha") and fn.outAlpha.inputs():
                            has_alpha = True

                        # 1. Check connections to material (Priority)
                        connected_channels = set()
                        try:
                            # Check outputs of file node
                            # We look for connections to the material node, possibly via bump nodes
                            for dest_plug in fn.outputs(plugs=True):
                                dest_node = dest_plug.node()

                                # Direct connection
                                if dest_node == mat_node:
                                    attr_name = dest_plug.attrName(
                                        longName=True
                                    ).lower()
                                    connected_channels.add(attr_name)

                                # Via Bump/Normal node
                                elif dest_node.type() in ["bump2d", "bump3d"]:
                                    # Check if bump node connects to mat_node
                                    for b_plug in dest_node.outputs(plugs=True):
                                        if b_plug.node() == mat_node:
                                            connected_channels.add("normal")
                                            break
                        except Exception:
                            pass

                        if connected_channels:
                            # Determine type from channels
                            # Priority: BaseColor, Normal, Emissive, then PBR/Masks
                            if any(
                                x in c
                                for c in connected_channels
                                for x in ["basecolor", "diffuse", "tex_color"]
                            ):
                                tex_type = "BaseColor"
                            elif any(
                                x in c
                                for c in connected_channels
                                for x in ["normal", "bump"]
                            ):
                                tex_type = "Normal"
                            elif any(
                                x in c
                                for c in connected_channels
                                for x in ["emiss", "emission"]
                            ):
                                tex_type = "Emissive"
                            else:
                                # Check for PBR components
                                pbr_comps = []
                                if any("rough" in c for c in connected_channels):
                                    pbr_comps.append("Roughness")
                                if any("metal" in c for c in connected_channels):
                                    pbr_comps.append("Metallic")
                                if any("spec" in c for c in connected_channels):
                                    pbr_comps.append("Specular")
                                if any(
                                    "ao" in c or "ambient" in c
                                    for c in connected_channels
                                ):
                                    pbr_comps.append("AO")
                                if any(
                                    "trans" in c or "opacity" in c
                                    for c in connected_channels
                                ):
                                    pbr_comps.append("Opacity")

                                if len(pbr_comps) > 1:
                                    # Sort for consistency
                                    pbr_comps.sort()
                                    tex_type = "Packed (" + "+".join(pbr_comps) + ")"
                                elif len(pbr_comps) == 1:
                                    tex_type = pbr_comps[0]
                                else:
                                    # Fallback to first channel name
                                    first_channel = list(connected_channels)[0]
                                    # Clean up name (e.g. "TEX_global_diffuse" -> "Global Diffuse")
                                    clean_name = (
                                        first_channel.replace("tex_", "")
                                        .replace("_map", "")
                                        .replace("_", " ")
                                        .title()
                                    )
                                    tex_type = clean_name

                        # 2. Fallback to filename
                        if tex_type == "Unknown":
                            lower_path = resolved_path.lower()
                            if any(
                                x in lower_path
                                for x in [
                                    "_bc",
                                    "_d",
                                    "_diff",
                                    "_albedo",
                                    "_color",
                                    "_basecolor",
                                ]
                            ):
                                tex_type = "BaseColor"
                            elif any(
                                x in lower_path
                                for x in ["_n", "_nrm", "_norm", "_normal"]
                            ):
                                tex_type = "Normal"
                            elif any(
                                x in lower_path
                                for x in [
                                    "_m",
                                    "_met",
                                    "_metal",
                                    "_r",
                                    "_rough",
                                    "_s",
                                    "_spec",
                                    "_orm",
                                    "_arm",
                                    "_mask",
                                ]
                            ):
                                tex_type = "Masks"
                            elif any(
                                x in lower_path for x in ["_e", "_emiss", "_emit"]
                            ):
                                tex_type = "Emissive"

                        # Fallback: Check connection if possible (not implemented here to keep it fast/simple)

                        textures.append(
                            {
                                "path": resolved_path,
                                "res": res,
                                "size_mb": size_mb,
                                "node": fn.name(),
                                "type": tex_type,
                                "has_alpha": has_alpha,
                            }
                        )
            except Exception:
                pass

        flags["textures"] = textures
        flags["missing_textures"] = missing_count
        flags["missing_paths"] = missing_paths
        return flags

    def _analyze_mesh(self, shape: pm.nt.Mesh) -> MeshRecord:
        """Fast mesh analysis."""
        # Tris/Verts
        # polyEvaluate is fast
        # Split calls to avoid mixed return type error
        counts = pm.polyEvaluate(shape, triangle=True, vertex=True)
        bbox = pm.polyEvaluate(shape, boundingBox=True)

        tris = counts.get("triangle", 0)
        verts = counts.get("vertex", 0)

        # UV Sets
        uv_sets = shape.getUVSetNames()
        uv_count = len(uv_sets) if uv_sets else 0
        uv_set_names = uv_sets if uv_sets else []

        # Vertex Colors
        has_colors = len(shape.getColorSetNames()) > 0

        # Skinning
        has_skin = False
        try:
            history = pm.listHistory(shape, type="skinCluster")
            if history:
                has_skin = True
        except Exception:
            pass

        # Instanced
        instanced = shape.isInstanced()

        # Bounds
        # bbox is ((xmin, xmax), (ymin, ymax), (zmin, zmax))
        dx = bbox[0][1] - bbox[0][0]
        dy = bbox[1][1] - bbox[1][0]
        dz = bbox[2][1] - bbox[2][0]
        diag = math.sqrt(dx * dx + dy * dy + dz * dz)

        # Ngons and Non-Manifold Edges
        ngons = 0
        non_manifold_edges = 0
        try:
            import maya.api.OpenMaya as om

            sel = om.MSelectionList()
            sel.add(shape.name())
            dag_path = sel.getDagPath(0)
            mesh_fn = om.MFnMesh(dag_path)

            # Count Ngons
            vertex_counts, _ = mesh_fn.getVertices()
            ngons = sum(1 for count in vertex_counts if count > 4)

            # Non-manifold edges
            nme = pm.polyInfo(shape, nonManifoldEdges=True)
            if nme:
                non_manifold_edges = len(nme)

            # Lamina faces
            lf = pm.polyInfo(shape, laminaFaces=True)
            if lf:
                lamina_faces = len(lf)
            else:
                lamina_faces = 0
        except Exception:
            lamina_faces = 0
            pass

        # Vertex Payload Estimate
        # Pos(12) + Norm(4) + Tan(4) + UV(8*count) + Color(4) + Skin(8)
        # Baseline: 20 bytes
        v_bytes = 20 + (uv_count * 8)
        if has_colors:
            v_bytes += 4
        if has_skin:
            v_bytes += 8  # 4 weights + 4 indices (approx)

        breakdown_parts = ["Pos+Norm+Tan: 20B"]
        if uv_count > 0:
            breakdown_parts.append(f"UVs ({uv_count}): +{uv_count * 8}B")
        if has_colors:
            breakdown_parts.append("Color: +4B")
        if has_skin:
            breakdown_parts.append("Skin: +8B")

        v_breakdown = ", ".join(breakdown_parts)

        return MeshRecord(
            shape_name=shape.name(),
            tris=tris,
            verts=verts,
            uv_sets=uv_count,
            uv_set_names=uv_set_names,
            has_colors=has_colors,
            instanced=instanced,
            bounds_diag=diag,
            ngons=ngons,
            non_manifold_edges=non_manifold_edges,
            lamina_faces=lamina_faces,
            vertex_bytes=v_bytes,
            vertex_byte_breakdown=v_breakdown,
        )

    def _analyze_material(self, shape: pm.nt.Mesh) -> MaterialRecord:
        """Fast material analysis using cache."""
        shape_name = shape.name()
        assigned_ses = self._shading_map.get(shape_name, set())

        # If no SE found in map, it might be using default shader or not in a set?
        # If empty, try direct connection as fallback?
        # The bulk collection should have caught it if it's in a shading group.

        materials = []
        uses_transparency = False
        unpacked_pbr = False
        all_textures = []
        missing_textures = 0
        max_samplers = 0

        for se in assigned_ses:
            mat_name = self._material_map.get(se)
            if mat_name:
                materials.append(mat_name)
                flags = self._material_flags.get(mat_name, {})
                if flags.get("transparent"):
                    uses_transparency = True
                if flags.get("unpacked_pbr"):
                    unpacked_pbr = True
                if flags.get("textures"):
                    mat_textures = flags["textures"]
                    all_textures.extend(mat_textures)

                    # Calculate samplers for this specific material
                    # Dedup by path to handle same texture used multiple times in one shader
                    unique_mat_textures = {t["path"] for t in mat_textures}
                    count = len(unique_mat_textures)
                    if count > max_samplers:
                        max_samplers = count

                if flags.get("missing_textures"):
                    missing_textures += flags["missing_textures"]

        # Aggregate texture stats
        # Dedup by path to avoid double counting same texture used in multiple slots of same material
        unique_textures = {t["path"]: t for t in all_textures}.values()
        texture_count = len(unique_textures)
        max_res = 0
        max_res_is_unique = False
        total_size = 0.0
        est_gpu_size = 0.0
        unique_paths_local = 0

        for t in unique_textures:
            w, h = t["res"]
            max_dim = max(w, h)

            path = t["path"]
            usage_data = self._global_texture_usage.get(path, {})
            usage_count = (
                usage_data.get("count", 0) if isinstance(usage_data, dict) else 0
            )
            is_unique = usage_count == 1

            if is_unique:
                unique_paths_local += 1

            if max_dim > max_res:
                max_res = max_dim
                max_res_is_unique = is_unique
            elif max_dim == max_res:
                if is_unique:
                    max_res_is_unique = True

            total_size += t["size_mb"]

            # Estimate GPU size based on format
            # BaseColor: BC7 (1 byte) or BC1 (0.5 byte) if no alpha
            # Normal: BC5 (1 byte)
            # Masks: BC1 (0.5 byte) or BC4 (0.5 byte)
            # Emissive: BC1 (0.5 byte)
            # Uncompressed: RGBA8 (4 bytes)

            # Mips: 1.33x
            pixels = w * h
            mips = 1.33

            # Uncompressed baseline
            est_gpu_size += (pixels * 4 * mips) / (1024 * 1024)

            # Compressed estimate
            bpp = 1.0  # Default BC7/BC5 (8 bits per pixel = 1 byte)
            tex_type = t.get("type", "Unknown")
            has_alpha = t.get("has_alpha", False)

            if tex_type == "BaseColor":
                if has_alpha:
                    bpp = 1.0  # BC7
                else:
                    bpp = 0.5  # BC1
            elif tex_type == "Normal":
                bpp = 1.0  # BC5
            elif tex_type in [
                "Masks",
                "Roughness",
                "Metallic",
                "AO",
                "Specular",
                "Opacity",
            ]:
                bpp = 0.5  # BC1/BC4
            elif "Packed" in tex_type:
                bpp = 1.0  # Assume BC7 for packed maps to be safe, or BC1 if RGB. Let's use 1.0.
            elif tex_type == "Emissive":
                bpp = 0.5  # BC1

            # Add to compressed total (unique)
            # Note: MaterialRecord stores total for this material's textures.
            # But we are iterating unique textures for this material.
            # Wait, MaterialRecord.est_gpu_size_mb is per-material unique.
            # SceneOverview aggregates this differently.

            # We'll store the compressed size in the record too?
            # MaterialRecord doesn't have est_gpu_size_mb_compressed field yet.
            # We can repurpose or add it.
            # For now, let's just update the logic in SceneOverview using global usage.
            # But here we are in _analyze_material.

            pass  # Logic moved to SceneOverview for global stats, but we need it here for per-asset score?
            # Current score uses total_tex_size_mb (disk).
            # Let's stick to disk size for scoring for now.

        return MaterialRecord(
            slot_count=len(assigned_ses),
            uses_transparency=uses_transparency,
            materials=sorted(list(set(materials))),  # Dedup material names
            texture_count=texture_count,
            max_res=int(max_res),
            total_tex_size_mb=total_size,
            est_gpu_size_mb=est_gpu_size,
            unpacked_pbr=unpacked_pbr,
            missing_textures=missing_textures,
            max_samplers=max_samplers,
            unique_paths_scene=texture_count,
            unique_paths_local=unique_paths_local,
            max_res_is_unique=max_res_is_unique,
        )

    def _calculate_score(
        self, mesh: MeshRecord, mat: MaterialRecord
    ) -> Tuple[float, float, float, List[str], Dict[str, float], str, List[str], int]:
        """Calculates 'badness' scores (Perf/Risk) and generates findings."""
        perf_score = 0.0
        risk_score = 0.0
        findings = []
        breakdown = {}
        fix_plan = []

        # Determine Target Tris (Adaptive)
        target_tris = self.profile.max_tris
        if self.profile.adaptive_tris and self.profile.reference_diag > 0:
            # Linear scaling: size / ref_size * max_tris
            # Clamped between min_tris and max_tris
            ratio = min(1.0, mesh.bounds_diag / self.profile.reference_diag)
            calculated = int(self.profile.max_tris * ratio)
            target_tris = max(self.profile.min_tris, calculated)

        # Delta Summary Construction
        deltas = []
        if mesh.tris > target_tris:
            deltas.append(f"tris +{mesh.tris - target_tris}")
        if mat.slot_count > self.profile.max_slots:
            deltas.append(f"slots +{mat.slot_count - self.profile.max_slots}")
        if mesh.uv_sets > self.profile.max_uvs:
            deltas.append(f"uv +{mesh.uv_sets - self.profile.max_uvs}")
        if mat.max_res > self.profile.max_tex_res:
            deltas.append(f"maxTex +{mat.max_res - self.profile.max_tex_res}")

        delta_summary = " | ".join(deltas)

        # --- Mesh Scoring ---

        # Tris
        if mesh.tris > target_tris:
            delta = mesh.tris - target_tris
            penalty = delta / 1000.0  # 1 point per 1k over
            perf_score += penalty
            findings.append(
                f"High Poly: {mesh.tris} tris (budget {target_tris}, +{delta})"
            )
            breakdown["High Poly"] = penalty
            fix_plan.append(
                f"Reduce tris {mesh.tris:,} -> {target_tris:,} (Decimate/Retopo)"
            )

        # Verts per tri (Bloat check)
        if mesh.tris > 0:
            ratio = mesh.verts / mesh.tris
            if ratio > 3.0:
                penalty = 10.0
                perf_score += penalty
                findings.append(f"Vert Bloat: {ratio:.1f} verts/tri")
                breakdown["Vert Bloat"] = penalty
                fix_plan.append(
                    "Merge vertices / Fix hard edges to reduce vertex count"
                )

        # UV Sets
        if mesh.uv_sets > self.profile.max_uvs:
            delta = mesh.uv_sets - self.profile.max_uvs
            penalty = delta * 5.0
            perf_score += penalty
            uv_names_str = ", ".join(mesh.uv_set_names)
            findings.append(
                f"Extra Vertex Streams: {mesh.uv_sets} UV sets ({uv_names_str}) (budget {self.profile.max_uvs}, +{delta})"
            )
            breakdown["Extra UV Sets"] = penalty
            fix_plan.append(f"Remove {delta} unused UV sets")

        # Vertex Bytes
        target_bytes = 40  # Arbitrary target based on user request
        if mesh.vertex_bytes > target_bytes:
            findings.append(
                f"Vertex Payload: {mesh.vertex_bytes}B (target {target_bytes}B)"
            )

        # Ngons (Risk, not Perf)
        if mesh.ngons > 0:
            # User requested: "Ngons are risk, not perf"
            # Severity: Warning only if exporting triangulated inconsistently
            # Treat as [M] by default, [H] if extreme or over tri budget
            penalty = mesh.ngons * 0.1
            risk_score += penalty

            severity = "[M]"
            if mesh.ngons > 100 or mesh.tris > target_tris:
                severity = "[H]"

            # Report per 10k tris if possible, otherwise raw
            if mesh.tris > 0:
                ngons_per_10k = (mesh.ngons / mesh.tris) * 10000
                findings.append(
                    f"N-gons: {mesh.ngons} ({ngons_per_10k:.1f} per 10k tris) {severity}"
                )
            else:
                findings.append(f"N-gons: {mesh.ngons} {severity}")

            breakdown["N-gons"] = penalty
            fix_plan.append("Triangulate or Quadrangulate N-gons")

        # Non-manifold edges (Risk)
        if mesh.non_manifold_edges > 0:
            penalty = mesh.non_manifold_edges * 2.0
            risk_score += penalty
            findings.append(f"Non-Manifold: {mesh.non_manifold_edges} edges [H]")
            breakdown["Non-Manifold"] = penalty
            fix_plan.append("Cleanup non-manifold geometry")

        # Lamina faces (Risk)
        if mesh.lamina_faces > 0:
            penalty = mesh.lamina_faces * 2.0
            risk_score += penalty
            findings.append(f"Lamina Faces: {mesh.lamina_faces} [H]")
            breakdown["Lamina Faces"] = penalty
            fix_plan.append("Remove lamina faces")

        # --- Material Scoring ---

        # Slots (Draw calls)
        unique_mat_count = len(mat.materials)
        if mat.slot_count > self.profile.max_slots:
            delta = mat.slot_count - self.profile.max_slots
            penalty = delta * 10.0
            perf_score += penalty

            # Check for redundant slots (same material used multiple times)
            redundancy_note = ""
            if mat.slot_count > unique_mat_count:
                redundancy_note = f" ({unique_mat_count} unique materials)"

            findings.append(
                f"Draw Call Split: {mat.slot_count} slots{redundancy_note} (budget {self.profile.max_slots}, +{delta})"
            )
            breakdown["Draw Call Split"] = penalty

            # Specific fix strategy
            if mat.slot_count > unique_mat_count:
                fix_plan.append(
                    f"Consolidate {mat.slot_count - unique_mat_count} redundant slots (Assign same material to all faces)"
                )
            else:
                fix_plan.append(
                    f"Reduce slots {mat.slot_count} -> {self.profile.max_slots} (Merge materials: Combine textures or use Vertex Colors)"
                )

        # Transparency
        if mat.uses_transparency:
            penalty = 5.0
            perf_score += penalty
            findings.append("Transparent")
            breakdown["Transparency"] = penalty

        # Textures
        ideal_res = (mesh.bounds_diag / 100.0) * 512

        if mat.max_res > self.profile.max_tex_res:
            delta = mat.max_res - self.profile.max_tex_res
            if mat.max_res_is_unique and mat.max_res > ideal_res * 2.0:
                penalty = 10.0
                perf_score += penalty
                findings.append(
                    f"Oversized Texture: {mat.max_res}px (vs ideal {int(ideal_res)}px)"
                )
                breakdown["Oversized Texture"] = penalty
                fix_plan.append(f"Downscale textures to {int(ideal_res)}px")
            else:
                # Deterministic line
                findings.append(
                    f"Max texture dimension: {mat.max_res} (budget {self.profile.max_tex_res}, +{delta}) [H]"
                )
                fix_plan.append(f"Downscale textures to {self.profile.max_tex_res}px")

        if mat.total_tex_size_mb > 50.0:  # 50MB soft limit per mesh
            penalty = (mat.total_tex_size_mb - 50.0) * 0.5
            perf_score += penalty
            findings.append(f"Heavy Textures: {mat.total_tex_size_mb:.1f} MB")
            breakdown["Heavy Textures"] = penalty

        # Sampler Count / Packing
        if mat.unpacked_pbr:
            penalty = 15.0
            perf_score += penalty
            findings.append("Unpacked PBR Maps (Inefficient)")
            breakdown["Unpacked PBR"] = penalty
            fix_plan.append("Pack PBR maps (ORM/ARM)")

        # Max Samplers (Per-material limit, usually 16)
        if mat.max_samplers > 8:
            penalty = (mat.max_samplers - 8) * 2.0
            perf_score += penalty
            findings.append(f"Texture Samplers: {mat.max_samplers} samplers")
            breakdown["Texture Samplers"] = penalty

        # Unique Files (Local Impact)
        if mat.unique_paths_local > 0:
            penalty = mat.unique_paths_local * 2.0
            perf_score += penalty
            findings.append(f"Unique Textures: {mat.unique_paths_local} (Local only)")
            breakdown["Unique Textures"] = penalty

        # Shader Complexity (Total textures)
        if mat.texture_count > 5:
            penalty = (mat.texture_count - 5) * 0.5
            perf_score += penalty
            breakdown["Shader Complexity"] = penalty

        if mat.missing_textures > 0:
            penalty = mat.missing_textures * 2.0
            risk_score += penalty
            findings.append(f"Missing Textures: {mat.missing_textures} files [H]")
            breakdown["Missing Textures"] = penalty
            fix_plan.append("Relink missing textures")

        total_score = perf_score + risk_score

        # Deduplicate fix plan
        fix_plan = list(dict.fromkeys(fix_plan))

        return (
            total_score,
            perf_score,
            risk_score,
            findings,
            breakdown,
            delta_summary,
            fix_plan,
            target_tris,
        )

    def _group_records(
        self, records: List[AssetRecord], sort_key: Callable[[AssetRecord], float]
    ) -> List[Tuple[str, List[AssetRecord]]]:
        """Groups records by base name and sorts groups by the max value of sort_key."""
        import re

        groups = {}
        for rec in records:
            # Match base name (e.g. "cargo_details1_b" -> "cargo_details")
            base_name = re.sub(r"[\d_]+[a-zA-Z]?$", "", rec.transform)
            if not base_name:
                base_name = rec.transform

            if base_name not in groups:
                groups[base_name] = []
            groups[base_name].append(rec)

        # Sort groups by max value in group
        sorted_groups = sorted(
            groups.items(),
            key=lambda item: max(sort_key(r) for r in item[1]),
            reverse=True,
        )
        return sorted_groups

    def print_report(self, overview: SceneOverview):
        """Prints a formatted report to the logger."""
        import datetime

        # 1. Context Header
        header_lines = [
            f"Profile: {overview.profile.name}",
            f"  - Max Tris: {overview.profile.max_tris:,} {'(Adaptive)' if overview.profile.adaptive_tris else ''}",
            f"  - Max Slots: {overview.profile.max_slots}",
            f"  - Max Tex Res: {overview.profile.max_tex_res}px",
            f"  - Max UV Sets: {overview.profile.max_uvs}",
        ]

        self.logger.log_box("Scene Audit Report", header_lines)

        col_width = 30

        # 2. Executive Summary (Metrics)
        self.logger.info("")
        self.logger.notice("Executive Summary")
        self.logger.log_divider()

        # Scene Health Flags
        if overview.scene_health_flags:
            for flag in overview.scene_health_flags:
                self.logger.warning(f"  [!] {flag}")
            self.logger.log_raw("")

        self.logger.log_raw(
            f"{'Mesh Shapes':<{col_width}}: {overview.total_meshes} unique"
        )
        self.logger.log_raw(
            f"{'Instances':<{col_width}}: {overview.instance_stats['total_instances']} total ({overview.instance_stats['instanced_shapes']} instanced shapes)"
        )

        self.logger.log_raw(
            f"{'Triangles':<{col_width}}: {overview.effective_total_tris:,} Effective (Raw: {overview.raw_total_tris:,})"
        )
        # Vertices removed as requested

        # Material Slots Block
        if overview.slot_stats:
            s = overview.slot_stats
            self.logger.log_raw(
                f"{'Slots per mesh':<{col_width}}: avg {s['avg_unique']:.1f} | median {s['median']} | p90 {s['p90']} | max {s['max']}"
            )
        self.logger.log_raw(
            f"{'Effective draw calls':<{col_width}}: {overview.effective_total_slots} (slot proxy)"
        )

        # Compressed Breakdown
        comp_total = overview.est_gpu_mb_compressed
        self.logger.log_raw(
            f"{'Est. GPU Compressed':<{col_width}}: {comp_total:.1f} MB (assumed formats by map type)"
        )

        # Missing Files
        missing_count = len(overview.missing_textures_project)
        if missing_count > 0:
            affected_meshes = 0
            if (
                overview.missing_texture_impact
                and overview.missing_texture_impact["meshes"]
            ):
                affected_meshes = len(overview.missing_texture_impact["meshes"])
            self.logger.log_raw(
                f"{'Missing Files':<{col_width}}: {missing_count} project textures (affecting {affected_meshes} meshes)"
            )

        # 3. Fix First (High Impact)
        if (
            overview.fix_first_items
            or overview.total_tris > overview.total_target_tris
            or overview.total_slots_over_budget > 0
        ):
            self.logger.info("")
            self.logger.notice("Fix First (High Impact)")
            self.logger.log_divider()

            # Add Delta Summary
            deltas = []
            if overview.total_tris > overview.total_target_tris:
                deltas.append(
                    f"+{overview.total_tris - overview.total_target_tris:,} tris ({overview.meshes_over_tri_threshold} meshes)"
                )
            if overview.total_slots_over_budget > 0:
                deltas.append(f"+{overview.total_slots_over_budget} slots")

            if deltas:
                self.logger.log_raw(f"Over budget deltas: {' | '.join(deltas)}")
                self.logger.log_raw("")

            # Filter redundant items
            for item in overview.fix_first_items:
                # If we printed deltas, skip generic "Reduce material slots" or "Decimate meshes" summary lines
                if deltas:
                    if "Reduce material slots by" in item and "to reach budget" in item:
                        continue
                    if "Decimate meshes to save" in item:
                        continue
                self.logger.log_raw(f"  - {item}")

        # Pareto View
        if overview.pareto_tris or overview.pareto_slots:
            self.logger.info("")
            self.logger.notice("Pareto View (Top 10)")
            self.logger.log_divider()

            if overview.pareto_tris:
                # Calculate top 10 %
                last_item = overview.pareto_tris[-1]
                match = re.search(r"\(([\d\.]+)% cum\)", last_item)
                if match:
                    top_10_pct = match.group(1)
                    self.logger.log_raw(
                        f"Triangles (Top 10 account for {top_10_pct}%):"
                    )
                else:
                    self.logger.log_raw("Triangles (Effective):")

                for item in overview.pareto_tris:
                    # Strip cum % and ordinal number: "1. Name: 123 (10.0% cum)" -> "Name: 123"
                    clean_item = re.sub(r"\s*\([\d\.]+% cum\)", "", item)
                    clean_item = re.sub(r"^\d+\.\s*", "", clean_item)
                    self.logger.log_raw(f"  {clean_item}")
                self.logger.log_raw("")

            if overview.pareto_slots:
                last_item = overview.pareto_slots[-1]
                match = re.search(r"\(([\d\.]+)% cum\)", last_item)
                if match:
                    top_10_pct = match.group(1)
                    self.logger.log_raw(f"Slots (Top 10 account for {top_10_pct}%):")
                else:
                    self.logger.log_raw("Slots (Effective Draw Calls):")

                for item in overview.pareto_slots:
                    clean_item = re.sub(r"\s*\([\d\.]+% cum\)", "", item)
                    clean_item = re.sub(r"^\d+\.\s*", "", clean_item)
                    self.logger.log_raw(f"  {clean_item}")
                self.logger.log_raw("")

        # 5. Top Offenders (Grouped)
        if overview.top_offenders:
            offenders = [r for r in overview.top_offenders if r.score > 0]
            if offenders:
                self.logger.info("")
                self.logger.notice("Top Issues by Asset (Base Score)")
                self.logger.log_divider()

                for i, rec in enumerate(offenders[:5], 1):
                    self._print_asset_record(rec, i)

        # 7. Category Breakdowns
        self.logger.info("")
        self.logger.notice("Top Offenders by Category")
        self.logger.log_divider()

        if overview.materials_causing_splits:
            headers = ["Material", "Unique Meshes", "Avg Slots", "Over-Slot"]
            data = []
            # Sort by Over-Slot (index 2) then Avg Slots (index 3)
            # materials_causing_splits is list of tuples: (m, unique, over_budget, avg_slots)
            sorted_mats = sorted(
                overview.materials_causing_splits,
                key=lambda x: (x[2], x[3]),
                reverse=True,
            )

            for m, unique, over_budget, avg_slots in sorted_mats[:5]:
                data.append([m, unique, f"{avg_slots:.1f}", over_budget])
            self.log_table(
                data,
                headers,
                title="Materials correlated with high slot meshes",
            )
            self.logger.log_raw("")

        # 8. Textures
        self.logger.info("")
        self.logger.notice("Textures")
        self.logger.log_divider()

        # Histogram
        if overview.texture_dim_histogram:
            self.logger.log_raw("Dimension Histogram:")
            hist = overview.texture_dim_histogram
            self.logger.log_raw(
                f"  4k+: {hist['4k+']} | 2k: {hist['2k']} | 1k: {hist['1k']} | 512: {hist['512']} | <512: {hist['<512']}"
            )

            # 4K Analysis
            if hist["4k+"] > 0:
                self.logger.log_raw(
                    f"  4K Analysis: {hist['4k+']} textures (Shared: {overview.shared_4k_count} | Single-use: {overview.single_use_4k_count})"
                )
                if overview.shared_4k_textures:
                    headers = ["Texture Name", "Mesh Count"]
                    data = []
                    for path, count in overview.shared_4k_textures:
                        name = os.path.basename(path)
                        data.append([name, count])
                    self.log_table(data, headers, title="Top Shared 4K Textures")
            self.logger.log_raw("")

        # Heaviest Textures (Files)
        # Print only if single-use 4K count is high (>25) OR if shared 4K list is empty/small?
        # User said: "Print Heaviest only when single-use 4K is high (e.g., >25)"
        if overview.heaviest_textures and overview.single_use_4k_count > 25:
            headers = ["Path", "Size (MB)", "Res", "Mats", "Meshes", "Inst"]
            data = []
            for (
                path,
                size_mb,
                res,
                mat_count,
                mats,
                mesh_count,
                inst_count,
            ) in overview.heaviest_textures[:10]:
                display_path = path
                if len(display_path) > 50:
                    display_path = "..." + display_path[-47:]

                data.append(
                    [
                        display_path,
                        f"{size_mb:.1f}",
                        f"{res[0]}x{res[1]}",
                        mat_count,
                        mesh_count,
                        inst_count,
                    ]
                )
            self.log_table(data, headers, title="Heaviest Textures (Files)")

        # 9. Pipeline Integrity
        if overview.pipeline_integrity:
            self.logger.info("")
            self.logger.notice("Pipeline Integrity")
            self.logger.log_divider()

            # Collapsed preamble
            if overview.missing_textures_project:
                self.logger.log_raw(
                    f"Missing project files: {len(overview.missing_textures_project)}"
                )

            # Impact Analysis
            if (
                overview.missing_texture_impact
                and overview.missing_texture_impact["meshes"]
            ):
                imp = overview.missing_texture_impact
                if imp["top_offenders"]:
                    self.logger.log_raw(
                        f"Affected top offenders: {', '.join(imp['top_offenders'])}"
                    )

            if overview.missing_textures_project:
                headers = ["Missing File Path", "Mats"]
                data = []
                for path, count, mats in overview.missing_textures_project[:5]:
                    display_path = path if len(path) <= 60 else "..." + path[-57:]
                    data.append([display_path, count])
                self.log_table(data, headers, title="Missing Project Files")

        # 10. Data Assumptions
        self.logger.info("")
        self.logger.notice("Data Assumptions")
        self.logger.log_divider()
        self.logger.log_raw(
            "- GPU Size Est: Uncompressed RGBA8 + 33% Mips. Actual usage depends on engine compression (BC1/BC3/ASTC)."
        )
        self.logger.log_raw(
            "- Compression assumptions: BaseColor BC7/BC1, Normal BC5, Masks (AO/Rough/Metal) BC4/BC1 (varies)."
        )
        self.logger.log_raw(
            "- Unique Texture Disk Size: Sum of file sizes on disk for unique paths referenced by materials."
        )
        self.logger.log_raw(
            "- Effective Score: Base Score * Instance Count. Prioritize high effective scores."
        )

    def _print_asset_record(self, rec: AssetRecord, rank: int, effective: bool = False):
        """Helper to print a single asset record."""
        effective_score = rec.score * max(1, rec.instance_count)

        # Header
        severity_label = "Fail" if rec.score > 50 else "Warn"
        score_display = f"Score: {rec.score:.0f}"
        if effective:
            score_display = f"Effective: {effective_score:.0f} (Base: {rec.score:.0f})"

        self.logger.warning(
            f"{rec.transform:<40} {rec.instance_count} instances | {score_display} | Rank: #{rank}"
        )

        # Delta Summary
        if rec.delta_summary:
            self.logger.log_raw(f"  Deltas: {rec.delta_summary}")

        # Vertex Payload removed as requested

        # Evidence Chain
        # Slots
        if rec.material.slot_count > 1:
            # Limit to top 3 + ...
            mats = rec.material.materials
            # Keep only first 2-3 names, don't wrap
            limit = 3
            mat_str = ", ".join(mats[:limit])
            if len(mats) > limit:
                mat_str += "..."
            self.logger.log_raw(f"  Slots ({rec.material.slot_count}): {mat_str}")

        # Findings with Confidence
        for finding in rec.findings:
            # Add confidence labels based on finding text
            conf = "[L]"
            if any(
                x in finding
                for x in [
                    "High Poly",
                    "Vert Bloat",
                    "Draw Call",
                    "Non-Manifold",
                    "Lamina",
                    "Missing",
                ]
            ):
                conf = "[H]"
            elif any(x in finding for x in ["UV sets", "Transparent", "Oversized"]):
                conf = "[M]"

            # Filter out Vertex Payload findings if they exist in the list
            if "Vertex Payload" in finding:
                continue

            # Remove dual tags if present in finding text already
            # e.g. "N-gons: 5 [M]" -> "N-gons: 5"
            clean_finding = re.sub(r"\s*\[[HML]\]", "", finding)

            self.logger.log_raw(f"  - {clean_finding} {conf}")

        # Fix Plan
        if rec.fix_plan:
            self.logger.log_raw("  Fix Plan:")
            for step in rec.fix_plan[:3]:  # Limit to 3 steps
                # Update UV sets text
                if "Remove" in step and "unused UV sets" in step:
                    step = step.replace(
                        "unused UV sets",
                        "extra UV sets (if not required by export/profile)",
                    )
                self.logger.log_raw(f"    > {step}")

        self.logger.log_raw("")  # Spacer
