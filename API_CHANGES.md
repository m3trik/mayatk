# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-22._

## Removed (2)

- `core_utils/diagnostics/scene_diag.py::SceneOverview` — was `(class)`
- `mat_utils/game_shader.py::GameShaderSlots.affix_is_prefix` — was `(self) -> bool`

## Added (42)

- `anim_utils/segment_keys.py::SegmentKeys.format_scene_info_html(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> str`
- `anim_utils/segment_keys.py::SegmentKeys.format_scene_info_text(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None) -> str`
- `anim_utils/segment_keys.py::SegmentKeys.get_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]`
- `anim_utils/segment_keys.py::SegmentKeysInfo.format_time_ranges_html(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], title: Optional[str] = None, **kwargs) -> str`
- `anim_utils/segment_keys.py::SegmentKeysInfo.format_time_ranges_text(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], **kwargs) -> str`
- `core_utils/diagnostics/scene_diag.py::AnalysisManifest(class)`
- `core_utils/diagnostics/scene_diag.py::BudgetBuckets(class)`
- `core_utils/diagnostics/scene_diag.py::BudgetDelta(class)`
- `core_utils/diagnostics/scene_diag.py::BudgetDelta.is_over_budget(self) -> bool`
- `core_utils/diagnostics/scene_diag.py::BudgetDelta.summary(self) -> str`
- `core_utils/diagnostics/scene_diag.py::BudgetStats(class)`
- `core_utils/diagnostics/scene_diag.py::ComplianceStats(class)`
- `core_utils/diagnostics/scene_diag.py::Finding(class)`
- `core_utils/diagnostics/scene_diag.py::FixAction(class)`
- `core_utils/diagnostics/scene_diag.py::InstanceStats(class)`
- `core_utils/diagnostics/scene_diag.py::MaterialSplit(class)`
- `core_utils/diagnostics/scene_diag.py::MissingTexture(class)`
- `core_utils/diagnostics/scene_diag.py::MissingTextureImpact(class)`
- `core_utils/diagnostics/scene_diag.py::MissingTextureImpact.is_empty(self) -> bool`
- `core_utils/diagnostics/scene_diag.py::OffenderLists(class)`
- `core_utils/diagnostics/scene_diag.py::ParetoEntry(class)`
- `core_utils/diagnostics/scene_diag.py::PipelineStats(class)`
- `core_utils/diagnostics/scene_diag.py::SceneAnalyzer.format_audit_html(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]`
- `core_utils/diagnostics/scene_diag.py::SceneAnalyzer.format_audit_text(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]`
- `core_utils/diagnostics/scene_diag.py::SceneInfoSection(class)`
- `core_utils/diagnostics/scene_diag.py::SceneInfoSection.normalize(cls, sections: Optional[List[str]]) -> List[str]`
- `core_utils/diagnostics/scene_diag.py::SceneReport(class)`
- `core_utils/diagnostics/scene_diag.py::SceneReport.to_dict(self) -> Dict[str, Any]`
- `core_utils/diagnostics/scene_diag.py::SharedTexture(class)`
- `core_utils/diagnostics/scene_diag.py::SlotStats(class)`
- `core_utils/diagnostics/scene_diag.py::SummaryStats(class)`
- `core_utils/diagnostics/scene_diag.py::TextureFile(class)`
- `core_utils/diagnostics/scene_diag.py::TextureStats(class)`
- `mat_utils/_affix_mode.py::add_affix_mode_menu(widget, default_mode: str = 'auto', on_change=None)`
- `mat_utils/_affix_mode.py::current_affix_mode(widget) -> str`
- `mat_utils/_affix_mode.py::resolve_affix(widget, default: str = 'prefix') -> Tuple[str, str]`
- `mat_utils/_mat_utils.py::MatUtils.format_mat_info_html(cls, records: List[Dict[str, Any]]) -> str`
- `mat_utils/_mat_utils.py::MatUtils.format_mat_info_text(cls, records: List[Dict[str, Any]]) -> str`
- `mat_utils/_mat_utils.py::MatUtils.format_texture_info_html(cls, info_list: List[Dict[str, Any]]) -> str`
- `mat_utils/_mat_utils.py::MatUtils.format_texture_info_text(cls, info_list: List[Dict[str, Any]]) -> str`
- `mat_utils/_mat_utils.py::MatUtils.get_mat_info(cls, materials: Optional[List[Any]] = None, objects: Optional[List[Any]] = None, optimize_check: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, **optimize_kwargs) -> List[Dict[str, Any]]`
- `uv_utils/rizom_bridge/_rizom_bridge.py::RizomUVBridge.send_to_rizomuv(self, objects, params=None)`

## Signature changed (11)

- `anim_utils/_anim_utils.py::AnimUtils.optimize_keys`
  - was: `(cls, objects: Union[str, str, List[Union[str, str]]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None) -> List[str]`
  - now: `(cls, objects: Union[str, str, List[Union[str, str]]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[str]`
- `anim_utils/playblast_exporter.py::PlayblastExporter.export_variations`
  - was: `(self, output_path: str, base_kwargs: Optional[Dict[str, Any]] = None, scene_name: Optional[str] = None, variations: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]`
  - now: `(self, output_path: str, base_kwargs: Optional[Dict[str, Any]] = None, scene_name: Optional[str] = None, variations: Optional[List[Dict[str, Any]]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]`
- `anim_utils/segment_keys.py::SegmentKeys.collect_segments`
  - was: `(cls, objects: List[Any], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001) -> List[Dict[str, Any]]`
  - now: `(cls, objects: List[Any], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]`
- `anim_utils/shots/_shot_apply.py::apply`
  - was: `(store: ShotStore, plan: MovePlan) -> None`
  - now: `(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> None`
- `core_utils/diagnostics/scene_diag.py::SceneAnalyzer.analyze`
  - was: `(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None) -> List[AssetRecord]`
  - now: `(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None, sections: Optional[List[str]] = None) -> List[AssetRecord]`
- `core_utils/diagnostics/scene_diag.py::SceneAnalyzer.generate_report`
  - was: `(self, records: List[AssetRecord]) -> SceneOverview`
  - now: `(self, records: List[AssetRecord]) -> SceneReport`
- `core_utils/diagnostics/scene_diag.py::SceneAnalyzer.print_report`
  - was: `(self, overview: SceneOverview)`
  - now: `(self, report: SceneReport, sections: Optional[List[str]] = None)`
- `mat_utils/_mat_utils.py::MatUtils.find_texture_files`
  - was: `(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None) -> List[Union[str, Tuple[str, str]]]`
  - now: `(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Union[str, Tuple[str, str]]]`
- `mat_utils/_mat_utils.py::MatUtils.get_scene_mats`
  - was: `(inc=None, exc=None, node_type=None, sort: bool = False, as_dict: bool = False, **filter_kwargs)`
  - now: `(inc=None, exc=None, node_type=None, sort: bool = False, as_dict: bool = False, exclude_defaults: bool = True, **filter_kwargs)`
- `mat_utils/_mat_utils.py::MatUtils.migrate_textures`
  - was: `(cls, materials: Optional[List[str]] = None, old_dir: Optional[str] = None, new_dir: Optional[str] = None, silent: bool = False, delete_old: bool = False, objects: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None) -> None`
  - now: `(cls, materials: Optional[List[str]] = None, old_dir: Optional[str] = None, new_dir: Optional[str] = None, silent: bool = False, delete_old: bool = False, objects: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> None`
- `mat_utils/mat_updater.py::MatUpdater.update_materials`
  - was: `(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False) -> Dict[str, Any]`
  - now: `(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]`
