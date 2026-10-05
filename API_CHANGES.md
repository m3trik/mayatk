# mayatk — API Changes

_Diff vs the last release (origin/main @ 49f8b1b7)._

## Removed (4)

- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000` — was `(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000_init` — was `(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001` — was `(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001_init` — was `(self, widget)`

## Added (77)

- `anim_utils/shots/_shots.py::ShotStore.resolve_member(self, name: str) -> Tuple[str, str]`
- `audio_utils/_audio_utils.py::AudioUtils.clip_length_frames(cls, track_id: str, carrier: Optional[str] = None) -> float`
- `audio_utils/_audio_utils.py::AudioUtils.key_clip(cls, track_id: str, start: float, end: Optional[float] = None, duration: Optional[float] = None, auto_end: bool = True, carrier: Optional[str] = None) -> List[Tuple[str, float]]`
- `audio_utils/_audio_utils.py::AudioUtils.track_curve(cls, track_id: str, carrier: Optional[str] = None) -> Optional[str]`
- `audio_utils/audio_clips/audio_clips_slots.py::AudioClipsSlots.select_track(self, name: str) -> bool`
- `core_utils/plugins/_plugins.py::Plugins(class)`
- `core_utils/plugins/_plugins.py::Plugins.available(cls, name: str) -> bool`
- `core_utils/plugins/_plugins.py::Plugins.is_loaded(name: str) -> bool`
- `core_utils/plugins/_plugins.py::Plugins.load(cls, name: str) -> None`
- `env_utils/_env_utils.py::EnvUtils.scene_project_root() -> Optional[str]`
- `env_utils/blender_bridge/templates/bake_lightmaps.py::hidden_in_maya(obj)`
- `env_utils/fbx_utils.py::FbxUtils.export_flag(command: str) -> bool`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.decide_check_failure(self, check: str, messages: List[str], remaining: List[str]) -> str`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ship_declared_takes(self, fbx_path: str) -> Optional[dict]`
- `light_utils/lightmap_baker/_probe_placement.py::ProbePlacement(class)`
- `light_utils/lightmap_baker/_probe_placement.py::ProbePlacement.faces(self, point: Sequence[float], scene_units: bool = True) -> List[List[Optional[float]]]`
- `light_utils/lightmap_baker/_probe_placement.py::ProbePlacement.place(self) -> Optional[ProbeSite]`
- `light_utils/lightmap_baker/_probe_placement.py::ProbePlacement.room(self, point: Sequence[float]) -> List[List[Optional[float]]]`
- `light_utils/lightmap_baker/_probe_placement.py::ProbeSite(class)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_probe(self, maps: Optional[Dict[str, str]] = None) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.commit_probe(cls, path: str, position: Sequence[float], box: Optional[Sequence[Sequence[float]]] = None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.lightmap_info(cls, obj: str) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.probe(cls) -> Optional[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.project_root() -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.rename_lightmap(cls, old_name: str, new_name: str) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.transfer_lightmaps(cls, targets, source, *, output_dir: Optional[str] = None, output_name: Optional[str] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1) -> Dict[str, Dict[str, str]]`
- `mat_utils/_mat_utils.py::MatUtils.rename_texture_file(cls, path: str, new_name: str, file_nodes: Optional[List[str]] = None) -> Dict[str, Any]`
- `mat_utils/_mat_utils.py::MatUtils.sync_material_names(cls, material: Optional[str], base: str, material_affix: Tuple[str, str] = ('', ''), file_node_affix: Tuple[str, str] = ('', ''), file_nodes: Optional[List[str]] = None, dry_run: bool = False, lightmaps: Any = None) -> Dict[str, Any]`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.write_keys(cls, obj, keys, spec: ChannelSpec = OPACITY, tangent: str = 'linear', mirror: Optional[bool] = None) -> List[Tuple[str, float]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.apply_effect(cls, obj, effect: str, start: float, end: float, recipe: Optional[ptk.EffectRecipe] = None, fps: Optional[float] = None, place=None, anchor: Optional[float] = None) -> List[Tuple[str, float]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.scene_recipe(cls) -> ptk.EffectRecipe`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.scene_store()`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.b000(self, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.btn_remove(self, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.btn_webxr(self, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.btn_webxr_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.cmb_effect(self, index, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.cmb_effect_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.focus(self, channel, objects, title='', apply=None, apply_text='')`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.stk_effects_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.ui_field(self, name)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.unfocus(self, *_args) -> None`
- `mat_utils/shader_attribute_map.py::ShaderAttributeMap.constant_attr(cls, shader_type: str, logical: str) -> Optional[str]`
- `mat_utils/shader_attribute_map.py::ShaderAttributeMap.emission_weight(cls, shader: str, shader_type: Optional[str] = None) -> float`
- `mat_utils/shader_attribute_map.py::ShaderAttributeMap.read_constant(cls, shader: str, logical: str, shader_type: Optional[str] = None) -> Optional[Tuple[float, ...]]`
- `mat_utils/texture_baker.py::TextureBaker.render_panorama(self, position: Sequence[float], path: str, width: int = 1024, hide: Optional[Sequence[str]] = None, lights: Optional[Sequence[str]] = None) -> Optional[str]`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.row_rename_file(self, selection=None)`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.adjust_handles(self) -> Dict[str, str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.adjusting(self) -> bool`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.begin_adjust(self) -> Dict[str, str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.channel_value(self, joint_id: str, channel: str) -> float`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.controls(self) -> List[str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.end_adjust(self, apply: bool = True) -> bool`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.end_control(self) -> Optional[str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.ensure_solver(self) -> bool`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.fk_state(self, slots: Optional[Sequence[Tuple[str, str]]] = None) -> List[float]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.ik_blend(self) -> float`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.key_hold(self, hold: Dict[str, Any], key: Optional[bool] = None) -> None`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.match_end_control(self, key: Optional[bool] = None) -> None`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.repair_scene(cls) -> int`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.reset_pose(self, key: Optional[bool] = None) -> None`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.set_end_control(self, enabled: bool = True, part: Optional[str] = None) -> 'ArticulatedRig'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.set_weight(self, joint_id: str, weight: float, channel: Optional[str] = None) -> None`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.solver(self) -> Optional[str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.switch_ik(self, on: Optional[bool] = None, key: Optional[bool] = None) -> bool`
- `rig_utils/articulated_rig/_solver_expression.py::SolverExpression(class)`
- `rig_utils/articulated_rig/_solver_expression.py::SolverExpression.text(self) -> str`
- `rig_utils/articulated_rig/_solver_expression.py::SolverExpression.text_for(cls, rig: Mapping[str, Any], end: int, pivot: Sequence[float], turn: Sequence[float], plugs: Mapping[str, Any]) -> str`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_ik_fk(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_ik_fk_init(self, widget)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_rest(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.match_end_control(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.parse_limits(text: str, channels: Sequence[str]) -> Dict[str, Tuple[Optional[float], Optional[float]]]`
- `uv_utils/_uv_utils.py::UvUtils.export_uv_layout(objects, uv_set: str = None) -> dict`
- `uv_utils/texture_transfer.py::TextureTransfer.CONSTANT_ATTRS(cls) -> Dict[str, Dict[str, str]]`
- `uv_utils/texture_transfer.py::TextureTransfer.find_combined(cls, meshes: Sequence[str]) -> Optional[Tuple[str, Tuple]]`
- `uv_utils/texture_transfer.py::TextureTransfer.pair_sources(cls, targets: Sequence[str], sources: Sequence[str]) -> Dict`

## Deprecations (14)

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
- `env_utils/_env_utils.py::EnvUtils.is_plugin_loaded` — remove in 0.23.0, not before 2026-11-02
- `env_utils/_env_utils.py::EnvUtils.load_plugin` — remove in 0.23.0, not before 2026-11-02
- `uv_utils/texture_transfer.py::TextureTransfer.CONSTANT_ATTRS` — remove in 0.23.0, not before 2026-11-03

## Signature changed (18)

- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.apply_audio_clip`
  - was: `(obj: str, start: float, end: float, source_path: str = '') -> None`
  - now: `(obj: str, start: float, end: float, source_path: str = '') -> List[Tuple[str, float]]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.apply_behavior`
  - was: `(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None`
  - now: `(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None, recipe: Optional[Any] = None, fps: Optional[float] = None) -> List[Tuple[str, float]]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.apply_to_shots`
  - was: `(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]`
  - now: `(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None, resolve_fn=None, conflict_fn=None, release_fn=None) -> Dict[str, list]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.verify_behavior`
  - was: `(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None) -> bool`
  - now: `(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None, recipe: Optional[Any] = None, fps: Optional[float] = None) -> bool`
- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.format_behavior_html`
  - was: `(behaviors, broken=(), status_color=None) -> str`
  - now: `(behaviors, broken=(), status_color=None, stale=()) -> str`
- `audio_utils/_audio_utils.py::AudioUtils.shift_keys_in_range`
  - was: `(cls, old_start: float, old_end: float, delta: float, track_ids: Optional[List[str]] = None, carrier: Optional[str] = None) -> List[str]`
  - now: `(cls, old_start: float, old_end: float, delta: float, track_ids: Optional[List[str]] = None, carrier: Optional[str] = None, ledger=None) -> List[str]`
- `env_utils/fbx_utils.py::FbxUtils.apply_takes`
  - was: `(takes: Iterable[Any]) -> int`
  - now: `(takes: Iterable[Any], resample: bool = True) -> int`
- `env_utils/fbx_utils.py::FbxUtils.apply_takes_from_node`
  - was: `(node: Optional[str] = None, attr: Optional[str] = None) -> int`
  - now: `(node: Optional[str] = None, attr: Optional[str] = None, resample: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.pack_atlas`
  - was: `(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', keep_sources: bool = False, plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None, claims: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[str, List[float]]]`
  - now: `(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', keep_sources: bool = False, plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None, claims: Optional[Dict[str, Any]] = None, regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None) -> Dict[str, Tuple[str, List[float]]]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.commit`
  - was: `(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0) -> Dict[str, str]`
  - now: `(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0, written: bool = True) -> Dict[str, str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_fade`
  - was: `(cls, objects=None, start: float = 0, end: float = 15, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', delete_visibility_keys: bool = False, channel='opacity', whole_frames: bool = True) -> List[Tuple[str, str]]`
  - now: `(cls, objects=None, start: float = 0, end: Optional[float] = None, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', delete_visibility_keys: bool = False, channel='opacity', whole_frames: bool = True, recipe: Optional[ptk.EffectRecipe] = None) -> List[Tuple[str, str]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, dim_color=None, auto_create: bool = True, channel='highlight', delete_visibility_keys: bool = False, whole_frames: bool = True) -> List[str]`
  - now: `(cls, objects=None, start: float = 0, end: float = 100, period: Optional[float] = None, bright_fraction: Optional[float] = None, ramp_fraction: Optional[float] = None, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, dim_color=None, auto_create: bool = True, channel='highlight', delete_visibility_keys: bool = False, whole_frames: bool = True, recipe: Optional[ptk.EffectRecipe] = None) -> List[str]`
- `mat_utils/texture_baker.py::TextureBaker.bake`
  - was: `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', suffix: str = '', backend: str = 'auto', uv_set: Optional[Union[str, Dict[str, str]]] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None, size: Optional[Any] = None, shader: Optional[str] = None, batch: bool = False, claims: Optional[Any] = None) -> Dict[str, str]`
  - now: `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', suffix: str = '', backend: str = 'auto', uv_set: Optional[Union[str, Dict[str, str]]] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None, size: Optional[Any] = None, shader: Optional[str] = None, batch: bool = False, claims: Optional[Any] = None, region: Optional[Any] = None, camera_shader: Optional[str] = None) -> Dict[str, str]`
- `node_utils/_node_utils.py::NodeUtils.get_shape_node`
  - was: `(cls, nodes, returned_type='obj', attributes=False, inc=[], exc=[])`
  - now: `(cls, nodes, returned_type='obj', attributes=False, inc=[], exc=[], no_intermediate=True)`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.create`
  - was: `(cls, links: Sequence[Any], joints: Optional[Sequence[Dict[str, Any]]] = None, name: Optional[str] = None, parent: Optional[str] = None) -> 'ArticulatedRig'`
  - now: `(cls, links: Sequence[Any], joints: Optional[Sequence[Dict[str, Any]]] = None, name: Optional[str] = None, parent: Optional[str] = None, end_control: bool = True) -> 'ArticulatedRig'`
- `uv_utils/texture_transfer.py::TextureTransfer.assign_results`
  - was: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '') -> Dict[str, str]`
  - now: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '', assign_from: str = 'target') -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.material_constant`
  - was: `(cls, material: str, channel: str) -> Optional[Tuple[float, ...]]`
  - now: `(material: str, channel: str) -> Optional[Tuple[float, ...]]`
- `uv_utils/texture_transfer.py::TextureTransfer.transfer`
  - was: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None, assign_shader_type: Optional[str] = None) -> Dict[str, Dict[str, str]]`
  - now: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None, assign_shader_type: Optional[str] = None, assign_from: str = 'target') -> Dict[str, Dict[str, str]]`
