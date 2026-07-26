# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-07-26._

## Removed (3)

- `core_utils/auto_instancer/_auto_instancer.py::auto_instance` — was `(nodes: Optional[Sequence[object]] = None, tolerance: float = 0.001, scale_tolerance: Optional[float] = None, require_same_material: Union[bool, int] = True, check_uvs: bool = False, check_hierarchy: bool = False, separate_combined: bool = False, combine_assemblies: bool = True, combine_non_instanced: bool = True, combine_by_material: bool = True, combine_by_distance: bool = True, combine_distance_threshold: float = 10000.0, search_radius_mult: float = 1.5, is_static: bool = True, needs_individual: bool = False, will_be_lightmapped: bool = False, can_gpu_instance: bool = True, verbose: bool = True, log_level: str = 'WARNING', return_summary: bool = False) -> Union[List[str], Tuple[List[str], Dict[str, object]]]`
- `env_utils/blender_bridge/_scene_import.py::bake_blender_scene` — was `(src_path: str, **kwargs: Any) -> str`
- `env_utils/blender_bridge/_scene_import.py::import_blender_scene` — was `(src_path: str, **kwargs: Any) -> List[str]`

## Added (1)

- `core_utils/auto_instancer/_auto_instancer.py::AutoInstancer.run_once(cls, nodes: Optional[Sequence[object]] = None, *, return_summary: bool = False, **config) -> Union[List[str], Tuple[List[str], Dict[str, object]]]`
