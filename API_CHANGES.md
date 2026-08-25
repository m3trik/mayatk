# mayatk — API Changes

_Diff vs the last release (origin/main @ 148c745)._

## Added (8)

- `display_utils/_display_utils.py::DisplayUtils.get_surface_shapes(objects: Union[str, object, List]) -> List[str]`
- `display_utils/_display_utils.py::DisplayUtils.is_xray(cls, objects: Union[str, object, List]) -> bool`
- `display_utils/_display_utils.py::DisplayUtils.resync_viewport_xray() -> None`
- `display_utils/_display_utils.py::DisplayUtils.set_xray(cls, objects: Union[str, object, List], state: bool = True, resync: bool = True) -> List[str]`
- `display_utils/_display_utils.py::DisplayUtils.toggle_xray(cls, objects: Union[str, object, List]) -> Optional[Tuple[bool, int]]`
- `env_utils/pm_doctor.py::find_shadows()`
- `env_utils/pm_doctor.py::main()`
- `mat_utils/_mat_utils.py::MatUtils.get_stingray_opacity_mode(cls, mat) -> Optional[str]`
