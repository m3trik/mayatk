# Maya Development Instructions

> **System Prompt Override**:
> You are an expert Maya Technical Artist and Python Developer.
> Your primary goal is **stability**, **performance**, and **native integration** with Maya 2025+.
>
> **Global Standards**: For general workflow, testing, and coding standards, refer to the [Main Copilot Instructions](../../.github/copilot-instructions.md).
>
> **Work Logs**: When completing a task, you MUST update the **Work Logs** at the bottom of this file.

---

## 1. Meta-Instructions

- **Living Document**: This file (`mayatk/.github/copilot-instructions.md`) is the SSoT for Maya specific workflows.
- **Future Proofing**: Maintain backward compatibility with Maya 2024 where possible, but prioritize 2025 features.
- **Style**: Use Type Hints essential for PyMEL/OpenMaya interoperability.
- **Imports**: 
  - `import maya.cmds as cmds`
  - `import pymel.core as pm` (Use sparingly in performance-critical loops)
  - `import maya.api.OpenMaya as om` (API 2.0 preferred over 1.0)

## 2. Architecture & Infrastructure

### Project Structure
- **Source**: `mayatk/` (Maya-specific), `pythontk/` (Core utils, separate repo/folder but linked).
- **Tests**: 
  - `mayatk/test/` (Production/Standardized).
  - `mayatk/test/temp_tests/` (Scratchpad - Gitignored usually).

### MayaConnection Defaults

> **CRITICAL — Protect User Work**:
> `MayaConnection.connect()` defaults to `launch=True, force_new_instance=True`.
> This means every call **launches a fresh Maya instance on an unused port** so an
> existing user session is **never** disturbed.
>
> When `force_new_instance=False` is used and an existing Maya session is detected,
> a **confirmation dialog** is shown by default (via `confirm_existing=True`).
> The user must click "Yes" before the connection proceeds.  Pass
> `confirm_existing=False` only in automated scripts that knowingly reuse a session.
>
> - **`run_tests.py` also defaults to `force_new_instance=True`** (launches a new Maya).
>   Only `--reuse` overrides this to attach to an existing session.
> - To reuse an already-running instance, pass `force_new_instance=False` (or `--reuse`
>   on the CLI). **This will DESTROY any unsaved work in that session.**
> - Unit tests in `test_maya_connection.py` pass `force_new_instance=False`
>   only because they use mocks — never in a real Maya context.
>
> **AI AGENT RULE — HARD BLOCK**:
> When running tests, **NEVER** pass `force_new_instance=False` or `--reuse`.
> Always let the runner launch its own Maya instance.
>
> **This rule has NO exceptions for convenience, speed, or retry attempts.**
> If a prior test run's results are slow to appear, **wait longer or re-run
> without `--reuse`** — do NOT switch to `--reuse` to save time.
>
> Connecting to an existing session risks destroying the user's scene and
> hours of unsaved work. **NEVER** kill Maya processes you did not launch.
>
> **FORBIDDEN COMMANDS** (grep yourself before executing):
> ```
> --reuse                        # NEVER
> force_new_instance=False        # NEVER (except in mock-only unit tests)
> ```
>
> `_launch_maya_gui()` delegates to `pythontk.AppLauncher` internally — do **not**
> bypass it with raw `subprocess` calls.

### Test Infrastructure

> **CRITICAL — Maya Runtime Required**:
> All mayatk tests depend on `pymel` / `maya.cmds` and **cannot** be run with
> `pytest`, the workspace `.venv`, or any standard Python interpreter.
> They **must** be executed through one of the methods below.
>
> **Syntax-only validation** (no Maya needed):
> ```powershell
> .\.venv\Scripts\python.exe -c "import ast, os; c=0; [((ast.parse(open(os.path.join(r,f),encoding='utf-8').read()), c:=c+1) if f.endswith('.py') else None) for r,_,fs in os.walk(r'mayatk\mayatk') for f in fs]; print(f'{c} files OK')"
> ```
> Use this to verify edits parse correctly when Maya is unavailable.

