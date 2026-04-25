# mayatk — Changelog

## 2026

- **Render Opacity Pre-Export Safety Net** — added `RenderOpacity.prepare_for_export()` (per-object or scene-wide) that mirrors animated `opacity` onto `visibility` for every object missing the dual-key. Closes the silent-failure path where hand-authored opacity animation produced an empty Unity controller. Idempotent; preserves manually-keyed visibility (skips when vis_keys ≥ opa_keys). Updated stale `test_no_opacity_attr_falls_back_to_visibility_only` to reflect the production auto-promote-to-opacity design.
- **Audio Events Import Conversion** — automatic source-to-WAV conversion (MP3/OGG/M4A/FLAC via `ffmpeg`) for timeline-safe Maya audio playback; cached outputs + UI/tooling updates.
- **Audio Composite Refactor** — moved composite WAV mixing from `mayatk` into reusable `pythontk.AudioUtils`; Audio Events calls the shared utility.
- **Audio Events DRY Cleanup** — `EventTriggers.remove` is the teardown SSoT; removed stale sync flags/guards in `audio_events_slots.py`; simplified Channel Box connect/disconnect.
- **Overlap None-Key Cleanup** — Key Event auto-end now removes stale intermediate `None` keys inside overlapping clip ranges before writing the latest end-None key; added lifecycle regression coverage.
- **Audio Events SoC/DRY Pass 2** — extracted `_sync_and_refresh_target` and `_prune_overlap_none_keys` so `tb000` and `b005` share primitives.
- **Overlap None-Key Hardening** — pruning is enum-index aware (`EventTriggers.event_index(..., "None")` instead of hardcoded `0`) and boundary-inclusive.
- **Overlap None-Key Hardening (Pass 2)** — `_prune_overlap_none_keys` removes all `None` keys from the new clip's start frame up to the *next non-None key* (not bounded by the new clip's end). Fixes shorter-overlap leaving the longer clip's `None` key behind.
- **Audio Events UI Grouping** — reorganized `audio_events.ui` into collapsible groups (Tracks / Key / Sync / Manage) mirroring polygons UI style.
- **Audio Events Designer Compatibility** — switched grouping containers to standard `QGroupBox` so sections render in Qt Designer while preserving layout + widget IDs.
- **Render Opacity VisDriver Name Fix** — replaced fragile `endswith("_VisDriver")` with regex `_VisDriver\d*$` in `OpacityAttributeMode._connect_visibility_driver` and `remove`. Handles Maya auto-incrementing (`cube_VisDriver1`). Added regression tests.
- **Adjust Key Spacing Tangent Preservation** — rewrote `adjust_key_spacing` to MOVE keys via `pm.keyframe(edit=True, timeChange=...)` instead of recreating them (natively preserves tangent data). Added `set_tangent_info` (angles/weights separated from types to prevent stepped→fixed override). Verified with 7 in-Maya tests.
- **MayaConnection Safe Defaults** — `connect()` defaults `launch=True, force_new_instance=True` so every call gets a fresh Maya; protects existing user sessions. `run_tests.py` and `test_maya_connection.py` pass `force_new_instance=False` where needed.
- **run_tests.py Session Safety** — fixed default to `force_new_instance=True` (was `False`, hijacked open Maya). Added `--reuse` CLI flag as the only opt-in; warning banners in both `run_tests.py` and `MayaConnection.connect()` when reuse is active. Copilot-instructions updated with AI agent rule to never pass `--reuse`.
- **Audio Events Test Locator Fix** — removed invalid `pm.spaceLocator(...)[0]` indexing in `test_audio_events.py`. Fresh Maya run: `test_audio_events` passes (89 tests, 0 failures).
- **Render Opacity Visibility Export Fix** — replaced condition-node visibility driver (Maya-only, didn't survive FBX) with direct keyframe mirroring. When behaviors target `visibility` and the object has `opacity`, both channels are keyed together. Added `sync_visibility_from_opacity()`. Legacy condition nodes auto-clean on create. Verified with 28 Maya tests (19 core + 9 export).
- **MayaConnection Forced Shutdown** — added `force` flag to `shutdown` / `close_instance` to cleanly exit Maya without blocking "Save Changes" prompts.
- **Default Camera Exclusion** — `CamUtils.DEFAULT_CAMERAS` frozenset is the SSoT for Maya default camera names (persp, top, front, side, back, bottom, left, right, alignToPoly). `group_cameras`, scene exporter `_initialize_objects`, and `hierarchy_manager` all consume it.

## 2025

- **Test Infrastructure** — unified `run_tests.py` runner, standardized `test_*.py` files.
- **Maya Connection** — robust support for Standalone, Port, and Interactive modes.
- **Game Shader** — refactored to `GameShader`, extracted `MaterialUpdater`.
- **Texture Map Factory** — in-memory pipeline (`PIL`), dynamic `MapRegistry`.
- **Animation Tools** — recursive scaling, overlap prevention strategies, absolute/relative modes.
- **AutoInstancer** — deep hierarchy support, robust PCA alignment, `InstancingStrategy` implementation.
- **Scene Exporter** — transitioned critical paths from PyMEL to `maya.cmds` (~5× speedup).
