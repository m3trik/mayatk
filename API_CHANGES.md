# mayatk — API Changes

_Diff vs the last release (origin/main @ 4cc9baa3)._

## Added (6)

- `__init__.py::MOVED_NAMES(constant)`
- `anim_utils/_anim_utils.py::AnimUtils.exact_scale(cls)`
- `anim_utils/_anim_utils.py::AnimUtils.hold_segments(curve: str, times: List[float]) -> List[bool]`
- `anim_utils/_anim_utils.py::AnimUtils.move_curve_keys(cls, curve: str, time_pairs: List[Tuple[float, ...]], tolerance: float = 0.0001, allow_merge: bool = False, on_replace=None, on_move=None) -> int`
- `anim_utils/_anim_utils.py::AnimUtils.resolve_keys(cls, objects=None, mode: str = 'auto', resolution_order: Optional[Tuple[str, ...]] = None) -> Dict[str, Any]`
- `anim_utils/_anim_utils.py::AnimUtils.tangent_half(crv: str, t: Optional[float], side: str) -> Optional[Tuple[str, float, float]]`

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
