# mayatk

**Role**: Maya 2025+ utils. Maya tech-artist + Python work. Prioritize stability, performance, native integration.

**Nav**: [← root](../CLAUDE.md) · [docs](docs/README.md) · **Deps**: [pythontk](../pythontk/CLAUDE.md) · **Used by**: [tentacle](../tentacle/CLAUDE.md)

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

**Before adding a helper, check the registry** (navigation rules: [root](../CLAUDE.md)):

- [`API_INDEX.md`](API_INDEX.md) (compact — read first) · [`API_REGISTRY.md`](API_REGISTRY.md) (grep, don't Read whole) · [`API_CHANGES.md`](API_CHANGES.md)
- Upstream: [pythontk](../pythontk/API_INDEX.md) · [uitk](../uitk/API_INDEX.md)
- Cross-package shadows: [`m3trik/docs/API_SHADOWS.md`](../m3trik/docs/API_SHADOWS.md) — `AudioUtils`/`CoreUtils` shadow pythontk by design (mayatk extends; don't duplicate logic).

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

## Tests

Tests need the Maya runtime (`maya.cmds`) — they can't run under plain `pytest` / the workspace `.venv`. Set once (from repo root):

```powershell
$MAYAPY = "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe"
$env:PYTHONPATH = "$PWD\mayatk;$PWD\pythontk;$PWD\uitk;$PWD\tentacle"
```

- **Pre-flight (no Maya)** — AST syntax sweep: `python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('mayatk/mayatk/**/*.py',recursive=True)]"`
- **Command-name check (mayapy)** — `& $MAYAPY mayatk\test\check_cmds_syntax.py` validates every `cmds.*` / `mel.eval` name against the live registry. `--report` writes a file; append `mayatk/mayatk tentacle/tentacle` to scope to a subset.
- **Base classes** `test/base_test.py` → `MayaTkTestCase` (full cleanup) / `QuickTestCase` (fast). **Runner** `test/run_tests.py`. **Connection** `mayatk/env_utils/maya_connection.py` (Port / Standalone / Interactive).

| Test kind | How to tell | Run |
|:---|:---|:---|
| Standalone repro | has `maya.standalone.initialize()` + `__main__` | `& $MAYAPY <script.py>` |
| Production module | uses `MayaTkTestCase` / `QuickTestCase` | `& $MAYAPY mayatk\test\run_tests.py --all` (or `… core_utils components`, `… --list`) |
| GUI-dependent | needs Qt / viewport | Maya Script Editor → `import mayatk.test.run_tests as r; r.MayaTestRunner().run_tests(['core_utils'])` |

## Style

- Backward compat with Maya 2024 where feasible; prioritize 2025.
- Temp / debug tests: `mayatk/test/temp_tests/` (gitignored).

See [CHANGELOG.md](CHANGELOG.md) for history.
