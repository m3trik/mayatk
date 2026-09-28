# coding=utf-8
"""Unit tests for the mayatk test RUNNER (``mayatk/test/run_tests.py``).

Maya is never launched here: the runner module is loaded by path and driven
against synthetic status dicts / results files with the connection stubbed
out, so these tests run under plain python as well as inside the suite.

Covers the contract that a run which did not run everything must be
distinguishable from a clean one (BACKLOG 2026-08-02 "GUI test pass cannot
connect, silently parking 16 modules"): a non-empty NOT RUN section gets its
own exit code, and a GUI deferral records WHY it could not connect.
"""

import contextlib
import importlib.util
import io
import os
import unittest
from unittest import mock
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
RUN_TESTS_PATH = TEST_DIR / "run_tests.py"

# Load by path, not by package import: the runner is a script that the suite
# itself may already have imported under a different name.
_SPEC = importlib.util.spec_from_file_location(
    "_mayatk_run_tests_under_test", RUN_TESTS_PATH
)
rt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rt)


class TestRunnerExitCodes(unittest.TestCase):
    """A run that did not run everything must not look like a clean run."""

    @staticmethod
    def _status(**overrides):
        """A green full-run status dict, overridable per case."""
        status = {
            "tests": 3318,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "passed": 3318,
            "failed": 0,
            "failed_modules": [],
            "not_run": [],
            "ok": True,
            "all_ran": True,
        }
        status.update(overrides)
        return status

    def test_exit_codes_are_distinct_and_documented(self):
        codes = (rt.EXIT_OK, rt.EXIT_FAILED, rt.EXIT_NOT_RUN, rt.EXIT_USAGE)
        self.assertEqual(len(set(codes)), len(codes), "exit codes collide")
        self.assertEqual(rt.EXIT_OK, 0)
        for code in codes[1:]:
            self.assertNotEqual(code, 0)
        doc = rt.__doc__ or ""
        self.assertIn("Exit codes", doc)
        self.assertIn(f"{rt.EXIT_NOT_RUN}", doc)
        self.assertIn("NOT RUN", doc)

    def test_clean_run_exits_zero(self):
        self.assertEqual(rt.MayaTestRunner.exit_code_for(self._status()), rt.EXIT_OK)

    def test_not_run_section_gets_its_own_exit_code(self):
        """The dangerous case: 0 failures, yet modules never executed."""
        status = self._status(
            not_run=["test_preview", "test_sequencer"], ok=False, all_ran=False
        )
        code = rt.MayaTestRunner.exit_code_for(status)
        self.assertEqual(code, rt.EXIT_NOT_RUN)
        self.assertNotEqual(code, rt.EXIT_OK)
        self.assertNotEqual(code, rt.EXIT_FAILED)

    def test_no_gui_pass_deferral_is_incomplete_not_clean(self):
        status = self._status(
            not_run=["test_sequencer", "test_hdr_manager"], ok=False, all_ran=False
        )
        self.assertEqual(rt.MayaTestRunner.exit_code_for(status), rt.EXIT_NOT_RUN)

    def test_failures_outrank_not_run(self):
        status = self._status(
            failures=2,
            failed=2,
            failed_modules=["test_core_utils"],
            not_run=["test_preview"],
            ok=False,
            all_ran=False,
        )
        self.assertEqual(rt.MayaTestRunner.exit_code_for(status), rt.EXIT_FAILED)

    def test_errors_without_failed_modules_exit_failed(self):
        status = self._status(errors=1, failed=1, ok=False)
        self.assertEqual(rt.MayaTestRunner.exit_code_for(status), rt.EXIT_FAILED)

    def test_phase_failure_without_counts_exits_failed(self):
        # A phase blew up but every module still reported: not "incomplete".
        status = self._status(ok=False)
        self.assertEqual(rt.MayaTestRunner.exit_code_for(status), rt.EXIT_FAILED)

    def test_dry_run_and_nowait_exit_zero(self):
        self.assertEqual(
            rt.MayaTestRunner.exit_code_for({"ok": True, "dry_run": True}), rt.EXIT_OK
        )
        self.assertEqual(
            rt.MayaTestRunner.exit_code_for({"ok": True, "nowait": True}), rt.EXIT_OK
        )

    def test_non_dict_status_exits_failed(self):
        self.assertEqual(rt.MayaTestRunner.exit_code_for(None), rt.EXIT_FAILED)
        self.assertEqual(rt.MayaTestRunner.exit_code_for(False), rt.EXIT_FAILED)

    def test_deferred_module_in_results_file_maps_to_incomplete_exit(self):
        """End-to-end over the real parser: green counts + one DEFERRED module."""
        runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(runner.results_file.unlink, True)
        runner.results_file.write_text(
            "test_core_utils: PASS [1.0s]\n"
            "  Tests: 10, Failures: 0, Errors: 0, Skipped: 0\n"
            "\ntest_preview: DEFERRED (GUI connection failed)\n",
            encoding="utf-8",
        )
        status = runner._finalize_results()
        status["ok"] = not status["failed_modules"] and not status["not_run"]

        self.assertEqual(status["not_run"], ["test_preview"])
        self.assertEqual(status["failures"] + status["errors"], 0)
        self.assertEqual(rt.MayaTestRunner.exit_code_for(status), rt.EXIT_NOT_RUN)


