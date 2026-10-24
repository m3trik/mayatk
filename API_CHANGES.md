# mayatk — API Changes

_Diff vs the last release (origin/main @ aeb92e81)._

## Removed (19)

- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — was `(cls, export_dir: str) -> int`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name` — was `(self, name: str, name_regex: Optional[str] = None) -> str`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — was `(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — was `(self, objects: Optional[List[str]] = None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — was `(self, objects: Optional[List[str]] = None, search_dirs: Optional[List[str]] = None, walk: bool = True) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — was `(self, objects: Optional[List[str]] = None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — was `(cls) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — was `(self, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — was `(self, dirs_by_map: Dict[str, str], objects: Optional[List[str]] = None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — was `(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.clear_bake_set` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_scope` — was `(self, index) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.list_changed_objects` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.open_sourceimages` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.revert_to_source` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_bake_set` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_bake_set_init` — was `(self, widget) -> None`
- `mat_utils/bake_sets.py::LightmapBakeSet` — was `(class)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.setup` — was `(cls, *args, **kwargs) -> Dict[str, Dict]`

## Added (34)

- `anim_utils/shots/shots_slots.py::ShotsController.on_drop_stale_shots_changed(self, checked: bool) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.chk_drop_stale_shots(self, checked)`
- `env_utils/material_records.py::MaterialRecords(class)`
- `env_utils/material_records.py::MaterialRecords.blinn_shininess(eccentricity: float) -> float`
- `env_utils/material_records.py::MaterialRecords.entries(cls, objects: Iterable[str], notes: Optional[List[str]] = None) -> List[Dict[str, Any]]`
- `env_utils/material_records.py::MaterialRecords.export_record(cls, ctx: 'ptk.ExportContext') -> Optional['ptk.Record']`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_breakdown(self, output_dir: Optional[str] = None, relocate: bool = False) -> 'ptk.ReportDoc'`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_maps(self, report: Optional['ptk.StaleReport'] = None, output_dir: Optional[str] = None, relocate: bool = False) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.combine(self, maps: Optional[List[str]] = None, size: Optional[int] = None, merge_materials: bool = True, on_progress: Optional[Callable[[int, int, str], Any]] = None) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.combine_sizes(self, maps: Optional[List[str]] = None) -> List[Tuple[int, float]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.map_folders(self, objects: Optional[List[str]] = None) -> Dict[str, List[str]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.rebake(self, objects: Optional[List[str]] = None, packing: Optional[str] = None, relocate: bool = False, **bake_kwargs) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.rebake_report(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, relocate: bool = False, intensity: Optional[float] = None) -> 'ptk.StaleReport'`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.track(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_page(self, index) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_page_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.combine_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.fix_all(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.open_map_folder(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.open_output(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.rebake_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.revert_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_by_state(self, kind: str) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.show_bake_state(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.tbl_maps_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.track_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.fork_lightmap(cls, old_name: str, new_name: str) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.strangers(cls, folder: str, stem: str, ext: str = '.exr', claims: Optional[Dict[str, FrozenSet[str]]] = None) -> Set[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.track(cls, objects: Optional[List[str]] = None, lighting: Optional[Dict[str, Any]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.untracked(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.wrote(cls, map_name: str, writers: Optional[Dict[str, str]] = None) -> bool`
- `mat_utils/shader_attribute_map.py::ShaderAttributeMap.open_emission_gate(cls, shader: str, shader_type: Optional[str] = None) -> bool`
- `node_utils/data_nodes.py::DataNodes.discard_if_empty(cls, scope: ptk.Scope) -> bool`
- `node_utils/data_nodes.py::DataNodes.remove(cls, scope: ptk.Scope, key: str) -> bool`

## Deprecations (10)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `env_utils/_env_utils.py::EnvUtils.is_plugin_loaded` — remove in 0.23.0, not before 2026-11-02
- **HELD** `env_utils/_env_utils.py::EnvUtils.load_plugin` — remove in 0.23.0, not before 2026-11-02
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `uv_utils/texture_transfer.py::TextureTransfer.CONSTANT_ATTRS` — remove in 0.23.0, not before 2026-11-03
- **HELD** `env_utils/scene_exporter/task_manager.py::TaskManager.check_uv_snapshots` — remove in 0.24.0, not before 2026-11-07
- `env_utils/scene_state.py::SceneState.read` — remove in 0.29.0, not before 2026-11-23
- `env_utils/scene_state.py::SceneState.source` — remove in 0.29.0, not before 2026-11-23

## Signature changed (22)

- `anim_utils/_anim_utils.py::AnimUtils.get_redundant_flat_keys`
  - was: `(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, as_strings: bool = False, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False) -> List[Tuple[Any, List[float]]]`
  - now: `(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, *, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False) -> List[Tuple[Any, List[float]]]`
- `anim_utils/_anim_utils.py::AnimUtils.get_static_curves`
  - was: `(cls, objects: List[str], value_tolerance: float = 1e-05, recursive: bool = False, as_strings: bool = False) -> List[str]`
  - now: `(cls, objects: List[str], value_tolerance: float = 1e-05, recursive: bool = False) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.objects_to_curves`
  - was: `(objects: Union[str, List[str]], recursive: bool = False, as_strings: bool = False, through_blends: bool = True) -> List[str]`
  - now: `(objects: Union[str, List[str]], recursive: bool = False, *, through_blends: bool = True) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.optimize_keys`
  - was: `(cls, objects: Union[str, List[str]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, through_blends: bool = True, on_delete: Optional[Callable[[str, Optional[List[float]]], Any]] = None) -> List[str]`
  - now: `(cls, objects: Union[str, List[str]], value_tolerance: float = 0.001, *, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, through_blends: bool = True, on_delete: Optional[Callable[[str, Optional[List[float]]], Any]] = None) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.simplify_curve`
  - was: `(cls, objects: List[str], value_tolerance: float = 0.001, time_tolerance: float = 0.001, recursive: bool = False, as_strings: bool = False, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False, on_delete: Optional[Callable[[str, List[float]], Any]] = None) -> List[str]`
  - now: `(cls, objects: List[str], value_tolerance: float = 0.001, *, recursive: bool = False, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False, on_delete: Optional[Callable[[str, List[float]], Any]] = None) -> List[str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.generate_export_path`
  - was: `(self, version_format: str = '', extension: str = '.fbx', output_format: Optional[str] = None) -> str`
  - now: `(self, *, extension: str = '.fbx', output_format: Optional[str] = None) -> str`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.name_context`
  - was: `(self, name_regex: Optional[str] = None) -> Dict[str, str]`
  - now: `(self) -> Dict[str, str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.perform_export`
  - was: `(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: Optional[str] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None) -> bool`
  - now: `(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, *, log_level: Optional[str] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None) -> bool`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.resolve_export_path`
  - was: `(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', name_regex: Optional[str] = None, report: bool = True, version_format: str = '', timestamp: bool = False) -> Dict[str, Any]`
  - now: `(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', *, report: bool = True) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.commit_lightmap`
  - was: `(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None, uv_rects: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
  - now: `(self, mapping: Dict[str, str], *, scale_offsets: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.rebake_changed`
  - was: `(self, packing: Optional[str] = None, reach: Optional[float] = None, lighting: bool = True, **bake_kwargs) -> LightmapBakeResult`
  - now: `(self, packing: Optional[str] = None, reach: Optional[float] = None, lighting: bool = True, relocate: bool = False, **bake_kwargs) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.stale_report`
  - was: `(self, objects: Optional[List[str]] = None, reach: Optional[float] = None) -> 'ptk.StaleReport'`
  - now: `(self, objects: Optional[List[str]] = None, reach: Optional[float] = None, output_dir: Optional[str] = None, relocate: bool = False) -> 'ptk.StaleReport'`
- `mat_utils/_mat_utils.py::MatUtils.create_stingray_shader`
  - was: `(cls, name, opacity=False, opacity_mode=None)`
  - now: `(cls, name, *, opacity_mode=None)`
- `mat_utils/_mat_utils.py::MatUtils.filter_materials_by_objects`
  - was: `(objects: List[str], as_strings: bool = True, include_displacement: bool = False) -> List[str]`
  - now: `(objects: List[str], *, include_displacement: bool = False) -> List[str]`
- `mat_utils/_mat_utils.py::MatUtils.get_mats`
  - was: `(objs=None, as_strings=True, mat_type=None, include_displacement=False) -> List[str]`
  - now: `(objs=None, *, mat_type=None, include_displacement=False) -> List[str]`
- `mat_utils/_mat_utils.py::MatUtils.load_stingray_graph`
  - was: `(cls, mat, opacity_mode=None, opacity: bool = False) -> bool`
  - now: `(cls, mat, opacity_mode=None) -> bool`
- `mat_utils/_mat_utils.py::MatUtils.resolve_opacity_mode`
  - was: `(cls, opacity_mode=None, opacity: bool = False) -> str`
  - now: `(cls, opacity_mode=None) -> str`
- `mat_utils/_mat_utils.py::MatUtils.resolve_stingray_graph`
  - was: `(cls, opacity_mode=None, opacity: bool = False)`
  - now: `(cls, opacity_mode=None)`
- `mat_utils/game_shader.py::GameShader.setup_stringray_node`
  - was: `(self, name: str, opacity: bool = False, opacity_mode: str = None) -> object`
  - now: `(self, name: str, *, opacity_mode: str = None) -> object`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.prepare_for_export`
  - was: `(cls, objects=None) -> List[str]`
  - now: `(cls) -> List[str]`
- `rig_utils/shadow_rig/_shadow_rig.py::ShadowRig.create`
  - was: `(cls, targets, light_pos=(5, 10, 5), texture_res=512, axis='auto', source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, shader_type='standard', rig_type='projected', horizon_size=None, horizon_spans=None)`
  - now: `(cls, targets, light_pos=(5, 10, 5), texture_res=512, *, source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, shader_type='standard', rig_type='projected', horizon_size=None, horizon_spans=None)`
- `rig_utils/shadow_rig/_shadow_rig.py::ShadowRig.create_silhouette_texture`
  - was: `(self, size=512, axis='auto', recursive=True, *, uniform_alpha=True, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.0, path=None, refit=True, source_size=None)`
  - now: `(self, size=512, *, recursive=True, uniform_alpha=True, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.0, path=None, refit=True, source_size=None)`