> **Testing Workflow**: Follow the **Issue-Driven TDD** workflow defined in the main [copilot-instructions.md](../../.github/copilot-instructions.md#testing-workflow-issue-driven-tdd).

- **Base Classes**: `test/base_test.py` (`MayaTkTestCase` for full cleanup, `QuickTestCase` for speed).
- **Runner**: `test/run_tests.py` (CLI entry point).
- **Connection**: `test/maya_connection.py` handles Port, Standalone, and Interactive modes.

### Running Tests — Decision Tree

> **Pick the right method for the scenario.** Do not default to one approach
> for every situation — read the test file first and follow this decision tree.

**Step 1: Identify what you're running.**

| Scenario | How to tell | Method |
|:---|:---|:---|
| **Standalone script** (temp/repro test) | File has `maya.standalone.initialize()` and a `__main__` block | **Direct mayapy** |
| **Standard test module** (production suite) | File uses `MayaTkTestCase`/`QuickTestCase` from `base_test.py`, no standalone init | **run_tests.py** |
| **GUI-dependent test** | Test requires Qt widgets, UI interaction, or viewport rendering | **Maya Script Editor** or **Command Port** |

**Step 2: Run with the matching method.**

#### A. Direct `mayapy` — for standalone scripts
Use when the test file manages its own `maya.standalone` session (typical for
`test/temp_tests/` reproduction scripts and one-off diagnostics).
```powershell
$env:PYTHONPATH = "o:\Cloud\Code\_scripts\mayatk;o:\Cloud\Code\_scripts\pythontk;o:\Cloud\Code\_scripts\uitk;o:\Cloud\Code\_scripts\tentacle"
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" <path_to_script.py> 2>&1
```

#### B. `run_tests.py` — for production test modules
Use when running test modules from `mayatk/test/test_*.py` that extend
`MayaTkTestCase` or `QuickTestCase`. The runner handles Maya connection,
scene cleanup, and result reporting.
```powershell
$env:PYTHONPATH = "o:\Cloud\Code\_scripts\mayatk;o:\Cloud\Code\_scripts\pythontk"

# Run all tests (launches a NEW Maya instance automatically)
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --all

# Run specific module(s) by name (without test_ prefix)
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py core_utils components

# List available test modules
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --list
```

#### C. Maya Script Editor / Command Port — for GUI-dependent tests
Use when the test requires a running Maya GUI (Qt widgets, viewport, etc.).
Run from Maya's Script Editor or via an open command port.
```python
# In Maya Script Editor:
import mayatk.test.run_tests as runner
runner.MayaTestRunner().run_tests(['core_utils'])
```

---

## 4. Work Logs & History (2025-2026)

### Maya Development (2025)
- [x] **Test Infrastructure** — Unified `run_tests.py` runner, standardized `test_*.py` files.
- [x] **Maya Connection** — Robust support for Standalone, Port, and Interactive modes.
- [x] **Game Shader** — Refactored to `GameShader`, extracted `MaterialUpdater`.
- [x] **Texture Map Factory** — Implemented in-memory pipeline (`PIL`), dynamic `MapRegistry`.
- [x] **Animation Tools** — Recursive scaling, overlap prevention strategies, absolute/relative modes.
- [x] **AutoInstancer** — Deep hierarchy support, robust PCA alignment, `InstancingStrategy` implementation.
- [x] **Scene Exporter** — Transitioned critical paths from PyMEL to `maya.cmds` for 5x speedup.

### Maya Development (2026)
- [x] **Audio Events Import Conversion** — Added automatic source-to-WAV conversion (MP3/OGG/M4A/FLAC via `ffmpeg`) for timeline-safe Maya audio playback, with cached outputs and UI/tooling updates.
- [x] **Audio Composite Refactor** — Moved composite WAV mixing from `mayatk` into reusable `pythontk.AudioUtils` and updated Audio Events to call shared utility logic.
- [x] **Audio Events DRY Cleanup** — Consolidated remove-flow to use `EventTriggers.remove` as the teardown SSoT, removed stale sync flags/guards in `audio_events_slots.py`, and simplified Channel Box connect/disconnect handling.
- [x] **Overlap None-Key Cleanup** — Updated Key Event auto-end behavior to remove stale intermediate `None` keys inside overlapping clip ranges before writing the latest end-None key; added lifecycle regression coverage.
- [x] **Audio Events SoC/DRY Pass 2** — Extracted shared sync/persist flow (`_sync_and_refresh_target`) and overlap-none pruning (`_prune_overlap_none_keys`) so `tb000` and `b005` reuse single internal primitives.
- [x] **Overlap None-Key Hardening** — Made overlap pruning enum-index aware (uses `EventTriggers.event_index(..., "None")` instead of hardcoded `0`) and boundary-inclusive to clean stale `None` keys at overlap boundaries.
- [x] **Overlap None-Key Hardening (Pass 2)** — Modified `_prune_overlap_none_keys` to remove all `None` keys from the new clip's start frame up to the *next non-None key*, rather than bounding it by the new clip's end frame. This fixes the bug where a shorter overlapping clip would leave behind the longer clip's `None` key.
- [x] **Audio Events UI Grouping** — Reorganized `audio_events.ui` into collapsible groups (`Tracks`, `Key`, `Sync`, `Manage`) to mirror the grouped layout style used in the polygons UI.
- [x] **Audio Events Designer Compatibility** — Switched `audio_events.ui` grouping containers to standard `QGroupBox` so section groups are visible in Qt Designer while preserving grouped layout and existing widget IDs.- [x] **Render Opacity VisDriver Name Fix** — Fixed fragile `endswith("_VisDriver")` check in `OpacityAttributeMode` that broke when Maya auto-incremented condition node names (e.g. `cube_VisDriver1`). Replaced with regex `_VisDriver\d*$` in both `_connect_visibility_driver` and `remove`. Added regression tests for name-collision and object-recreate scenarios.
- [x] **Adjust Key Spacing Tangent Preservation** — Rewrote `adjust_key_spacing` to MOVE keys via `pm.keyframe(edit=True, timeChange=...)` instead of recreating them, which natively preserves all tangent data. Added `set_tangent_info` helper that applies angles/weights in a separate call from types to prevent Maya from overriding stepped→fixed tangents. Updated `transfer_keyframes` to use `set_tangent_info`. Verified with 7 in-Maya tests (stepped, flat, mixed, negative spacing, preserve_keys).
- [x] **MayaConnection Safe Defaults** — Changed `MayaConnection.connect()` defaults to `launch=True, force_new_instance=True` so callers always get a fresh Maya instance by default, protecting existing user sessions. Updated `run_tests.py` and `test_maya_connection.py` to pass `force_new_instance=False` where needed. `_launch_maya_gui()` already delegates to `pythontk.AppLauncher` internally.
- [x] **run_tests.py Session Safety** — Fixed `run_tests.py` to default to `force_new_instance=True` (was `False`, which hijacked the user's open Maya). Added `--reuse` CLI flag as the only opt-in to existing sessions. Added warning banners in both `run_tests.py` and `MayaConnection.connect()` when reuse is active. Updated copilot-instructions with explicit AI agent rule to never pass `--reuse`.
- [x] **Audio Events Test Locator Fix** — Stabilized `test_audio_events.py` by removing invalid `pm.spaceLocator(...)[0]` usage (which indexed node names into single characters and caused `MayaNodeError` during select/listRelatives). Verified with a fresh Maya test run: `test_audio_events` now passes (`89 tests, 0 failures, 0 errors`).
- [x] **Render Opacity Visibility Export Fix** — Replaced the condition-node visibility driver (Maya-only, didn't survive FBX export) with direct keyframe mirroring. When behaviors target `visibility` and the object has an `opacity` attribute, both channels are now keyed simultaneously. Added `sync_visibility_from_opacity()` for manual keying workflows. Legacy condition nodes are auto-cleaned on create. Unity/game engines now receive real `Visibility` animation curves via FBX without baking. Verified with 28 Maya tests (19 core + 9 export), including dual-key FBX round-trip tests.