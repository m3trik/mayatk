# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-20._

## Signature changed (1)

- `env_utils/_env_utils.py::EnvUtils.export_scene_as_fbx`
  - was: `(file_path: str = None, **fbx_options: Any) -> None`
  - now: `(file_path: str = None, *, selection_only: bool = False, **fbx_options: Any) -> None`
