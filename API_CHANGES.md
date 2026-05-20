# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-05-20._

## Removed (12)

- `node_utils/attributes/event_triggers.py::EventTriggers` — was `(class)`
- `node_utils/attributes/event_triggers.py::EventTriggers.add_events` — was `(cls, objects: Optional[List] = None, events: Optional[List[str]] = None, category: Optional[str] = None) -> None`
- `node_utils/attributes/event_triggers.py::EventTriggers.attr_names` — was `(cls, category: Optional[str] = None) -> Tuple[str, str]`
- `node_utils/attributes/event_triggers.py::EventTriggers.bake_manifest` — was `(cls, objects: Optional[List] = None, category: Optional[str] = None) -> Dict[str, str]`
- `node_utils/attributes/event_triggers.py::EventTriggers.clear_key` — was `(cls, obj, time: Optional[float] = None, category: Optional[str] = None) -> None`
- `node_utils/attributes/event_triggers.py::EventTriggers.create` — was `(cls, objects: Optional[List] = None, events: Optional[List[str]] = None, category: Optional[str] = None) -> Dict[str, Dict]`
- `node_utils/attributes/event_triggers.py::EventTriggers.ensure` — was `(cls, objects: Optional[List] = None, events: Optional[List[str]] = None, category: Optional[str] = None) -> Dict[str, Dict]`
- `node_utils/attributes/event_triggers.py::EventTriggers.event_index` — was `(cls, obj, event_name: str, category: Optional[str] = None) -> int`
- `node_utils/attributes/event_triggers.py::EventTriggers.get_events` — was `(cls, obj, category: Optional[str] = None) -> List[str]`
- `node_utils/attributes/event_triggers.py::EventTriggers.iter_keyed_events` — was `(cls, obj, category: Optional[str] = None) -> List[Tuple[float, str]]`
- `node_utils/attributes/event_triggers.py::EventTriggers.remove` — was `(cls, objects: Optional[List] = None, category: Optional[str] = None, clean_audio: bool = True) -> None`
- `node_utils/attributes/event_triggers.py::EventTriggers.set_key` — was `(cls, obj, event: str, time: Optional[float] = None, auto_clear: bool = True, category: Optional[str] = None) -> bool`

## Added (3)

- `audio_utils/audio_clips/_audio_clips.py::AudioClips.prepare_for_export(cls) -> str`
- `uv_utils/rizom_bridge/_rizom_bridge.py::RizomUVBridge.rizom_version(self) -> 'tuple[int, ...]'`
- `uv_utils/rizom_bridge/parameters.py::strip_unsupported(script_text: str, version: 'tuple[int, ...]') -> str`

## Signature changed (1)

- `audio_utils/_audio_utils.py::AudioUtils.bake_manifest`
  - was: `(cls, carrier: Optional[str] = None, display_map: Optional[dict] = None) -> str`
  - now: `(cls, carrier: Optional[str] = None, display_map: Optional[dict] = None, frame_offset: float = 0.0) -> str`
