# mayatk — API Changes

_Diff vs the last release (origin/main @ 8aa7bdc). Generated 2026-08-19._

## Removed (1)

- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.reorder_shots` — was `(self, shot_id_a: int, shot_id_b: int) -> None`

## Added (22)

- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.move_curve_keys(cls, crv: str, times: list, delta: float, plug: Optional[str] = None, eps: float = 0.001) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.recreate_curve_keys(cls, crv: str, pairs: list, plug: Optional[str] = None, eps: float = 0.001) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.b012(self) -> None`
- `mat_utils/_mat_utils.py::MatUtils.probe_texture_path(cls, path: str) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.baked_texture_dir(cls) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.source_material_name(cls, mat_name: str) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.texture_set_aliases(cls, materials, log=None) -> Dict[str, str]`
- `ui_utils/maya_bridge_slots_base.py::MayaBridgeSlotsBase.live_param_tooltips(self)`
- `uv_utils/texture_transfer.py::TextureTransfer(class)`
- `uv_utils/texture_transfer.py::TextureTransfer.assign_results(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None) -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.auto_source_uv_set(cls, obj) -> str`
- `uv_utils/texture_transfer.py::TextureTransfer.correspondence(cls, target, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None) -> Dict[str, Any]`
- `uv_utils/texture_transfer.py::TextureTransfer.default_output_dir(cls) -> str`
- `uv_utils/texture_transfer.py::TextureTransfer.face_materials(cls, obj) -> Tuple[List[str], 'np.ndarray']`
- `uv_utils/texture_transfer.py::TextureTransfer.material_constant(cls, material: str, channel: str) -> Optional[Tuple[float, ...]]`
- `uv_utils/texture_transfer.py::TextureTransfer.material_maps(material: str) -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.output_base_dir() -> Optional[str]`
- `uv_utils/texture_transfer.py::TextureTransfer.pair_by_name(targets: Sequence[str], sources: Sequence[str]) -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.positions_match(cls, a, b, tolerance: float = 0.0001) -> bool`
- `uv_utils/texture_transfer.py::TextureTransfer.resolve_output_dir(cls, entry: Optional[str] = None) -> str`
- `uv_utils/texture_transfer.py::TextureTransfer.topology_matches(cls, a, b) -> Tuple[bool, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.transfer(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_suffix: str = '_TRANSFER') -> Dict[str, Dict[str, str]]`

## Signature changed (3)

- `env_utils/_env_utils.py::EnvUtils.get_env_info`
  - was: `(key)`
  - now: `(key=None)`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.build_bake_pairs_manifest`
  - was: `(objects: Sequence[str], high_suffix: str, low_suffix: str, include_children: bool = True) -> Dict[str, str]`
  - now: `(objects: Sequence[str], high_suffix: str, low_suffix: str, include_children: bool = True, log=None) -> Dict[str, str]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.send`
  - was: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
  - now: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
