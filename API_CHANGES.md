# mayatk — API Changes

_Diff vs the last release (origin/main @ f58c05fa)._

## Moved out (26)

- `edit_utils/duplicate_radial.py::DuplicateRadialSlots` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.b001_init` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.header_init` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.perform_operation` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.regroup_copies` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s015_init` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s016_init` → `tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b000` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b001` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b002` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b003` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.compact_slots` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.header_init` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.key_weights` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.make_weights_keyable` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.remove_group` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.remove_keyable_weights` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.republish_export` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.select_members` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.tb000` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.tb000_init` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.tbl000_init` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.txt000_init` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.weights_all_off` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.weights_all_on` → `tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots` (from 0.30.0)

## Added (31)

- `core_utils/_core_utils.py::CoreUtils.selected_objects() -> List[str]`
- `core_utils/components.py::Components.reverse_normals(cls, objects, mode: int = 0) -> None`
- `core_utils/components.py::Components.set_edge_softness(cls, objects, soft: bool = True) -> None`
- `core_utils/components.py::Components.set_to_face_normals(objects) -> None`
- `core_utils/components.py::Components.toggle_normal_lock(objects) -> Optional[bool]`
- `edit_utils/duplicate_radial.py::DuplicateRadial.regroup_copies(copies: Dict[str, List[str]]) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups(class)`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.add_members(self, store: str, face_map: FaceMap) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.carrier_weights(self, prefix: str) -> List[str]`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.delete_weight(self, attr: str) -> bool`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.discard_stores(self, stores: List[str], ctx) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.ensure_weight(self, attr: str, default: float) -> str`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.extra_warnings(self) -> List[str]`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.face_count(self, obj: str) -> int`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.faces(self, faces=None) -> FaceMap`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.foreign_color_sets(self, objects: List[str], color_set: str) -> Dict[str, List[str]]`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.key_weight(self, attr: str, value: Optional[float], frame: Optional[float]) -> str`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.mask_path(self, suffix: str) -> str`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.members(self, store: str) -> Optional[FaceMap]`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.membership_stores(self, prefix: str)`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.merge_stores(self, stores: List[str], ctx) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.mesh(self, name: str) -> Optional[str]`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.object_names(self, objects) -> List[str]`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.publish(self, records) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.remove_members(self, store: str) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.select(self, face_map: FaceMap) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.sync_weight(self, attr: str, value: float) -> None`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.uv_triangles(self, obj: str, indices: List[int], uv_set: Optional[str]) -> list`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.weight_exists(self, attr: str) -> bool`
- `mat_utils/emissive_groups.py::MayaEmissiveGroups.write_colors(self, obj: str, channels, color_set: str) -> None`
- `ui_utils/node_icons.py::NodeIcons.audio_icon(size: int = 16)`

## Deprecations (17)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `env_utils/_env_utils.py::EnvUtils.is_plugin_loaded` — remove in 0.23.0, not before 2026-11-02
- **HELD** `env_utils/_env_utils.py::EnvUtils.load_plugin` — remove in 0.23.0, not before 2026-11-02
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `uv_utils/texture_transfer.py::TextureTransfer.CONSTANT_ATTRS` — remove in 0.23.0, not before 2026-11-03
- **HELD** `env_utils/scene_exporter/task_manager.py::TaskManager.check_uv_snapshots` — remove in 0.24.0, not before 2026-11-07
- **HELD** `env_utils/scene_state.py::SceneState.read` — remove in 0.29.0, not before 2026-11-23
- **HELD** `env_utils/scene_state.py::SceneState.source` — remove in 0.29.0, not before 2026-11-23
- `core_utils/_core_utils.py::CoreUtils.get_bounding_box` — remove in 0.30.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_project_root` — remove in 0.30.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_source` — remove in 0.30.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_source_images_dir` — remove in 0.30.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_workspace` — remove in 0.30.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.set_scene_source` — remove in 0.30.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.twin_save_target` — remove in 0.30.0, not before 2026-11-24

## Moved (18)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `mat_utils/emissive_groups.py::EmissiveGroups.add_group`
- `mat_utils/emissive_groups.py::EmissiveGroups.bake_mask`
- `mat_utils/emissive_groups.py::EmissiveGroups.bake_vertex_colors`
- `mat_utils/emissive_groups.py::EmissiveGroups.compact_slots`
- `mat_utils/emissive_groups.py::EmissiveGroups.discard_carrier`
- `mat_utils/emissive_groups.py::EmissiveGroups.export_record`
- `mat_utils/emissive_groups.py::EmissiveGroups.key_weight`
- `mat_utils/emissive_groups.py::EmissiveGroups.list_groups`
- `mat_utils/emissive_groups.py::EmissiveGroups.make_weights_keyable`
- `mat_utils/emissive_groups.py::EmissiveGroups.merge_carrier`
- `mat_utils/emissive_groups.py::EmissiveGroups.refresh_export_metadata`
- `mat_utils/emissive_groups.py::EmissiveGroups.remove_group`
- `mat_utils/emissive_groups.py::EmissiveGroups.remove_keyable_weights`
- `mat_utils/emissive_groups.py::EmissiveGroups.select_group`
- `mat_utils/emissive_groups.py::EmissiveGroups.set_default`
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_in`
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_out`
- `mat_utils/emissive_groups.py::EmissiveGroups.validate`
