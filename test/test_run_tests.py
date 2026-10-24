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
import sys
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

    def test_mayapy_chunk_runs_in_a_scratch_cwd_not_the_workspace_root(self):
        """A Maya child drops ``mayaDiskCache_<pid>`` folders in its working
        directory: launched from the workspace root, they turned the root
        guard red. The chunk runs in a temp-root scratch folder instead (its
        packages come from the pinned PYTHONPATH). Added: 2026-10-13"""
        import pythontk as ptk

        runner = rt.MayaTestRunner(port=7904)
        self.addCleanup(runner.results_file.unlink, True)
        runner.temp_test_dir.mkdir(exist_ok=True)
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(kwargs)
            raise OSError("launch refused by the test")

        with mock.patch.object(rt.subprocess, "Popen", side_effect=fake_popen):
            with contextlib.redirect_stdout(io.StringIO()):
                runner._run_chunk(
                    "mayapy.exe", 0, 1, ["test_x"], {"test_x": "test_x.py"}, False
                )
        for stale in runner.temp_test_dir.glob(f"chunk_{os.getpid()}_00_*"):
            self.addCleanup(stale.unlink, True)
        cwd = os.path.normcase(os.path.realpath(calls[0]["cwd"]))
        self.assertNotEqual(cwd, os.path.normcase(os.path.realpath(rt.SCRIPTS_ROOT)))
        self.assertTrue(
            cwd.startswith(
                os.path.normcase(os.path.realpath(ptk.TempArtifacts.root()))
            ),
            cwd,
        )
        self.assertTrue(os.path.isdir(cwd))

    def test_the_chunk_command_line_carries_no_path(self):
        """mayapy decodes its command line in the ANSI code page (measured on Maya
        2025: "\\u00e9" arrived as the lone surrogate "\\udce9", Cyrillic as
        "???"), so a checkout under such a folder handed it a driver that does not
        exist. The driver and its config ride in the env instead
        (``AppLauncher.python_args_via_env``)."""
        import json

        runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(runner.results_file.unlink, True)
        runner.temp_test_dir.mkdir(exist_ok=True)
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            raise OSError("launch refused by the test")

        driver = Path("X:/Zo\u00eb \u2605/_suite_driver.py")
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

    @unittest.skipUnless(sys.platform == "win32", "Autodesk's helpers are Windows")
    def test_a_chunk_never_inherits_an_offscreen_qpa(self):
        """Measured 2026-10-21: ``affected_tests.py --run`` sets
        ``QT_QPA_PLATFORM=offscreen``; a Maya child hands it to Autodesk's
        licensing helpers, whose private ``platforms/`` holds only qwindows, and
        each one pops "no Qt platform plugin could be initialized". Standalone
        pins its own ``minimal`` platform, so the variable buys mayapy nothing."""
        runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(runner.results_file.unlink, True)
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            env = runner._child_env()
        self.assertNotIn("QT_QPA_PLATFORM", env)


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


