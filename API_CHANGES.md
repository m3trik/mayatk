# mayatk — API Changes

_Diff vs the last release (origin/main @ 978801c)._

## Removed (18)

- `env_utils/blender_bridge/templates/_import_scene.py::shots_section` — was `(bpy, spell)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::shots_section` — was `(bpy, spell)`
- `env_utils/fbx_utils.py::FbxUtils.register_export_finalizer` — was `(cls, name: str, finish: Callable[[], Any]) -> None`
- `env_utils/fbx_utils.py::FbxUtils.register_export_preparer` — was `(cls, name: str, prepare: Callable[[], Any]) -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_finalizers` — was `(cls, include_known: bool = True) -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers` — was `(cls, include_known: bool = True, only: Optional[Iterable[str]] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_finalizer` — was `(cls, name: str) -> None`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_preparer` — was `(cls, name: str) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_mode` — was `(self) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_origin` — was `(self) -> None`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.restamp_stack_span` — was `(cls, start: float, end: float) -> bool`
- `node_utils/data_nodes.py::DataNodes.get_export_string` — was `(cls, attr: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.get_internal_json` — was `(cls, attr: str, default=None)`
- `node_utils/data_nodes.py::DataNodes.get_internal_string` — was `(cls, attr: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_export_json` — was `(cls, attr: str, payload) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_export_string` — was `(cls, attr: str, value: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_internal_json` — was `(cls, attr: str, payload) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_internal_string` — was `(cls, attr: str, value: str) -> Optional[str]`

## Added (29)

- `anim_utils/key_stash/_key_stash.py::KeyStash.discard_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/key_stash/_key_stash.py::KeyStash.merge_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/shots/_shots.py::ShotStore.discard_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/shots/_shots.py::ShotStore.merge_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/shots/_shots.py::ShotStore.transfer_in(cls, payload: Dict[str, Any], ctx: 'ptk.TransferContext') -> None`
- `anim_utils/shots/_shots.py::ShotStore.transfer_out(cls, ctx: 'ptk.TransferContext') -> Optional[Dict[str, Any]]`
- `anim_utils/smart_bake/bake_session.py::BakeSessionStore.discard_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/smart_bake/bake_session.py::BakeSessionStore.merge_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/smart_bake/bake_session.py::BakeSessionStore.reconnect(src: str, dst: str, factor: Optional[float] = None) -> None`
- `anim_utils/smart_bake/bake_session.py::BakeSessionStore.trace_source(plug: str) -> Tuple[Optional[str], Optional[float]]`
- `anim_utils/world_fit_bake.py::WorldFitBake.apply(cls, prepared: dict, records: Optional[List[dict]] = None) -> dict`
- `anim_utils/world_fit_bake.py::WorldFitBake.flatten(cls, plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], records: Optional[List[dict]] = None, max_residual: Optional[float] = None) -> dict`
- `anim_utils/world_fit_bake.py::WorldFitBake.flatten_target(node: str, qualifies: Dict[str, bool]) -> Optional[str]`
- `anim_utils/world_fit_bake.py::WorldFitBake.prepare(cls, plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], max_residual: Optional[float] = None) -> dict`
- `anim_utils/world_fit_bake.py::WorldFitBake.restore(cls, records: Sequence[dict]) -> Tuple[int, List[str]]`
- `anim_utils/world_fit_bake.py::WorldFitBake.restore_node(record: dict) -> bool`
- `anim_utils/world_fit_bake.py::WorldFitBake.similarity_ancestors(nodes: Iterable[str], frames: Sequence[float], tolerance: float = 0.05) -> Dict[str, bool]`
- `env_utils/blender_bridge/templates/_import_scene.py::FBX_DROPPED_TYPES(constant)`
- `env_utils/blender_bridge/templates/_import_scene.py::scene_data_sections(bpy, spell)`
- `env_utils/blender_bridge/templates/_import_scene.py::stand_in_dropped_objects(bpy)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::scene_data_sections(bpy, spell)`
- `env_utils/fbx_utils.py::FbxUtils.declared_takes() -> list`
- `env_utils/fbx_utils.py::FbxUtils.drop_rig_apparatus(file_path: str, scope: Optional[Iterable[str]] = None, logger: Optional[logging.Logger] = None) -> Optional[Dict[str, Any]]`
- `mat_utils/emissive_groups.py::EmissiveGroups.discard_carrier(cls, carriers, other, ctx) -> None`
- `mat_utils/emissive_groups.py::EmissiveGroups.merge_carrier(cls, carriers, other, ctx) -> None`
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_in(cls, payload: dict, ctx: 'ptk.TransferContext') -> None`
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_out(cls, ctx: 'ptk.TransferContext') -> Optional[dict]`
- `node_utils/data_nodes.py::DataNodes.carriers_in(cls, namespace: str) -> Dict[ptk.Scope, str]`
- `rig_utils/rig_graph_extract.py::RigGraphExtractor.machinery(self, rig: Optional[Dict[str, Any]] = None, scope: Optional[Sequence[str]] = None) -> Tuple[Dict[str, str], Tuple[str, ...]]`

