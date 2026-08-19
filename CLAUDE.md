# mayatk

**Role**: Maya 2025+ utils. Maya tech-artist + Python work. Prioritize stability, performance, native integration.

**Nav**: [← root](../CLAUDE.md) · [docs](docs/README.md) · **Deps**: [pythontk](../pythontk/CLAUDE.md) · [uitk](../uitk/CLAUDE.md) · **Used by**: [tentacle](../tentacle/CLAUDE.md) · **Mirrored by**: [blendertk](../blendertk/CLAUDE.md)

## Hard rule — session safety (protect user work)

`MayaConnection.connect()` defaults to `launch=True, force_new_instance=True`: every call launches a **fresh** Maya on an unused port; the user's session is never disturbed. `run_tests.py` defaults the same way.

**AI agent rule — HARD BLOCK**: never pass `--reuse` or `force_new_instance=False` when running tests (sole exception: mock-only unit tests that launch nothing). No exceptions for convenience, speed, or retries: if a run is slow, **wait**. Connecting to an existing session can destroy hours of unsaved work. Never kill Maya processes you did not launch. `_launch_maya_gui()` delegates to `pythontk.AppLauncher` — do not bypass with raw `subprocess`.

## API surface

[`API_INDEX.md`](API_INDEX.md) · [`API_REGISTRY.md`](API_REGISTRY.md) · [`API_CHANGES.md`](API_CHANGES.md) · shadows [`API_SHADOWS.md`](../m3trik/docs/API_SHADOWS.md) — registry rules in [root](../CLAUDE.md). Upstream: [pythontk](../pythontk/API_INDEX.md) · [uitk](../uitk/API_INDEX.md). `AudioUtils`/`CoreUtils` shadow pythontk by design (extend, don't duplicate).

## Imports

```python
try:                                  # module top, guarded: the surface must import
    import maya.cmds as cmds          # without Maya (registry, docs tooling, mock tests)
    import maya.mel as mel
    import maya.api.OpenMaya as om    # API 2.0 over 1.0; object refs and math
except Exception:
    cmds = mel = om = None
```

- The try-guard is the house policy (a heavy `om`-only module may import it inside the method instead). **No PyMEL** — `maya.cmds` / `maya.mel` only (`import pymel.core` at module top blocks Maya's UI for minutes during init).
- Use `cmds.*` directly; a few names don't exist — `om.MGlobal.displayInfo` (no `cmds.displayInfo`), `cmds.file(query=True, sceneName=True)` (no `cmds.sceneName`).
- Node helpers live on canonical classes — `CoreUtils` (`short_name`/`leaf_name`/`as_strings`, `BoundingBox`), `NodeUtils` (`get_parent`/`get_children`/`get_shapes`/`node_is`…), `Attributes` (`has_attr`/`set_plug`), `XformUtils` + `xform_utils/matrices.py` — call them via the class (`mtk.CoreUtils.short_name`; the wildcard-flat `mtk.short_name` is the same object, legacy). Check `API_INDEX.md` before adding one.
- Coerce inputs to strings at production entry points: `cmds.X(str(node), ...)` — Maya 2025 cmds reject some non-string node args.
- Use type hints (essential for OpenMaya interop).

## Tests

Tests need the Maya runtime — not plain `pytest` / the workspace `.venv`. Set once (from repo root):

```powershell
$MAYAPY = "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe"
$env:PYTHONPATH = "$PWD\mayatk;$PWD\pythontk;$PWD\uitk;$PWD\tentacle"
```

- **Pre-flight (no Maya)** — `ruff check . --select E9,F82` (syntax + undefined names, tests included) is green — keep it so. Bare `ruff check .` is ~276 legacy errors — fix only what you introduce. **Command-name check (mayapy)** — `& $MAYAPY mayatk/test/check_cmds_syntax.py` validates every `cmds.*` / `mel.eval` name against the live registry (`--report` writes a file; append paths to scope).
- **Base classes** `test/base_test.py` → `MayaTkTestCase` (scene reset in `setUp`) / `QuickTestCase` (no reset); interactive-only tests `@skipIfBatch`. **Runner** `test/run_tests.py` (in-session harness `test/_suite_driver.py`). **Connection** `env_utils/maya_connection.py` (Port / Standalone / Interactive).

| Test kind | How to tell | Run |
|:---|:---|:---|
| Standalone repro | has `maya.standalone.initialize()` + `__main__` | `& $MAYAPY <script.py>` |
| Production module | uses `MayaTkTestCase` / `QuickTestCase` | `python mayatk/test/run_tests.py --all` (or `… core_utils components`, `… --list`). Headless by default: chunked fresh-mayapy processes; modules that native-crash mayapy auto-defer to one GUI pass (`GUI_REQUIRED` in the runner) |
| GUI-dependent | needs Qt / viewport (→ `GUI_REQUIRED`) | auto GUI pass, or `--gui` to force the whole run through a launched GUI Maya. In-session (Script Editor): `import mayatk.test.run_tests as r; r.MayaTestRunner().run_tests(['core_utils'])` |

Runner notes: per-module timings (`SLOWEST MODULES`); the badge only updates when every in-scope module ran; `--jobs N` runs chunks concurrently — drop to 1 if a run stalls at init (FlexLM reclaim after a killed mayapy).

## Tool panels — co-locate in mayatk, never in tentacle

A tool that ships a Switchboard panel lives **here, next to its engine** (canonical: `light_utils/hdr_manager.py`, `edit_utils/mirror.py`); blendertk mirrors the split. Contract:

- `<tool>.ui` + `<Tool>Slots` in the SAME module dir as the domain it acts on; the `.ui` is the tracked SSoT, runtime-loaded (no compiled `_ui.py`). Name modules after the tool (`mirror.py`), never `_*_utils.py`.
- `<Tool>Slots` is tentacle-independent: base `ptk.LoggingMixin`, `__init__(self, switchboard, log_level=...)` sets `self.sb`; a thin driver over the logic class, calling the engine directly. Guard the uitk import in the *logic* module so the headless surface stays uitk-free.
- Discovery: `MayaUiHandler` (`ui_utils/maya_ui_handler.py`, `discover_slots=True, recursive=True`) finds the `.ui` and the slots class — no `DEFAULT_INCLUDE` entry. The loader tries `<Base>Slots` before `<Base>`.
- tentacle only exposes it: a `bNNN` slot in `tentacle/slots/maya/<panel>.py` calls `self.sb.handlers.marking_menu.show("<tool>")` (+ the button in that panel's `.ui`/`#submenu.ui`). Only the **navigation** menus live in tentacle.

## Style

- PySide6 via `qtpy`; OpenMaya API 2.0.
- Temp / debug tests: `test/temp_tests/` (gitignored).

See [CHANGELOG.md](CHANGELOG.md) for history.
