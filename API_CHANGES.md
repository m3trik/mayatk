# mayatk — API Changes

_Diff vs the last release (origin/main @ a9933c1). Generated 2026-08-20._

## Removed (1)

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb006_init` — was `(self, widget) -> None`

## Added (3)

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b013(self) -> None`
- `mat_utils/_mat_utils.py::MatUtils.to_absolute(path: str, workspace: Optional[str] = None) -> str`
- `mat_utils/_mat_utils.py::MatUtils.to_project_relative(cls, path: str, workspace: Optional[str] = None) -> str`

## Signature changed (1)

- `mat_utils/_mat_utils.py::MatUtils.stage_textures_relative`
  - was: `(cls, file_nodes: List[str], sourceimages: Optional[str] = None) -> Dict[str, str]`
  - now: `(cls, file_nodes: List[str], sourceimages: Optional[str] = None, external_mode: str = 'copy', scope: str = 'sourceimages') -> Dict[str, str]`
