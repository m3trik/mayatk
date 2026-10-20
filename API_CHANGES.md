# mayatk — API Changes

_Diff vs the last release (origin/main @ 91629994)._

No public API changes since the last release (origin/main @ 91629994).

## Deprecations (19)

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
- **HELD** `env_utils/_env_utils.py::EnvUtils.is_plugin_loaded` — remove in 0.23.0, not before 2026-11-02
- **HELD** `env_utils/_env_utils.py::EnvUtils.load_plugin` — remove in 0.23.0, not before 2026-11-02
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.23.0, not before 2026-11-06
- **HELD** `uv_utils/texture_transfer.py::TextureTransfer.CONSTANT_ATTRS` — remove in 0.23.0, not before 2026-11-03
- **HELD** `env_utils/scene_exporter/task_manager.py::TaskManager.check_uv_snapshots` — remove in 0.24.0, not before 2026-11-07

