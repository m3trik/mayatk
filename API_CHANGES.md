# mayatk — API Changes

_Diff vs the last release (origin/main @ c7ff612)._

## Removed (15)

- `edit_utils/mesh_graph.py::MeshGraph` — was `(class)`
- `edit_utils/mesh_graph.py::MeshGraph.build_graph` — was `(self)`
- `edit_utils/mesh_graph.py::MeshGraph.heuristic` — was `(self, node1, node2)`
- `env_utils/blender_bridge/templates/_bake_scene.py::apply_manifest` — was `(engine, new_nodes, carrier='fbx')`
- `env_utils/blender_bridge/templates/_bake_scene.py::apply_scene` — was `(engine)`
- `env_utils/blender_bridge/templates/_bake_scene.py::restore_empty_groups` — was `(engine, new_nodes)`
- `env_utils/blender_bridge/templates/_save_scene.py::apply_texture_manifest` — was `(new_objects)`
- `env_utils/blender_bridge/templates/_save_scene.py::import_usd` — was `()`
- `env_utils/blender_bridge/templates/import.py::GROUP_EMPTY_DISPLAY_SIZE` — was `(constant)`
- `env_utils/blender_bridge/templates/import.py::apply_texture_manifest` — was `(new_objects)`
- `env_utils/blender_bridge/templates/import.py::import_usd` — was `()`
- `env_utils/blender_bridge/templates/import.py::rebuild_scene_lights` — was `()`
- `env_utils/blender_bridge/templates/import.py::tag_node_types` — was `(new_objects)`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.rename` — was `(cls, old_export_path: str, new_export_path: str) -> list`
- `node_utils/_node_utils.py::NodeUtils.instance` — was `(cls, *args, **kwargs)`

## Added (46)

- `anim_utils/shots/_shots.py::ShotStore.apply_transfer(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None) -> Optional['ShotStore']`
- `anim_utils/shots/_shots.py::ShotStore.export_transfer(cls, spell=None, objects: Optional[List[str]] = None) -> Optional[Dict[str, Any]]`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_keys_tangent_dragged(self, groups: list, side: str, broken: bool) -> None`
- `anim_utils/world_fit_bake.py::WorldFitBake(class)`
- `anim_utils/world_fit_bake.py::WorldFitBake.bake_node(cls, node: str, target: str, frames: Sequence[float], rows: Sequence[Sequence[float]], reparent: bool = True) -> dict`
- `anim_utils/world_fit_bake.py::WorldFitBake.ik_handles_touching(paths: Iterable[str]) -> Dict[str, set]`
- `anim_utils/world_fit_bake.py::WorldFitBake.sample_locals(plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], orient: Optional[Dict[str, Sequence[float]]] = None) -> Dict[Tuple[str, str], List[List[float]]]`
- `audio_utils/_audio_utils.py::AudioUtils.add_clip(cls, path: str, frame_start: float, name: Optional[str] = None, frame_end: Optional[float] = None, carrier: Optional[str] = None) -> str`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.import_payload(self, payload_path: str, *, via: str = 'fbx', fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', adopt_scene: bool = False, shots: bool = True, step: Optional[Callable[[int, int, str], Any]] = None) -> List[str]`
- `env_utils/blender_bridge/templates/_import_scene.py::EXTRA_SYS_PATH(constant)`
- `env_utils/blender_bridge/templates/_import_scene.py::RIG_CAPABILITY(constant)`
- `env_utils/blender_bridge/templates/_import_scene.py::RIG_MODE(constant)`
- `env_utils/blender_bridge/templates/_import_scene.py::shots_section(bpy, spell)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::EXTRA_SYS_PATH(constant)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::RIG_CAPABILITY(constant)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::RIG_MODE(constant)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::mark_skinning_methods(bpy, filepath, objects=None, root_prim_path='')`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::pin_primvar_indices(filepath)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::shots_section(bpy, spell)`
- `env_utils/blender_bridge/templates/_save_scene.py::SEND_FBX_OPTIONS(constant)`
- `env_utils/blender_bridge/templates/import.py::SEND_FBX_OPTIONS(constant)`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline(class)`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.compare(cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None) -> Tuple[bool, List[str], List[str], bool]`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.is_unreadable(cls) -> bool`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar(cls, export_dir: str) -> int`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.read(cls) -> Set[str]`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.write(cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None) -> bool`
- `env_utils/usd.py::UsdUtils.apply_skinning_methods(nodes: List[str], methods: Dict[str, str]) -> int`
- `env_utils/usd.py::UsdUtils.dq_safe_source(cls, usd_path: str) -> Tuple[str, Dict[str, str]]`
- `env_utils/usd.py::UsdUtils.skinning_methods(usd_path: str) -> Dict[str, str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.apply_channel_records(cls, node: str, records: Dict[str, Dict]) -> int`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_records(cls, objects=None) -> Dict[str, Dict[str, Dict]]`
- `node_utils/data_nodes.py::DataNodes.get_internal_json(attr: str, default=None)`
- `node_utils/data_nodes.py::DataNodes.set_internal_json(attr: str, payload) -> Optional[str]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder(class)`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.build(self, graph: Dict[str, Any], nodes: Sequence[str], is_usd: bool = False) -> Dict[str, Any]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.capability(cls) -> Dict[str, Any]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.commit(self, record_id: str) -> int`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.linear_unit() -> str`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.remove(self, record_id: str) -> int`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.sample_world(self, node_id: str, frame: int) -> Optional[Tuple[float, float, float]]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.scope(self)`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.up_axis() -> str`
- `rig_utils/rig_graph_extract.py::RigGraphExtractor(class)`
- `rig_utils/rig_graph_extract.py::RigGraphExtractor.extract(self, objects: Optional[Sequence[str]] = None) -> Dict[str, Any]`
- `rig_utils/skinning.py::SkinUtils.flatten_influences(cls, skin_clusters: Optional[Sequence[str]] = None, frames: Optional[Sequence[float]] = None, unpin_geometry: bool = True, root_suffix: str = '_skeleton', root_parent: str = 'ancestor', orient_bones: bool = False) -> Dict[str, Dict[str, str]]`

## Signature changed (10)

- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake`
  - was: `(self, src_path: str, out_path: str, *, timeout: float = 600) -> Any`
  - now: `(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None) -> Any`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.bake_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: float = 600, **script_opts: Any) -> str`
  - now: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', **script_opts: Any) -> str`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.convert`
  - was: `(self, src_path: str, out_path: str, *, via: str = 'fbx', timeout: float = 600, texture_dir: Optional[str] = None, **script_opts: Any) -> 'ptk.ScriptRunResult'`
  - now: `(self, src_path: str, out_path: str, *, via: str = 'fbx', timeout: Optional[float] = None, texture_dir: Optional[str] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None, **script_opts: Any) -> 'ptk.ScriptRunResult'`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.import_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: float = 600, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', scene_settings: Any = 'auto', **script_opts: Any) -> List[str]`
  - now: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', scene_settings: Any = 'auto', shots: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', **script_opts: Any) -> List[str]`
- `env_utils/blender_bridge/_scene_import.py::BlenderSceneImport.render_script`
  - was: `(self, src_path: str, out_path: str, *, via: str = 'fbx', embed_textures: bool = False, include_animation: bool = True, texture_dir: str = '') -> str`
  - now: `(self, src_path: str, out_path: str, *, via: str = 'fbx', embed_textures: bool = False, include_animation: bool = True, texture_dir: str = '', rig_mode: str = 'auto') -> str`
- `env_utils/blender_bridge/templates/_bake_scene.py::apply_instances`
  - was: `(engine, new_nodes)`
  - now: `()`
- `env_utils/blender_bridge/templates/_bake_scene.py::import_source`
  - was: `(cmds, engine)`
  - now: `(cmds)`
- `env_utils/blender_bridge/templates/_bake_scene.py::restore_usd_locators`
  - was: `(cmds, engine, new_nodes)`
  - now: `(cmds, new_nodes)`
- `env_utils/blender_bridge/templates/_import_scene.py::write_texture_manifest`
  - was: `(entries, scene_materials, empties, scene, path)`
  - now: `(entries, scene_materials, empties, scene, path, shots=None, rig=None)`
- `env_utils/blender_bridge/templates/_import_scene_usd.py::write_manifest`
  - was: `(bpy, scene, materials=None, scene_materials=None)`
  - now: `(bpy, scene, materials=None, scene_materials=None, shots=None, rig=None)`