class TestGuiDeferralDiagnostics(unittest.TestCase):
    """A GUI deferral must be self-documenting (why it could not connect)."""

    def setUp(self):
        self.runner = rt.MayaTestRunner(port=7902)
        self.addCleanup(self.runner.results_file.unlink, True)

    def _defer(self):
        """Drive the GUI pass with a connection that always fails."""
        self.runner.connect_to_maya = lambda: False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = self.runner._run_via_port(
                ["test_preview"], {"test_preview": "test_preview.py"}, False
            )
        return result, buf.getvalue()

    def test_connection_failure_prints_probe_and_launcher_output(self):
        self.runner._launch_log = (
            "[MayaConnection] Timeout waiting for Maya Command Port."
        )
        result, out = self._defer()

        self.assertFalse(result)
        lowered = out.lower()
        self.assertIn("port probe", lowered)
        self.assertIn("launcher output", lowered)
        self.assertIn("Timeout waiting for Maya Command Port", out)

    def test_deferral_diagnostics_are_persisted_in_the_results_file(self):
        self.runner._launch_log = "[MayaConnection] Failed to launch Maya executable."
        self._defer()

        recorded = self.runner.results_file.read_text(encoding="utf-8")
        self.assertIn("test_preview: DEFERRED", recorded)
        self.assertIn("Failed to launch Maya executable", recorded)
        self.assertIn("port probe", recorded.lower())

    def test_diagnostics_block_does_not_corrupt_module_parsing(self):
        self.runner._launch_log = "test_preview: PASS\n  Tests: 9, Failures: 0"
        report = self.runner._gui_deferral_report("GUI connection failed")
        self.assertEqual(rt._parse_module_blocks(report), [])

    def test_launch_output_is_recorded_while_still_printing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.runner._record_launch_output():
                print("[MayaConnection] Maya process exited prematurely with code 1.")

        self.assertIn("exited prematurely", buf.getvalue())
        self.assertIn("exited prematurely", self.runner._launch_log)


class TestChunkChildStdin(unittest.TestCase):
    """A mayapy chunk must never be able to wait on a human.

    ``SceneExporter.confirm`` answers a ``[y/N]`` on the console whenever
    ``sys.stdin.isatty()`` is true, and a child that inherits the launching
    console reads as interactive -- blendertk's twin harness lost
    ``test_smart_bake`` to exactly that (2026-09-04: its deliberate
    failed-check export sat on ``readline()`` until the kill timer fired).
    The chunk launch therefore hands mayapy a closed stdin.
    """

    def test_mayapy_chunk_gets_a_closed_stdin(self):
        runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(runner.results_file.unlink, True)
        runner.temp_test_dir.mkdir(exist_ok=True)
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            raise OSError("launch refused by the test")

        buf = io.StringIO()
        with mock.patch.object(rt.subprocess, "Popen", side_effect=fake_popen):
            with contextlib.redirect_stdout(buf):
                ok, deferred = runner._run_chunk(
                    "mayapy.exe", 0, 1, ["test_x"], {"test_x": "test_x.py"}, False
                )
        for stale in runner.temp_test_dir.glob(f"chunk_{os.getpid()}_00_*"):
            self.addCleanup(stale.unlink, True)
        # A refused launch defers the chunk to the GUI pass, as documented.
        self.assertEqual((ok, deferred), (True, ["test_x"]), buf.getvalue())
        self.assertEqual(len(calls), 1, "one mayapy per chunk attempt")
        self.assertIs(calls[0][1].get("stdin"), rt.subprocess.DEVNULL)

    def test_the_chunk_command_line_carries_no_path(self):
        """mayapy decodes its command line in the ANSI code page (measured on Maya
        2025: "Jos\\u00e9" arrived as "Jos\\udce9", Cyrillic as "???"), so a checkout
        under such a folder handed it a driver that does not exist. The driver and
        its config ride in the env instead (``AppLauncher.python_args_via_env``)."""
        import json

        runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(runner.results_file.unlink, True)
        runner.temp_test_dir.mkdir(exist_ok=True)
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            raise OSError("launch refused by the test")

        driver = Path("X:/Jos\u00e9 \u0416\u0443\u043a/_suite_driver.py")
        popen = mock.patch.object(rt.subprocess, "Popen", side_effect=fake_popen)
        with mock.patch.object(rt, "DRIVER_PATH", driver), popen:
            with contextlib.redirect_stdout(io.StringIO()):
                runner._run_chunk(
                    "mayapy.exe", 0, 1, ["test_x"], {"test_x": "test_x.py"}, False
                )
        for stale in runner.temp_test_dir.glob(f"chunk_{os.getpid()}_00_*"):
            self.addCleanup(stale.unlink, True)
        cmd, kwargs = calls[0]
        self.assertTrue(all(str(part).isascii() for part in cmd), cmd)
        from pythontk import AppLauncher

        argv = json.loads(kwargs["env"][AppLauncher.PYTHON_ARGV_VAR])
        self.assertEqual(argv[0], str(driver))
        self.assertTrue(argv[1].endswith(".json"), argv)
        self.assertEqual(
            kwargs["env"].get("PYTHONPATH"), runner._child_env()["PYTHONPATH"]
        )


