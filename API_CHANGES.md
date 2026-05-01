# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-01._

## Removed (35)

- `anim_utils/blendshape_animator/_applicator.py::Applicator` — was `(class)`
- `anim_utils/blendshape_animator/_applicator.py::Applicator.apply_tweens` — was `(self, tweens: Optional[List[Target]] = None, skip_duplicates: bool = True, validate_topology: bool = False) -> List[Tuple[Target, ApplyStatus]]`
- `anim_utils/blendshape_animator/_applicator.py::Applicator.validate_topology` — was `(self, tweens: List[Target]) -> List[Target]`
- `anim_utils/blendshape_animator/_applicator.py::ApplyStatus` — was `(class)`
- `anim_utils/blendshape_animator/_creator.py::Creator` — was `(class)`
- `anim_utils/blendshape_animator/_creator.py::Creator.create_frame_based_tween` — was `(self, target_frame: int) -> Optional[Target]`
- `anim_utils/blendshape_animator/_creator.py::Creator.create_weight_based_tweens` — was `(self, weights: List[float], group_name: str = '_morphInbetweens_GRP', name_prefix: str = 'morph_ib') -> List[Target]`
- `anim_utils/blendshape_animator/_creator.py::Creator.find_nearby_weight` — was `(self, target_weight: float, existing_weights: Set[float], tolerance: float = 0.01) -> Optional[float]`
- `anim_utils/blendshape_animator/_creator.py::Creator.get_existing_weights` — was `(self) -> Set[float]`
- `anim_utils/blendshape_animator/_creator.py::Creator.tag_tween_mesh` — was `(self, mesh: str, weight: float, target_frame: Optional[int] = None) -> None`
- `anim_utils/blendshape_animator/_helpers.py::list_history` — was `(node: str, type_filter: Optional[str] = None) -> List[str]`
- `anim_utils/blendshape_animator/_keyframes.py::Keyframes` — was `(class)`
- `anim_utils/blendshape_animator/_keyframes.py::Keyframes.create_keyframes` — was `(self, start_frame: int, end_frame: int) -> bool`
- `anim_utils/blendshape_animator/_keyframes.py::Keyframes.get_frame_range` — was `(self) -> Tuple[int, int]`
- `anim_utils/blendshape_animator/_keyframes.py::Keyframes.test_morph` — was `(self) -> bool`
- `anim_utils/blendshape_animator/_recovery.py::Recovery` — was `(class)`
- `anim_utils/blendshape_animator/_recovery.py::Recovery.fix_corrupted_animation` — was `(cls, base_mesh: str, target_mesh: str) -> bool`
- `anim_utils/blendshape_animator/_recovery.py::Recovery.recover_with_targets` — was `(cls, base_mesh: str, target_mesh: str) -> bool`
- `anim_utils/blendshape_animator/_target.py::Target` — was `(class)`
- `anim_utils/blendshape_animator/_target.py::Target.base_mesh_name` — was `(self) -> str`
- `anim_utils/blendshape_animator/_target.py::Target.blendshape_name` — was `(self) -> str`
- `anim_utils/blendshape_animator/_target.py::Target.target_frame` — was `(self) -> Optional[int]`
- `anim_utils/blendshape_animator/_target.py::Target.update_references` — was `(self, new_blendshape: str, new_base_mesh: str) -> None`
- `anim_utils/blendshape_animator/_target.py::Target.weight` — was `(self) -> float`
- `anim_utils/blendshape_animator/_target.py::Targets` — was `(class)`
- `anim_utils/blendshape_animator/_target.py::Targets.find_all_targets` — was `(cls) -> List[Target]`
- `anim_utils/blendshape_animator/_target.py::Targets.group_by_weight` — was `(cls, tweens: List[Target]) -> Dict[float, List[Target]]`
- `anim_utils/blendshape_animator/_target.py::Targets.update_all_references` — was `(cls, new_blendshape: str, new_base_mesh: str) -> int`
- `anim_utils/blendshape_animator/_validator.py::Validator` — was `(class)`
- `anim_utils/blendshape_animator/_validator.py::Validator.validate_blendshape` — was `(cls, blendshape: str) -> bool`
- `anim_utils/blendshape_animator/_validator.py::Validator.validate_meshes` — was `(cls, mesh1: str, mesh2: str) -> bool`
- `anim_utils/blendshape_animator/_weights.py::Weights` — was `(class)`
- `anim_utils/blendshape_animator/_weights.py::Weights.frame_to_weight` — was `(cls, frame: int, start_frame: int, end_frame: int) -> float`
- `anim_utils/blendshape_animator/_weights.py::Weights.generate_weights` — was `(cls, count: int, weight_range: Tuple[float, float] = (0.0, 1.0), include_endpoints: bool = False) -> List[float]`
- `anim_utils/blendshape_animator/_weights.py::Weights.round_weight` — was `(cls, weight: float) -> float`

