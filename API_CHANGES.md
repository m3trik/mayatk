# mayatk — API Changes

_Diff vs the last release (origin/main @ 566cfd7)._

## Added (1)

- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_origin(self) -> None`

## Signature changed (1)

- `env_utils/scene_exporter/task_manager.py::TaskManager.apply_declared_takes`
  - was: `(self)`
  - now: `(self, mode: Union[bool, str, None] = 'both')`
