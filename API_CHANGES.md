# mayatk — API Changes

_Diff vs the last release (origin/main @ a9fc7dd)._

## Removed (1)

- `anim_utils/key_stash/_key_stash.py::KeyStash.is_previewing` — was `(self, clip_id: Optional[int] = None) -> bool`

## Added (47)

- `anim_utils/_anim_utils.py::AnimUtils.curve_key_spans(curves: List[str], windows: Sequence[Tuple[Optional[float], Optional[float]]]) -> List[Optional[Tuple[float, float]]]`
- `anim_utils/_anim_utils.py::AnimUtils.has_keyframes(sources: Union[str, List[str]]) -> bool`
- `anim_utils/_anim_utils.py::AnimUtils.keyframe_range(sources: Union[str, List[str]]) -> Optional[Tuple[float, float]]`
- `anim_utils/shots/_shots.py::MayaScenePersistence.record_changed(self) -> bool`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_sub_track_selected(self, rows: list) -> None`
- `core_utils/undo_recorder.py::UndoRecorder(class)`
- `core_utils/undo_recorder.py::UndoRecorder.record(cls) -> Iterator[_Recorder]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.name_context(self, name_regex: Optional[str] = None) -> Dict[str, str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.resolve_export_path(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', name_regex: Optional[str] = None, report: bool = True, version_format: str = '', timestamp: bool = False) -> Dict[str, Any]`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots(class)`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b000(self) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b006(self) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b007(self) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b008(self) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b010(self) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b012(self) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb000_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb001_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb002_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb004_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb005_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb007_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb008_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.confirm(self, question: str) -> bool`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.header_init(self, widget)`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.ignore_groups_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.output_name_preview(self) -> str`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.save_output_dir(self, output_dir: str) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.save_output_name(self, output_name: str) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.txt000_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.txt001_init(self, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.workspace(self) -> Optional[str]`
- `env_utils/scene_exporter/task_manager.py::TaskManager.begin_run(self, run: ptk.ExportRun) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_uv_snapshots(self) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.export_path(self) -> str`
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_mode(self) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.run_tasks(self, tasks: Dict[str, Any]) -> bool`
- `mat_utils/_mat_utils.py::MatUtils.apply_uv_tiling(cls, file_nodes) -> List[str]`
- `mat_utils/mat_snapshot.py::MatSnapshot.surviving_node(cls, snapshot: Dict[str, Any], node: str) -> Optional[str]`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.get_color_stops(cls, obj, spec: ChannelSpec = HIGHLIGHT) -> Tuple`
- `mat_utils/render_opacity/channels.py::ChannelSpec.color_attr(self) -> Optional[str]`
- `mat_utils/render_opacity/channels.py::ChannelSpec.stop_attr(self, stop: str = 'hi') -> Optional[str]`
- `mat_utils/render_opacity/channels.py::ChannelSpec.track_color_stops(self) -> Optional[ColorStops]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_color_stops(cls, objects=None, channel='highlight') -> Dict[str, Tuple]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.preview_channels(cls, objects, channel='highlight', keys=(), colors=None, fps: Optional[float] = None) -> Dict`
- `node_utils/attributes/channels/_channels.py::Channels.set_key_at_current_time(nodes, attr_name, keyed=True)`
- `uv_utils/_uv_utils.py::UvUtils.find_uv_snapshots(objects: Sequence[Union[str, object]], prefix: str = '_uv_snap', stale_only: bool = False) -> List[UvSnapshot]`

## Moved (22)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b000`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b006`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b007`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b008`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b010`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b012`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb000_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb001_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb002_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb004_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb005_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb007_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb008_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.confirm`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.header_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.ignore_groups_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.save_output_dir`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.save_output_name`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.txt000_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.txt001_init`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.workspace`

## Signature changed (17)

- `anim_utils/_anim_utils.py::AnimUtils.get_redundant_flat_keys`
  - was: `(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, as_strings: bool = False) -> List[Tuple[Any, List[float]]]`
  - now: `(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, as_strings: bool = False, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False) -> List[Tuple[Any, List[float]]]`
- `anim_utils/_anim_utils.py::AnimUtils.objects_to_curves`
  - was: `(objects: Union[str, List[str]], recursive: bool = False, as_strings: bool = False, through_blends: bool = False) -> List[str]`
  - now: `(objects: Union[str, List[str]], recursive: bool = False, as_strings: bool = False, through_blends: bool = True) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.optimize_keys`
  - was: `(cls, objects: Union[str, List[str]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[str]`
  - now: `(cls, objects: Union[str, List[str]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, through_blends: bool = True) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.remove_intermediate_keys`
  - was: `(objects: Union[str, List[str]], time_range: Optional[Union[int, Tuple[int, int]]] = None, ignore: Union[str, List[str], None] = None) -> int`
  - now: `(objects: Union[str, List[str]], time_range: Optional[Union[int, Tuple[int, int]]] = None, ignore: Union[str, List[str], None] = None, attributes: Union[str, List[str], None] = None) -> int`
- `anim_utils/_anim_utils.py::AnimUtils.simplify_curve`
  - was: `(cls, objects: List[str], value_tolerance: float = 0.001, time_tolerance: float = 0.001, recursive: bool = False, as_strings: bool = False) -> List[str]`
  - now: `(cls, objects: List[str], value_tolerance: float = 0.001, time_tolerance: float = 0.001, recursive: bool = False, as_strings: bool = False, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False) -> List[str]`
- `anim_utils/_anim_utils.py::AnimUtils.snap_keys_to_frames`
  - was: `(objects: Optional[List[str]] = None, method: str = 'nearest', selected_only: bool = False, time_range: Optional[Tuple[float, float]] = None, include_driven: bool = False) -> int`
  - now: `(objects: Optional[List[str]] = None, method: str = 'nearest', selected_only: bool = False, time_range: Optional[Tuple[float, float]] = None, include_driven: bool = False, through_blends: bool = True) -> int`
- `anim_utils/shots/_shots.py::MayaScenePersistence.save`
  - was: `(self, data: Dict[str, Any]) -> None`
  - now: `(self, data: Dict[str, Any], undoable: bool = False) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name`
  - was: `(self, name: str) -> str`
  - now: `(self, name: str, name_regex: Optional[str] = None) -> str`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.generate_export_path`
  - was: `(self, version_format: str = '', extension: str = '.fbx') -> str`
  - now: `(self, version_format: str = '', extension: str = '.fbx', output_format: Optional[str] = None) -> str`
- `env_utils/scene_exporter/task_manager.py::TaskManager.verify_deliverables`
  - was: `(self, *paths: str, max_fbx_bytes: Optional[int] = None) -> Optional[Any]`
  - now: `(self, *paths: str, max_fbx_bytes: Optional[int] = None, max_image_bytes: Optional[int] = None) -> Optional[Any]`
- `env_utils/scene_exporter/task_manager.py::TaskManager.write_scene_data_sidecar`
  - was: `(self) -> None`
  - now: `(self, glb_path: Optional[str] = None) -> None`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.get_color`
  - was: `(cls, obj, spec: ChannelSpec = HIGHLIGHT) -> Optional[Tuple[float, float, float]]`
  - now: `(cls, obj, spec: ChannelSpec = HIGHLIGHT, stop: str = 'hi') -> Optional[Tuple[float, float, float]]`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.key_pulse`
  - was: `(cls, objects, start: float, end: float, period: float, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color: Optional[Sequence[float]] = None, auto_create: bool = True, spec: ChannelSpec = HIGHLIGHT, whole_frames: bool = True) -> List[str]`
  - now: `(cls, objects, start: float, end: float, period: float, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color: Optional[Sequence[float]] = None, dim_color: Optional[Sequence[float]] = None, auto_create: bool = True, spec: ChannelSpec = HIGHLIGHT, whole_frames: bool = True) -> List[str]`
- `mat_utils/render_opacity/attribute_mode.py::OpacityAttributeMode.set_color`
  - was: `(cls, objects, color, spec: ChannelSpec = HIGHLIGHT) -> List[str]`
  - now: `(cls, objects, color, spec: ChannelSpec = HIGHLIGHT, stop: str = 'hi') -> List[str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_colors`
  - was: `(cls, objects=None, channel='highlight') -> Dict[str, Tuple]`
  - now: `(cls, objects=None, channel='highlight', stop: str = 'hi') -> Dict[str, Tuple]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, auto_create: bool = True, channel='highlight', preview: Optional[bool] = None, delete_visibility_keys: bool = False, whole_frames: bool = True) -> List[str]`
  - now: `(cls, objects=None, start: float = 0, end: float = 100, period: float = 86, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, dim_color=None, auto_create: bool = True, channel='highlight', preview: Optional[bool] = None, delete_visibility_keys: bool = False, whole_frames: bool = True) -> List[str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.set_channel_color`
  - was: `(cls, objects=None, color=None, channel='highlight') -> List[str]`
  - now: `(cls, objects=None, color=None, channel='highlight', stop: str = 'hi') -> List[str]`
