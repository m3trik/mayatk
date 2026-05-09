# mayatk

**Role**: Maya 2025+ utils. Maya tech-artist + Python work. Prioritize stability, performance, native integration.

**Nav**: [← root](../CLAUDE.md) · **Deps**: [pythontk](../pythontk/CLAUDE.md) · **Used by**: [tentacle](../tentacle/CLAUDE.md)

## Hard rule — session safety (protect user work)

`MayaConnection.connect()` defaults to `launch=True, force_new_instance=True`. Every call launches a **fresh** Maya on an unused port; the user's session is never disturbed. `run_tests.py` defaults the same way; only `--reuse` overrides.

**AI agent rule — HARD BLOCK**: never pass any of these when running tests:

```
--reuse                      # NEVER
force_new_instance=False     # NEVER (except in mock-only unit tests)
```

No exceptions for convenience, speed, or retries. If a run is slow, **wait** — don't switch to reuse. Connecting to an existing session can destroy hours of unsaved work. Never kill Maya processes you did not launch.

`_launch_maya_gui()` delegates to `pythontk.AppLauncher` — do not bypass with raw `subprocess`.

## API surface

Before writing a new helper, **check the registry first** — duplicates undermine the SSoT goal.

- This package: [`API_REGISTRY.md`](API_REGISTRY.md) · [`API_CHANGES.md`](API_CHANGES.md) (diff vs last refresh)
- Upstream: [`pythontk` API](../pythontk/API_REGISTRY.md) · [`uitk` API](../uitk/API_REGISTRY.md)
- Cross-package shadows: [`m3trik/docs/API_SHADOWS.md`](../m3trik/docs/API_SHADOWS.md) — `AudioUtils` / `CoreUtils` shadow pythontk by design (mayatk extends; do not duplicate logic).

Refresh manually: `python m3trik/scripts/generate_api_registry.py mayatk` — otherwise auto-refreshed bi-weekly.

## Imports

```python
import maya.cmds as cmds          # primary command API
import maya.api.OpenMaya as om    # API 2.0 over 1.0; use for object refs and math
```

- Use `cmds.*` directly. A few cmds names don't exist — use `om.MGlobal.displayInfo` (no `cmds.displayInfo`) and `cmds.file(query=True, sceneName=True)` (no `cmds.sceneName`).
- Common node-handling helpers live on canonical classes/modules:
  - Names / coercion: `short_name`, `leaf_name`, `as_strings` — module-level in `mayatk/core_utils/_core_utils.py`. `BoundingBox` + `get_bounding_box` also live there.
  - Hierarchy / type checks: `NodeUtils.get_parent`, `get_children`, `get_shapes`, `get_shape`, `is_intermediate`, `list_transforms`, `node_is`.
  - Attributes: `Attributes.has_attr`, `Attributes.set_plug`.
  - Matrices: `get_matrix` / `set_matrix` in `xform_utils/matrices.py`; `get_translation` / `get_object_matrix` / `set_object_matrix` in `xform_utils/_xform_utils.py`.
- Coerce inputs to strings at production entry points: `cmds.X(str(node), ...)` — Maya 2025 cmds reject some non-string node args.
- Use type hints (essential for OpenMaya interop).

## Test infrastructure

Tests require Maya runtime (`maya.cmds`) — cannot run under plain `pytest` or workspace `.venv`.

**Syntax-only check** (no Maya needed):
```powershell
.\.venv\Scripts\python.exe -c "import ast, os; c=0; [((ast.parse(open(os.path.join(r,f),encoding='utf-8').read()), c:=c+1) if f.endswith('.py') else None) for r,_,fs in os.walk(r'mayatk\mayatk') for f in fs]; print(f'{c} files OK')"
```

**maya.cmds / mel.eval command name check** (requires mayapy — validates names against the live Maya registry):
```powershell
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" mayatk\test\check_cmds_syntax.py            # all ecosystem packages (default)
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" mayatk\test\check_cmds_syntax.py --report   # write report file
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" mayatk\test\check_cmds_syntax.py mayatk/mayatk tentacle/tentacle  # scope to subset
```

**Test base classes**: `test/base_test.py` → `MayaTkTestCase` (full cleanup) or `QuickTestCase` (fast).
**Runner**: `test/run_tests.py`. **Connection**: `test/maya_connection.py` (Port / Standalone / Interactive).

## Running tests — decision tree

| Scenario | How to tell | Method |
|:---|:---|:---|
| Standalone script (temp / repro) | File has `maya.standalone.initialize()` + `__main__` | **Direct mayapy** |
| Production test module | Uses `MayaTkTestCase` / `QuickTestCase`, no standalone init | **run_tests.py** |
| GUI-dependent test | Needs Qt widgets / viewport | **Maya Script Editor** / command port |

### A. Direct mayapy
```powershell
$env:PYTHONPATH = "o:\Cloud\Code\_scripts\mayatk;o:\Cloud\Code\_scripts\pythontk;o:\Cloud\Code\_scripts\uitk;o:\Cloud\Code\_scripts\tentacle"
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" <script.py> 2>&1
```

### B. run_tests.py
```powershell
$env:PYTHONPATH = "o:\Cloud\Code\_scripts\mayatk;o:\Cloud\Code\_scripts\pythontk"
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --all
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py core_utils components
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --list
```

### C. Script Editor / command port
```python
import mayatk.test.run_tests as runner
runner.MayaTestRunner().run_tests(['core_utils'])
```

## Style

- Backward compat with Maya 2024 where feasible; prioritize 2025.
- Temp / debug tests: `mayatk/test/temp_tests/` (gitignored).

See [CHANGELOG.md](CHANGELOG.md) for history.
