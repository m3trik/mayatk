# mayatk Test Suite

Tests need the real Maya runtime (`maya.cmds`) and can't run under plain `pytest` /
the workspace `.venv` — **except** `mock_tests/`, which mocks `maya.cmds` via its
own `conftest.py` and runs under plain `pytest`. See the root
[`CLAUDE.md`](../../CLAUDE.md) hard rule: never connect to an existing Maya
session (`--reuse` / `force_new_instance=False` are forbidden outside mock-only
unit tests) — every run launches a **fresh** Maya instance.

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
  `setUp`/`tearDown`. Default for anything that touches the scene.
- `QuickTestCase` — skips scene reset, for tests that don't need a clean scene.
- `skipUnlessExtended` — gates a test behind `MAYATK_EXTENDED_TESTS=1`
  (set automatically by `run_tests.py --extended`).

## Running

```powershell
$MAYAPY = "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe"
$env:PYTHONPATH = "$PWD\..\..\mayatk;$PWD\..\..\pythontk;$PWD\..\..\uitk;$PWD\..\..\tentacle"

& $MAYAPY run_tests.py                # default "core" modules (fast sanity check)
& $MAYAPY run_tests.py core_utils components   # specific modules (test_ prefix optional)
& $MAYAPY run_tests.py --all          # every test_*.py in the main suite
& $MAYAPY run_tests.py --all --extended --mocks  # + extended/ + mock_tests/ too
& $MAYAPY run_tests.py --list         # list discovered modules by category
& $MAYAPY run_tests.py --quick        # single quick validation test
& $MAYAPY run_tests.py --dry-run      # validate module names/paths, run nothing

# mock_tests/ needs no Maya at all:
python -m pytest test/mock_tests/ -q
```

`run_tests.py` launches Maya via `MayaConnection`
(`mayatk/env_utils/maya_connection.py`), waits for results (`--no-wait` for
fire-and-forget), writes `test/temp_tests/test_results_<port>_<pid>.txt`
(scoped by port **and** runner PID so concurrent invocations — even on the
same default port — can't clobber each other's results; stale files are
swept after 7 days), and updates the `docs/README.md` test badge
(`--no-badge` to skip). `--keep-maya` leaves the launched instance open
afterward.

## Static checks (no Maya)

- `test_static_analysis.py` — pyflakes guard across `mayatk/mayatk/` for
  undefined names; runs under any interpreter, skips if pyflakes isn't
  installed.
- `check_cmds_syntax.py` — validates every `cmds.*` / `mel.eval` name against
  the live Maya command registry (needs `mayapy`). `--report` writes a file.
- `check_cmds_naming.py` — naming-convention lint for `cmds` usage.

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
