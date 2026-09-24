# mayatk — API Changes

_Diff vs the last release (origin/main @ 3988123)._

## Added (19)

- `env_utils/blender_bridge/templates/bake_lightmaps.py::LIGHTMAP_ADAPTIVE(constant)`
- `env_utils/blender_bridge/templates/bake_lightmaps.py::LIGHTMAP_BESIDE_TEXTURES(constant)`
- `env_utils/blender_bridge/templates/bake_lightmaps.py::LIGHTMAP_BOUNCES(constant)`
- `env_utils/blender_bridge/templates/bake_lightmaps.py::lightmap_records(meshes)`
- `env_utils/reference_manager.py::ReferenceManagerController.set_maya_project(self)`
- `env_utils/reference_manager.py::ReferenceManagerSlots.btn_copy_path(self)`
- `env_utils/reference_manager.py::ReferenceManagerSlots.txt_subfolder_structure(self, text)`
- `env_utils/usd.py::UsdUtils.conform_roots(roots: List[str], conform: Optional[Tuple[float, float]], name: str) -> Optional[str]`
- `env_utils/usd.py::UsdUtils.stage_conform(cls, usd_path: str) -> Optional[Tuple[float, float]]`
- `env_utils/usd.py::UsdUtils.top_transforms(nodes: List[str]) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.migrate_folder_hints(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.superseding(cls, objects: List[str]) -> Iterator[List[str]]`
- `mat_utils/arnold_bridge.py::ArnoldBridge.temporary(self, materials: Union[str, List[str]]) -> Iterator[List[str]]`
- `mat_utils/arnold_bridge.py::ArnoldBridge.unrenderable_materials(cls) -> List[str]`
- `node_utils/data_nodes.py::DataNodes.install_path_rebase(cls) -> bool`
- `node_utils/data_nodes.py::DataNodes.project_root(cls) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.remove_path_rebase(cls) -> None`
- `uv_utils/_uv_utils.py::UvUtils.pins_lifted(uvs)`
- `uv_utils/rizom_bridge/parameters.py::HOST_TOKEN_DEFAULTS(constant)`

## Deprecations (10)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — remove in 0.20.0, not before 2026-10-23
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — remove in 0.20.0, not before 2026-10-23
- `mat_utils/render_opacity/render_effects.py::RenderEffects.setup` — remove in 0.20.0, not before 2026-10-23
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name` — remove in 0.20.0, not before 2026-10-23

## Signature changed (3)

- `env_utils/blender_bridge/_blender_bridge.py::BlenderBridge.bake_lightmaps`
  - was: `(self, out: Optional[str] = None, objects: Optional[List[Any]] = None, *, environment_hdr: Optional[str] = None, quality: Optional[str] = None, resolution: Optional[int] = None, samples: Optional[int] = None, packing: Optional[str] = None, scene_lights: Optional[bool] = None, light_strength: Optional[float] = None, timeout: Optional[float] = None, reassemble: bool = True, **params: Any) -> Optional[Dict[str, Any]]`
  - now: `(self, out: Optional[str] = None, objects: Optional[List[Any]] = None, *, environment_hdr: Optional[str] = None, quality: Optional[str] = None, resolution: Optional[int] = None, samples: Optional[int] = None, bounces: Optional[int] = None, packing: Optional[str] = None, scene_lights: Optional[bool] = None, light_strength: Optional[float] = None, timeout: Optional[float] = None, reassemble: bool = True, **params: Any) -> Optional[Dict[str, Any]]`
- `env_utils/usd.py::UsdUtils.import_scene`
  - was: `(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True, read_animation: bool = True) -> List[str]`
  - now: `(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True, read_animation: bool = True, conform: bool = False) -> List[str]`
- `mat_utils/game_shader.py::GameShader.setup_stringray_node`
  - was: `(self, name: str, opacity: bool, opacity_mode: str = None) -> object`
  - now: `(self, name: str, opacity: bool = False, opacity_mode: str = None) -> object`
