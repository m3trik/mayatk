# mayatk Test Suite

Tests need the real Maya runtime (`maya.cmds`) and can't run under plain `pytest` /
the workspace `.venv` — **except** `mock_tests/`, which mocks `maya.cmds` via its
own `conftest.py` and runs under plain `pytest`. See the root
[`AGENTS.md`](../../AGENTS.md) hard rule: never connect to a Maya session the
tooling did not launch (`force_new_instance=False` is forbidden outside mock-only
unit tests) — a run uses **fresh** Maya processes (chunked `mayapy` by default;
the GUI pass launches a **new** Maya), or with `--reuse` mayapys this tooling
launched and keeps between runs.

## Layout

| Path | What | How it runs |
|:---|:---|:---|
| `test/test_*.py` | Main suite | `run_tests.py`, auto-discovered by glob |
| `test/extended/` | Needs real scene assets on disk | `run_tests.py --extended` (opt-in; skips cleanly if assets are missing) |
| `test/mock_tests/` | `maya.cmds` mocked, no Maya needed | plain `pytest test/mock_tests/` |
| `test/*_live_e2e.py` | Gated live checks — need another DCC installed and a Maya license, and take minutes | run by hand; NOT `test_*`-named, so `run_tests.py` never picks them up. `scene_import_live_e2e.py` is the Blender→Maya pull (mirrored in blendertk); `scene_roundtrip_live_e2e.py` is the `.ma → .blend → .ma → .blend` **fixed-point** check and has ONE home, since a round trip is not a per-direction thing |
| `test/temp_tests/` | Gitignored scratch (repro/probe/verify scripts) | ad hoc; swept freely, never promoted without review |
| `test/test_assets/` | Fixture files (images, `.fbx`, …) | read-only inputs |

## Base classes (`base_test.py`)

- `MayaTkTestCase` — full scene reset (`cmds.file(new=True, force=True)`) in
  `setUp` only (`tearDown` deliberately doesn't wipe again; the suite driver
  also resets between modules). Default for anything that touches the scene.
- `QuickTestCase` — skips scene reset, for tests that don't need a clean scene.
- `skipUnlessExtended` — gates a test behind `MAYATK_EXTENDED_TESTS=1`
  (set automatically by `run_tests.py --extended`).
- `skipIfBatch` — skips under mayapy/batch; the test still runs in the GUI pass.
- `asset_path()` / `MAYATK_TEST_ASSETS` — machine-local scene assets a few
  extended tests replay; unset, guards resolve to a can't-exist sentinel and skip.
  The fixtures carry neutral names; a staged folder's own `legacy_aliases.json`
  (`TestAssets.ALIASES_FILE`, kept beside the private fixtures, never in source)
  still resolves its old names while it is renamed on disk.

## Running

```powershell
python run_tests.py                # default "core" modules (fast sanity check)
python run_tests.py core_utils components   # specific modules (test_ prefix optional)
python run_tests.py --all          # every test_*.py in the main suite
python run_tests.py --all --extended --mocks  # + extended/ + mock_tests/ too
python run_tests.py --list         # list discovered modules by category
python run_tests.py --gui          # force everything through one GUI Maya
python run_tests.py --dry-run      # validate module names/paths, run nothing

# mock_tests/ needs no Maya at all:
python -m pytest test/mock_tests/ -q
```

