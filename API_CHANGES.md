# mayatk — API Changes

_Diff vs the last release (origin/main @ bd3b980)._

## Removed (12)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_key_tangent_dragged` — was `(self, clip_id: int, time: float, side: str, dt: float, dv: float) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_delete_all_shots` — was `(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_all_shots` — was `(self)`
- `env_utils/maya_connection.py::MayaConnection.disconnect` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_locator_names` — was `(self, enabled=True) -> tuple`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb000` — was `(self, index, widget) -> None`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::ROUNDTRIP` — was `(constant)`
- `mat_utils/render_opacity/channels.py::ChannelSpec.color_attr` — was `(self) -> Optional[str]`
- `mat_utils/render_opacity/channels.py::ChannelSpec.track_color_key` — was `(self) -> Optional[str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.preview` — was `(cls, objects=None, channel='highlight', enabled: bool = True) -> Dict`
- `mat_utils/substance_bridge/_substance_bridge.py::ROUNDTRIP` — was `(constant)`
- `uv_utils/_uv_utils.py::UvUtils.flip_uvs` — was `(cls, objects, axis: str = 'u', pivot: tuple | None = None, per_shell: bool = True, preserve_position: bool = True)`

## Added (65)

- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.collapse_nested_levels(cls, nodes: List[str], joints: bool = False) -> int`
- `env_utils/blender_bridge/templates/_import_scene.py::collect_instance_groups(bpy)`
- `env_utils/blender_bridge/templates/_import_scene.py::collect_visibility(bpy)`
- `env_utils/blender_bridge/templates/_import_scene.py::sanitize_names(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::collapse_static_xforms(filepath, tolerance=0.0001, distance=1e-05)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::collect_visibility(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::hide_is_animated(obj)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::mark_orthographic(filepath, cameras, root_prim_path='')`
- `env_utils/usd.py::UsdReadRefused(class)`
- `env_utils/usd.py::UsdUtils.crashing_skins(cls, usd_path: str) -> List[str]`
- `env_utils/usd.py::UsdUtils.file_options(cls, options: Optional[Dict[str, Any]] = None, read_animation: bool = True) -> Dict[str, str]`
- `env_utils/usd.py::UsdUtils.live_read_options(cls, file_path: str, options: Optional[Dict[str, Any]] = None, read_animation: bool = True) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakeResult(class)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakeResult.files(self) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakeResult.folders(self) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.adaptive(self) -> bool`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake(self, objects: Optional[List[str]] = None, packing: str = 'atlas', output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', on_progress: Optional[Callable[[int, int, str], bool]] = None, intensity: float = 1.0, **kwargs) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_targets(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_verdict(self, paths) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.baked_objects(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.map_levels(cls, paths) -> Dict[str, Tuple[float, float]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.peak_level(cls, paths) -> Optional[Tuple[str, float, float]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.preflight(self) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots(class)`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.b000(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.btn_reset_defaults_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.clear_exclusions(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb000_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb002_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_device_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_resolution_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_scope_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.header_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.open_sourceimages(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.revert_to_source(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_exclusions(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.set_exclusions(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.set_exclusions_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.spn_samples_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.txt000_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.txt_output_dir_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords(class)`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.baked_objects(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.claims(cls, objects: Optional[List[str]] = None) -> Dict[str, FrozenSet[str]]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.commit(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.heal_lightmap_paths(cls, objects: Optional[List[str]] = None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.lightmap_dependencies(cls, objects: Optional[List[str]] = None, search_dirs: Optional[List[str]] = None, walk: bool = True) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.migrate_legacy(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.normalize_lightmap_paths(cls, objects: Optional[List[str]] = None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.refresh_export_metadata(cls) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.relocate_lightmaps(cls, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.repath_lightmaps(cls, dirs_by_map: Dict[str, str], objects: Optional[List[str]] = None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.revert(cls, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.search_dirs(cls, objects: Optional[List[str]] = None) -> List[str]`
- `mat_utils/bake_sets.py::BakeSet(class)`
- `mat_utils/bake_sets.py::BakeSet.clear(cls) -> None`
- `mat_utils/bake_sets.py::BakeSet.define(cls, objects: Optional[List[str]] = None) -> List[str]`
- `mat_utils/bake_sets.py::BakeSet.exists(cls) -> bool`
- `mat_utils/bake_sets.py::BakeSet.members(cls) -> List[str]`
- `mat_utils/bake_sets.py::BakeSet.meshes(cls) -> List[str]`
- `mat_utils/bake_sets.py::LightmapExcludeSet(class)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.setup(cls, *args, **kwargs) -> Dict[str, Dict]`
- `mat_utils/texture_baker.py::TextureBaker.ensure_arnold(cls) -> bool`
- `mat_utils/texture_baker.py::TextureBaker.gpu_available() -> bool`

## Deprecations (9)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its one-release window: delete the alias and its tests rather than moving the date._

- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — remove in 0.20.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — remove in 0.20.0
- `mat_utils/render_opacity/render_effects.py::RenderEffects.setup` — remove in 0.20.0

## Moved (16)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.b000`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb000_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb002_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_device_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_resolution_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_scope_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.header_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.open_sourceimages`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.revert_to_source`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.txt000_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.txt_output_dir_init`
- `mat_utils/bake_sets.py::BakeSourceSet.clear`
- `mat_utils/bake_sets.py::BakeSourceSet.define`
- `mat_utils/bake_sets.py::BakeSourceSet.exists`
- `mat_utils/bake_sets.py::BakeSourceSet.members`

## Signature changed (15)

- `core_utils/diagnostics/scene_diag.py::SceneDiagnostics.repair_mangled_names`
  - was: `(cls, objects: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]`
  - now: `(cls, objects: Optional[List[str]] = None, dry_run: bool = False, descend: bool = True, decode_only: bool = False) -> Dict[str, Any]`
- `edit_utils/_edit_utils.py::EditUtils.cut_along_axis`
  - was: `(cls, objects, axis='x', pivot='center', amount=1, offset=0, spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, invert=False, ortho=False, delete=False, mirror=False, axis_frame=None, use_object_axes=None)`
  - now: `(cls, objects, axis='x', pivot='center', amount=1, offset=0, spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, invert=False, ortho=False, delete=False, mirror=False, axis_frame=None)`
- `edit_utils/_edit_utils.py::EditUtils.delete_along_axis`
  - was: `(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, axis_frame=None, use_object_axes=None)`
  - now: `(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, axis_frame=None)`
- `edit_utils/_edit_utils.py::EditUtils.get_all_faces_on_axis`
  - was: `(obj, axis='x', pivot='center', use_object_axes=None, axis_frame=None)`
  - now: `(obj, axis='x', pivot='center', axis_frame=None)`
- `edit_utils/_edit_utils.py::EditUtils.mirror`
  - was: `(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, axis_frame: Optional[str] = None, use_object_axes: Optional[bool] = None, delete_original: bool = False, center_pivot: bool = True, **kwargs)`
  - now: `(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, axis_frame: Optional[str] = None, delete_original: bool = False, center_pivot: bool = True, **kwargs)`
- `edit_utils/_edit_utils.py::EditUtils.mirror_instance`
  - was: `(cls, objects=None, axis: str = 'x', pivot: Union[str, tuple] = 'object', axis_frame: Optional[str] = None, use_object_axes: Optional[bool] = None) -> list`
  - now: `(cls, objects=None, axis: str = 'x', pivot: Union[str, tuple] = 'object', axis_frame: Optional[str] = None) -> list`
- `edit_utils/cut_on_axis.py::CutOnAxis.perform_cut_on_axis`
  - was: `(objects, axis='-x', cuts=0, cut_offset=0, cut_spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, delete=False, mirror=False, pivot='manip', axis_frame=None, use_object_axes=None)`
  - now: `(objects, axis='-x', cuts=0, cut_offset=0, cut_spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, delete=False, mirror=False, pivot='manip', axis_frame=None)`
- `env_utils/_env_utils.py::EnvUtils.list_reference_nodes`
  - was: `(top_level: bool = True) -> list`
  - now: `(top_level: bool = True, file_less: bool = False) -> list`
- `env_utils/blender_bridge/templates/_import_scene.py::write_texture_manifest`
  - was: `(entries, scene_materials, empties, scene, path, scene_data=None, rig=None)`
  - now: `(entries, scene_materials, empties, scene, path, scene_data=None, rig=None, visibility=None, instances=None)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::write_manifest`
  - was: `(bpy, scene, materials=None, scene_materials=None, scene_data=None, rig=None)`
  - now: `(bpy, scene, materials=None, scene_materials=None, scene_data=None, rig=None, visibility=None)`
- `env_utils/reference_manager.py::ReferenceManager.import_references`
  - was: `(self, namespaces=None, namespace_mode='remove', remove_namespace=None, scene_data='merge')`
  - now: `(self, namespaces=None, namespace_mode='remove', scene_data='merge')`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.pack_atlas`
  - was: `(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', keep_sources: bool = False, plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None) -> Dict[str, Tuple[str, List[float]]]`
  - now: `(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', keep_sources: bool = False, plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None, claims: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[str, List[float]]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_fade`
  - was: `(cls, objects=None, start: float = 0, end: float = 15, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', preview: Optional[bool] = None, delete_visibility_keys: bool = False, channel='opacity', whole_frames: bool = True) -> List[Tuple[str, str]]`
  - now: `(cls, objects=None, start: float = 0, end: float = 15, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', delete_visibility_keys: bool = False, channel='opacity', whole_frames: bool = True) -> List[Tuple[str, str]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, dim_color=None, auto_create: bool = True, channel='highlight', preview: Optional[bool] = None, delete_visibility_keys: bool = False, whole_frames: bool = True) -> List[str]`
  - now: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, dim_color=None, auto_create: bool = True, channel='highlight', delete_visibility_keys: bool = False, whole_frames: bool = True) -> List[str]`
- `mat_utils/texture_baker.py::TextureBaker.bake`
  - was: `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', suffix: str = '', backend: str = 'auto', uv_set: Optional[Union[str, Dict[str, str]]] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None, size: Optional[Any] = None, shader: Optional[str] = None, batch: bool = False) -> Dict[str, str]`
  - now: `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', suffix: str = '', backend: str = 'auto', uv_set: Optional[Union[str, Dict[str, str]]] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None, size: Optional[Any] = None, shader: Optional[str] = None, batch: bool = False, claims: Optional[Any] = None) -> Dict[str, str]`
