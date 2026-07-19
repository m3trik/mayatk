# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-07-19._

## Signature changed (1)

- `audio_utils/_audio_utils.py::AudioUtils.pair_on_off_events`
  - was: `(pairs) -> List[tuple]`
  - now: `(pairs) -> List[Tuple[float, Optional[float]]]`
