# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-16._

## Removed (11)

- `core_utils/preview.py::Preview.disable_on_external_undo` — was `(self) -> None`
- `core_utils/preview.py::Preview.disable_on_selection_change` — was `(self) -> None`
- `core_utils/preview.py::Preview.eventFilter` — was `(self, obj, event)`
- `core_utils/preview.py::Preview.has_changes` — was `(self) -> bool`
- `core_utils/preview.py::Preview.safe_operation` — was `(func: Callable) -> Callable`
- `core_utils/preview.py::Preview.undo_if_needed` — was `(self) -> None`
- `mat_utils/marmoset/bridge.py::MarmosetBridge` — was `(class)`
- `mat_utils/marmoset/bridge.py::MarmosetBridge.send` — was `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, preset_file: Optional[str] = None, headless: bool = False, template: str = 'import') -> Optional[str]`
- `mat_utils/marmoset/templates/bake.py::main` — was `()`
- `mat_utils/marmoset/templates/import.py::main` — was `()`
- `rig_utils/shadow_rig.py::ShadowRigSlots.create_shadow` — was `(self)`

## Added (53)

- `core_utils/preview.py::CleanupContract(class)`
- `core_utils/preview.py::CleanupContract.add_file(self, path) -> None`
- `core_utils/preview.py::CleanupContract.record_modification(self, node: str, attr: str) -> None`
- `core_utils/preview.py::CleanupContract.rollback(self) -> None`
- `core_utils/preview_old.py::Preview(class)`
- `core_utils/preview_old.py::Preview.cleanup(self) -> None`
- `core_utils/preview_old.py::Preview.cleanup_all_instances(cls) -> None`
- `core_utils/preview_old.py::Preview.conditionally_disable(self) -> None`
- `core_utils/preview_old.py::Preview.conditionally_enable(self) -> None`
- `core_utils/preview_old.py::Preview.disable(self) -> None`
- `core_utils/preview_old.py::Preview.disable_on_external_undo(self) -> None`
- `core_utils/preview_old.py::Preview.disable_on_selection_change(self) -> None`
- `core_utils/preview_old.py::Preview.enable(self) -> None`
- `core_utils/preview_old.py::Preview.enabled(self) -> bool`
- `core_utils/preview_old.py::Preview.eventFilter(self, obj, event)`
- `core_utils/preview_old.py::Preview.finalize_changes(self)`
- `core_utils/preview_old.py::Preview.get_operated_objects(self) -> List[str]`
- `core_utils/preview_old.py::Preview.has_changes(self) -> bool`
- `core_utils/preview_old.py::Preview.init_show_hide_behavior(self, enable_on_show: bool, disable_on_hide: bool) -> None`
- `core_utils/preview_old.py::Preview.operated_object_count(self) -> int`
- `core_utils/preview_old.py::Preview.refresh(self, *args)`
- `core_utils/preview_old.py::Preview.safe_operation(func: Callable) -> Callable`
- `core_utils/preview_old.py::Preview.toggle(self, state: bool) -> None`
- `core_utils/preview_old.py::Preview.undo_if_needed(self) -> None`
- `core_utils/preview_old.py::Preview.validate_operation(self, objects: List[Any]) -> bool`
- `core_utils/preview_old.py::cleanup_all_previews() -> None`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge(class)`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.render_template(self, template: str, fbx_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.send(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, preset_file: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.toolbag_path(self, value: Optional[str]) -> None`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::list_template_modes() -> 'list[tuple[str, str]]'`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::list_templates() -> 'list[Path]'`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::template_modes(template_path: Path) -> Tuple[str, ...]`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots(class)`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.b000(self)`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.bridge(self) -> MarmosetBridge`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.cmb000_init(self, widget)`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.header_init(self, widget)`
- `mat_utils/marmoset_bridge/parameters.py::MarmosetParam(class)`
- `mat_utils/marmoset_bridge/parameters.py::MarmosetParam.format_value(self, value: Any) -> str`
- `mat_utils/marmoset_bridge/parameters.py::defaults() -> 'dict[str, Any]'`
- `mat_utils/marmoset_bridge/parameters.py::referenced_keys(script_text: str) -> 'set[str]'`
- `mat_utils/marmoset_bridge/parameters.py::render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`
- `mat_utils/marmoset_bridge/templates/bake.py::main()`
- `mat_utils/marmoset_bridge/templates/import.py::main()`
- `mat_utils/marmoset_bridge/templates/lookdev.py::main()`
- `rig_utils/shadow_rig.py::ShadowRigSlots.perform_operation(self, objects, contract)`
- `xform_utils/pivot_watcher.py::PivotWatcher(class)`
- `xform_utils/pivot_watcher.py::PivotWatcher.attach_widget(self, widget) -> None`
- `xform_utils/pivot_watcher.py::PivotWatcher.owner(self) -> Any`
- `xform_utils/pivot_watcher.py::PivotWatcher.start(self) -> None`
- `xform_utils/pivot_watcher.py::PivotWatcher.started(self) -> bool`
- `xform_utils/pivot_watcher.py::PivotWatcher.stop(self) -> None`

## Signature changed (13)

- `core_utils/preview.py::Preview.finalize_changes`
  - was: `(self)`
  - now: `(self) -> None`
- `core_utils/preview.py::Preview.refresh`
  - was: `(self, *args)`
  - now: `(self, *args) -> None`
- `edit_utils/bevel.py::BevelSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `edit_utils/bridge.py::BridgeSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `edit_utils/cut_on_axis.py::CutOnAxisSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `edit_utils/duplicate_grid.py::DuplicateGridSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `edit_utils/duplicate_linear.py::DuplicateLinearSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `edit_utils/mirror.py::MirrorSlots.perform_operation`
  - was: `(self, objects)`
  - now: `(self, objects, contract)`
- `mat_utils/_mat_utils.py::MatUtils.create_stingray_shader`
  - was: `(name, opacity=False)`
  - now: `(name, opacity=False, opacity_mode=None)`
- `mat_utils/image_to_plane/_image_to_plane.py::ImageToPlane.create`
  - was: `(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: str = '_MAT', prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP') -> Dict[str, object]`
  - now: `(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: str = '_MAT', prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP', stingray_opacity_mode: str = 'transparent', mask_threshold: float = 0.5) -> Dict[str, object]`
- `rig_utils/shadow_rig.py::ShadowRig.create_material`
  - was: `(self)`
  - now: `(self, shader_type='stingray', stingray_opacity_mode='transparent')`
- `rig_utils/shadow_rig.py::ShadowRig.create_silhouette_texture`
  - was: `(self, size=512, axis='auto', recursive=True)`
  - now: `(self, size=512, axis='auto', recursive=True, *, uniform_alpha=False, falloff_source=None, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.5)`
