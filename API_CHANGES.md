# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-19._

## Removed (13)

- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.bridge` — was `(self) -> MarmosetBridge`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.cmb000_init` — was `(self, widget)`
- `mat_utils/marmoset_bridge/parameters.py::MarmosetParam` — was `(class)`
- `mat_utils/marmoset_bridge/parameters.py::MarmosetParam.format_value` — was `(self, value: Any) -> str`
- `mat_utils/substance_bridge/parameters.py::SubstanceParam` — was `(class)`
- `mat_utils/substance_bridge/parameters.py::SubstanceParam.format_cli` — was `(self, value: Any) -> str`
- `mat_utils/substance_bridge/parameters.py::SubstanceParam.format_js` — was `(self, value: Any) -> str`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.bridge` — was `(self) -> SubstanceBridge`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.cmb000_init` — was `(self, widget)`
- `uv_utils/rizom_bridge/parameters.py::RizomParam` — was `(class)`
- `uv_utils/rizom_bridge/parameters.py::RizomParam.format_value` — was `(self, value: Any) -> str`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.bridge` — was `(self) -> RizomUVBridge`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.cmb000_init` — was `(self, widget)`

## Added (18)

- `env_utils/_env_utils.py::EnvUtils.default_artifact_dir(cls) -> str`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.list_template_modes(self)`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.make_bridge(self) -> MarmosetBridge`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.params_module(self)`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.select_initial_template_index(self, pairs)`
- `mat_utils/marmoset_bridge/marmoset_bridge_slots.py::MarmosetBridgeSlots.template_dir(self) -> Path`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.list_template_modes(self)`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.make_bridge(self) -> SubstanceBridge`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.params_module(self)`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.select_initial_template_index(self, pairs)`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.template_dir(self) -> Path`
- `ui_utils/maya_bridge_slots.py::MayaBridgeSlotsBase(class)`
- `ui_utils/maya_bridge_slots.py::MayaBridgeSlotsBase.default_output_dir(self) -> str`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.list_template_modes(self)`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.make_bridge(self) -> RizomUVBridge`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.open_uv_editor(self)`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.params_module(self)`
- `uv_utils/rizom_bridge/rizom_bridge_slots.py::RizomBridgeSlots.template_dir(self) -> Path`
