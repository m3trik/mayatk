# mayatk — API Changes

_Diff vs the last release (origin/main @ b9717f9). Generated 2026-08-18._

## Removed (1)

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb004` — was `(self, index, widget) -> None`

## Added (4)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.drop_intermediate(nodes) -> list`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.with_ancestors(paths) -> set`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb007_init(self, widget) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb008_init(self, widget) -> None`

## Signature changed (2)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.build_clean_path_set`
  - was: `(objects) -> set`
  - now: `(cls, objects) -> set`
- `env_utils/namespace_sandbox.py::NamespaceSandbox.get_supported_formats`
  - was: `(self) -> List[str]`
  - now: `(cls) -> List[str]`
