# mayatk — API Changes

_Diff vs the last release (origin/main @ f72e1bd)._

## Added (51)

- `anim_utils/smart_bake/bake_session.py::BakeSessionStore.remove_override_layer(session: dict, layer: Optional[str] = None, warnings: Optional[List[str]] = None) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.ensure_path_rebase(cls) -> bool`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig(class)`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.analyze(cls, nodes, root=None, ordered: bool = False) -> Dict[str, Any]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.control(self, joint_id: str) -> str`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.create(cls, links: Sequence[Any], joints: Optional[Sequence[Dict[str, Any]]] = None, name: Optional[str] = None, parent: Optional[str] = None) -> 'ArticulatedRig'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.edit_joint(self, joint_id: str, **fields) -> 'ArticulatedRig'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.export_record(cls, ctx) -> Optional['ptk.Record']`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.for_node(cls, node) -> Optional['ArticulatedRig']`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.grab_begin(self, node, point: Sequence[float]) -> Dict[str, Any]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.grab_to(self, hold: Dict[str, Any], target: Sequence[float], turn: Optional[Sequence[float]] = None, key: Optional[bool] = None) -> List[float]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.group(self) -> str`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.held_point(self, hold: Dict[str, Any]) -> Tuple[float, float, float]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.insert_joint(self, members, joint_type: Optional[str] = None, **fields) -> 'ArticulatedRig'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.joint(self, joint_id: str) -> str`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.joint_id_of(self, node) -> Optional[str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.joint_ids(self) -> List[str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.model(self) -> 'ptk.ArticulationModel'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.name(self) -> str`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.pose_to(self, node, target: Sequence[float], point: Optional[Sequence[float]] = None, key: Optional[bool] = None, attempts: int = 8) -> float`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.rebuild(self, spec: Optional[Dict[str, Any]] = None) -> 'ArticulatedRig'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.record(self) -> Dict[str, Any]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.refresh_export_metadata(cls) -> Optional[str]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.remove_joint(self, joint_id: str) -> 'ArticulatedRig'`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.scene_data(cls, node) -> Optional[Dict[str, Any]]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.scene_rigs(cls) -> List['ArticulatedRig']`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.set_limit_from_pose(self, joint_id: str, channel: str, side: str) -> float`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.set_limits(self, joint_id: str, channel: str, minimum: Optional[float], maximum: Optional[float]) -> None`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.set_state(self, values: Sequence[float], key: Optional[bool] = None, slots: Optional[Sequence[Tuple[str, str]]] = None) -> None`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.spec(self) -> Dict[str, Any]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.state(self, slots: Optional[Sequence[Tuple[str, str]]] = None) -> List[float]`
- `rig_utils/articulated_rig/_articulated_rig.py::ArticulatedRig.teardown(self) -> None`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots(class)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_analyze(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_build(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_clear_limits(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_fold(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_grab(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_insert(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_limits_from_pose(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_rebuild(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.btn_remove(self)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.header_init(self, widget)`
- `rig_utils/articulated_rig/articulated_rig_slots.py::ArticulatedRigSlots.refresh_table(self)`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab(class)`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab.activate(cls) -> str`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab.drag(self, origin: Sequence[float], direction: Sequence[float]) -> Optional[list]`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab.pick(origin: Sequence[float], direction: Sequence[float]) -> Optional[Tuple[ArticulatedRig, str, Tuple[float, float, float]]]`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab.press(self, origin: Sequence[float], direction: Sequence[float]) -> bool`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab.ray(cls, point: Sequence[float]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]`
- `rig_utils/articulated_rig/grab_tool.py::ArticulatedRigGrab.release(self) -> None`

## Deprecations (11)

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

## Signature changed (2)

- `env_utils/usd.py::UsdUtils.export`
  - was: `(cls, file_path: str, objects: Optional[List] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True, material_names: str = 'shader') -> str`
  - now: `(cls, file_path: str, objects: Optional[List] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True, material_names: str = 'shader', prune_static: bool = False) -> str`
- `uv_utils/_uv_utils.py::UvUtils.transfer_uvs`
  - was: `(cls, source: Union[str, object, List[Union[str, object]]], target: Union[str, object, List[Union[str, object]]], tolerance: float = 0.1, match_by_similarity: bool = True, sample_space: str = 'auto') -> List[Tuple[str, str, str]]`
  - now: `(cls, source: Union[str, object, List[Union[str, object]]], target: Union[str, object, List[Union[str, object]]], tolerance: float = 0.1, match_by_similarity: bool = True, sample_space: str = 'auto', preserve_uv_ids: bool = False) -> List[Tuple[str, str, str]]`
