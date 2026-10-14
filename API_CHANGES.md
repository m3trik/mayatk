# mayatk — API Changes

_Diff vs the last release (origin/main @ 1dfe4057)._

## Added (38)

- `audio_utils/_audio_utils.py::LEGACY_CACHE_DIR_NAMES(constant)`
- `env_utils/_env_utils.py::EnvUtils.scene_source_images_dir(subfolder: str = '', create: bool = False) -> str`
- `env_utils/_env_utils.py::EnvUtils.scene_workspace() -> Optional[ptk.Workspace]`
- `env_utils/_env_utils.py::EnvUtils.twin_save_target(cls, scene: Optional[str] = None) -> Optional[str]`
- `env_utils/blender_bridge/_scene_import.py::BAKE_CACHE_KIND(constant)`
- `env_utils/blender_bridge/_scene_import.py::BAKE_INFO_KEY(constant)`
- `env_utils/blender_bridge/_scene_import.py::BAKE_INFO_SOURCE(constant)`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake_dir(cls, project: Optional[str] = None, create: bool = False) -> Optional[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake_info(cls, baked_path: str) -> Dict[str, str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake_slot(cls, src_path: str, project: Optional[str] = None) -> Optional[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.purge_unused_bakes(self, project: Optional[str] = None, dry_run: bool = False) -> List[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.rehome_bake(self, baked_path: str, project: Optional[str] = None) -> Optional[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.stamp_bake_info(cls, baked_path: str, info: Mapping[str, str]) -> None`
- `env_utils/blender_bridge/templates/_bake_scene.py::BAKE_INFO(constant)`
- `env_utils/blender_bridge/templates/_bake_scene.py::MEDIA_HOME(constant)`
- `env_utils/blender_bridge/templates/_bake_scene.py::PROJECT(constant)`
- `env_utils/blender_bridge/templates/_save_scene.py::pack_payload_images()`
- `env_utils/reference_manager/_reference_manager.py::ReferenceManager.purge_unused_bakes(self, dry_run: bool = False) -> list`
- `env_utils/reference_manager/reference_manager_controller.py::ReferenceManagerController.scene_notes(self) -> 'ptk.SceneNotes'`
- `mat_utils/game_shader.py::GameShaderSlots.source_images_dir(self) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.toolbag_scene_dir(cls) -> str`
- `mat_utils/shader_templates/_shader_templates.py::ShaderTemplates.migrate_package_templates(cls, store: 'ptk.PresetStore') -> List[str]`
- `mat_utils/shader_templates/_shader_templates.py::ShaderTemplates.store(cls) -> 'ptk.PresetStore'`
- `mat_utils/shader_templates/shader_templates_slots.py::ShaderTemplatesSlots.source_images_dir(self) -> str`
- `node_utils/data_nodes.py::DataNodes.scene_project_root(cls) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.scene_source(cls, scene: Optional[str] = None) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.scene_source_images_dir(cls, subfolder: str = '', create: bool = False) -> str`
- `node_utils/data_nodes.py::DataNodes.scene_workspace(cls) -> Optional[ptk.Workspace]`
- `node_utils/data_nodes.py::DataNodes.set_scene_source(cls, scene: str, source: Optional[str] = None) -> Optional[str]`
- `render_utils/_render_utils.py::RenderUtils.ensure_arnold_options(cls) -> str`
- `render_utils/_render_utils.py::RenderUtils.prepare_arnold_preview(cls) -> Dict[str, List[str]]`
- `render_utils/_render_utils.py::RenderUtils.render_preset_store() -> 'ptk.PresetStore'`
- `render_utils/_render_utils.py::RenderUtils.render_settings(cls) -> Dict[str, Any]`
- `render_utils/_render_utils.py::RenderUtils.set_render_settings(cls, values: Dict[str, Any]) -> int`
- `render_utils/_render_utils.py::RenderUtils.sync_arnold_backface_culling(cls) -> List[str]`
- `rig_utils/shadow_rig/_shadow_rig.py::ShadowRig.default_texture_dir() -> str`
- `rig_utils/shadow_rig/shadow_preview.py::ShadowPreview.preview_dir(png: Optional[str] = None) -> str`
- `rig_utils/shadow_rig/shadow_preview.py::ShadowPreview.texture_path(cls, png: str) -> str`

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

## Moved (1)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `ui_utils/maya_bridge_slots_base.py::MayaBridgeSlotsBase.default_output_dir`

## Signature changed (9)

- `env_utils/_env_utils.py::EnvUtils.scene_source`
  - was: `(cls, scene: Optional[str] = None) -> Optional[str]`
  - now: `(scene: Optional[str] = None) -> Optional[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake`
  - was: `(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None) -> Any`
  - now: `(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None, info: Optional[Mapping[str, str]] = None, media_home: Optional[str] = None, project: Optional[str] = None) -> Any`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', **script_opts: Any) -> str`
  - now: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', project: Optional[str] = None, **script_opts: Any) -> str`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake_source`
  - was: `(baked_path: str) -> Optional[str]`
  - now: `(cls, baked_path: str) -> Optional[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.import_payload`
  - was: `(self, payload_path: str, *, via: str = 'fbx', fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', adopt_scene: bool = False, scene_data: bool = True, step: Optional[Callable[[int, int, str], Any]] = None) -> List[str]`
  - now: `(self, payload_path: str, *, via: str = 'fbx', fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', adopt_scene: bool = False, scene_data: bool = True, step: Optional[Callable[[int, int, str], Any]] = None, media_home: Optional[str] = None) -> List[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.render_bake_script`
  - was: `(self, src_path: str, out_path: str) -> str`
  - now: `(self, src_path: str, out_path: str, info: Optional[Mapping[str, str]] = None, media_home: Optional[str] = None, project: Optional[str] = None) -> str`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.render_template`
  - was: `(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None) -> Optional[str]`
  - now: `(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, save_dir: Optional[str] = None) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.send`
  - was: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
  - now: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, scene_dir: Optional[str] = None) -> Optional[Dict[str, Any]]`
- `mat_utils/texture_baker.py::TextureBaker.default_output_dir`
  - was: `(subdir: str = 'baked_textures') -> str`
  - now: `(subdir: str = 'baked') -> str`
