# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-06-04._

## Removed (9)

- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.render_template` — was `(self, template: str, fbx_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.toolbag_log_path` — was `(self) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.toolbag_path` — was `(self, value: Optional[str]) -> None`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::classify_log_line` — was `(line: str) -> 'Optional[Tuple[str, str]]'`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::dispatch_log_lines` — was `(lines, logger) -> None`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::list_template_modes` — was `() -> 'list[tuple[str, str]]'`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::list_templates` — was `() -> 'list[Path]'`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::resolve_toolbag_log_path` — was `(toolbag_exe: Optional[str]) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::template_modes` — was `(template_path: Path) -> Tuple[str, ...]`

## Added (35)

- `core_utils/preview.py::OperationError(class)`
- `edit_utils/curtain.py::CurtainMesh(class)`
- `edit_utils/curtain.py::CurtainMesh.build(self) -> str`
- `edit_utils/curtain.py::CurtainMesh.create(cls, rail: Sequence[Vec], **opts) -> str`
- `edit_utils/curtain.py::CurtainRig(class)`
- `edit_utils/curtain.py::CurtainRig.attach(curtain: str, curve: str, dropoff: float, cluster: bool = True) -> str`
- `edit_utils/curtain.py::CurtainSlots(class)`
- `edit_utils/curtain.py::CurtainSlots.b001(self)`
- `edit_utils/curtain.py::CurtainSlots.cmb000_init(self, widget)`
- `edit_utils/curtain.py::CurtainSlots.header_init(self, widget)`
- `edit_utils/curtain.py::CurtainSlots.perform_operation(self, objects, contract)`
- `edit_utils/curtain.py::Rail(class)`
- `edit_utils/curtain.py::Rail.frames(points: Sequence[Vec], u_segs: int, closed: bool) -> List[Tuple[Vec, Vec, Vec]]`
- `edit_utils/curtain.py::Rail.from_selection(objects) -> Optional[Tuple[List[Vec], bool]]`
- `edit_utils/curtain.py::Rail.length(points: Sequence[Vec], closed: bool) -> float`
- `edit_utils/curtain.py::Rail.make(width: float = 6.0, curvature: float = 0.0, segments: int = 24, closed: bool = False, y: float = 0.0) -> Tuple[List[Vec], bool]`
- `edit_utils/curtain.py::Rail.resample(points: Sequence[Vec], count: int) -> List[Vec]`
- `edit_utils/curtain.py::Rail.sample_curve(shape: str, count: int = 200) -> Tuple[List[Vec], bool]`
- `edit_utils/curtain.py::catenary_shape(t: float, tension: float) -> float`
- `edit_utils/curtain.py::sag_profile(t: float, tension: float, round_amount: float) -> float`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine(class)`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.render_template(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.send(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.toolbag_log_path(self) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.toolbag_path(self, value: Optional[str]) -> None`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::list_template_modes() -> List[Tuple[str, str]]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::list_templates() -> List[Path]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::template_modes(template_path: Path) -> Tuple[str, ...]`
- `mat_utils/marmoset_bridge/template_params.py::defaults() -> Dict[str, Any]`
- `mat_utils/marmoset_bridge/template_params.py::python_literal(value: Any) -> str`
- `mat_utils/marmoset_bridge/template_params.py::to_context(values: Dict[str, Any]) -> Dict[str, str]`
- `mat_utils/marmoset_bridge/toolbag_log.py::classify_log_line(line: str) -> Optional[Tuple[str, str]]`
- `mat_utils/marmoset_bridge/toolbag_log.py::dispatch_log_lines(lines, logger) -> None`
- `mat_utils/marmoset_bridge/toolbag_log.py::resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]`
- `mat_utils/marmoset_bridge/toolbag_log.py::start_toolbag_log_tail(log_path: str, start_offset: int, process, logger, poll_interval: float = 0.4, file_wait_timeout: float = 60.0)`

## Signature changed (1)

- `xform_utils/_xform_utils.py::XformUtils.move_to`
  - was: `(cls, source, target, group_move=False)`
  - now: `(cls, source, target, pivot='center', group_move=False)`