class TestTheConfigNamesALaunchedMaya(unittest.TestCase):
    """The driver's config says whether its Maya is one this runner launched.

    ``launched`` lets the suite driver's sandbox -- uitk's QSettings + preset
    redirect with pythontk's guards (``test_suite_driver``) -- stay up for
    good, which suits a mayapy chunk or a GUI Maya the run closes. The user's
    own session -- the in-session harness (Script Editor) or ``--reuse`` --
    gets it for the run only, since it would outlive the run; so does a GUI
    Maya the run leaves open (``--keep-maya``, ``--no-wait``), where a
    permanent sandbox refused every browser launch (the WebXR preview's tab)
    and kept the user's settings in throwaway stores.

    No Maya: the chunk launch is refused and the GUI connection stubbed (the
    send fails once the config is written), and the config the driver would
    have read is read back from disk.
    """

    def setUp(self):
        self.runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(self.runner.results_file.unlink, True)
        self.runner.temp_test_dir.mkdir(exist_ok=True)

    def _read_config(self, path):
        import json

        self.addCleanup(path.unlink, True)
        return json.loads(path.read_text(encoding="utf-8"))

    def _chunk_config(self):
        refused = mock.patch.object(
            rt.subprocess, "Popen", side_effect=OSError("launch refused by the test")
        )
        with refused, contextlib.redirect_stdout(io.StringIO()):
            self.runner._run_chunk(
                "mayapy.exe", 0, 1, ["test_x"], {"test_x": "test_x.py"}, False
            )
        for stale in self.runner.temp_test_dir.glob(f"chunk_{os.getpid()}_00_*"):
            self.addCleanup(stale.unlink, True)
        return self._read_config(
            self.runner.temp_test_dir / f"chunk_{os.getpid()}_00_1.json"
        )

    def _gui_config(self, mode, reuse=False, keep_maya=False, no_wait=False):
        self.runner.reuse_instance = reuse
        self.runner.keep_maya = keep_maya
        self.runner.connection = mock.Mock(mode=mode)
        self.runner.connect_to_maya = lambda: True
        self.runner.send_code = lambda code: False
        with contextlib.redirect_stdout(io.StringIO()):
            self.runner._run_via_port(
                ["test_x"], {"test_x": "test_x.py"}, False, no_wait=no_wait
            )
        return self._read_config(self.runner.temp_test_dir / f"gui_{os.getpid()}.json")

    def test_a_mayapy_chunk_is_launched(self):
        self.assertIs(self._chunk_config().get("launched"), True)

    def test_a_gui_maya_the_run_closes_is_launched(self):
        self.assertIs(self._gui_config("port").get("launched"), True)

    def test_a_gui_maya_the_run_leaves_open_is_not(self):
        for flags in ({"keep_maya": True}, {"no_wait": True}):
            with self.subTest(**flags):
                config = self._gui_config("port", **flags)
                self.assertIs(config.get("launched"), False, config)

    def test_the_users_own_session_is_not(self):
        """The in-session harness (Script Editor), and an attach (``--reuse``)."""
        for mode, reuse in (("interactive", False), ("port", True)):
            with self.subTest(mode=mode, reuse=reuse):
                config = self._gui_config(mode, reuse)
                self.assertFalse(config.get("launched", False), config)

    def test_k_reaches_the_chunk_and_the_gui_pass(self):
        """``-k`` narrows inside the driver, so both phases must be handed it."""
        self.runner.patterns = ["mirror"]
        self.assertEqual(self._chunk_config().get("patterns"), ["mirror"])
        self.assertEqual(self._gui_config("port").get("patterns"), ["mirror"])


class TestTheConsoleReport(unittest.TestCase):
    """The end-of-run console report is the modules that did not pass, then the
    SUMMARY -- not the whole results file, whose passing blocks repeat the
    SUMMARY's lines (a clean full run printed ~760 of them)."""

    RESULTS = (
        "=" * 70 + "\nMAYATK TEST RESULTS\n" + "=" * 70 + "\n\n"
        "\ntest_quiet: PASS [1.0s]\n  Tests: 3, Failures: 0, Errors: 0, Skipped: 1\n"
        "  SKIP: test_x (m.T.test_x) | needs a GUI\n"
        "\ntest_broken: FAIL [2.0s]\n  Tests: 2, Failures: 1, Errors: 0, Skipped: 0\n"
        "\n  FAILURE: test_y (m.T.test_y)\n  Traceback ... PROBE-TRACE\n"
        "\ntest_noted: PASS [1.5s]\n  Tests: 1, Failures: 0, Errors: 0, Skipped: 0\n"
        "  [ERROR] UNEXPECTED SUCCESS: PROBE-NOTE\n"
        "\n" + "=" * 70 + "\nSUMMARY\n" + "=" * 70 + "\n"
        "  test_quiet: PASS (3 tests, 0 failures, 0 errors) [1.0s]\n"
        "  test_broken: FAIL (2 tests, 1 failures, 0 errors) [2.0s]\n"
        "Total: 6 tests, 1 failures, 0 errors, 1 skipped\n"
    )

    def setUp(self):
        self.report = rt.MayaTestRunner.concise_results(self.RESULTS)

    def test_a_failing_module_is_shown_in_full(self):
        self.assertIn("PROBE-TRACE", self.report)

    def test_a_plain_pass_is_left_to_its_summary_line(self):
        self.assertNotIn("SKIP: test_x", self.report)
        self.assertEqual(self.report.count("test_quiet: PASS"), 1)  # the SUMMARY's

    def test_a_pass_carrying_a_note_is_kept(self):
        self.assertIn("PROBE-NOTE", self.report)

    def test_the_summary_survives_whole(self):
        self.assertIn("Total: 6 tests, 1 failures", self.report)

    def test_text_without_a_summary_is_returned_unchanged(self):
        self.assertEqual(rt.MayaTestRunner.concise_results("partial"), "partial")


