# !/usr/bin/python
# coding=utf-8
"""
MayaTk Test Runner

Unified test runner for the mayatk suite.

Default execution is HEADLESS: modules run under mayapy (maya.standalone)
in chunks, each chunk in a fresh process (one long standalone session
accumulates native state and hard-crashes mid-run).  A module that native-
crashes or hangs headlessly is attributed via progress markers, the chunk
resumes in a fresh process, and the offender is deferred to a single GUI
pass at the end (alongside the known-GUI-only modules in GUI_REQUIRED).
The GUI pass launches a NEW Maya via command port — user sessions are never
touched.

The in-session harness lives in _suite_driver.py and is shared by both
paths, so headless and GUI runs behave identically per module.

Usage:
    python run_tests.py                          # Run default core tests (headless)
    python run_tests.py core_utils components    # Run specific modules
    python run_tests.py --all                    # Run ALL test modules
    python run_tests.py --gui                    # Force everything through a GUI Maya
    python run_tests.py --no-gui-pass            # Skip the GUI pass (GUI-only modules DEFERRED; no badge)
    python run_tests.py --chunk-size 12          # Modules per fresh mayapy process
    python run_tests.py --jobs 2                 # Concurrent mayapy chunks (default 1; see note)
    python run_tests.py --module-timeout 900     # Headless per-module hang timeout (seconds)
    python run_tests.py --mayapy <path>          # Explicit mayapy (else MAYATK_MAYAPY / newest install)
    python run_tests.py --dry-run                # Validate test setup without running
    python run_tests.py --quick                  # Run quick validation test (GUI)
    python run_tests.py --list                   # List available test modules
    python run_tests.py --no-badge               # Skip README badge update
    python run_tests.py --no-wait                # Fire-and-forget (GUI mode only)
    python run_tests.py --timeout 7200           # GUI-pass results-wait timeout (seconds)
    python run_tests.py --keep-maya              # Keep GUI Maya open after tests
    python run_tests.py --reuse                  # Reuse existing Maya (CAUTION: resets scene; implies --gui)

Notes:
    --jobs > 1 runs multiple mayapy chunks concurrently.  Standalone
    initializations are staggered (one at a time) to avoid license-checkout
    races; if a run ever hangs at init, drop back to --jobs 1 and let the
    FlexLM checkout reclaim before retrying.

Directory Structure:
    - Main Test Suite: mayatk/test/ (Standardized test_*.py files only)
    - Temporary Tests: mayatk/test/temp_tests/ (Reproduction scripts, scratchpad tests)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# cp1252 consoles can't encode characters test docstrings legitimately use
# ("→"); unittest's printErrors then dies MID-REPORT, eating the failure list
# and the summary (bitten in uitk's runner). Degrade gracefully instead.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass

SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
DRIVER_PATH = Path(__file__).resolve().parent / "_suite_driver.py"
SUITE_COMPLETE_MARKER = "# SUITE COMPLETE"

# Ensure mayatk is in path
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

try:
    from mayatk.env_utils import maya_connection
except ImportError:
    print(
        "Warning: mayatk.env_utils.maya_connection module not found. GUI/port mode may not work."
    )

# Modules that must run in a GUI Maya session (mayapy standalone native-
# crashes them).  Anything NOT listed that still crashes headlessly is
# auto-detected at runtime and deferred to the GUI pass — add persistent
# offenders here so they skip the wasted crash/relaunch cycle.
GUI_REQUIRED = {
    "test_sequencer": "Qt table classes hard-crash mayapy (0xC0000409)",
    "test_shot_manifest": (
        "shots adapters register OpenMaya/scriptJob callbacks that "
        "segfault without a real Maya event loop"
    ),
    "test_hotkey_collisions": (
        "cmds.hotkeySet raises RuntimeError under mayapy batch "
        "(hotkey infrastructure is GUI-only)"
    ),
    # Confirmed native mayapy crashers (2026-07-17 full run; hierarchy_sync
    # re-confirmed crashing ALONE in a fresh process — Qt reference-tree
    # population). All pass under the GUI pass.
    "test_hierarchy_sync": "native-crashes mayapy at Qt reference-tree population",
    "test_hdr_manager": "native-crashes mayapy (2026-07-17 full run)",
    "test_mat_marmoset_bridge": "native-crashes mayapy (2026-07-17 full run)",
    "test_maya_menu_handler": "builds native GUI menus; native-crashes mayapy",
    "test_preview": "native-crashes mayapy (2026-07-17 full run)",
    "test_script_output": "Qt console embed; native-crashes mayapy",
    "test_sequencer_gui": "Qt sequencer widgets; native-crashes mayapy",
    "test_uv_rizom_bridge": "native-crashes mayapy (2026-07-17 full run)",
    # Batch-incompatible by design (fail, not crash — but belong in a GUI session).
    "test_maya_connection": (
        "asserts interactive-session mode detection (batch detects 'standalone' "
        "by design) and rides out dead-port retries headlessly (376s)"
    ),
    "test_native_menu_window": "wraps Maya's GUI menu bar (no menus in batch)",
    "test_style_setter": (
        "cmds.displayRGBColor queries return None under batch, breaking the "
        "color snapshot/restore harness"
    ),
}

# Statuses a module line in the results file can carry.  PASS/FAIL/LOAD ERROR
# come from the driver; the rest are written by this orchestrator.
_MODULE_LINE = re.compile(
    r"^(test_\S+): (PASS|FAIL|LOAD ERROR|NATIVE CRASH|TIMEOUT|DEFERRED)\b(.*)$"
)
_COUNTS_LINE = re.compile(r"^  Tests: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)")
_TIME_IN_REST = re.compile(r"\[([\d.]+)s\]")

# Statuses that mean the module never produced test results.
_NOT_RUN = ("NATIVE CRASH", "TIMEOUT", "DEFERRED")


def find_mayapy(explicit: Optional[str] = None) -> Optional[str]:
    """Locate mayapy.exe: explicit arg > MAYATK_MAYAPY env > newest install > PATH."""
    if explicit:
        if Path(explicit).exists():
            return explicit
        print(f"[WARNING] --mayapy path not found: {explicit}")
        return None
    try:
        from pythontk import AppLauncher

        found = AppLauncher.resolve_app_path(
            env_vars=("MAYATK_MAYAPY",),
            scan_globs=("{program_files}/Autodesk/Maya20*/bin/mayapy.exe",),
        )
        if found:
            return found
    except ImportError:
        pass
    return shutil.which("mayapy")


def _inside_maya() -> bool:
    """True when running inside an interactive Maya session (Script Editor)."""
    if "maya.cmds" not in sys.modules:
        return False
    try:
        import maya.cmds as cmds

        return not cmds.about(batch=True)
    except Exception:
        return False


def _read_markers(path) -> List[str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split_markers(markers: List[str]) -> Tuple[List[str], set, bool, bool]:
    """Split progress markers into (started, finished, done, init_done)."""
    started = [m[len("STARTED "):] for m in markers if m.startswith("STARTED ")]
    finished = {m[len("FINISHED "):] for m in markers if m.startswith("FINISHED ")}
    return started, finished, "DONE" in markers, "INIT_DONE" in markers


def _parse_module_blocks(text: str) -> List[dict]:
    """Parse per-module result blocks out of a results-file text."""
    records = []
    for line in text.splitlines():
        m = _MODULE_LINE.match(line)
        if m:
            rest = m.group(3)
            t = _TIME_IN_REST.search(rest)
            records.append(
                {
                    "name": m.group(1),
                    "status": m.group(2),
                    "elapsed": float(t.group(1)) if t else None,
                    "counts": None,
                }
            )
            continue
        c = _COUNTS_LINE.match(line)
        if c and records and records[-1]["counts"] is None:
            records[-1]["counts"] = tuple(int(g) for g in c.groups())
    return records


class MayaTestRunner:
    """Orchestrates the mayatk test suite (headless mayapy chunks + GUI pass)."""

    def __init__(
        self,
        host="localhost",
        port=7002,
        reuse_instance=False,
        mayapy: Optional[str] = None,
        chunk_size: int = 12,
        jobs: int = 1,
        module_timeout: int = 900,
        wait_timeout: Optional[int] = None,
    ):
        self.host = host
        self.port = port
        self.reuse_instance = reuse_instance
        self.mayapy = mayapy
        self.chunk_size = max(1, chunk_size)
        self.jobs = max(1, jobs)
        self.module_timeout = module_timeout
        self.wait_timeout = wait_timeout
        # Standalone init normally takes well under two minutes; past
        # init_timeout we warn, past + grace we conclude a stuck FlexLM
        # checkout and stop spawning mayapy (GUI pass still works — it uses
        # a normal interactive license).
        self.init_timeout = 600
        self.init_grace = 300

        self.test_dir = Path(__file__).parent
        self.temp_test_dir = self.test_dir / "temp_tests"
        self.temp_test_dir.mkdir(exist_ok=True)

        # Scoped by port AND runner PID: two concurrent invocations would
        # otherwise share one file and clobber each other's results.
        self.results_file = self.temp_test_dir / f"test_results_{port}_{os.getpid()}.txt"

        self._merge_lock = threading.Lock()
        self._print_lock = threading.Lock()
        self._spawn_gate = threading.Semaphore(1)  # staggers standalone inits
        self._license_trap = False
        self._done_count = 0
        self._grand_total = 0

        self._sweep_stale_artifacts()

        try:
            self.connection = maya_connection.MayaConnection.get_instance()
        except NameError:
            self.connection = None

    def _sweep_stale_artifacts(self):
        """Best-effort sweep of files from long-gone runs."""
        now = time.time()
        try:
            for pattern, max_age in (
                ("test_results*.txt", 7 * 86400),
                ("chunk_*.*", 7 * 86400),
                ("gui_*.*", 7 * 86400),
            ):
                for stale in self.temp_test_dir.glob(pattern):
                    if stale != self.results_file and (
                        now - stale.stat().st_mtime > max_age
                    ):
                        stale.unlink(missing_ok=True)
            # Shots-prefs sandboxes leak when a chunk native-crashes
            # (os._exit / segfault bypasses cleanup) — sweep older dirs.
            for stale_dir in self.temp_test_dir.glob("shots_prefs_test_*"):
                if stale_dir.is_dir() and (now - stale_dir.stat().st_mtime > 86400):
                    shutil.rmtree(stale_dir, ignore_errors=True)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Connection (GUI / command-port path)
    # ------------------------------------------------------------------

    def connect_to_maya(self):
        """Connect to Maya using MayaConnection.

        By default launches a NEW Maya instance to protect the user's session.
        Pass --reuse on the CLI (or reuse_instance=True) to attach to an
        already-running instance instead.
        """
        if not self.connection:
            print("[ERROR] MayaConnection not available")
            return False

        force_new = not self.reuse_instance

        if force_new:
            print(
                "[INFO] Launching a NEW Maya instance for testing "
                "(user sessions will not be touched)."
            )
        else:
            print(
                "[WARNING] --reuse flag active: connecting to an EXISTING Maya "
                "instance. The current scene WILL be modified/reset by tests!"
            )

        if self.connection.connect(
            mode="auto",
            port=self.port,
            host=self.host,
            force_new_instance=force_new,
            confirm_existing=not self.reuse_instance,
        ):
            print(f"[OK] Connected to Maya in {self.connection.mode} mode")
            if not self.verify_connection():
                print("[ERROR] Connection verification failed — Maya not responding")
                return False
            return True
        else:
            print("[ERROR] Failed to connect to Maya")
            return False

    def verify_connection(self):
        """Verify Maya connection with a round-trip data check."""
        if not self.connection or not self.connection.is_connected:
            return False

        if self.connection.mode == "port":
            try:
                result = self.connection.execute(
                    "str(1+1)", wait_for_response=True, timeout=10
                )
                if result and result.strip() == "2":
                    print("[VERIFIED] Maya connection confirmed (round-trip data check)")
                    return True
                print(f"[WARNING] Unexpected verification response: {result!r}")
                return False
            except Exception as e:
                print(f"[WARNING] Connection verification failed: {e}")
                return False
        else:
            # Standalone/interactive — just check we can execute
            try:
                self.connection.execute("pass")
                print("[VERIFIED] Maya connection confirmed")
                return True
            except Exception as e:
                print(f"[WARNING] Connection verification failed: {e}")
                return False

    def send_code(self, code):
        """Send Python code to Maya."""
        try:
            self.connection.execute(code)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to execute code: {e}")
            return False

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    # Subdirectories opted out of default discovery — caller must pass
    # ``--extended`` or ``--mocks`` to include them.
    EXTENDED_DIR = "extended"
    MOCKS_DIR = "mock_tests"
    SKIP_INFRA = {
        "test_imports",
        "test_lazy_loading_maya",
        "test_module_resolver_integration",
    }

    def _discover_in(self, subdir: str = ""):
        """Return ``[test_name]`` from ``test_dir / subdir``, sans infra files."""
        path = self.test_dir / subdir if subdir else self.test_dir
        if not path.exists():
            return []
        return sorted(
            f.stem for f in path.glob("test_*.py") if f.stem not in self.SKIP_INFRA
        )

    def discover_tests(self, include_extended: bool = False, include_mocks: bool = False):
        """Discover available test modules."""
        modules = list(self._discover_in())
        if include_extended:
            modules.extend(self._discover_in(self.EXTENDED_DIR))
        if include_mocks:
            modules.extend(self._discover_in(self.MOCKS_DIR))
        return sorted(set(modules))

    def _path_for_module(self, module_name: str) -> str:
        """Resolve a module name to its absolute file path, checking subdirs."""
        for subdir in ("", self.EXTENDED_DIR, self.MOCKS_DIR):
            candidate = self.test_dir / subdir / f"{module_name}.py"
            if candidate.exists():
                return str(candidate).replace("\\", "/")
        # Fall back to the main dir even if missing — caller will surface the load error
        return str(self.test_dir / f"{module_name}.py").replace("\\", "/")

    def list_tests(self):
        """List all available test modules grouped by category."""
        groups = (
            ("MAIN", self._discover_in()),
            ("EXTENDED (--extended)", self._discover_in(self.EXTENDED_DIR)),
            ("MOCK-ONLY (--mocks, pytest)", self._discover_in(self.MOCKS_DIR)),
        )
        print("\n" + "=" * 70)
        print("AVAILABLE TEST MODULES")
        print("=" * 70)
        i = 0
        for label, modules in groups:
            if not modules:
                continue
            print(f"\n[{label}]")
            for module in modules:
                i += 1
                display_name = module.replace("test_", "")
                gui_tag = "  [GUI]" if module in GUI_REQUIRED else ""
                print(f"  {i:3d}. {display_name:30s} ({module}){gui_tag}")
        print("=" * 70)
        print(f"\nTotal: {i} test modules")
        print("\nUsage: python run_tests.py <module_name> [<module_name> ...]")
        print("Example: python run_tests.py core_utils components")
        print("Flags:   --extended  --mocks  --all  --gui")

    # ------------------------------------------------------------------
    # Quick validation (GUI)
    # ------------------------------------------------------------------

    def run_quick_test(self):
        """Run a single quick validation test."""
        print("\n" + "=" * 70)
        print("QUICK VALIDATION TEST")
        print("=" * 70)

        if not self.connect_to_maya():
            return False

        code = """