class TestChunkTempIsolation(unittest.TestCase):
    """A chunk's Maya reads TEMP once, at startup, and files its crash-save there.

    Measured 2026-09-27 (fresh mayapy, TEMP = a counting dir): a process that
    leaves through a crash -- ``os._exit``, which runs Maya's faulting DLL
    detach, or a native crash -- writes ``untitled[Recovered-...].ma``, a
    ``MayaCrashLog*.dmp`` and its log into ITS TEMP. The runner handed chunks
    the real TEMP (``os.environ.copy()``): the suite driver's own
    ``TestSandbox.activate()`` runs after Maya has read it, so a crashing chunk
    -- the case the resume machinery exists for -- littered the user's temp
    dir. The runner now isolates TEMP before it spawns anything, like
    blendertk's runner, so every chunk inherits the throwaway root.

    Hermetic: the "real" TEMP here is a test-owned folder, and the process-wide
    sandbox state is restored afterwards.
    """

    def setUp(self):
        import shutil
        import tempfile

        from pythontk.core_utils.test_sandbox import TestSandbox

        self.real = str(TEST_DIR / "temp_tests" / f"realtemp_{os.getpid()}")
        os.makedirs(self.real, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.real, True)
        for patcher in (
            mock.patch.dict(os.environ, {"TEMP": self.real, "TMP": self.real}),
            mock.patch.object(tempfile, "tempdir", self.real),
            mock.patch.dict(TestSandbox._state, {"temp_dir": None, "temp_store": None}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(self.runner.results_file.unlink, True)

    def test_a_chunk_is_handed_the_sandbox_temp(self):
        root = self.runner._isolate_temp()
        env = self.runner._child_env()
        self.assertTrue(root and os.path.isdir(root), root)
        self.assertNotEqual(os.path.normcase(root), os.path.normcase(self.real))
        for key in ("TEMP", "TMP"):
            self.assertEqual(os.path.normcase(env[key]), os.path.normcase(root))

    def test_a_crashing_chunk_files_its_recovered_scene_inside_the_sandbox(self):
        import glob
        import subprocess

        mayapy = rt.find_mayapy()
        if not mayapy:
            self.skipTest("mayapy not installed")
        self.runner._isolate_temp()
        env = self.runner._child_env()
        script = os.path.join(self.real, "crash_like_a_chunk.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(
                "import os\n"
                "import maya.standalone\n"
                "maya.standalone.initialize(name='python')\n"
                "import maya.cmds as cmds\n"
                "cmds.polyCube()\n"
                "os._exit(0)  # the teardown crash a native-crashing chunk also takes\n"
            )
        env.update(
            MAYA_SKIP_USERSETUP_PY="1", MAYA_DISABLE_CIP="1", MAYA_DISABLE_CER="1"
        )
        subprocess.run([mayapy, script], env=env, capture_output=True, timeout=600)

        def crash_files(folder):
            # The recovered scene, and the crash log + dump written beside it. The
            # scene copy is not written on every crash (measured: skipped in one of
            # two identical runs); the crash log always is.
            return glob.glob(os.path.join(folder, "*[[]Recovered-*")) + glob.glob(
                os.path.join(folder, "MayaCrashLog*")
            )

        child_temp = env["TEMP"]
        self.assertEqual(crash_files(self.real), [], "landed in the real TEMP")
        self.assertTrue(crash_files(child_temp), os.listdir(child_temp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