class TestTheCommandLine(unittest.TestCase):
    """A flag the runner does not know is refused before anything launches."""

    def _main(self, *argv):
        with mock.patch.object(rt.sys, "argv", ["run_tests.py", *argv]), \
                mock.patch.object(rt, "MayaTestRunner") as built, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = rt.main()
        return code, built, out.getvalue()

    def test_an_unknown_flag_is_a_usage_error_not_a_module(self):
        code, built, out = self._main("--quiet", "core_utils")
        self.assertEqual(code, rt.EXIT_USAGE)
        built.assert_not_called()  # no runner, so no Maya
        self.assertIn("--quiet", out)

    def test_dash_q_is_refused_with_its_history(self):
        """`-q` once meant --quick, which opens a GUI Maya."""
        code, built, out = self._main("-q")
        self.assertEqual(code, rt.EXIT_USAGE)
        built.assert_not_called()
        self.assertIn("--quick", out)

    def test_k_is_collected_and_handed_to_the_runner(self):
        with mock.patch.object(rt.sys, "argv", ["run_tests.py", "--list", "-k", "a", "-k", "b"]), \
                mock.patch.object(rt, "MayaTestRunner") as built, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rt.main(), rt.EXIT_OK)
        self.assertEqual(built.call_args.kwargs["patterns"], ["a", "b"])


