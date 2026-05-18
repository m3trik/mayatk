# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-18._

## Signature changed (1)

- `core_utils/components.py::Components.set_edge_hardness`
  - was: `(cls, objects, angle_threshold: float, upper_hardness: float = None, lower_hardness: float = None) -> None`
  - now: `(cls, objects, angle_threshold: float, upper_hardness: float = None, lower_hardness: float = None, unlock_normals: bool = False) -> None`
