# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-06-22._

## Removed (1)

- `mat_utils/arnold_bridge.py::ArnoldBridgeSlots.b002` — was `(self) -> None`

## Added (8)

- `render_utils/_render_utils.py::RenderUtils(class)`
- `render_utils/_render_utils.py::RenderUtils.current_renderer() -> str`
- `render_utils/_render_utils.py::RenderUtils.get_available_renderers(cls) -> List[Dict[str, object]]`
- `render_utils/_render_utils.py::RenderUtils.redo_previous_render(editor: str = 'render') -> None`
- `render_utils/_render_utils.py::RenderUtils.render_camera(camera: str, editor: str = 'render') -> None`
- `render_utils/_render_utils.py::RenderUtils.set_renderer(cls, name: str) -> None`
- `render_utils/_render_utils.py::RenderUtils.start_ipr(cls, camera: str, renderer: Optional[str] = None) -> bool`
- `render_utils/_render_utils.py::RenderUtils.supports_ipr(cls, renderer: Optional[str] = None) -> bool`