class TestAnInSessionRunLeavesTheUsersTempAlone(unittest.TestCase):
    """The runner redirects TEMP for the processes it spawns -- not in-session.

    In the Script Editor (``MayaTestRunner().run_tests``) the runner runs in
    the user's own Maya and spawns nothing; its ``TestSandbox.temp()`` there
    left their session's temp dir in a root deleted when Maya exits, and the
    driver's sandbox, up for the run only, found that guard standing and left
    it standing. The driver gives an in-session run a root of its own.

    No Maya: the session check and the GUI pass are stubbed.
    """

    def _isolations(self, inside_maya):
        runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(runner.results_file.unlink, True)
        status = {"failed_modules": [], "not_run": []}
        with (
            mock.patch.object(rt, "_inside_maya", return_value=inside_maya),
            mock.patch.object(runner, "_isolate_temp") as isolate,
            mock.patch.object(runner, "_run_via_port", return_value=True),
            mock.patch.object(runner, "_finalize_results", return_value=status),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            runner.run_tests(["test_x"], gui=True)
        return isolate.call_count

    def test_in_session_the_temp_dir_is_left_alone(self):
        self.assertEqual(self._isolations(inside_maya=True), 0)

    def test_a_run_the_runner_hosts_still_isolates_its_temp(self):
        self.assertEqual(self._isolations(inside_maya=False), 1)


class _FakeChunk:
    """A mayapy chunk that initializes, starts ``test_x`` and never finishes it;
    it exits on its own after *exits_after* polls (never, by default), and a
    kill does not end it within any wait (*wait_raises*)."""

    def __init__(self, progress, exits_after=None, wait_raises=False):
        progress.write_text("INIT_DONE\nSTARTED test_x\n", encoding="utf-8")
        self.polls = 0
        self.exits_after = exits_after
        self.wait_raises = wait_raises
        self.killed = False
        self.pid = 4242

    def poll(self):
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            return 0
        return None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.wait_raises:
            raise rt.subprocess.TimeoutExpired("mayapy", timeout)
        return 0


class TestAHungChunk(unittest.TestCase):
    """A hung chunk is killed on its module's own budget, and a kill that does
    not take never ends the run.

    ``test_texture_baker`` runs 780-880 s headless against the 900 s default
    ``--module-timeout``: one slow day from being killed as hung, and raising
    the default for every module would make every real hang cost as much. It
    gets its own budget (``MODULE_TIMEOUTS``); the flag stays the floor.

    A killed mayapy that will not exit (held by its licence checkout, or stuck
    in a driver call) made ``proc.wait(timeout=30)`` raise ``TimeoutExpired``
    through the run, which then ended with a traceback instead of recording
    the module.
    """

    def setUp(self):
        self.runner = rt.MayaTestRunner(port=7903)
        self.addCleanup(self.runner.results_file.unlink, True)
        self.runner.temp_test_dir.mkdir(exist_ok=True)
        self.progress = self.runner.temp_test_dir / f"chunk_{os.getpid()}_00_1.progress"
        self.addCleanup(self._remove_chunk_files)

    def _remove_chunk_files(self):
        for stale in self.runner.temp_test_dir.glob(f"chunk_{os.getpid()}_00_*"):
            stale.unlink(missing_ok=True)

    def _run(self, chunk):
        popen = mock.patch.object(rt.subprocess, "Popen", return_value=chunk)
        with popen, contextlib.redirect_stdout(io.StringIO()) as out:
            result = self.runner._run_chunk(
                "mayapy.exe", 0, 1, ["test_x"], {"test_x": "test_x.py"}, False
            )
        return result, out.getvalue()

    def test_a_module_gets_its_own_budget_and_the_flag_stays_the_floor(self):
        self.runner.module_timeout = 900
        self.assertEqual(self.runner._hang_timeout("test_texture_baker"), 1800)
        self.assertEqual(self.runner._hang_timeout("test_x"), 900)
        self.assertEqual(self.runner._hang_timeout(None), 900)
        self.runner.module_timeout = 3000
        self.assertEqual(self.runner._hang_timeout("test_texture_baker"), 3000)

    def test_a_slow_module_within_its_budget_is_not_killed(self):
        self.runner.module_timeout = 0  # any other module would be killed at once
        chunk = _FakeChunk(self.progress, exits_after=2)
        with mock.patch.dict(rt.MODULE_TIMEOUTS, {"test_x": (3600, "slow")}):
            self._run(chunk)
        self.assertFalse(chunk.killed)

    def test_a_kill_that_does_not_take_still_records_and_defers_the_module(self):
        self.runner.module_timeout = 0
        chunk = _FakeChunk(self.progress, wait_raises=True)

        (ok, deferred), out = self._run(chunk)

        self.assertTrue(chunk.killed)
        self.assertEqual((ok, deferred), (True, ["test_x"]), out)
        self.assertIn("test_x: TIMEOUT", self.runner.results_file.read_text())


class TestTheGuiWaitFollowsTests(unittest.TestCase):
    """A GUI pass is stalled only when no TEST has started for the window.

    Bug: the stall clock moved only when a whole module finished, so every
    module longer than the 600 s window was killed while healthy --
    ``test_sequencer`` timed out twice and could never be verified ("no
    module finished in 600s").  The driver's ``TEST <id>`` heartbeat on the
    progress stream now keeps it alive; a real hang still stops it.
    Fixed: 2026-10-10
    """

    def setUp(self):
        self.runner = rt.MayaTestRunner(port=7904)
        self.addCleanup(self.runner.results_file.unlink, True)
        self.runner.temp_test_dir.mkdir(exist_ok=True)
        stem = self.runner.temp_test_dir / f"gui_wait_{os.getpid()}"
        self.results = stem.with_suffix(".results")
        self.progress = stem.with_suffix(".progress")
        for path in (self.results, self.progress):
            self.addCleanup(path.unlink, True)
        self.progress.write_text("STARTED test_long\n", encoding="utf-8")

    def _wait(self, beat):
        """Wait with a clock that moves 400 s per poll; *beat* starts a test
        on each poll, and the suite completes after the sixth."""
        clock = {"now": 0.0, "polls": 0}

        def sleep(_seconds):
            clock["now"] += 400.0
            clock["polls"] += 1
            if beat:
                with open(self.progress, "a", encoding="utf-8") as fh:
                    fh.write(f"TEST t{clock['polls']}\n")
            if clock["polls"] == 6:
                self.results.write_text(rt.SUITE_COMPLETE_MARKER, encoding="utf-8")

        with (
            mock.patch.object(rt.time, "monotonic", lambda: clock["now"]),
            mock.patch.object(rt.time, "sleep", sleep),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            done = self.runner.wait_for_results(
                self.results,
                timeout=99999,
                idle_timeout=600,
                progress_path=self.progress,
            )
        return done, out.getvalue()

    def test_a_long_module_whose_tests_keep_starting_is_waited_for(self):
        done, out = self._wait(beat=True)
        self.assertTrue(done, out)

    def test_no_test_starting_for_the_window_is_still_a_stall(self):
        done, out = self._wait(beat=False)
        self.assertFalse(done)
        self.assertIn("no test started in 600s", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
