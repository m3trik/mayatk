# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-06-10._

## Removed (30)

- `anim_utils/shots/shot_manifest/behaviors/__init__.py::apply_audio_clip` — was `(obj: str, start: float, end: float, source_path: str = '') -> None`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::apply_behavior` — was `(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::apply_to_shots` — was `(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::compute_duration` — was `(behavior_entries: List[Dict[str, str]], fallback: float = 30, fps: Optional[float] = None) -> float`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::list_behaviors` — was `(search_path: Optional[Path] = None, kind: Optional[str] = None) -> List[str]`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::load_behavior` — was `(name: str, search_path: Optional[Path] = None) -> Dict[str, Any]`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::resolve_keys` — was `(block_def: Dict, start: float, end: float) -> List[Dict[str, Any]]`
- `anim_utils/shots/shot_manifest/behaviors/__init__.py::verify_behavior` — was `(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None) -> bool`
- `anim_utils/shots/shot_manifest/mapping/__init__.py::discover` — was `(directory: Optional[str] = None) -> List[str]`
- `anim_utils/shots/shot_manifest/mapping/__init__.py::load_mapping` — was `(name: str, directory: Optional[str] = None) -> Dict[str, Any]`
- `anim_utils/shots/shot_manifest/mapping/__init__.py::resolve` — was `(csv_path: str, mapping: Optional[Dict[str, Any]] = None, *, name: Optional[str] = None, directory: Optional[str] = None) -> List[BuilderStep]`
- `light_utils/lightmap_baker.py::LightmapBaker` — was `(class)`
- `light_utils/lightmap_baker.py::LightmapBaker.bake_fused` — was `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, uv_set: Optional[str] = None, map_size: Optional[int] = None, create_uvs: bool = True, dilate: bool = True, dilate_iterations: Optional[int] = None, alpha_threshold: float = 0.001, prefix: str = 'lightmap_', suffix: str = '', backend: str = 'arnold', on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Any] = None) -> Dict[str, str]`
- `light_utils/lightmap_baker.py::LightmapBaker.bake_separated` — was `(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'lightmap_irr_', **kwargs) -> Dict[str, str]`
- `light_utils/lightmap_baker.py::LightmapBaker.commit_lightmap` — was `(self, mapping: Dict[str, str], intensity: float = 1.0) -> Dict[str, str]`
- `light_utils/lightmap_baker.py::LightmapBaker.commit_unlit` — was `(self, mapping: Dict[str, str]) -> Dict[str, str]`
- `light_utils/lightmap_baker.py::LightmapBaker.from_preset` — was `(cls, name: str, **overrides) -> 'LightmapBaker'`
- `light_utils/lightmap_baker.py::LightmapBaker.preset_store` — was `() -> 'ptk.PresetStore'`
- `light_utils/lightmap_baker.py::LightmapBaker.revert` — was `(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker.py::LightmapBaker.revert_lightmap` — was `(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker.py::LightmapBaker.revert_unlit` — was `(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker.py::LightmapBakerSlots` — was `(class)`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.b000` — was `(self) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.cmb000` — was `(self, index, widget) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.cmb000_init` — was `(self, widget) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.cmb001_init` — was `(self, widget) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.header_init` — was `(self, widget) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.open_sourceimages` — was `(self) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.revert_to_source` — was `(self) -> None`
- `light_utils/lightmap_baker.py::LightmapBakerSlots.txt000_init` — was `(self, widget) -> None`

## Added (32)

- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::apply_audio_clip(obj: str, start: float, end: float, source_path: str = '') -> None`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::apply_behavior(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::apply_to_shots(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::compute_duration(behavior_entries: List[Dict[str, str]], fallback: float = 30, fps: Optional[float] = None) -> float`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::list_behaviors(search_path: Optional[Path] = None, kind: Optional[str] = None) -> List[str]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::load_behavior(name: str, search_path: Optional[Path] = None) -> Dict[str, Any]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::resolve_keys(block_def: Dict, start: float, end: float) -> List[Dict[str, Any]]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::verify_behavior(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None) -> bool`
- `anim_utils/shots/shot_manifest/mapping/_mapping.py::discover(directory: Optional[str] = None) -> List[str]`
- `anim_utils/shots/shot_manifest/mapping/_mapping.py::load_mapping(name: str, directory: Optional[str] = None) -> Dict[str, Any]`
- `anim_utils/shots/shot_manifest/mapping/_mapping.py::resolve(csv_path: str, mapping: Optional[Dict[str, Any]] = None, *, name: Optional[str] = None, directory: Optional[str] = None) -> List[BuilderStep]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker(class)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_fused(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, uv_set: Optional[str] = None, map_size: Optional[int] = None, create_uvs: bool = True, dilate: bool = True, dilate_iterations: Optional[int] = None, alpha_threshold: float = 0.001, prefix: str = 'lightmap_', suffix: str = '', backend: str = 'arnold', on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Any] = None) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_separated(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'lightmap_irr_', **kwargs) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.commit_lightmap(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.commit_unlit(self, mapping: Dict[str, str]) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.from_preset(cls, name: str, **overrides) -> 'LightmapBaker'`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.pack_atlas(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap') -> Dict[str, Tuple[str, List[float]]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.preset_store() -> 'ptk.PresetStore'`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.revert(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.revert_lightmap(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.revert_unlit(self, objects: Optional[List[str]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots(class)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.b000(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb000(self, index, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb000_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb001_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb002_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.header_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.open_sourceimages(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.revert_to_source(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.txt000_init(self, widget) -> None`