## Signature changed (10)

- `anim_utils/shots/_shot_apply.py::ShotApply.retime_gaps`
  - was: `(retimes: Iterable[Any], objects: Iterable[str], after_move: bool) -> int`
  - now: `(retimes: Iterable[Any], objects: Iterable[str], after_move: bool, ledger: Optional[Any] = None) -> int`
- `anim_utils/shots/_shots.py::ShotStore.apply_transfer`
  - was: `(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None) -> Optional['ShotStore']`
  - now: `(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None, ctx: Optional['ptk.TransferContext'] = None) -> Optional['ShotStore']`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.reconcile_system_edits`
  - was: `(self) -> Dict[str, int]`
  - now: `(self, follow: bool = True) -> Dict[str, int]`
- `anim_utils/shots/shot_sequencer/clip_motion.py::scale_attribute_keys`
  - was: `(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> bool`
  - now: `(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float, ledger=None) -> bool`
- `anim_utils/world_fit_bake.py::WorldFitBake.sample_locals`
  - was: `(plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], orient: Optional[Dict[str, Sequence[float]]] = None) -> Dict[Tuple[str, str], List[List[float]]]`
  - now: `(plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], orient: Optional[Dict[str, Sequence[float]]] = None, residuals: Optional[Dict[Tuple[str, str], float]] = None) -> Dict[Tuple[str, str], List[List[float]]]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.import_payload`
  - was: `(self, payload_path: str, *, via: str = 'fbx', fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', adopt_scene: bool = False, shots: bool = True, step: Optional[Callable[[int, int, str], Any]] = None) -> List[str]`
  - now: `(self, payload_path: str, *, via: str = 'fbx', fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', adopt_scene: bool = False, scene_data: bool = True, step: Optional[Callable[[int, int, str], Any]] = None) -> List[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.import_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', scene_settings: Any = 'auto', shots: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', **script_opts: Any) -> List[str]`
  - now: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', scene_settings: Any = 'auto', scene_data: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', **script_opts: Any) -> List[str]`
- `env_utils/blender_bridge/templates/_import_scene.py::write_texture_manifest`
  - was: `(entries, scene_materials, empties, scene, path, shots=None, rig=None)`
  - now: `(entries, scene_materials, empties, scene, path, scene_data=None, rig=None)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::write_manifest`
  - was: `(bpy, scene, materials=None, scene_materials=None, shots=None, rig=None)`
  - now: `(bpy, scene, materials=None, scene_materials=None, scene_data=None, rig=None)`
- `env_utils/reference_manager.py::ReferenceManager.import_references`
  - was: `(self, namespaces=None, namespace_mode='remove', remove_namespace=None)`
  - now: `(self, namespaces=None, namespace_mode='remove', remove_namespace=None, scene_data='merge')`
