# mayatk — API Changes

_Diff vs the last release (origin/main @ 168efd2)._

## Removed (15)

- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.b002` — was `(self) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.presets` — was `(self) -> Dict[str, Optional[str]]`
- `mat_utils/render_opacity/channels.py::ViewportBinding` — was `(class)`
- `mat_utils/render_opacity/material_mode.py::OpacityMaterialMode.create` — was `(cls, objects, spec: ChannelSpec = OPACITY) -> Dict[str, Dict]`
- `mat_utils/render_opacity/material_mode.py::OpacityMaterialMode.ensure_connections` — was `(cls, objects) -> None`
- `mat_utils/render_opacity/material_mode.py::OpacityMaterialMode.get_stingray_mats` — was `(cls, objects: Optional[list] = None) -> list`
- `mat_utils/render_opacity/material_mode.py::OpacityMaterialMode.resume_after_export` — was `(cls) -> List[str]`
- `mat_utils/render_opacity/material_mode.py::OpacityMaterialMode.suspend_for_export` — was `(cls) -> List[str]`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots` — was `(class)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.header_init` — was `(self, widget)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.tb000` — was `(self, widget)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.tb000_init` — was `(self, widget)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.tb001` — was `(self, widget)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.tb001_init` — was `(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.b004` — was `(self)`

## Added (37)

- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.chk001(self, checked: bool) -> None`
- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.header_init(self, widget) -> None`
- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.refresh_from_scene(self) -> None`
- `anim_utils/shots/_detection.py::Detection.curve_moves_in(crv: str, start: float, end: float, value_tolerance: float = 0.0001) -> bool`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.move_attribute_keys(self, obj: str, attr: Optional[str], delta: float, times: Optional[List[float]] = None, window: Optional[tuple] = None) -> int`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.scale_shot_keys(self, old_start: float, old_end: float, new_start: float, new_end: float) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_key_menu(self, menu, key_groups: list) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_key_tangent_dragged(self, clip_id: int, time: float, side: str, dt: float, dv: float) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.tangent_from_handle(side: str, dt: float, dv: float) -> tuple`
- `anim_utils/smart_bake/_smart_bake.py::SmartBake.get_object_time_ranges(self, analysis: Dict[str, BakeAnalysis], fallback: Tuple[int, int]) -> Dict[str, Tuple[int, int]]`
- `core_utils/_core_utils.py::CoreUtils.preserved_selection()`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.presets(self) -> Dict[str, Optional[str]]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.run_config_from_values(self, values: Dict[str, Any], override_checks: bool = False, ignore_groups_case_sensitive: bool = False) -> Dict[str, Any]`
- `mat_utils/render_opacity/channels.py::ChannelSpec.attrs(self) -> Tuple[str, ...]`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots(class)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.header_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001_init(self, widget)`
- `node_utils/attributes/_attributes.py::Attributes.classify_driver(cls, node: str, node_type: Optional[str] = None, passthrough_types: Optional[Set[str]] = None, *, is_constraint: Optional[bool] = None, is_driven: Optional[bool] = None, visited: Optional[set] = None) -> Tuple[Optional[str], Optional[str]]`
- `rig_utils/_rig_utils.py::RigUtils.ik_handles_by_joint(handles: Optional[Iterable[str]] = None) -> Dict[str, List[str]]`
- `rig_utils/shadow_rig.py::ShadowRig.auto_recalculate(cls, on=True)`
- `rig_utils/shadow_rig.py::ShadowRig.auto_recalculate_enabled(cls)`
- `rig_utils/shadow_rig.py::ShadowRig.planes_lit_by(cls, source)`
- `rig_utils/shadow_rig.py::ShadowRig.recalculate_stale(cls, planes=None)`
- `rig_utils/shadow_rig.py::ShadowRig.set_source_softness(cls, source, value)`
- `rig_utils/shadow_rig.py::ShadowRig.source_size(cls, source)`
- `rig_utils/shadow_rig.py::ShadowRig.source_softness(cls, source)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.chk_follow(self, checked)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.chk_follow_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.chk_horizon_preview_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.reproject_sources(self)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.s001(self, value)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.s001_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.source_from_selection(self)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.txt_source_init(self, widget)`

## Signature changed (14)

- `anim_utils/_anim_utils.py::AnimUtils.reduce_to_extremes`
  - was: `(cls, objects: Optional[Union[str, List[str]]] = None, value_tolerance: float = 0.001, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None) -> List[str]`
  - now: `(cls, objects: Optional[Union[str, List[str]]] = None, value_tolerance: float = 0.001, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, max_error: Optional[float] = None) -> List[str]`
- `anim_utils/shots/_shot_apply.py::ShotApply.apply`
  - was: `(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> None`
  - now: `(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None, objects: Optional[Iterable[str]] = None) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.resize_shot_bounds`
  - was: `(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True) -> None`
  - now: `(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True, clamp: bool = True) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.ripple_downstream`
  - was: `(self, shot_id: int, after_frame: float, delta: float)`
  - now: `(self, shot_id: int, after_frame: float, delta: float, carry_gap: bool = True)`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.ripple_upstream`
  - was: `(self, shot_id: int, before_frame: float, delta: float)`
  - now: `(self, shot_id: int, before_frame: float, delta: float, carry_gap: bool = True)`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.key_fade`
  - was: `(cls, objects, start: float, end: float, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', spec: ChannelSpec = OPACITY) -> List[Tuple[str, str]]`
  - now: `(cls, objects, start: float, end: float, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', spec: ChannelSpec = OPACITY, whole_frames: bool = True) -> List[Tuple[str, str]]`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.key_pulse`
  - was: `(cls, objects, start: float, end: float, period: float, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, color: Optional[Sequence[float]] = None, auto_create: bool = True, spec: ChannelSpec = HIGHLIGHT) -> List[str]`
  - now: `(cls, objects, start: float, end: float, period: float, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color: Optional[Sequence[float]] = None, auto_create: bool = True, spec: ChannelSpec = HIGHLIGHT, whole_frames: bool = True) -> List[str]`
- `mat_utils/render_opacity/material_mode.py::OpacityMaterialMode.remove`
  - was: `(cls, objects, spec: Optional[ChannelSpec] = None)`
  - now: `(cls, objects, spec: Optional[ChannelSpec] = None) -> List[str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_fade`
  - was: `(cls, objects=None, start: float = 0, end: float = 15, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear') -> List[Tuple[str, str]]`
  - now: `(cls, objects=None, start: float = 0, end: float = 15, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', preview: Optional[bool] = None, delete_visibility_keys: bool = False, channel='opacity', whole_frames: bool = True) -> List[Tuple[str, str]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, color=None, auto_create: bool = True, channel='highlight') -> List[str]`
  - now: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, auto_create: bool = True, channel='highlight', preview: Optional[bool] = None, delete_visibility_keys: bool = False, whole_frames: bool = True) -> List[str]`
- `rig_utils/_rig_utils.py::RigUtils.create_locator_at_object`
  - was: `(cls, objects: Union[str, List[str]], parent: bool = True, freeze_object: bool = True, freeze_locator: bool = True, loc_scale: float = 1.0, lock_translate: bool = False, lock_rotation: bool = False, lock_scale: bool = False, grp_suffix: Optional[str] = None, loc_suffix: Optional[str] = None, obj_suffix: Optional[str] = None, obj_affix_mode: str = 'auto', strip_digits: bool = False, strip_trailing_underscores: bool = True, strip_suffix: bool = True) -> None`
  - now: `(cls, objects: Union[str, List[str]], parent: bool = True, freeze_object: bool = True, freeze_locator: bool = True, loc_scale: float = 1.0, lock_translate: bool = False, lock_rotation: bool = False, lock_scale: bool = False, grp_suffix: Optional[str] = None, grp_affix_mode: str = 'auto', loc_suffix: Optional[str] = None, loc_affix_mode: str = 'auto', obj_suffix: Optional[str] = None, obj_affix_mode: str = 'auto', strip_digits: bool = False, strip_trailing_underscores: bool = True, strip_suffix: bool = True) -> None`
- `rig_utils/shadow_rig.py::ShadowRig.bake_horizon`
  - was: `(self, bins=None, size=None, path=None, *, only_if_changed=False)`
  - now: `(self, size=None, spans=None, path=None, *, only_if_changed=False)`
- `rig_utils/shadow_rig.py::ShadowRig.create`
  - was: `(cls, targets, light_pos=(5, 10, 5), texture_res=512, axis='auto', source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, shader_type='standard', rig_type='projected', horizon_bins=None, horizon_size=None)`
  - now: `(cls, targets, light_pos=(5, 10, 5), texture_res=512, axis='auto', source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, shader_type='standard', rig_type='projected', horizon_size=None, horizon_spans=None)`
- `rig_utils/shadow_rig.py::ShadowRig.silhouette_is_stale`
  - was: `(cls, plane)`
  - now: `(cls, plane, *, degrees=None, distance=None)`
