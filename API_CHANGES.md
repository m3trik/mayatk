# mayatk — API Changes

_Diff vs the last release (origin/main @ f58c05fa)._

## Moved out (7)

- `edit_utils/duplicate_radial.py::DuplicateRadialSlots` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.b001_init` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.header_init` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.perform_operation` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.regroup_copies` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s015_init` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s016_init` → `tentacle.ui.panels.duplicate_radial` (from 0.30.0)

## Added (5)

- `core_utils/components.py::Components.reverse_normals(cls, objects, mode: int = 0) -> None`
- `core_utils/components.py::Components.set_edge_softness(cls, objects, soft: bool = True) -> None`
- `core_utils/components.py::Components.set_to_face_normals(objects) -> None`
- `core_utils/components.py::Components.toggle_normal_lock(objects) -> Optional[bool]`
- `edit_utils/duplicate_radial.py::DuplicateRadial.regroup_copies(copies: Dict[str, List[str]]) -> None`

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
