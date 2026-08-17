# mayatk Test Suite

Tests need the real Maya runtime (`maya.cmds`) and can't run under plain `pytest` /
the workspace `.venv` — **except** `mock_tests/`, which mocks `maya.cmds` via its
own `conftest.py` and runs under plain `pytest`. See the root
[`CLAUDE.md`](../../CLAUDE.md) hard rule: never connect to an existing Maya
session (`--reuse` / `force_new_instance=False` are forbidden outside mock-only
unit tests) — every run uses **fresh** Maya processes (chunked `mayapy` by
default; the GUI pass launches a **new** Maya).

## Layout

| Path | What | How it runs |
|:---|:---|:---|
| `test/test_*.py` | Main suite | `run_tests.py`, auto-discovered by glob |
| `test/extended/` | Needs real scene assets on disk | `run_tests.py --extended` (opt-in; skips cleanly if assets are missing) |
| `test/mock_tests/` | `maya.cmds` mocked, no Maya needed | plain `pytest test/mock_tests/` |
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
`test/temp_tests/`, not the main suite.