Default execution is **headless**: modules run under `mayapy`
(`maya.standalone`) in chunks (`--chunk-size`, `--jobs`), each chunk in a
fresh process — one long standalone session accumulates native state and
hard-crashes mid-run. `mayapy` is resolved via `--mayapy` > `MAYATK_MAYAPY` >
newest install > PATH; children get `PYTHONPATH` pinned to the ecosystem
roots so a workspace-venv PySide6 can never leak into mayapy. Modules that
can't survive batch (listed with reasons in `GUI_REQUIRED` in
`run_tests.py`) — plus anything that native-crashes or hangs headlessly,
auto-detected via progress markers (`--module-timeout`) — are deferred to a
single **GUI pass** at the end: a NEW Maya launched over the command port via
`MayaConnection` — for that path the runner's own interpreter needs `mayatk`
importable (repo roots on `PYTHONPATH`, not the bare workspace root)
(`--no-gui-pass` skips it, leaving those modules DEFERRED;
`--keep-maya` keeps it open; `--no-wait` is fire-and-forget; `--quick` sends
one validation test). Both paths run the same in-session harness,
`_suite_driver.py`.

Results append to `test/temp_tests/test_results_<port>_<pid>.txt` (scoped by
port **and** runner PID so concurrent invocations can't clobber each other;
stale result/chunk/gui artifacts are swept after 7 days). Module statuses:
PASS / FAIL / LOAD ERROR, plus NATIVE CRASH / TIMEOUT / DEFERRED (= NOT RUN).
The `docs/README.md` test badge updates only on a full `--all` run where
every module actually ran — scoped or partial runs never touch it
(`--no-badge` skips it entirely).

## Static checks (no Maya session)

- `test_static_analysis.py` — pyflakes guard across `mayatk/mayatk/` for
  undefined names; runs under any interpreter, skips if pyflakes isn't
  installed.
- `check_cmds_syntax.py` — validates every `cmds.*` / `mel.eval` command *and
  flag* name against the live Maya command registry (needs `mayapy`).
  `--report` writes a file.
- `check_cmds_naming.py` — naming-pitfall lint for `cmds` usage (discarded
  rename returns, stale names, mixed flag forms; needs `mayapy`).

## Writing tests

```python
import maya.cmds as cmds
from base_test import MayaTkTestCase

class TestSomething(MayaTkTestCase):
    def test_basic_behavior(self):
        cube = cmds.polyCube(name="test_cube")[0]
        self.assertNodeExists(cube)
```

No `pymel` — mayatk is fully migrated to `maya.cmds` (see root `CLAUDE.md`).
One test file per production module; new `test_*.py` files are picked up
automatically, no registration needed. Reproduction/debug scripts go in
`test/temp_tests/`, not the main suite. A test that writes project files opens
a project of its own under its temp dir
(`cmds.workspace(path, openWorkspace=True)`, restored through `addCleanup`, the
temp delete registered first so it runs last), never the active one.

## Probes

A probe that needs Maya launches its own (the hard rule above) and is run by
whoever is debugging, never handed to the user unless the repro needs a human
hand. Beyond the general verification rules ([CODE_STANDARD §9](../../m3trik/docs/CODE_STANDARD.md#9-tests)):

- A one-off `mayapy` script leaves through `ptk.ProcessExit.hard_exit`:
  `os._exit` still runs Maya's faulting DLL detach and files a crash dump and
  an `untitled[Recovered]` scene in the temp folder.
- A payload sent over the command port runs with separate globals and locals:
  wrap its whole body in one function (a module-level name is invisible to
  nested functions and generator expressions), send it with
  `wait_for_response=True` before anything that quits Maya, and have it write
  a UTF-8 file rather than print (Maya's console output can fault the driver's
  codec).
- A live panel probe that edits widgets writes the user's real panel state
  unless it sandboxes first: launch with `MAYA_SKIP_USERSETUP_PY=1` and call
  `uitk.testing.TestSandbox.activate()` before opening the panel.
- A visual or material claim needs pixels: grab the widget
  (`viewport().grab()`), or playblast the viewport with a marker object in
  frame so the image's orientation reads.
- Only a Maya whose PID your own run logged (`Maya launched (PID: N)`) is
  yours; a port in its title, port ownership or a start time near the run
  prove nothing. After an aborted run (a stopped background task can leave its
  `mayapy` runner alive), confirm nothing your run started is left before
  starting another, and report any other Maya rather than closing it.
