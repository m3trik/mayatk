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
import unittest
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
        self.assertEqual(
            rt.MayaTestRunner.exit_code_for(self._status()), rt.EXIT_OK
        )

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
        self.assertEqual(
            rt.MayaTestRunner.exit_code_for(status), rt.EXIT_NOT_RUN
        )


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
