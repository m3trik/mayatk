# mayatk — API Changes

_Diff vs the last release (origin/main @ 89c6db1)._

## Removed (2)

- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_find_and_copy_textures_init` — was `(self, widget)`
- `ui_utils/maya_bridge_slots_base.py::MayaBridgeSlotsBase.live_param_tooltips` — was `(self)`

## Added (40)

- `core_utils/preview.py::CleanupContract.snapshot_created(self) -> Set[str]`
- `display_utils/_display_utils.py::DisplayUtils.get_isolated_panels() -> List[str]`
- `edit_utils/naming/_naming.py::Naming.SUFFIX_TYPES(cls) -> Tuple[Tuple[str, str, str, str], ...]`
- `edit_utils/naming/_naming.py::Naming.affix_rules(cls, overrides: Optional[Dict[str, str]] = None, modes: Optional[Dict[str, str]] = None) -> Dict[str, 'ptk.AffixRule']`
- `env_utils/_env_utils.py::EnvUtils.texture_search_dirs(path: Optional[str] = None) -> List[str]`
- `env_utils/handoff_export.py::MayaExportMixin.lightmap_search_dirs(self) -> List[str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.confirm(self, question: str) -> bool`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.confirm(self, question: str) -> bool`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.ignore_groups_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths(self, objects: Optional[List[str]] = None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies(self, objects: Optional[List[str]] = None, search_dirs: Optional[List[str]] = None, walk: bool = True) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths(self, objects: Optional[List[str]] = None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps(self, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps(self, dirs_by_map: Dict[str, str], objects: Optional[List[str]] = None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs(cls, objects: Optional[List[str]] = None) -> List[str]`
- `mat_utils/_mat_utils.py::MatUtils.has_path_token(cls, path: str) -> bool`
- `mat_utils/_mat_utils.py::MatUtils.texture_tiles(cls, path: str) -> List[str]`
- `mat_utils/_mat_utils.py::MatUtils.token_wildcard(cls, path: str, wildcard: Optional[str] = '*') -> str`
- `mat_utils/game_shader.py::GameShaderSlots.opacity_mode(self) -> Optional[str]`
- `mat_utils/game_shader.py::GameShaderSlots.txt000_init(self, widget)`
- `mat_utils/mat_updater.py::MatUpdaterSlots.acceptable_types(self)`
- `mat_utils/mat_updater.py::MatUpdaterSlots.shader_type(self)`
- `mat_utils/shader_attribute_map.py::ShaderAttributeMap.map_toggle_state(cls, attr: str) -> Tuple[str, int]`
- `mat_utils/shader_attribute_map.py::ShaderAttributeMap.select_color_alpha(cls, shader: str, file_node: str) -> bool`
- `mat_utils/substance_bridge/parameters.py::Parameters.affix_parts(value: 'Any', *, default: str = 'prefix') -> 'tuple[str, str]'`
- `node_utils/_node_utils.py::NodeUtils.bake_onto_input_shape(cls, target, transfer_nodes, capture, apply, label: str = 'transfer') -> bool`
- `node_utils/_node_utils.py::NodeUtils.deformers_preserved(cls, objects, label: str = '')`
- `node_utils/_node_utils.py::NodeUtils.delete_history(cls, objects, preserve_deformers: bool = True) -> List[str]`
- `node_utils/_node_utils.py::NodeUtils.get_deformers(cls, obj) -> List[str]`
- `node_utils/_node_utils.py::NodeUtils.get_input_shape(cls, mesh) -> Optional[str]`
- `node_utils/_node_utils.py::NodeUtils.static_copy(cls, obj, name: Optional[str] = None, strip_children: bool = True) -> str`
- `rig_utils/tube_rig.py::TubeRig.from_scene(cls, node) -> Optional['TubeRig']`
- `rig_utils/tube_rig.py::TubeRig.rebind_skin(self, skinning_method: str = 'dqs', mesh: Optional[str] = None) -> str`
- `rig_utils/tube_rig.py::TubeRig.rename(self, new_name: str) -> str`
- `rig_utils/tube_rig.py::TubeRig.scene_data(cls, node) -> Optional[dict]`
- `rig_utils/tube_rig.py::TubeRigSlots.b005(self)`
- `rig_utils/tube_rig.py::TubeRigSlots.b006(self)`
- `rig_utils/tube_rig.py::TubeRigSlots.b007(self)`
- `ui_utils/maya_bridge_slots_base.py::MayaBridgeSlotsBase.live_param_tooltip_blocks(self)`
- `uv_utils/texture_transfer.py::TextureTransfer.new_material_from(material: str) -> str`

## Signature changed (19)

- `display_utils/_display_utils.py::DisplayUtils.add_to_isolation_set`
  - was: `(objects: Union[str, object, List[Union[str, object]]])`
  - now: `(cls, objects: Union[str, object, List[Union[str, object]]]) -> List[str]`
- `edit_utils/naming/_naming.py::Naming.suffix_by_type`
  - was: `(cls, objects: Union[str, object, List[Union[str, object]]], group_suffix: str = '_GRP', locator_suffix: str = '_LOC', joint_suffix: str = '_JNT', mesh_suffix: str = '_GEO', nurbs_curve_suffix: str = '_CRV', camera_suffix: str = '_CAM', light_suffix: str = '_LGT', display_layer_suffix: str = '_LYR', ik_handle_suffix: str = '_IKH', nurbs_surface_suffix: str = '_SRF', cluster_suffix: str = '_CLS', lattice_suffix: str = '_LAT', skin_cluster_suffix: str = '_SKN', blend_shape_suffix: str = '_BS', constraint_suffix: str = '_CON', material_suffix: str = '_MAT', shading_group_suffix: str = '_SG', texture_suffix: str = '_TEX', set_suffix: str = '_SET', custom_suffixes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True, dry_run: bool = False) -> List[str]`
  - now: `(cls, objects: Union[str, object, List[Union[str, object]]], group_suffix: Optional[str] = None, locator_suffix: Optional[str] = None, joint_suffix: Optional[str] = None, mesh_suffix: Optional[str] = None, nurbs_curve_suffix: Optional[str] = None, camera_suffix: Optional[str] = None, light_suffix: Optional[str] = None, display_layer_suffix: Optional[str] = None, ik_handle_suffix: Optional[str] = None, nurbs_surface_suffix: Optional[str] = None, cluster_suffix: Optional[str] = None, lattice_suffix: Optional[str] = None, skin_cluster_suffix: Optional[str] = None, blend_shape_suffix: Optional[str] = None, constraint_suffix: Optional[str] = None, material_suffix: Optional[str] = None, shading_group_suffix: Optional[str] = None, texture_suffix: Optional[str] = None, set_suffix: Optional[str] = None, custom_suffixes: Optional[Dict[str, str]] = None, affix_mode: Optional[str] = None, affix_modes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True, dry_run: bool = False) -> List[str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.perform_export`
  - was: `(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]`
  - now: `(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None) -> bool`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ignore_groups`
  - was: `(self, names: str) -> None`
  - now: `(self, names: str, case_sensitive: bool = False) -> None`
- `mat_utils/_mat_utils.py::MatUtils.find_texture_files`
  - was: `(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Union[str, Tuple[str, str]]]`
  - now: `(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, filenames: Optional[List[str]] = None) -> List[Union[str, Tuple[str, str]]]`
- `mat_utils/_mat_utils.py::MatUtils.to_absolute`
  - was: `(path: str, workspace: Optional[str] = None) -> str`
  - now: `(cls, path: str, workspace: Optional[str] = None, sourceimages: Optional[str] = None) -> str`
- `mat_utils/_mat_utils.py::MatUtils.to_project_relative`
  - was: `(cls, path: str, workspace: Optional[str] = None) -> str`
  - now: `(cls, path: str, workspace: Optional[str] = None, sourceimages: Optional[str] = None) -> str`
- `mat_utils/image_to_plane/_image_to_plane.py::ImageToPlane.create`
  - was: `(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: str = '_MAT', prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP', stingray_opacity_mode: str = 'transparent', mask_threshold: float = 0.5, roughness: float = 0.0) -> Dict[str, object]`
  - now: `(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: Optional[str] = None, prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP', stingray_opacity_mode: str = 'transparent', mask_threshold: float = 0.5, roughness: float = 0.0) -> Dict[str, object]`
- `mat_utils/mat_updater.py::MatUpdater.update_materials`
  - was: `(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]`
  - now: `(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, shader_type: Optional[str] = None) -> Dict[str, Any]`
- `rig_utils/_rig_utils.py::RigUtils.create_locator_at_object`
  - was: `(cls, objects: Union[str, List[str]], parent: bool = True, freeze_object: bool = True, freeze_locator: bool = True, loc_scale: float = 1.0, lock_translate: bool = False, lock_rotation: bool = False, lock_scale: bool = False, grp_suffix: str = '_GRP', loc_suffix: str = '_LOC', obj_suffix: str = '_GEO', strip_digits: bool = False, strip_trailing_underscores: bool = True, strip_suffix: bool = True) -> None`
  - now: `(cls, objects: Union[str, List[str]], parent: bool = True, freeze_object: bool = True, freeze_locator: bool = True, loc_scale: float = 1.0, lock_translate: bool = False, lock_rotation: bool = False, lock_scale: bool = False, grp_suffix: Optional[str] = None, loc_suffix: Optional[str] = None, obj_suffix: Optional[str] = None, strip_digits: bool = False, strip_trailing_underscores: bool = True, strip_suffix: bool = True) -> None`
- `rig_utils/controls.py::Controls.combine`
  - was: `(cls, controls: Iterable[Any], name: Optional[str] = None, *, parent: Optional[str] = None, match: Any = None, color: Union[int, Tuple[float, float, float], None] = None, delete_sources: bool = True, ctrl_suffix: str = '_CTRL') -> str`
  - now: `(cls, controls: Iterable[Any], name: Optional[str] = None, *, parent: Optional[str] = None, match: Any = None, color: Union[int, Tuple[float, float, float], None] = None, delete_sources: bool = True, ctrl_suffix: Optional[str] = None) -> str`
- `rig_utils/controls.py::Controls.create`
  - was: `(cls, preset: str = 'diamond', name: Optional[str] = None, *, size: float = 1.0, axis: str = 'y', match: Any = None, parent: Optional[str] = None, color: Union[int, Tuple[float, float, float], None] = None, offset_group: bool = True, group_suffix: str = '_GRP', ctrl_suffix: str = '_CTRL', freeze: bool = True, tag_as_controller: bool = True, return_nodes: bool = False, **kwargs) -> Union[str, ControlNodes]`
  - now: `(cls, preset: str = 'diamond', name: Optional[str] = None, *, size: float = 1.0, axis: str = 'y', match: Any = None, parent: Optional[str] = None, color: Union[int, Tuple[float, float, float], None] = None, offset_group: bool = True, group_suffix: Optional[str] = None, ctrl_suffix: Optional[str] = None, freeze: bool = True, tag_as_controller: bool = True, return_nodes: bool = False, **kwargs) -> Union[str, ControlNodes]`
- `rig_utils/tube_path.py::TubePath.get_centerline`
  - was: `(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True) -> Tuple[List, int]`
  - now: `(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True, rings: Optional[List[List[int]]] = None) -> Tuple[List, int]`
- `rig_utils/tube_path.py::TubePath.get_edge_loop_centers`
  - was: `(mesh) -> Tuple[List[om.MPoint], int]`
  - now: `(mesh, rings: Optional[List[List[int]]] = None) -> Tuple[List[om.MPoint], int]`
- `rig_utils/tube_path.py::TubePath.get_end_normals`
  - was: `(mesh) -> Tuple[Optional['om.MVector'], Optional['om.MVector']]`
  - now: `(mesh, rings: Optional[List[List[int]]] = None) -> Tuple[Optional['om.MVector'], Optional['om.MVector']]`
- `rig_utils/tube_rig.py::TubeRigSlots.create_joints_from_tube`
  - was: `(self, obj)`
  - now: `(self, obj, rig_name: Optional[str] = None)`
- `rig_utils/tube_rig.py::TubeRigSlots.get_tube_rig`
  - was: `(self, obj)`
  - now: `(self, obj, rig_name: Optional[str] = None)`
- `uv_utils/texture_transfer.py::TextureTransfer.assign_results`
  - was: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None) -> Dict[str, str]`
  - now: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '') -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.transfer`
  - was: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_suffix: str = '_TRANSFER') -> Dict[str, Dict[str, str]]`
  - now: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None, assign_shader_type: Optional[str] = None) -> Dict[str, Dict[str, str]]`
