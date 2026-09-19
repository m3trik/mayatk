# mayatk — API Changes

_Diff vs the last release (origin/main @ e66766b)._

## Added (26)

- `anim_utils/smart_bake/_smart_bake.py::BakeResult.declined(self) -> Dict[str, str]`
- `anim_utils/smart_bake/_smart_bake.py::BakeResult.skip(self, obj: str, reason: str, declined: bool = True) -> None`
- `audio_utils/audio_clips/_audio_clips.py::AudioClips.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `env_utils/_env_utils.py::EnvUtils.scene_artifact_path(cls, suffix: str) -> str`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.scene_has_complex_animation(cls, src_path: str) -> bool`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::mark_container_skeletons(filepath)`
- `env_utils/fbx_utils.py::FbxUtils.disable_export_producer(cls, spec) -> None`
- `env_utils/fbx_utils.py::FbxUtils.enable_export_producer(cls, spec) -> None`
- `env_utils/fbx_utils.py::FbxUtils.export_context(cls, mode: str = ptk.ExportContext.PIPELINE, **decisions) -> ptk.ExportContext`
- `env_utils/fbx_utils.py::FbxUtils.producers(cls, only: Optional[Iterable[Any]] = None) -> Dict[Any, Callable]`
- `env_utils/fbx_utils.py::FbxUtils.publish(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None) -> ptk.ExportSnapshot`
- `env_utils/fbx_utils.py::FbxUtils.publish_authored(cls, records: Dict[Any, Any]) -> ptk.ExportSnapshot`
- `env_utils/fbx_utils.py::FbxUtils.register_export_stager(cls, name: str, prepare: Optional[Callable[[], Any]] = None, finish: Optional[Callable[[], Any]] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.stage(cls, names: Optional[Iterable[str]] = None) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]`
- `env_utils/fbx_utils.py::FbxUtils.stagers(cls, names: Optional[Iterable[str]] = None) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_stager(cls, name: str) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.export_data_node_init(self, widget) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ensure_scene_records_published(self)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `mat_utils/emissive_groups.py::EmissiveGroups.export_record(cls, ctx: 'ptk.ExportContext') -> Optional['ptk.Record']`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `node_utils/data_nodes.py::DataNodes.dump_export_nodes(cls, decode: bool = True) -> Dict[str, Dict[str, object]]`
- `node_utils/data_nodes.py::DataNodes.read(cls, scope: ptk.Scope, key: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.values(cls, scope: ptk.Scope) -> Dict[str, object]`
- `node_utils/data_nodes.py::DataNodes.write(cls, scope: ptk.Scope, key: str, text: Optional[str]) -> Optional[str]`
- `rig_utils/shadow_rig.py::ShadowRig.plane_record(cls, plane)`

## Deprecations (16)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its one-release window: delete the alias and its tests rather than moving the date._

- `mat_utils/render_opacity/render_effects.py::RenderEffects.restamp_stack_span` — remove in 0.18.0
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_mode` — remove in 0.18.0
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_origin` — remove in 0.18.0
- `node_utils/data_nodes.py::DataNodes.get_export_string` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.get_internal_json` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.get_internal_string` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_export_json` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_export_string` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_internal_json` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_internal_string` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.register_export_finalizer` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.register_export_preparer` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.run_export_finalizers` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_finalizer` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_preparer` — **no removal version recorded**

## Moved (2)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `node_utils/data_nodes.py::DataNodes.dump`
- `node_utils/data_nodes.py::DataNodes.format_dump`

## Signature changed (23)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotEditDialog.show`
  - was: `(parent=None, name: str = '', start: float = 1.0, end: float = 100.0, description: str = '', title: str = 'Shot')`
  - now: `(parent=None, name: str = '', start: float = 1.0, end: float = 100.0, description: str = '', title: str = 'Shot', validate=None)`
- `env_utils/fbx_utils.py::FbxUtils.begin_export`
  - was: `(only: Optional[Iterable[str]] = None) -> None`
  - now: `(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None, stagers: Optional[Iterable[str]] = None) -> Optional[ptk.ExportSnapshot]`
- `env_utils/fbx_utils.py::FbxUtils.end_export`
  - was: `() -> None`
  - now: `(cls) -> None`
- `env_utils/fbx_utils.py::FbxUtils.export_prepared`
  - was: `(only: Optional[Iterable[str]] = None)`
  - now: `(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None, stagers: Optional[Iterable[str]] = None)`
- `env_utils/fbx_utils.py::FbxUtils.register_export_finalizer`
  - was: `(name: str, finish: Callable[[], Any]) -> None`
  - now: `(cls, name: str, finish: Callable[[], Any]) -> None`
- `env_utils/fbx_utils.py::FbxUtils.register_export_preparer`
  - was: `(name: str, prepare: Callable[[], Any]) -> None`
  - now: `(cls, name: str, prepare: Callable[[], Any]) -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_finalizers`
  - was: `(include_known: bool = True) -> None`
  - now: `(cls, include_known: bool = True) -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers`
  - was: `(include_known: bool = True, only: Optional[Iterable[str]] = None) -> None`
  - now: `(cls, include_known: bool = True, only: Optional[Iterable[str]] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_finalizer`
  - was: `(name: str) -> None`
  - now: `(cls, name: str) -> None`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_preparer`
  - was: `(name: str) -> None`
  - now: `(cls, name: str) -> None`
- `node_utils/data_nodes.py::DataNodes.ensure_export`
  - was: `()`
  - now: `(cls) -> str`
- `node_utils/data_nodes.py::DataNodes.ensure_internal`
  - was: `()`
  - now: `(cls) -> str`
- `node_utils/data_nodes.py::DataNodes.get_export_node`
  - was: `(create: bool = True) -> Optional[str]`
  - now: `(cls, create: bool = True) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.get_export_nodes`
  - was: `() -> List[str]`
  - now: `(cls) -> List[str]`
- `node_utils/data_nodes.py::DataNodes.get_export_string`
  - was: `(attr: str) -> Optional[str]`
  - now: `(cls, attr: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.get_internal_json`
  - was: `(attr: str, default=None)`
  - now: `(cls, attr: str, default=None)`
- `node_utils/data_nodes.py::DataNodes.get_internal_node`
  - was: `(create: bool = True) -> Optional[str]`
  - now: `(cls, create: bool = True) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.get_internal_string`
  - was: `(attr: str) -> Optional[str]`
  - now: `(cls, attr: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_export_json`
  - was: `(attr: str, payload) -> Optional[str]`
  - now: `(cls, attr: str, payload) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_export_string`
  - was: `(attr: str, value: str) -> Optional[str]`
  - now: `(cls, attr: str, value: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_internal_json`
  - was: `(attr: str, payload) -> Optional[str]`
  - now: `(cls, attr: str, payload) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_internal_string`
  - was: `(attr: str, value: str) -> Optional[str]`
  - now: `(cls, attr: str, value: str) -> Optional[str]`
- `rig_utils/shadow_rig.py::ShadowRig.export_record`
  - was: `(cls, plane)`
  - now: `(cls, ctx)`
