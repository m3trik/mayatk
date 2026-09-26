# mayatk — API Changes

_Diff vs the last release (origin/main @ 74a06db)._

## Added (14)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.delete_stale_shots(self) -> None`
- `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal(stale, parent=None) -> bool`
- `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots(self) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_delete_stale(self)`
- `core_utils/diagnostics/audit_records.py::MaterialAudit(class)`
- `core_utils/diagnostics/audit_records.py::SceneOverview(class)`
- `core_utils/diagnostics/audit_records.py::TRANSPARENCY_BLEND(constant)`
- `core_utils/diagnostics/audit_records.py::TRANSPARENCY_MASKED(constant)`
- `core_utils/diagnostics/audit_records.py::TRANSPARENCY_OPAQUE(constant)`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.adopt_sidecar(cls, export_path: str, *, base_stem: bool = False) -> bool`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.inherited_from(cls) -> Optional[str]`
- `env_utils/maya_connection.py::MayaConnection.close_launched(self, force: bool = False) -> bool`
- `mat_utils/_mat_utils.py::MatUtils.is_frame_sequence(cls, path: str) -> bool`
- `node_utils/data_nodes.py::DataNodes.scene_path(cls) -> str`

## Deprecations (11)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — remove in 0.20.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — remove in 0.20.0, not before 2026-10-23
- **HELD** `mat_utils/render_opacity/render_effects.py::RenderEffects.setup` — remove in 0.20.0, not before 2026-10-23
- **HELD** `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name` — remove in 0.20.0, not before 2026-10-23
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — remove in 0.21.0, not before 2026-10-24

## Moved (1)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `node_utils/data_nodes.py::DataNodes.project_root`

## Signature changed (3)

- `core_utils/diagnostics/scene_audit.py::SceneAnalyzer.analyze`
  - was: `(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None, sections: Optional[List[str]] = None) -> List[AssetRecord]`
  - now: `(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None, sections: Optional[List[str]] = None, scope: Optional[str] = None) -> List[AssetRecord]`
- `core_utils/diagnostics/scene_audit.py::SceneAnalyzer.format_audit_html`
  - was: `(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]`
  - now: `(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[List[str]] = None, scope: Optional[str] = None) -> Dict[str, str]`
- `core_utils/diagnostics/scene_audit.py::SceneAnalyzer.format_audit_text`
  - was: `(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]`
  - now: `(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, sections: Optional[List[str]] = None, scope: Optional[str] = None) -> Dict[str, str]`
