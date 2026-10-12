# mayatk — API Changes

_Diff vs the last release (origin/main @ 87e1ca96)._

## Removed (2)

- `anim_utils/shots/shot_manifest/export_menu.py::ExportMenuMixin` — was `(class)`
- `anim_utils/shots/shot_manifest/export_menu.py::ExportMenuMixin.export` — was `(self) -> None`

## Added (24)

- `anim_utils/_anim_utils.py::AnimUtils.euler_filter(objects: Union[str, List[str]]) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.get_key_curves(curves: List[str]) -> List['ptk.KeyCurve']`
- `anim_utils/_anim_utils.py::AnimUtils.shift_key_values(edits: Dict[str, List[Tuple[float, float]]]) -> int`
- `anim_utils/shots/shot_manifest/export_preview.py::ExportPreviewMixin(class)`
- `anim_utils/shots/shot_manifest/export_preview.py::ExportPreviewMixin.export(self) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync(class)`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.detach(self) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.key_targets(self, groups=None) -> list`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_infinity(self, side: str, mode: str, curves) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_key_menu(self, menu, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_key_selection(self, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_keys_deleted(self, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_keys_edited(self, edits) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_keys_inserted(self, items) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_tangent_type(self, type_id: str, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_tangents_broken(self, broken: bool, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_tangents_edited(self, rows) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_weighted(self, weighted: bool, curves) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.outline(self) -> List[dict]`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.sync(self, full: bool = False) -> None`
- `anim_utils/shots/shot_sequencer/key_menu.py::KeyMenuMixin.register_key_targets(self, editor_name: str, source) -> None`
- `anim_utils/shots/shot_sequencer/segment_collector.py::SegmentCollector.summary_times(segments, claimed_times) -> list`
- `env_utils/blender_bridge/templates/_bake_scene.py::covering_take(mel, path)`
- `env_utils/handoff_export.py::MayaExportMixin.stepped_keys(nodes: Optional[List[str]] = None, spell: Optional[Callable] = None) -> Dict[str, Dict[str, List[float]]]`

## Deprecations (19)

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
- **HELD** `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — remove in 0.21.0, not before 2026-10-24
- **HELD** `env_utils/_env_utils.py::EnvUtils.is_plugin_loaded` — remove in 0.23.0, not before 2026-11-02
- **HELD** `env_utils/_env_utils.py::EnvUtils.load_plugin` — remove in 0.23.0, not before 2026-11-02
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `uv_utils/texture_transfer.py::TextureTransfer.CONSTANT_ATTRS` — remove in 0.23.0, not before 2026-11-03
- **HELD** `env_utils/scene_exporter/task_manager.py::TaskManager.check_uv_snapshots` — remove in 0.24.0, not before 2026-11-07

## Signature changed (4)

- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.format_behavior_html`
  - was: `(behaviors, broken=(), status_color=None, stale=(), disabled=()) -> str`
  - now: `(behaviors, broken=(), status_color=None, stale=(), disabled=(), marked=()) -> str`
- `anim_utils/shots/shot_manifest/shot_manifest_controller.py::ShotManifestController.assess`
  - was: `(self, skip_key_check: bool = False) -> None`
  - now: `(self, skip_key_check: bool = False, note: str = '') -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.scale_object_keys`
  - was: `(self, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None`
  - now: `(self, obj: str, old_start: float, old_end: float, new_start: float, new_end: float, _scaled: Optional[set] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.import_scene`
  - was: `(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True) -> List[str]`
  - now: `(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True, *, stash: bool = True, file_options: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None) -> List[str]`
