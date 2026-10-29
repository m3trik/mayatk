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
$env:PYTHONPATH = "$PWD\mayatk;$PWD\pythontk;$PWD\uitk"
```

- **Pre-flight (no Maya)** — `ruff check . --select E9,F82` (syntax + undefined names, tests included) is green — keep it so. Bare `ruff check .` is ~276 legacy errors — fix only what you introduce. **Command-name check (mayapy)** — `& $MAYAPY mayatk/test/check_cmds_syntax.py` validates every `cmds.*` / `mel.eval` name against the live registry (`--report` writes a file; append paths to scope).
- **Base classes** `test/base_test.py` → `MayaTkTestCase` (scene reset in `setUp`) / `QuickTestCase` (no reset); interactive-only tests `@skipIfBatch`. **Runner** `test/run_tests.py` (in-session harness `test/_suite_driver.py`). **Connection** `env_utils/maya_connection.py` (Port / Standalone / Interactive).

| Test kind | How to tell | Run |
|:---|:---|:---|
| Standalone repro | has `maya.standalone.initialize()` + `__main__` | `& $MAYAPY <script.py>` |
| Production module | uses `MayaTkTestCase` / `QuickTestCase` | Pointed: `python mayatk/test/run_tests.py edit_utils -k mirror` (`--list`); `--all` = review / release. Headless: chunked fresh-mayapy processes; modules that native-crash mayapy defer to one GUI pass (`GUI_REQUIRED`) |
| GUI-dependent | needs Qt / viewport (→ `GUI_REQUIRED`) | auto GUI pass, or `--gui` to force the whole run through a launched GUI Maya. In-session (Script Editor): `import mayatk.test.run_tests as r; r.MayaTestRunner().run_tests(['core_utils'])` |

Runner notes: the console shows non-passing modules + SUMMARY + `SLOWEST MODULES` (`--full-results`: all); the badge only updates on a full `--all`; `--jobs N` runs chunks concurrently — drop to 1 if a run stalls at init (FlexLM reclaim after a killed mayapy).

## Tool panels — tentacle's

Every tool's panel is tentacle's (`ui/tools/<tool>.ui` + one shared Slots class in `slots/tools/<tool>.py`; [CODE_STANDARD](../m3trik/docs/CODE_STANDARD.md) §3, §14). mayatk holds each tool's API half (its operations and record rows) and no UI: no `.ui`, no `*Slots` class. A scene call the panel makes is a method on the tool's class or its host facade (`SequencerHost`, `ManifestHost`, `BridgeHost`), named as blendertk names it. `MayaUiHandler` registers no UI source of its own; it wraps Maya's native menus and names `sb.tk`.

**Host bindings**: a mayatk class keeping its own name for a shared pythontk class (`SceneDataSidecar`, `WorkspaceMap`, `CurtainMesh`, `ProbePlacement`) configures it (its host adapter, class attributes) and adds Maya-only methods; it never overrides a shared method (CODE_STANDARD §0; `check_dcc_twins.py` `HOST_BINDINGS`).

## Style

- PySide6 via `qtpy`; OpenMaya API 2.0.
- Temp / debug tests: `test/temp_tests/` (gitignored).

See [CHANGELOG.md](CHANGELOG.md) for history.
