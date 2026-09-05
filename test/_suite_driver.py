# !/usr/bin/python
# coding=utf-8
"""MayaTk in-session suite driver.

Executes a list of test modules inside a live Maya session and appends one
result block per module to ``config["results_file"]``.  Owned by
``run_tests.py`` — both of its execution paths load this exact file:

- Headless (default): ``mayapy _suite_driver.py <config.json>`` per chunk;
  ``__main__`` initializes ``maya.standalone`` first.
- GUI / command port: a small payload sent over the port imports this module
  inside the target Maya and calls :func:`run_suite`.

The driver only APPENDS module blocks; the orchestrator owns the results
header and the final SUMMARY.  Progress markers (``INIT_DONE`` / ``STARTED``
/ ``FINISHED`` / ``DONE``) stream to ``config["progress_file"]`` so the
orchestrator can attribute native crashes to a module and resume the
remaining modules in a fresh process.

Config keys:
    modules (list[str]):    Ordered test module names (``test_*``).
    module_paths (dict):    name -> absolute file path.
    results_file (str):     File to append per-module result blocks to.
    progress_file (str):    Marker stream (optional).
    temp_dir (str):         Scratch dir for sandboxes (optional).
    extended (bool):        Sets ``MAYATK_EXTENDED_TESTS=1`` for the run.
    reload (bool):          Reload pythontk/mayatk before running (only
                            meaningful in long-lived GUI sessions; fresh
                            processes skip it).
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = TEST_DIR.parents[1]

# Final line appended to the results file when every module has run —
# run_tests.py polls for it (and strips it when merging chunk files).
SUITE_COMPLETE_MARKER = "# SUITE COMPLETE"

# Real Maya modules snapshotted before each test module and restored after,
# so a test that mocks sys.modules can't poison later modules.  A key missing
# from the snapshot can never be restored, so the core ones are force-imported
# first (see _snapshot_real_maya_modules).
_REAL_MODULE_KEYS = (
    "maya",
    "maya.cmds",
    "maya.mel",
    "maya.OpenMaya",
    "maya.OpenMayaUI",
    "maya.api",
    "maya.api.OpenMaya",
    "maya.api.OpenMayaAnim",
    "maya.utils",
    "maya.app",
)

_sandbox_dir = None  # shots-prefs sandbox; cleaned in main() (os._exit skips atexit)
_qapp = None


class _TeeStream:
    """Stand in for ``sys.stdout`` / ``sys.stderr``, recording as it passes text on.

    ``unittest`` reports through the stream it bound at construction, so its own
    output never needed this -- but a bare ``print()``, or the
    ``traceback.print_exc()`` of an exception the product swallowed, goes to the
    real console and was absent from the results file, which is the only
    artifact that outlives the run. A failure whose sole explanation was printed
    therefore reached the reader as a bare assertion diff.

    Delegates anything it does not define to the real stream, so product code
    asking ``isatty()`` mid-test does not raise, and swallows the cp1252
    ``UnicodeEncodeError`` the console raises on an em-dash rather than letting
    one glyph abort the module.

    ``pythontk`` carries the canonical version of this (``ptk.TeeStream``), and
    the pythontk / tentacle / uitk runners all use it. This module deliberately
    does NOT: its GUI entry point is a generated script that exec's it and calls
    ``run_suite`` directly, WITHOUT the ``_ensure_sys_path`` that puts the
    ecosystem packages on the path (see run_tests.py's driver template), so an
    import here would rest on whatever the host session happens to have. Every
    module in the run would fail together if that assumption ever broke, which
    is too much to stake on a 30-line helper. ``run_tests.py``'s own copy, for
    the LAUNCHER's output, stays for the same reason in reverse: it runs before
    any of that path setup exists.
    """

    def __init__(self, stream, buffer):
        self._stream = stream
        self._buffer = buffer

    def write(self, text):
        self._buffer.write(text)
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", None) or "ascii"
            self._stream.write(text.encode(encoding, "replace").decode(encoding))
        except Exception:
            pass
        return len(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _reconfigure_streams():
    """Degrade unicode gracefully on cp1252 consoles (see run_tests.py)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _ensure_sys_path():
    """Add the test dirs and ecosystem package roots to sys.path."""
    paths = [
        str(TEST_DIR),
        str(TEST_DIR / "extended"),
        str(TEST_DIR / "mock_tests"),
        str(SCRIPTS_ROOT),
        *(
            str(SCRIPTS_ROOT / pkg)
            for pkg in ("mayatk", "pythontk", "uitk", "tentacle")
        ),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


def _progress(progress_file, marker):
    """Append a flushed marker line to the progress stream."""
    if not progress_file:
        return
    try:
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(marker + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def _append_results(results_file, text):
    with open(results_file, "a", encoding="utf-8") as f:
        f.write(text)


def _reload_packages():
    """Reload pythontk + mayatk (long-lived GUI sessions only)."""
    try:
        from pythontk import ModuleReloader

        reloader = ModuleReloader(include_submodules=True)
        import pythontk

        reloader.reload(pythontk)
        import mayatk

        reloaded = reloader.reload(mayatk)
        print(f"[ModuleReloader] Reloaded pythontk + {len(reloaded)} mayatk modules")
        # Force base_test to re-import against the reloaded packages.
        sys.modules.pop("base_test", None)
    except Exception as e:
        cleared = [k for k in list(sys.modules) if "mayatk" in k.lower()]
        for k in cleared:
            del sys.modules[k]
        print(
            f"[Fallback] Reloader failed ({e}); cleared {len(cleared)} cached mayatk modules"
        )


def _sandbox_shots_prefs(temp_dir):
    """Redirect ShotStore prefs writes away from the user's cloud-synced store.

    Set AFTER any reload so the override lands on the final class object.
    """
    global _sandbox_dir
    try:
        import atexit

        from pythontk.core_utils.engines.shots.shot_model import ShotStore

        _sandbox_dir = tempfile.mkdtemp(
            prefix="shots_prefs_test_", dir=temp_dir or None
        )
        ShotStore._prefs_dir_override = _sandbox_dir
        # atexit covers the GUI path; the mayapy path cleans up explicitly in
        # main() because os._exit bypasses atexit.
        atexit.register(shutil.rmtree, _sandbox_dir, ignore_errors=True)
        print(f"[Sandbox] shots prefs -> {_sandbox_dir}")
    except Exception as e:
        print(f"[Sandbox] shots prefs override failed: {e}")


def _snapshot_real_maya_modules():
    for key in ("maya.cmds", "maya.mel", "maya.api.OpenMaya", "maya.api.OpenMayaAnim"):
        try:
            __import__(key)
        except Exception:
            pass
    return {k: sys.modules[k] for k in _REAL_MODULE_KEYS if k in sys.modules}


def _restore_real_maya_modules(snapshot):
    """Undo any sys.modules mocking a test module leaked.

    Also restores the parent-package ATTRIBUTES: ``import maya.cmds as cmds``
    resolves via ``getattr(maya, 'cmds')`` BEFORE falling back to sys.modules,
    so a leaked attribute mock would reroute every later import even after
    sys.modules is restored.
    """
    for key in _REAL_MODULE_KEYS:
        if key in snapshot:
            sys.modules[key] = snapshot[key]
        else:
            sys.modules.pop(key, None)
    for key, real_mod in snapshot.items():
        if "." in key:
            parent_name, child_name = key.rsplit(".", 1)
            parent_mod = snapshot.get(parent_name)
            if parent_mod is not None:
                setattr(parent_mod, child_name, real_mod)


def _purge_mayatk_modules():
    """Drop cached mayatk modules so the next test module re-imports fresh."""
    for key in [k for k in list(sys.modules) if k.startswith("mayatk")]:
        del sys.modules[key]


def _reset_session_globals():
    """Clear the process-global FBX export hooks between modules.

    ``cmds.file(new=True)`` cannot reach a registry that lives on a CLASS, and
    ``MayaTkTestCase.tearDown`` cannot either when the registration is
    DEFERRED: a ShotStore flush schedules its save with ``evalDeferred``, so
    the preparer it installs lands during a LATER test -- already inside the
    next test's baseline, where the per-test guard reads it as pre-existing
    and leaves it. Measured 2026-08-31: ``test_shot_plan`` handed
    ``['shots']`` plus auto-takes to ``test_shot_export_view``, whose
    assertions on the export view then failed in a chunk and passed alone.
    Module boundaries are where a stale hook actually does damage, and they
    are the one place no suite legitimately holds one across.
    """
    try:
        from mayatk.env_utils.fbx_utils import FbxUtils
    except Exception:  # a test env without the fbx module still runs
        return
    for name in list(FbxUtils._export_preparers):
        FbxUtils.unregister_export_preparer(name)
    try:
        FbxUtils.disable_auto_takes()
    except Exception:
        pass
    # The active ShotStore is the other half of the same leak: the preparer is
    # ``ShotStore.refresh_export_view``, which republishes from whatever
    # ``_active`` happens to be.  A store left active by an earlier module has
    # no shots in THIS scene, and republishing an empty store CLEARS the export
    # channels -- which is how an export-view assertion reads None mid-run.
    try:
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        ShotStore._auto_export_disabled = False
    except Exception:
        pass


def format_totals(totals):
    return (
        f"Total: {totals['tests']} tests, {totals['failures']} failures, "
        f"{totals['errors']} errors, {totals['skipped']} skipped"
    )


def run_suite(config):
    """Run the configured test modules; return a totals dict.

    Appends one result block per module to ``config["results_file"]`` as it
    goes, so a native crash loses at most the in-flight module.
    """
    _ensure_sys_path()

    # Process-level isolation for the whole chunk -- no real browser launch,
    # one throwaway temp root -- before any module allocates. Here rather than
    # only in conftest.py: most modules never import that file.
    from pythontk.core_utils.test_sandbox import TestSandbox

    TestSandbox.activate()

    if config.get("extended"):
        os.environ["MAYATK_EXTENDED_TESTS"] = "1"
    else:
        os.environ.pop("MAYATK_EXTENDED_TESTS", None)

    if config.get("reload"):
        _reload_packages()

    _sandbox_shots_prefs(config.get("temp_dir"))

    import maya.cmds as cmds

    results_file = config["results_file"]
    progress_file = config.get("progress_file")
    module_paths = config.get("module_paths", {})
    modules = config["modules"]

    snapshot = _snapshot_real_maya_modules()
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "modules": 0}

    for module_name in modules:
        module_path = module_paths.get(module_name, str(TEST_DIR / f"{module_name}.py"))
        _progress(progress_file, f"STARTED {module_name}")
        print(f"\n{'-' * 70}\nTesting: {module_name}\n{'-' * 70}", flush=True)

        start = time.monotonic()
        # One scene reset per module; per-test resets are MayaTkTestCase.setUp's job.
        try:
            cmds.file(new=True, force=True)
        except Exception:
            pass
        _reset_session_globals()

        try:
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            test_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(test_module)

            suite = unittest.TestSuite()
            loader = unittest.defaultTestLoader
            for attr_name in dir(test_module):
                attr = getattr(test_module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, unittest.TestCase)
                    and attr is not unittest.TestCase
                ):
                    suite.addTest(loader.loadTestsFromTestCase(attr))

            # Construct the runner BEFORE the redirect: TextTestRunner binds
            # sys.stderr at __init__, so this keeps its report on the real
            # console and out of `printed` -- which then holds ONLY what the
            # module itself printed, with no duplicate of the tracebacks the
            # result block already lists.
            runner = unittest.TextTestRunner(verbosity=2)
            printed = io.StringIO()
            saved_out, saved_err = sys.stdout, sys.stderr
            sys.stdout = _TeeStream(saved_out, printed)
            sys.stderr = _TeeStream(saved_err, printed)
            try:
                result = runner.run(suite)
            finally:
                # In a finally: a module that dies mid-run must not leave every
                # later module writing into a buffer nobody reads.
                sys.stdout, sys.stderr = saved_out, saved_err
            elapsed = time.monotonic() - start

            # @skipUnlessExtended skips are an opt-in marker, not a real skip —
            # subtract them so the main run reports 0 skipped.
            extended_skips = [
                (t, r) for (t, r) in result.skipped if "Extended test" in r
            ]
            real_skipped = [
                (t, r) for (t, r) in result.skipped if "Extended test" not in r
            ]
            real_run = result.testsRun - len(extended_skips)

            totals["tests"] += real_run
            totals["failures"] += len(result.failures)
            totals["errors"] += len(result.errors)
            totals["skipped"] += len(real_skipped)
            totals["modules"] += 1

            status = "PASS" if result.wasSuccessful() else "FAIL"
            block = [
                f"\n{module_name}: {status} [{elapsed:.1f}s]\n",
                f"  Tests: {real_run}, Failures: {len(result.failures)}, "
                f"Errors: {len(result.errors)}, Skipped: {len(real_skipped)}",
            ]
            if extended_skips:
                block.append(f", Extended-deferred: {len(extended_skips)}")
            block.append("\n")
            for test, trace in result.failures:
                block.append(f"\n  FAILURE: {test}\n  {trace}\n")
            for test, trace in result.errors:
                block.append(f"\n  ERROR: {test}\n  {trace}\n")
            for test, reason in real_skipped:
                block.append(f"  SKIP: {test} | {reason}\n")
            # Only on failure, and only the tail: a passing module's chatter
            # would bury the report (the full suite is 5000+ tests), while a
            # failing one's last words are usually the explanation.
            if not result.wasSuccessful():
                tail = printed.getvalue().strip().splitlines()[-40:]
                if tail:
                    block.append("\n  CAPTURED OUTPUT (tail):\n")
                    block.extend(f"    {ln}\n" for ln in tail)
            _append_results(results_file, "".join(block))

        except Exception as e:
            elapsed = time.monotonic() - start
            print(f"[ERROR] Error loading {module_name}: {e}")
            totals["modules"] += 1
            _append_results(
                results_file,
                f"\n{module_name}: LOAD ERROR [{elapsed:.1f}s]\n  {e}\n",
            )
        finally:
            _restore_real_maya_modules(snapshot)
            _purge_mayatk_modules()

        _progress(progress_file, f"FINISHED {module_name}")

    _append_results(
        results_file, f"\n{SUITE_COMPLETE_MARKER} ({totals['modules']} modules)\n"
    )
    return totals


def main(argv):
    if len(argv) < 2:
        print("Usage: mayapy _suite_driver.py <config.json>")
        return 2

    with open(argv[1], encoding="utf-8") as f:
        config = json.load(f)

    _reconfigure_streams()
    progress_file = config.get("progress_file")

    # Initialize standalone only if we aren't already inside a Maya session.
    try:
        import maya.cmds as cmds

        cmds.about(version=True)
    except Exception:
        import maya.standalone

        maya.standalone.initialize(name="python")

    _progress(progress_file, "INIT_DONE")

    # Some test modules construct Qt widgets; give them an application object.
    global _qapp
    try:
        from qtpy import QtWidgets

        if QtWidgets.QApplication.instance() is None:
            _qapp = QtWidgets.QApplication([])
    except Exception:
        pass

    totals = run_suite(config)
    _progress(progress_file, "DONE")
    print("MAYATK_CHUNK_RESULT:" + json.dumps(totals), flush=True)

    if _sandbox_dir:
        shutil.rmtree(_sandbox_dir, ignore_errors=True)
    sys.stdout.flush()
    sys.stderr.flush()
    # Hard-exit: standalone teardown (scriptJob/OpenMaya callbacks, stacked
    # native libs) segfaults at interpreter shutdown, which would masquerade
    # as a mid-run crash.  Results are already on disk.
    os._exit(0 if (totals["failures"] == 0 and totals["errors"] == 0) else 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