## Added (35)

- `anim_utils/blendshape_animator/applicator.py::Applicator(class)`
- `anim_utils/blendshape_animator/applicator.py::Applicator.apply_tweens(self, tweens: Optional[List[Target]] = None, skip_duplicates: bool = True, validate_topology: bool = False) -> List[Tuple[Target, ApplyStatus]]`
- `anim_utils/blendshape_animator/applicator.py::Applicator.validate_topology(self, tweens: List[Target]) -> List[Target]`
- `anim_utils/blendshape_animator/applicator.py::ApplyStatus(class)`
- `anim_utils/blendshape_animator/creator.py::Creator(class)`
- `anim_utils/blendshape_animator/creator.py::Creator.create_frame_based_tween(self, target_frame: int) -> Optional[Target]`
- `anim_utils/blendshape_animator/creator.py::Creator.create_weight_based_tweens(self, weights: List[float], group_name: str = '_morphInbetweens_GRP', name_prefix: str = 'morph_ib') -> List[Target]`
- `anim_utils/blendshape_animator/creator.py::Creator.find_nearby_weight(self, target_weight: float, existing_weights: Set[float], tolerance: float = 0.01) -> Optional[float]`
- `anim_utils/blendshape_animator/creator.py::Creator.get_existing_weights(self) -> Set[float]`
- `anim_utils/blendshape_animator/creator.py::Creator.tag_tween_mesh(self, mesh: str, weight: float, target_frame: Optional[int] = None) -> None`
- `anim_utils/blendshape_animator/helpers.py::list_history(node: str, type_filter: Optional[str] = None) -> List[str]`
- `anim_utils/blendshape_animator/keyframes.py::Keyframes(class)`
- `anim_utils/blendshape_animator/keyframes.py::Keyframes.create_keyframes(self, start_frame: int, end_frame: int) -> bool`
- `anim_utils/blendshape_animator/keyframes.py::Keyframes.get_frame_range(self) -> Tuple[int, int]`
- `anim_utils/blendshape_animator/keyframes.py::Keyframes.test_morph(self) -> bool`
- `anim_utils/blendshape_animator/recovery.py::Recovery(class)`
- `anim_utils/blendshape_animator/recovery.py::Recovery.fix_corrupted_animation(cls, base_mesh: str, target_mesh: str) -> bool`
- `anim_utils/blendshape_animator/recovery.py::Recovery.recover_with_targets(cls, base_mesh: str, target_mesh: str) -> bool`
- `anim_utils/blendshape_animator/target.py::Target(class)`
- `anim_utils/blendshape_animator/target.py::Target.base_mesh_name(self) -> str`
- `anim_utils/blendshape_animator/target.py::Target.blendshape_name(self) -> str`
- `anim_utils/blendshape_animator/target.py::Target.target_frame(self) -> Optional[int]`
- `anim_utils/blendshape_animator/target.py::Target.update_references(self, new_blendshape: str, new_base_mesh: str) -> None`
- `anim_utils/blendshape_animator/target.py::Target.weight(self) -> float`
- `anim_utils/blendshape_animator/target.py::Targets(class)`
- `anim_utils/blendshape_animator/target.py::Targets.find_all_targets(cls) -> List[Target]`
- `anim_utils/blendshape_animator/target.py::Targets.group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]`
- `anim_utils/blendshape_animator/target.py::Targets.update_all_references(cls, new_blendshape: str, new_base_mesh: str) -> int`
- `anim_utils/blendshape_animator/validator.py::Validator(class)`
- `anim_utils/blendshape_animator/validator.py::Validator.validate_blendshape(cls, blendshape: str) -> bool`
- `anim_utils/blendshape_animator/validator.py::Validator.validate_meshes(cls, mesh1: str, mesh2: str) -> bool`
- `anim_utils/blendshape_animator/weights.py::Weights(class)`
- `anim_utils/blendshape_animator/weights.py::Weights.frame_to_weight(cls, frame: int, start_frame: int, end_frame: int) -> float`
- `anim_utils/blendshape_animator/weights.py::Weights.generate_weights(cls, count: int, weight_range: Tuple[float, float] = (0.0, 1.0), include_endpoints: bool = False) -> List[float]`
- `anim_utils/blendshape_animator/weights.py::Weights.round_weight(cls, weight: float) -> float`