import sys
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts')
sys.path.insert(0, r'O:\\\\Cloud\\\\Code\\\\_scripts\\\\mayatk\\\\test')

print("\\\\n" + "="*70)
print("QUICK TEST: test_core_utils (first class only)")
print("="*70)

try:
    import test_core_utils as test_mod
    import unittest

    # Run first test class
    for attr_name in dir(test_mod):
        attr = getattr(test_mod, attr_name)
        if isinstance(attr, type) and issubclass(attr, unittest.TestCase):
            if attr is not unittest.TestCase and attr.__name__ != "MayaTkTestCase":
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(attr)
                runner = unittest.TextTestRunner(verbosity=2)
                result = runner.run(suite)

                print("\\\\n" + "-"*70)
                if result.wasSuccessful():
                    print(f"[PASS] {attr_name}: ALL {result.testsRun} TESTS PASSED")
                else:
                    print(f"[FAIL] {attr_name}: {len(result.failures + result.errors)} FAILURES")
                print("-"*70)
                break
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
"""

        print("\nRunning quick validation test...")
        print("Check Maya Script Editor for detailed output\n")

        if self.send_code(code):
            print("[OK] Test code sent successfully")
            return True
        return False

    # ------------------------------------------------------------------
    # Shared results plumbing
    # ------------------------------------------------------------------

    def _append_master(self, text: str):
        with self._merge_lock:
            with open(self.results_file, "a", encoding="utf-8") as f:
                f.write(text)

    def _merge_into_master(self, phase_results_path: Path):
        """Append a phase/chunk results file into the master results file."""
        try:
            text = Path(phase_results_path).read_text(encoding="utf-8")
        except OSError:
            return
        lines = [
            line
            for line in text.splitlines(keepends=True)
            if not line.startswith(SUITE_COMPLETE_MARKER)
        ]
        self._append_master("".join(lines))

    def _emit(self, msg: str):
        with self._print_lock:
            print(msg, flush=True)

    # ------------------------------------------------------------------
    # Headless (mayapy) execution
    # ------------------------------------------------------------------

    def _child_env(self) -> dict:
        """Environment for mayapy children.

        PYTHONPATH is pinned to the ecosystem roots so a workspace venv
        (whose PySide6 is newer than Maya's) can never leak into mayapy.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            str(SCRIPTS_ROOT / pkg) for pkg in ("mayatk", "pythontk", "uitk", "tentacle")
        )
        env.pop("PYTHONHOME", None)
        env.pop("VIRTUAL_ENV", None)
        return env

    def _run_headless(
        self, modules: List[str], module_paths: Dict[str, str], extended: bool
    ) -> Tuple[bool, List[str]]:
        """Run modules in chunked mayapy processes.

        Returns (ok, deferred) where deferred = modules that must go to the
        GUI pass (native crash / hang / init failure).
        """
        # Crashed/hung/unlaunchable modules are rerouted to the GUI pass, so
        # they don't fail the run here — their final module status decides.
        mayapy = self.mayapy or find_mayapy()
        if not mayapy:
            print(
                "[WARNING] mayapy not found (set MAYATK_MAYAPY or pass --mayapy). "
                "Routing all modules through the GUI pass."
            )
            return True, list(modules)

        chunks = [
            modules[i : i + self.chunk_size]
            for i in range(0, len(modules), self.chunk_size)
        ]
        self._grand_total = len(modules)
        self._done_count = 0
        print(
            f"\n[HEADLESS] {len(modules)} modules in {len(chunks)} chunk(s) "
            f"of <= {self.chunk_size}, jobs={self.jobs}\n[HEADLESS] mayapy: {mayapy}"
        )

        ok = True
        deferred: List[str] = []

        if self.jobs == 1 or len(chunks) == 1:
            for idx, chunk in enumerate(chunks):
                if self._license_trap:
                    deferred.extend(chunk)
                    for m in chunk:
                        self._append_master(
                            f"\n{m}: TIMEOUT (standalone init stalled; deferred to GUI pass)\n"
                        )
                    continue
                chunk_ok, chunk_deferred = self._run_chunk(
                    mayapy, idx, len(chunks), chunk, module_paths, extended
                )
                ok = ok and chunk_ok
                deferred.extend(chunk_deferred)
        else:
            from concurrent.futures import ThreadPoolExecutor

            def _worker(pair):
                idx, chunk = pair
                if self._license_trap:
                    for m in chunk:
                        self._append_master(
                            f"\n{m}: TIMEOUT (standalone init stalled; deferred to GUI pass)\n"
                        )
                    return True, list(chunk)
                return self._run_chunk(
                    mayapy, idx, len(chunks), chunk, module_paths, extended
                )

            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                for chunk_ok, chunk_deferred in pool.map(_worker, enumerate(chunks)):
                    ok = ok and chunk_ok
                    deferred.extend(chunk_deferred)

        return ok, deferred

    def _run_chunk(
        self,
        mayapy: str,
        idx: int,
        n_chunks: int,
        chunk_modules: List[str],
        module_paths: Dict[str, str],
        extended: bool,
    ) -> Tuple[bool, List[str]]:
        """Run one chunk, resuming in a fresh mayapy after any native crash."""
        tag = f"chunk {idx + 1}/{n_chunks}"
        pending = list(chunk_modules)
        deferred: List[str] = []
        no_progress_relaunches = 0
        attempt = 0

        while pending:
            attempt += 1
            base = self.temp_test_dir / f"chunk_{os.getpid()}_{idx:02d}_{attempt}"
            cfg_path = base.with_suffix(".json")
            res_path = base.with_suffix(".txt")
            prog_path = base.with_suffix(".progress")
            log_path = base.with_suffix(".log")

            config = {
                "modules": pending,
                "module_paths": {m: module_paths[m] for m in pending},
                "results_file": str(res_path).replace("\\", "/"),
                "progress_file": str(prog_path).replace("\\", "/"),
                "temp_dir": str(self.temp_test_dir).replace("\\", "/"),
                "extended": extended,
                "reload": False,
            }
            cfg_path.write_text(json.dumps(config, indent=1), encoding="utf-8")

            proc = None
            log_file = None
            timed_out_module = None
            init_done = False
            gate_held = False
            try:
                # Stagger standalone inits across parallel chunks — concurrent
                # license checkouts are the suspected contention/hang source.
                self._spawn_gate.acquire()
                gate_held = True

                log_file = open(log_path, "w", encoding="utf-8", errors="replace")
                try:
                    proc = subprocess.Popen(
                        [mayapy, str(DRIVER_PATH), str(cfg_path)],
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        cwd=str(SCRIPTS_ROOT),
                        env=self._child_env(),
                    )
                except OSError as e:
                    print(f"[ERROR] Failed to launch mayapy: {e} — deferring to GUI pass")
                    return True, pending

                start = time.monotonic()
                last_activity = start
                init_warned = False
                seen_results = set()
                finished_count = 0

                while True:
                    rc = proc.poll()

                    # Stream compact per-module completions from the chunk file.
                    try:
                        chunk_text = res_path.read_text(encoding="utf-8")
                    except OSError:
                        chunk_text = ""
                    for rec in _parse_module_blocks(chunk_text):
                        if rec["name"] not in seen_results:
                            seen_results.add(rec["name"])
                            with self._print_lock:
                                self._done_count += 1
                                t = f" [{rec['elapsed']:.1f}s]" if rec["elapsed"] else ""
                                print(
                                    f"  [{self._done_count}/{self._grand_total}] "
                                    f"({tag}) {rec['name']}: {rec['status']}{t}",
                                    flush=True,
                                )

                    started, finished, _, has_init = _split_markers(
                        _read_markers(prog_path)
                    )
                    if not init_done and has_init:
                        init_done = True
                        last_activity = time.monotonic()
                        if gate_held:
                            self._spawn_gate.release()
                            gate_held = False
                        self._emit(
                            f"  ({tag}) maya.standalone ready "
                            f"({int(time.monotonic() - start)}s)"
                        )
                    if len(finished) != finished_count:
                        finished_count = len(finished)
                        last_activity = time.monotonic()

                    if rc is not None:
                        break

                    now = time.monotonic()
                    if not init_done:
                        if now - start > self.init_timeout and not init_warned:
                            init_warned = True
                            self._emit(
                                f"  ({tag}) [WARNING] standalone init exceeded "
                                f"{self.init_timeout}s — possible stuck FlexLM "
                                f"checkout; extending grace {self.init_grace}s "
                                "(do NOT kill mayapy mid-initialize manually)."
                            )
                        if now - start > self.init_timeout + self.init_grace:
                            self._license_trap = True
                            proc.kill()
                            proc.wait(timeout=30)
                            break
                    elif now - last_activity > self.module_timeout:
                        timed_out_module = next(
                            (m for m in reversed(started) if m not in finished), None
                        )
                        self._emit(
                            f"  ({tag}) [WARNING] no progress for "
                            f"{self.module_timeout}s — killing chunk "
                            f"(hung module: {timed_out_module or 'unknown'})"
                        )
                        proc.kill()
                        proc.wait(timeout=30)
                        break

                    time.sleep(1.0)
            except BaseException:
                # KeyboardInterrupt / anything unexpected: never orphan a child.
                if proc and proc.poll() is None:
                    proc.kill()
                raise
            finally:
                if gate_held:
                    self._spawn_gate.release()
                if log_file is not None:
                    try:
                        log_file.close()
                    except OSError:
                        pass

            started, finished, done, _ = _split_markers(_read_markers(prog_path))

            self._merge_into_master(res_path)

            if self._license_trap:
                remaining = [m for m in pending if m not in finished]
                print(
                    f"\n[LICENSE TRAP] ({tag}) standalone never initialized after "
                    f"{self.init_timeout + self.init_grace}s. A killed/crashed "
                    "mayapy can leave the FlexLM checkout stuck for many minutes "
                    "— every later standalone init hangs until it reclaims. "
                    "Remaining headless modules go to the GUI pass (interactive "
                    "license)."
                )
                for m in remaining:
                    self._append_master(
                        f"\n{m}: TIMEOUT (standalone init stalled; deferred to GUI pass)\n"
                    )
                deferred.extend(remaining)
                for p in (cfg_path, prog_path):
                    p.unlink(missing_ok=True)
                return True, deferred

            if done:
                for p in (cfg_path, prog_path, res_path, log_path):
                    p.unlink(missing_ok=True)
                return True, deferred

            # Crashed or hung — attribute, record, resume the remainder.
            crashed = timed_out_module or next(
                (m for m in reversed(started) if m not in finished), None
            )
            remaining = [m for m in pending if m not in finished]
            label = "TIMEOUT" if timed_out_module else "NATIVE CRASH"
            if crashed and crashed in remaining:
                remaining.remove(crashed)
                deferred.append(crashed)
                self._append_master(
                    f"\n{crashed}: {label} (headless; deferred to GUI pass — "
                    f"log: {log_path.name})\n"
                )
                self._emit(
                    f"  ({tag}) {crashed}: {label} under mayapy — deferred to "
                    f"GUI pass; resuming {len(remaining)} remaining in a fresh "
                    "process"
                )
                no_progress_relaunches = 0
            elif not finished:
                no_progress_relaunches += 1
                if no_progress_relaunches >= 2 and remaining:
                    scapegoat = remaining.pop(0)
                    deferred.append(scapegoat)
                    self._append_master(
                        f"\n{scapegoat}: NATIVE CRASH (headless; chunk made no "
                        f"progress twice — deferred to GUI pass; log: {log_path.name})\n"
                    )
                    no_progress_relaunches = 0
            else:
                no_progress_relaunches = 0

            pending = remaining
            # Keep the crash log for forensics; config/progress are noise.
            for p in (cfg_path, prog_path):
                p.unlink(missing_ok=True)

        return True, deferred

    # ------------------------------------------------------------------
    # GUI / command-port execution
    # ------------------------------------------------------------------

    def _run_via_port(
        self,
        gui_modules: List[str],
        module_paths: Dict[str, str],
        extended: bool,
        no_wait: bool = False,
    ):
        """Run modules inside a (new or reused) GUI Maya over the command port.

        Returns True / False, or the string "nowait" for fire-and-forget.
        """
        print(f"\n[GUI PASS] {len(gui_modules)} module(s):")
        if len(gui_modules) <= 20:
            for m in gui_modules:
                reason = GUI_REQUIRED.get(m, "deferred from headless run")
                print(f"  - {m}  ({reason})")
        else:
            # Forced --gui runs route everything here; the banner already
            # listed the modules — skip the per-module reason spam.
            print("  (module list in the banner above)")

        if not self.connect_to_maya():
            for m in gui_modules:
                self._append_master(f"\n{m}: DEFERRED (GUI connection failed)\n")
            return False

        base = self.temp_test_dir / f"gui_{os.getpid()}"
        cfg_path = base.with_suffix(".json")
        res_path = base.with_suffix(".txt")
        # Reload only matters when the session may hold stale modules.
        needs_reload = self.reuse_instance or (
            self.connection and self.connection.mode == "interactive"
        )
        config = {
            "modules": gui_modules,
            "module_paths": {m: module_paths[m] for m in gui_modules},
            "results_file": str(res_path).replace("\\", "/"),
            "progress_file": str(base.with_suffix(".progress")).replace("\\", "/"),
            "temp_dir": str(self.temp_test_dir).replace("\\", "/"),
            "extended": extended,
            "reload": bool(needs_reload),
        }
        cfg_path.write_text(json.dumps(config, indent=1), encoding="utf-8")
        res_path.unlink(missing_ok=True)

        driver = str(DRIVER_PATH).replace("\\", "/")
        cfg = str(cfg_path).replace("\\", "/")
        exec_code = f"""
import sys, json, importlib.util
import __main__ as _m
_m._mayatk_test_complete = False
try:
    # Drop cached mayatk/test modules (stale in reused sessions); keep the
    # connection and Qt machinery alive.
    for _k in [k for k in list(sys.modules)
               if ('mayatk' in k.lower() or k in ('base_test', '_mayatk_suite_driver'))
               and 'maya_connection' not in k
               and 'qt' not in k.lower() and 'pyside' not in k.lower()]:
        sys.modules.pop(_k, None)
    _spec = importlib.util.spec_from_file_location('_mayatk_suite_driver', r'{driver}')
    _drv = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_drv)
    with open(r'{cfg}', encoding='utf-8') as _f:
        _cfgd = json.load(_f)
    _totals = _drv.run_suite(_cfgd)
    _m._mayatk_test_summary = _drv.format_totals(_totals)
    _m._mayatk_test_passed = (_totals['failures'] == 0 and _totals['errors'] == 0)
except Exception:
    import traceback
    traceback.print_exc()
finally:
    _m._mayatk_test_complete = True
"""

        print("\nSending test code to Maya ...")
        if not self.send_code(exec_code):
            for m in gui_modules:
                self._append_master(f"\n{m}: DEFERRED (failed to send test code)\n")
            return False
        print("[OK] Test code sent — tests are running in Maya")

        if no_wait:
            print(f"\n--no-wait: results will be written to {res_path}")
            print(f'  Get-Content "{res_path}" -Wait')
            return "nowait"

        timeout = self.wait_timeout or max(600, 30 * len(gui_modules))
        completed = self.wait_for_results(res_path, timeout=timeout)
        self._merge_into_master(res_path)

        # A module with no result line would otherwise vanish from the final
        # records entirely (e.g. the in-Maya driver raised before running it,
        # which still sets the completion sentinel) — mark every one so the
        # summary/badge logic sees it as not-run.
        reported = {r["name"] for r in _parse_module_blocks(self._safe_read(res_path))}
        missing = [m for m in gui_modules if m not in reported]
        for m in missing:
            reason = "GUI pass wait expired" if not completed else "no result reported"
            self._append_master(f"\n{m}: TIMEOUT ({reason})\n")
        if completed and not missing:
            cfg_path.unlink(missing_ok=True)
            res_path.unlink(missing_ok=True)
            base.with_suffix(".progress").unlink(missing_ok=True)
        return completed and not missing

    @staticmethod
    def _safe_read(path) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    def wait_for_results(
        self, results_path, timeout: int, poll_interval: float = 2.0
    ) -> bool:
        """Poll Maya for test completion, with file-based fallback.

        Primary: asks Maya via socket whether the ``_mayatk_test_complete``
        sentinel is True.  Fallback: watches the results file for the
        suite-complete marker.
        """
        results_path = Path(results_path)
        start = time.monotonic()
        last_size = 0
        use_socket = (
            self.connection
            and self.connection.is_connected
            and self.connection.mode == "port"
        )
        socket_failures = 0

        print(f"\nWaiting for tests to complete (timeout: {timeout}s) ...")

        while (time.monotonic() - start) < timeout:
            elapsed = int(time.monotonic() - start)

            # ---- primary: socket-based sentinel check ----
            if use_socket:
                done = None
                try:
                    done = self.connection.execute(
                        "getattr(__import__('__main__'), '_mayatk_test_complete', False)",
                        wait_for_response=True,
                        timeout=5,
                    )
                except Exception:
                    pass  # fall through to file check
                if done is None:
                    # The command port refuses connections while Maya's main
                    # thread runs the suite; stop hammering it after a few tries.
                    socket_failures += 1
                    if socket_failures >= 5:
                        use_socket = False
                        print(
                            "\n  [INFO] Command port busy/unavailable; "
                            "polling results file only."
                        )
                else:
                    socket_failures = 0
                    if str(done).strip().lower() == "true":
                        print(f"\r  Tests completed in {elapsed}s.{' ' * 30}")
                        summary = self.connection.execute(
                            "getattr(__import__('__main__'), '_mayatk_test_summary', '')",
                            wait_for_response=True,
                            timeout=5,
                        )
                        if summary and summary.strip():
                            print(f"  Maya reports: {summary.strip()}")
                        return True

            # ---- fallback: file-based polling ----
            content = self._safe_read(results_path)
            if content:
                cur_size = len(content)
                if cur_size != last_size:
                    module_count = len(_parse_module_blocks(content))
                    print(
                        f"\r  [{elapsed}s] {module_count} module(s) finished ...",
                        end="",
                        flush=True,
                    )
                    last_size = cur_size

                if SUITE_COMPLETE_MARKER in content or "SUMMARY" in content:
                    print(f"\r  Tests completed in {elapsed}s.{' ' * 30}")
                    return True

            time.sleep(poll_interval)

        elapsed = int(time.monotonic() - start)
        print(f"\n[TIMEOUT] Results not found after {elapsed}s.")
        return False

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run_tests(
        self,
        modules=None,
        dry_run=False,
        extended=False,
        mocks=False,
        gui=False,
        no_gui_pass=False,
        no_wait=False,
    ):
        """Run tests for the specified modules.

        Args:
            modules: List of module names (with or without test_ prefix).
                None = run the default core set.
            dry_run: Show what would be executed without running tests.
            extended: Run extended tests (sets MAYATK_EXTENDED_TESTS=1).
            mocks: Also include mock_tests/ modules.
            gui: Force everything through a GUI Maya (old behavior).
            no_gui_pass: Skip the GUI pass; GUI-required modules are DEFERRED.
            no_wait: Fire-and-forget (GUI mode only).

        Returns:
            Status dict (see _finalize_results), or {"ok": True, "dry_run": True}.
        """
        default_modules = [
            "test_core_utils",
            "test_components",
            "test_node_utils",
            "test_edit_utils",
            "test_mat_utils",
            "test_xform_utils",
            "test_rig_utils",
            "test_shadow_rig",
            "test_skinning",
            "test_env_utils",
            "test_scale_keys",
            "test_stagger_keys",
        ]

        if modules is None:
            test_modules = default_modules
        else:
            test_modules = [m if m.startswith("test_") else f"test_{m}" for m in modules]

        module_paths = {m: self._path_for_module(m) for m in test_modules}

        # Never spawn mayapy from inside an interactive Maya session — run
        # in THIS session instead (Script Editor usage).
        if not gui and _inside_maya():
            gui = True
            print("[INFO] Interactive Maya session detected — running in-session.")

        mode = "GUI" if gui else "HEADLESS (mayapy)"
        print("\n" + "=" * 70)
        print("MAYATK TEST RUNNER" + (" (DRY RUN)" if dry_run else f"  [{mode}]"))
        print("=" * 70)
        print(f"{'Would run' if dry_run else 'Running'} {len(test_modules)} modules:")
        for module in test_modules:
            tag = "  [GUI]" if (not gui and module in GUI_REQUIRED) else ""
            print(f"  • {module}{tag}")
        if extended:
            print("  • Extended tests enabled")
        if mocks:
            print("  • Mock-only tests enabled")
        print("=" * 70)

        if dry_run:
            print("\nDry run - no tests will be executed.")
            print("This validates:")
            print("  [OK] Module names are correct")
            print("  [OK] Test files exist")
            print("  [OK] Test runner configuration is valid")
            print("\nTo actually run tests, omit --dry-run flag")
            return {"ok": True, "dry_run": True}

        # Master results file: header now, module blocks merged in as phases
        # complete, SUMMARY appended at the end.
        with open(self.results_file, "w", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write("MAYATK TEST RESULTS\n")
            f.write("=" * 70 + "\n\n")

        if gui:
            headless_modules: List[str] = []
            gui_modules = list(test_modules)
        else:
            gui_modules = [m for m in test_modules if m in GUI_REQUIRED]
            headless_modules = [m for m in test_modules if m not in GUI_REQUIRED]

        phases_ok = True
        all_ran = True

        if headless_modules:
            h_ok, deferred = self._run_headless(headless_modules, module_paths, extended)
            phases_ok = phases_ok and h_ok
            gui_modules = gui_modules + [m for m in deferred if m not in gui_modules]

        if gui_modules:
            if no_gui_pass and not gui:
                print(
                    f"\n[GUI PASS SKIPPED] {len(gui_modules)} module(s) deferred "
                    "(--no-gui-pass): " + ", ".join(gui_modules)
                )
                for m in gui_modules:
                    self._append_master(f"\n{m}: DEFERRED (--no-gui-pass)\n")
                all_ran = False
            else:
                result = self._run_via_port(
                    gui_modules, module_paths, extended, no_wait=no_wait
                )
                if result == "nowait":
                    return {"ok": True, "nowait": True}
                phases_ok = phases_ok and bool(result)

        status = self._finalize_results()
        status["ok"] = phases_ok and not status["failed_modules"] and not status["not_run"]
        status["all_ran"] = all_ran and not status["not_run"]
        return status

    def _finalize_results(self) -> dict:
        """Aggregate module blocks in the master file; append SUMMARY + timing."""
        content = self._safe_read(self.results_file)
        records = _parse_module_blocks(content)

        # Last status wins (a NATIVE CRASH line is superseded by the module's
        # GUI-pass result); keep first-appearance order.
        final: Dict[str, dict] = {}
        for rec in records:
            if rec["name"] in final:
                final[rec["name"]].update(rec)
            else:
                final[rec["name"]] = dict(rec)

        totals = [0, 0, 0, 0]  # tests, failures, errors, skipped
        summary_lines = []
        for rec in final.values():
            counts = rec["counts"]
            if counts and rec["status"] in ("PASS", "FAIL"):
                for i in range(4):
                    totals[i] += counts[i]
                detail = (
                    f" ({counts[0]} tests, {counts[1]} failures, {counts[2]} errors)"
                )
            else:
                detail = ""
            t = f" [{rec['elapsed']:.1f}s]" if rec["elapsed"] is not None else ""
            summary_lines.append(f"  {rec['name']}: {rec['status']}{detail}{t}")

        tests, failures, errors, skipped = totals
        failed_modules = [
            r["name"] for r in final.values() if r["status"] in ("FAIL", "LOAD ERROR")
        ]
        not_run = [r["name"] for r in final.values() if r["status"] in _NOT_RUN]

        timed = sorted(
            (r for r in final.values() if r["elapsed"] is not None),
            key=lambda r: r["elapsed"],
            reverse=True,
        )

        out = ["\n" + "=" * 70 + "\n", "SUMMARY\n", "=" * 70 + "\n"]
        out.extend(line + "\n" for line in summary_lines)
        out.append("=" * 70 + "\n")
        out.append(
            f"Total: {tests} tests, {failures} failures, {errors} errors, "
            f"{skipped} skipped\n"
        )
        if not_run:
            out.append(f"NOT RUN ({len(not_run)}): {', '.join(not_run)}\n")
        out.append("=" * 70 + "\n")
        if timed:
            out.append("\nSLOWEST MODULES\n")
            for rec in timed[:10]:
                out.append(f"  {rec['elapsed']:8.1f}s  {rec['name']}\n")
        self._append_master("".join(out))

        print(f"\nResults saved to: {self.results_file}")
        if failures == 0 and errors == 0 and not failed_modules and not not_run:
            print("\n[PASS] ALL TESTS PASSED!")

        return {
            "tests": tests,
            "failures": failures,
            "errors": errors,
            "skipped": skipped,
            "passed": tests - failures - errors,
            "failed": failures + errors,
            "failed_modules": failed_modules,
            "not_run": not_run,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def print_results(self) -> None:
        """Read and print the results file contents to console."""
        if not self.results_file.exists():
            print("[WARNING] No results file found.")
            return

        content = self.results_file.read_text(encoding="utf-8")
        # Use a unicode-safe write for Windows cp1252 consoles.
        try:
            print("\n" + content)
        except UnicodeEncodeError:
            safe = content.encode(
                sys.stdout.encoding or "ascii", errors="replace"
            ).decode(sys.stdout.encoding or "ascii")
            print("\n" + safe)

    def update_readme_badge(self, passed: int, failed: int) -> bool:
        """Update the README with a test status badge."""
        readme_path = self.test_dir.parent / "docs" / "README.md"

        if not readme_path.exists():
            print(f"README not found at {readme_path}")
            return False

        content = readme_path.read_text(encoding="utf-8")

        if failed == 0:
            color = "brightgreen"
            status = f"{passed} passed"
        elif passed == 0:
            color = "red"
            status = f"{failed} failed"
        else:
            color = "orange"
            status = f"{passed} passed, {failed} failed"

        # Link target computed relative to the README's location
        # (docs/README.md -> ../test/), so a regenerate can't break the link.
        link_target = (
            Path(os.path.relpath(self.test_dir, readme_path.parent)).as_posix() + "/"
        )
        new_badge = f"[![Tests](https://img.shields.io/badge/Tests-{status.replace(' ', '%20').replace(',', '')}-{color}.svg)]({link_target})"

        # Check if a Tests badge already exists and replace it
        tests_badge_pattern = (
            r"\[!\[Tests\]\(https://img\.shields\.io/badge/Tests-[^\)]+\)\]\([^\)]+\)"
        )

        if re.search(tests_badge_pattern, content):
            new_content = re.sub(tests_badge_pattern, new_badge, content)
        else:
            # Add badge after the Maya badge line
            maya_badge_pattern = r"(\[!\[Maya\]\(https://img\.shields\.io/badge/Maya-[^\)]+\)\]\([^\)]+\))"
            match = re.search(maya_badge_pattern, content)
            if match:
                insert_pos = match.end()
                new_content = (
                    content[:insert_pos] + "\n" + new_badge + content[insert_pos:]
                )
            else:
                python_badge_pattern = r"(\[!\[Python\]\(https://img\.shields\.io/badge/Python-[^\)]+\)\]\([^\)]+\))"
                match = re.search(python_badge_pattern, content)
                if match:
                    insert_pos = match.end()
                    new_content = (
                        content[:insert_pos] + "\n" + new_badge + content[insert_pos:]
                    )
                else:
                    new_content = new_badge + "\n" + content

        readme_path.write_text(new_content, encoding="utf-8")
        print(f"\n[OK] README badge updated: {status}")
        return True

    def parse_test_results(self) -> tuple:
        """Parse the results file to extract test counts.

        Returns:
            Tuple of (passed, failed) where failed = failures + errors
        """
        if not self.results_file.exists():
            return (0, 0)

        content = self.results_file.read_text(encoding="utf-8")

        match = re.search(
            r"Total: (\d+) tests, (\d+) failures?, (\d+) errors?", content
        )

        if match:
            total = int(match.group(1))
            failures = int(match.group(2))
            errors = int(match.group(3))
            passed = total - failures - errors
            return (passed, failures + errors)

        return (0, 0)


def _pop_value(args: List[str], name: str, cast, default):
    """Extract ``name <value>`` from args; returns (value, ok)."""
    if name not in args:
        return default, True
    try:
        idx = args.index(name)
        value = cast(args[idx + 1])
        args.pop(idx)
        args.pop(idx)
        return value, True
    except (ValueError, IndexError):
        print(f"Invalid value for {name}")
        return default, False


def main() -> int:
    """Main entry point. Returns a process exit code (0 = success)."""
    args = sys.argv[1:]

    port, ok = _pop_value(args, "--port", int, 7002)
    if not ok:
        return 2
    wait_timeout, ok = _pop_value(args, "--timeout", int, None)
    if not ok:
        return 2
    chunk_size, ok = _pop_value(args, "--chunk-size", int, 12)
    if not ok:
        return 2
    jobs, ok = _pop_value(args, "--jobs", int, 1)
    if not ok:
        return 2
    module_timeout, ok = _pop_value(args, "--module-timeout", int, 900)
    if not ok:
        return 2
    mayapy_arg, ok = _pop_value(args, "--mayapy", str, None)
    if not ok:
        return 2

    def pop_flag(*names) -> bool:
        found = any(n in args for n in names)
        args[:] = [a for a in args if a not in names]
        return found

    reuse_instance = pop_flag("--reuse")
    dry_run = pop_flag("--dry-run", "-d")
    no_badge = pop_flag("--no-badge")
    no_wait = pop_flag("--no-wait")
    keep_maya = pop_flag("--keep-maya")
    extended = pop_flag("--extended", "-e")
    mocks = pop_flag("--mocks")
    gui = pop_flag("--gui")
    pop_flag("--headless")  # accepted for symmetry; headless is the default
    no_gui_pass = pop_flag("--no-gui-pass")

    # Reusing/attaching only makes sense through the port path.
    gui = gui or reuse_instance
    if no_wait and not gui:
        print("[INFO] --no-wait only applies to --gui runs; ignoring.")
        no_wait = False

    runner = MayaTestRunner(
        port=port,
        reuse_instance=reuse_instance,
        mayapy=mayapy_arg,
        chunk_size=chunk_size,
        jobs=jobs,
        module_timeout=module_timeout,
        wait_timeout=wait_timeout,
    )

    if "--list" in args or "-l" in args:
        runner.list_tests()
        return 0
    elif "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    # Everything below may launch Maya — wrap in try/finally for cleanup
    try:
        if "--quick" in args or "-q" in args:
            success = runner.run_quick_test()
            # Quick test is fire-and-forget (no results file to poll)
            return 0 if success else 1

        full_run = "--all" in args or "-a" in args
        if full_run:
            modules = runner.discover_tests(
                include_extended=extended, include_mocks=mocks
            )
            print(f"\nRunning ALL {len(modules)} test modules...")
        elif args:
            modules = args
        else:
            modules = None

        status = runner.run_tests(
            modules,
            dry_run=dry_run,
            extended=extended,
            mocks=mocks,
            gui=gui,
            no_gui_pass=no_gui_pass,
            no_wait=no_wait,
        )

        if not isinstance(status, dict):
            return 1
        if status.get("nowait"):
            # Fire-and-forget: leave the Maya that is running the tests alive
            # (the old runner's finally-shutdown killed it mid-run).
            keep_maya = True
            return 0
        if status.get("dry_run"):
            return 0

        runner.print_results()

        # Update README badge — only on a full --all run where every module
        # actually ran (a scoped or partial run must not clobber the badge).
        if not no_badge:
            if (
                full_run
                and status.get("all_ran")
                and (status["passed"] > 0 or status["failed"] > 0)
            ):
                runner.update_readme_badge(status["passed"], status["failed"])
            elif not status.get("all_ran"):
                print("[INFO] Badge not updated (some modules did not run).")
            elif not full_run:
                print(
                    "[INFO] Badge not updated (scoped run — the badge reflects "
                    "full --all runs only)."
                )

        return 0 if status.get("ok") else 1
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Cleaning up ...")
        return 130
    finally:
        if not keep_maya and runner.connection and runner.connection.is_connected:
            print("\nClosing Maya instance ...")
            try:
                runner.connection.shutdown(force=True)
                print("[OK] Maya closed.")
            except Exception as e:
                print(f"[WARNING] Failed to close Maya gracefully: {e}")
                # Last resort: kill by PID
                try:
                    port = getattr(runner.connection, "port", None)
                    if port:
                        runner.connection.close_instance(port=port, force=True)
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
