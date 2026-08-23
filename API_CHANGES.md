# mayatk — API Changes

_Diff vs the last release (origin/main @ 2015838). Generated 2026-08-23._

## Removed (2)

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b005` — was `(self) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b013` — was `(self) -> None`

## Added (29)

- `anim_utils/_anim_utils.py::AnimUtils.unbake_keys(cls, objects: Optional[Union[str, List[str]]] = None, value_tolerance: float = 0.001, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None) -> List[str]`
- `edit_utils/macros.py::DisplayMacros.m_cycle_background(cls) -> str`
- `edit_utils/naming/_naming.py::Naming.scene_objects(cls) -> List[str]`
- `edit_utils/naming/_naming.py::Naming.type_key(cls, obj: str) -> str`
- `edit_utils/naming/naming_slots.py::NamingSlots.dry_run(self) -> bool`
- `edit_utils/naming/naming_slots.py::NamingSlots.file_scope(self) -> bool`
- `edit_utils/naming/naming_slots.py::NamingSlots.scope(self) -> str`
- `env_utils/_env_utils.py::EnvUtils.apply_scene_settings(settings: Dict[str, Any]) -> list`
- `env_utils/_env_utils.py::EnvUtils.scene_has_content() -> bool`
- `env_utils/_env_utils.py::EnvUtils.scene_settings() -> Dict[str, float]`
- `env_utils/blender_bridge/templates/_bake_scene.py::apply_scene(engine)`
- `env_utils/blender_bridge/templates/_bake_scene.py::restore_usd_locators(cmds, engine, new_nodes)`
- `env_utils/blender_bridge/templates/_import_scene.py::scene_settings(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::collect_empties(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::collect_texture_manifest(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::export_prim_path(obj, root_prim_path='')`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::fold_single_mesh_xforms(filepath)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::hidden_objects(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::mark_invisible(filepath, objects, root_prim_path='')`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::sanitize_prim_name(name)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::scene_settings(bpy)`
- `env_utils/blender_bridge/templates/_save_scene.py::import_payload()`
- `env_utils/blender_bridge/templates/_save_scene.py::import_usd()`
- `env_utils/blender_bridge/templates/import.py::import_payload()`
- `env_utils/blender_bridge/templates/import.py::import_usd()`
- `env_utils/usd.py::UsdUtils.name_materials_after_shaders(cls, file_path: str, mapping: Optional[Dict[str, str]] = None) -> int`
- `env_utils/usd.py::UsdUtils.options_string(options: Dict[str, Any]) -> str`
- `env_utils/usd.py::UsdUtils.sampling_frame_range(cls, objects: Optional[List[str]] = None) -> Optional[Tuple[float, float]]`
- `env_utils/usd.py::UsdUtils.sanitize_prim_name(name: str) -> str`

## Signature changed (20)

- `edit_utils/_edit_utils.py::EditUtils.cut_along_axis`
  - was: `(cls, objects, axis='x', pivot='center', amount=1, offset=0, spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, invert=False, ortho=False, delete=False, mirror=False, use_object_axes=True)`
  - now: `(cls, objects, axis='x', pivot='center', amount=1, offset=0, spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, invert=False, ortho=False, delete=False, mirror=False, axis_frame=None, use_object_axes=None)`
- `edit_utils/_edit_utils.py::EditUtils.delete_along_axis`
  - was: `(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, use_object_axes=True)`
  - now: `(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, axis_frame=None, use_object_axes=None)`
- `edit_utils/_edit_utils.py::EditUtils.get_all_faces_on_axis`
  - was: `(obj, axis='x', pivot='center', use_object_axes=True)`
  - now: `(obj, axis='x', pivot='center', use_object_axes=None, axis_frame=None)`
- `edit_utils/_edit_utils.py::EditUtils.mirror`
  - was: `(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, use_object_axes: bool = True, delete_original: bool = False, center_pivot: bool = True, **kwargs)`
  - now: `(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, axis_frame: Optional[str] = None, use_object_axes: Optional[bool] = None, delete_original: bool = False, center_pivot: bool = True, **kwargs)`
- `edit_utils/_edit_utils.py::EditUtils.mirror_instance`
  - was: `(cls, objects=None, axis: str = 'x', pivot: Union[str, tuple] = 'object', use_object_axes: bool = True) -> list`
  - now: `(cls, objects=None, axis: str = 'x', pivot: Union[str, tuple] = 'object', axis_frame: Optional[str] = None, use_object_axes: Optional[bool] = None) -> list`
- `edit_utils/cut_on_axis.py::CutOnAxis.perform_cut_on_axis`
  - was: `(objects, axis='-x', cuts=0, cut_offset=0, cut_spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, delete=False, mirror=False, pivot='manip', use_object_axes=True)`
  - now: `(objects, axis='-x', cuts=0, cut_offset=0, cut_spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, delete=False, mirror=False, pivot='manip', axis_frame=None, use_object_axes=None)`
- `edit_utils/naming/_naming.py::Naming.append_location_based_suffix`
  - was: `(objects, first_obj_as_ref=False, alphabetical=False, strip_trailing_ints=True, strip_defined_suffixes=True, valid_suffixes=None, reverse=False, independent_groups=False)`
  - now: `(cls, objects, first_obj_as_ref=False, alphabetical=False, strip_trailing_ints=True, strip_defined_suffixes=True, valid_suffixes=None, reverse=False, independent_groups=False, dry_run: bool = False)`
- `edit_utils/naming/_naming.py::Naming.rename`
  - was: `(cls, objects: Union[str, 'object', List[Union[str, 'object']]], to: str, fltr: str = '', regex: bool = False, ignore_case: bool = False, retain_suffix: bool = False, valid_suffixes: Optional[List[str]] = None, collapse_padding: bool = True) -> List[str]`
  - now: `(cls, objects: Union[str, 'object', List[Union[str, 'object']]], to: str, fltr: str = '', regex: bool = False, ignore_case: bool = False, retain_suffix: bool = False, valid_suffixes: Optional[List[str]] = None, collapse_padding: bool = True, dry_run: bool = False) -> List[str]`
- `edit_utils/naming/_naming.py::Naming.set_case`
  - was: `(objects=None, case='capitalize')`
  - now: `(cls, objects=None, case='capitalize', dry_run: bool = False)`
- `edit_utils/naming/_naming.py::Naming.strip_chars`
  - was: `(objects: Union[str, object, List[Union[str, object]]], num_chars: int = 1, trailing: bool = False) -> List[str]`
  - now: `(cls, objects: Union[str, object, List[Union[str, object]]], num_chars: int = 1, trailing: bool = False, dry_run: bool = False) -> List[str]`
- `edit_utils/naming/_naming.py::Naming.suffix_by_type`
  - was: `(objects: Union[str, object, List[Union[str, object]]], group_suffix: str = '_GRP', locator_suffix: str = '_LOC', joint_suffix: str = '_JNT', mesh_suffix: str = '_GEO', nurbs_curve_suffix: str = '_CRV', camera_suffix: str = '_CAM', light_suffix: str = '_LGT', display_layer_suffix: str = '_LYR', custom_suffixes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True) -> List[str]`
  - now: `(cls, objects: Union[str, object, List[Union[str, object]]], group_suffix: str = '_GRP', locator_suffix: str = '_LOC', joint_suffix: str = '_JNT', mesh_suffix: str = '_GEO', nurbs_curve_suffix: str = '_CRV', camera_suffix: str = '_CAM', light_suffix: str = '_LGT', display_layer_suffix: str = '_LYR', ik_handle_suffix: str = '_IKH', nurbs_surface_suffix: str = '_SRF', cluster_suffix: str = '_CLS', lattice_suffix: str = '_LAT', skin_cluster_suffix: str = '_SKN', blend_shape_suffix: str = '_BS', constraint_suffix: str = '_CON', material_suffix: str = '_MAT', shading_group_suffix: str = '_SG', texture_suffix: str = '_TEX', set_suffix: str = '_SET', custom_suffixes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True, dry_run: bool = False) -> List[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.import_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: float = 600, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', **script_opts: Any) -> List[str]`
  - now: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: float = 600, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', scene_settings: Any = 'auto', **script_opts: Any) -> List[str]`
- `env_utils/blender_bridge/templates/_bake_scene.py::apply_manifest`
  - was: `(engine, new_nodes)`
  - now: `(engine, new_nodes, carrier='fbx')`
- `env_utils/blender_bridge/templates/_import_scene.py::write_texture_manifest`
  - was: `(entries, scene_materials, empties, path)`
  - now: `(entries, scene_materials, empties, scene, path)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::write_manifest`
  - was: `(bpy)`
  - now: `(bpy, scene, materials=None, scene_materials=None)`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.generate_export_path`
  - was: `(self, version_format: str = '') -> str`
  - now: `(self, version_format: str = '', extension: str = '.fbx') -> str`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.perform_export`
  - was: `(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]`
  - now: `(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]`
- `env_utils/usd.py::UsdUtils.export`
  - was: `(cls, file_path: str, objects: Optional[List] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True) -> str`
  - now: `(cls, file_path: str, objects: Optional[List] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True, material_names: str = 'shader') -> str`
- `env_utils/usd.py::UsdUtils.import_scene`
  - was: `(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True) -> List[str]`
  - now: `(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True, read_animation: bool = True) -> List[str]`
- `node_utils/_node_utils.py::NodeUtils.get_instanced_shapes`
  - was: `(cls, node, intermediate: bool = True) -> List[str]`
  - now: `(cls, node, intermediate: bool = True, descendants: bool = False) -> List[str]`
