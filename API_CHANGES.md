# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-23._

## Added (1)

- `mat_utils/_mat_utils.py::MatUtils.is_mat_assigned(mat: object) -> bool`

## Signature changed (1)

- `mat_utils/_mat_utils.py::MatUtils.get_mat_info`
  - was: `(cls, materials: Optional[List[Any]] = None, objects: Optional[List[Any]] = None, optimize_check: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, **optimize_kwargs) -> List[Dict[str, Any]]`
  - now: `(cls, materials: Optional[List[Any]] = None, objects: Optional[List[Any]] = None, optimize_check: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, exclude_defaults: bool = False, exclude_unassigned: bool = False, include_textures: bool = True, include_image_metadata: bool = True, **optimize_kwargs) -> List[Dict[str, Any]]`
