# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-06-27._

## Added (30)

- `edit_utils/macro_manager/macro_manager_slots.py::MacroManagerSlots(class)`
- `edit_utils/macro_manager/macro_manager_slots.py::MacroManagerSlots.cmb000(self, index)`
- `edit_utils/macro_manager/macro_manager_slots.py::MacroManagerSlots.cmb000_init(self, widget)`
- `edit_utils/macro_manager/macro_manager_slots.py::MacroManagerSlots.header_init(self, widget)`
- `edit_utils/macro_manager/macro_manager_slots.py::MacroManagerSlots.tbl000_init(self, widget)`
- `edit_utils/macros.py::MacroManager.apply_bindings(cls, bindings: Dict[str, dict]) -> None`
- `edit_utils/macros.py::MacroManager.apply_saved_macros(cls, name: Optional[str] = None) -> None`
- `edit_utils/macros.py::MacroManager.clear_hotkey(cls, name: str, key: Optional[str] = None) -> None`
- `edit_utils/macros.py::MacroManager.delete_preset(cls, name: str) -> bool`
- `edit_utils/macros.py::MacroManager.find_conflicts(cls, bindings: Dict[str, dict]) -> Dict[str, List[str]]`
- `edit_utils/macros.py::MacroManager.get_active_preset(cls) -> Optional[str]`
- `edit_utils/macros.py::MacroManager.get_current_bindings(cls) -> Dict[str, dict]`
- `edit_utils/macros.py::MacroManager.list_available_macros(cls) -> Dict[str, str]`
- `edit_utils/macros.py::MacroManager.list_categories(cls) -> List[str]`
- `edit_utils/macros.py::MacroManager.list_presets(cls) -> List[str]`
- `edit_utils/macros.py::MacroManager.load_preset(cls, name: str) -> Dict[str, dict]`
- `edit_utils/macros.py::MacroManager.macro_category(cls, name: str) -> str`
- `edit_utils/macros.py::MacroManager.macro_help(cls, name: str) -> str`
- `edit_utils/macros.py::MacroManager.macro_label(cls, name: str) -> str`
- `edit_utils/macros.py::MacroManager.maya_key_to_qt_sequence(cls, key: str) -> str`
- `edit_utils/macros.py::MacroManager.qt_sequence_to_maya_key(cls, sequence: str) -> str`
- `edit_utils/macros.py::MacroManager.save_preset(cls, name: str, bindings: Optional[Dict[str, dict]] = None) -> str`
- `edit_utils/macros.py::MacroManager.set_active_preset(cls, name: Optional[str]) -> None`
- `edit_utils/macros.py::MacroManager.unset_macro(cls, name: str, key: Optional[str] = None) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb004_init(self, widget) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.save_output_name(self, output_name: str) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_resolution_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_scope_init(self, widget) -> None`
- `ui_utils/hotkey_collisions.py::keystring_to_token(ks: list) -> str`
- `ui_utils/hotkey_collisions.py::live_hotkey_map() -> dict`

## Signature changed (2)

- `core_utils/components.py::Components.set_edge_hardness`
  - was: `(cls, objects, angle_threshold: float, upper_hardness: float = None, lower_hardness: float = None, unlock_normals: bool = False) -> None`
  - now: `(cls, objects, angle_threshold: float, upper_hardness: float = None, lower_hardness: float = None, unlock_normals: bool = False) -> List[str]`
- `rig_utils/_rig_utils.py::RigUtils.rebind_skin_clusters`
  - was: `(cls, meshes: Optional[List[str]] = None, temp_dir: Optional[str] = None, inherits_transform: Optional[bool] = None) -> None`
  - now: `(cls, meshes: Optional[List[str]] = None, temp_dir: Optional[str] = None, inherits_transform: Optional[bool] = None) -> Dict[str, list]`
