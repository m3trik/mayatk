# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-07-29._

## Removed (19)

- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar` — was `(class)`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.base_stem` — was `(cls, export_path: str) -> str`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.build_clean_path_set` — was `(objects) -> set`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.build_full_path_set` — was `(cls, objects) -> set`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.clean_stale_diff` — was `(cls, export_path: str, *, base_stem: bool = False) -> None`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.compare` — was `(cls, export_path: str, current_paths: set, *, base_stem: bool = False) -> Tuple[bool, list, list]`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.count_descendants` — was `(top_path: str, all_paths) -> int`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.detect_reparenting` — was `(missing: list, extra: list) -> list`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.diff_report_path_for` — was `(cls, export_path: str, *, base_stem: bool = False) -> str`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.ensure_base_name` — was `(cls, export_path: str) -> Optional[str]`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.expand_to_descendants` — was `(objects) -> list`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.find_legacy_manifest` — was `(cls, export_path: str) -> Optional[str]`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.get_top_level` — was `(paths) -> list`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.manifest_path_for` — was `(cls, export_path: str, *, base_stem: bool = False) -> str`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.read_manifest` — was `(cls, export_path: str, *, base_stem: bool = False) -> Optional[Set[str]]`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.rename` — was `(cls, old_export_path: str, new_export_path: str) -> list`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.write_diff_report` — was `(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]`
- `env_utils/hierarchy_sync/hierarchy_sidecar.py::HierarchySidecar.write_manifest` — was `(cls, export_path: str, paths, *, base_stem: bool = False) -> Optional[str]`
- `ui_utils/maya_ui_handler.py::MayaUiHandler.apply_styles` — was `(self, ui, style=None)`

## Added (24)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar(class)`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.base_stem(cls, export_path: str) -> str`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.build_clean_path_set(objects) -> set`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.build_full_path_set(cls, objects) -> set`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.clean_stale_diff(cls, export_path: str, *, base_stem: bool = False) -> None`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.compare(cls, export_path: str, current_paths: set, *, base_stem: bool = False) -> Tuple[bool, list, list]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.count_descendants(top_path: str, all_paths) -> int`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.detect_reparenting(missing: list, extra: list) -> list`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.diff_report_path_for(cls, export_path: str, *, base_stem: bool = False) -> str`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.ensure_base_name(cls, export_path: str) -> Optional[str]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.expand_to_descendants(objects) -> list`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.find_legacy_manifest(cls, export_path: str) -> Optional[str]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.get_top_level(paths) -> list`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.manifest_path_for(cls, export_path: str, *, base_stem: bool = False) -> str`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.migrate_legacy(cls, export_path: str, *, base_stem: bool = False) -> Optional[str]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.read_data(cls, export_path: str, *, base_stem: bool = False) -> Optional[dict]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.read_manifest(cls, export_path: str, *, base_stem: bool = False) -> Optional[Set[str]]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.rename(cls, old_export_path: str, new_export_path: str) -> list`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_diff_report(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_manifest(cls, export_path: str, paths, *, data: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
- `node_utils/_node_utils.py::NodeUtils.get_classification_tokens(node_type: str) -> List[str]`
- `ui_utils/maya_ui_handler.py::MayaUiHandler.default_persistence(self, ui) -> str`
- `uv_utils/_uv_utils.py::UvUtils.gather_to_udim(cls, objects, udim: Optional[int] = None, map_size: int = 4096) -> Optional[int]`
- `uv_utils/shell_xform.py::ShellXformSlots.gather_to_udim(self)`

## Signature changed (5)

- `mat_utils/_mat_utils.py::MatUtils.get_connected_shaders`
  - was: `(file_nodes) -> List[str]`
  - now: `(cls, file_nodes) -> List[str]`
- `mat_utils/_mat_utils.py::MatUtils.get_file_nodes`
  - was: `(cls, materials: Optional[List[str]] = None, raw: bool = False, return_type: str = 'fileNode') -> list`
  - now: `(cls, materials: Optional[List[str]] = None, raw: bool = False, return_type: str = 'fileNode', exc_classification=None) -> list`
- `mat_utils/_mat_utils.py::MatUtils.get_scene_mats`
  - was: `(inc=None, exc=None, node_type=None, sort: bool = False, as_dict: bool = False, exclude_defaults: bool = True, **filter_kwargs)`
  - now: `(inc=None, exc=None, node_type=None, sort: bool = False, as_dict: bool = False, exclude_defaults: bool = True, exclude_utility_nodes: bool = True, exc_classification=None, **filter_kwargs)`
- `mat_utils/_mat_utils.py::MatUtils.resolve_path`
  - was: `(path: str) -> Union[str, None]`
  - now: `(path: str, search: bool = True) -> Union[str, None]`
- `uv_utils/_uv_utils.py::UvUtils.get_uv_shell_sets`
  - was: `(objects=None, returned_type='shell')`
  - now: `(objects=None, returned_type='shell', whole_shells=False)`
