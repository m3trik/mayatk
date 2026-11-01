# !/usr/bin/python
# coding=utf-8
"""Tests for the in-session suite driver's silent-truncation guard.

A module that runs FEWER tests than were collected and still reports PASS is
the worst failure a harness has, because every other result inherits its
credibility. Observed 2026-08-31 on `test_sequencer`: 400 methods collected,
two consecutive full runs reporting `PASS (381 tests)` and `PASS (399 tests)`
with the same wall time, so 19 and 1 tests respectively never STARTED and both
runs were folded into the suite total as passes.

The cause is still unknown -- something sets `shouldStop`, or the GUI-side
runner returns early. These do not guess at it. They pin the DETECTION, which
is what turns a silent truncation into a named, reproducible one.

Also pinned: the throwaway Maya project a run works in is given back when it
ends (`TestTheRunGivesTheProjectBack`), as are the shots prefs
(`TestTheRunGivesTheShotsPrefsBack`), and the run's sandbox stays up after
the run only in a Maya the runner launched (`TestTheSandboxOutlivesOnlyALaunchedMaya`).

Pure Python: the functions under test take ids, not a Maya session, so this
runs anywhere -- the project test stands a one-command `maya.cmds` in for
Maya's, the settings test a stand-in `uitk.testing`.
"""

import contextlib
import importlib.util
import inspect
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent


def _load_driver():
    """Import `_suite_driver` by path; it is a script, not a package member."""
    spec = importlib.util.spec_from_file_location(
        "_suite_driver_under_test", TEST_DIR / "_suite_driver.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DriverTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = _load_driver()

    def _keep(self, mapping, key):
        """Put *mapping[key]* -- or its absence -- back when the test ends."""
        if key in mapping:
            self.addCleanup(mapping.__setitem__, key, mapping[key])
        else:
            self.addCleanup(mapping.pop, key, None)


class TestModuleSuite(DriverTestCase):
    """A module's suite: every TestCase in it, narrowed by ``-k`` patterns."""

    def _module(self):
        import types

        module = types.ModuleType("test_probe_module")

        class TestWords(unittest.TestCase):
            def test_split_words(self):
                pass

            def test_join_words(self):
                pass

        class TestLines(unittest.TestCase):
            def test_split_lines(self):
                pass

        module.TestWords, module.TestLines = TestWords, TestLines
        module.TestCase = unittest.TestCase  # an imported base is not collected
        return module

    def test_no_pattern_collects_every_test(self):
        suite = self.driver._module_suite(self._module())
        self.assertEqual(
            sorted(t._testMethodName for t in _flatten(suite)),
            ["test_join_words", "test_split_lines", "test_split_words"],
        )

    def test_a_pattern_narrows_to_matching_ids(self):
        suite = self.driver._module_suite(self._module(), ["split"])
        self.assertEqual(
            sorted(t._testMethodName for t in _flatten(suite)),
            ["test_split_lines", "test_split_words"],
        )

    def test_the_shared_default_loader_is_left_alone(self):
        self.driver._module_suite(self._module(), ["split"])
        self.assertIsNone(unittest.defaultTestLoader.testNamePatterns)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


class TestCollectedIds(DriverTestCase):
    """Ids must be captured BEFORE the run, and must survive it."""

    @staticmethod
    def _suite():
        class Sample(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass

        loader = unittest.defaultTestLoader
        return loader.loadTestsFromTestCase(Sample)

    def test_ids_are_enumerated_from_a_nested_suite(self):
        ids = list(self.driver._iter_test_ids(self._suite()))
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(i.endswith(("test_a", "test_b")) for i in ids), ids)

    def test_a_suite_empties_itself_as_it_runs(self):
        """Why the ids must be captured first, stated as a test.

        ``TestSuite.run`` drops each test as it completes to free memory, so
        enumerating AFTER the run reports nothing missing no matter what
        happened -- the guard would be permanently, silently vacuous.
        """
        suite = self._suite()
        before = list(self.driver._iter_test_ids(suite))
        suite.run(unittest.TestResult())
        after = list(self.driver._iter_test_ids(suite))
        self.assertEqual(len(before), 2)
        self.assertEqual(after, [], "suite retained its tests; re-check the guard")


class TestMissingTests(DriverTestCase):
    """The comparison itself."""

    def test_nothing_missing_when_every_test_started(self):
        self.assertEqual(self.driver._missing_tests(["a", "b"], ["a", "b"]), [])

    def test_reports_the_ids_that_never_started(self):
        self.assertEqual(self.driver._missing_tests(["a", "b", "c"], ["a", "c"]), ["b"])

    def test_preserves_collection_order(self):
        missing = self.driver._missing_tests(["a", "b", "c", "d"], ["c"])
        self.assertEqual(missing, ["a", "b", "d"])

    def test_an_empty_run_reports_everything(self):
        """A module that started nothing at all is the extreme of the defect."""
        self.assertEqual(self.driver._missing_tests(["a", "b"], []), ["a", "b"])

    def test_extra_started_ids_do_not_confuse_it(self):
        """Subtests and dynamically added cases can start ids never collected.

        Those are not a truncation, so they must not mask one either.
        """
        self.assertEqual(
            self.driver._missing_tests(["a", "b"], ["a", "b", "a_subtest"]), []
        )


class TestUnexpectedSuccessIsAnError(DriverTestCase):
    """A fixed defect must not keep hiding behind its own expectedFailure.

    `wasSuccessful` already fails a module for an unexpected success, but the
    printed block said "Errors: 0" beside that FAIL and the orchestrator parses
    those blocks -- exactly the mismatch the truncation count was added to
    close. Measured 2026-09-10: two `expectedFailure` tube-rig tests started
    passing the moment the twist fix landed, and a scoped module run reported
    `Failures: 0, Errors: 0`.
    """

    def test_unittest_itself_fails_the_module(self):
        """The premise: the outcome is available, it just was not counted."""

        class Sample(unittest.TestCase):
            @unittest.expectedFailure
            def test_now_passes(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
        runner = unittest.TextTestRunner(
            verbosity=0,
            stream=open(os.devnull, "w"),
            resultclass=self.driver._RecordingResult,
        )
        result = runner.run(suite)
        self.assertEqual(len(result.unexpectedSuccesses), 1)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.failures), 0, "it is not counted as a failure")
        self.assertEqual(len(result.errors), 0, "nor as an error -- hence the guard")

    def test_the_driver_counts_it_into_the_module_error_count(self):
        """The reported number the orchestrator reads has to include it."""
        source = inspect.getsource(self.driver)
        self.assertIn("unexpectedSuccesses", source)
        self.assertIn("UNEXPECTED SUCCESS", source)
        # The count the block prints and the totals add must be the same one.
        self.assertIn("+ len(unexpected)", source)


class TestRecordingResult(DriverTestCase):
    """The result class that supplies the 'started' half of the comparison."""

    def test_it_records_every_test_it_starts(self):
        class Sample(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
        runner = unittest.TextTestRunner(
            verbosity=0,
            stream=open(os.devnull, "w"),
            resultclass=self.driver._RecordingResult,
        )
        result = runner.run(suite)
        self.assertEqual(len(result.started), 2)
        self.assertEqual(result.testsRun, 2)

    def test_a_stopped_run_records_only_what_started(self):
        """The defect's shape: `shouldStop` set partway through.

        This is the only mechanism the report is consistent with -- same wall
        time, no crash, fewer tests run -- so the guard is checked against it
        directly rather than against a contrived short suite.
        """

        class Sample(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                # Whatever really does this in the live suite, the effect is
                # that the runner stops asking for more tests.
                self._outcome.result.shouldStop = True

            def test_c(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
        collected = list(self.driver._iter_test_ids(suite))
        runner = unittest.TextTestRunner(
            verbosity=0,
            stream=open(os.devnull, "w"),
            resultclass=self.driver._RecordingResult,
        )
        result = runner.run(suite)

        missing = self.driver._missing_tests(collected, result.started)
        self.assertEqual(len(collected), 3)
        self.assertTrue(result.wasSuccessful(), "fixture should not fail outright")
        self.assertTrue(missing, "a stopped run must be detectable as a truncation")
        self.assertTrue(missing[0].endswith("test_c"), missing)


class TestClassLevelSkipIsNotATruncation(DriverTestCase):
    """A class skipped in `setUpClass` never starts its tests, and that is FINE.

    ``unittest`` reports a ``setUpClass`` skip once, against a holder named
    ``setUpClass (<module>.<Class>)``, and calls ``startTest`` for none of the
    class's methods -- so a collected-vs-started comparison sees every one of
    them as never started. That is indistinguishable from a truncation by
    counting alone, and it is the NORMAL state here: ``TestAnimUtilsRealWorld``
    skips when its production FBX is absent, which it is in any checkout
    without the asset. Measured 2026-09-10: a full mayatk run reported
    ``0 failures, 1 errors`` and exited 1 purely on that skip, which would fail
    every release gate for a suite that had nothing wrong with it.
    """

    def _run(self, cls):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(cls)
        collected = list(self.driver._iter_test_ids(suite))
        runner = unittest.TextTestRunner(
            verbosity=0,
            stream=open(os.devnull, "w"),
            resultclass=self.driver._RecordingResult,
        )
        return collected, runner.run(suite)

    def test_a_setupclass_skip_reports_no_truncation(self):
        class Sample(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("asset not found")

            def test_a(self):
                pass

            def test_b(self):
                pass

        collected, result = self._run(Sample)
        self.assertEqual(len(collected), 2)
        self.assertEqual(result.started, [], "premise: startTest is never called")
        self.assertEqual(
            self.driver._missing_tests(collected, result.started, result), []
        )

    def test_a_decorator_skip_still_starts_and_stays_clean(self):
        """The other skip shape, which unittest DOES start -- no regression."""

        class Sample(unittest.TestCase):
            @unittest.skip("not today")
            def test_a(self):
                pass

        collected, result = self._run(Sample)
        self.assertEqual(len(result.started), 1)
        self.assertEqual(
            self.driver._missing_tests(collected, result.started, result), []
        )

    def test_a_real_truncation_is_still_caught_beside_a_skipped_class(self):
        """The forgiveness is scoped to the skipped class, not to the module."""

        class Skipped(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("asset not found")

            def test_skipped_one(self):
                pass

        class Live(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                self._outcome.result.shouldStop = True

            def test_c(self):
                pass

        suite = unittest.TestSuite(
            [
                unittest.defaultTestLoader.loadTestsFromTestCase(Skipped),
                unittest.defaultTestLoader.loadTestsFromTestCase(Live),
            ]
        )
        collected = list(self.driver._iter_test_ids(suite))
        runner = unittest.TextTestRunner(
            verbosity=0,
            stream=open(os.devnull, "w"),
            resultclass=self.driver._RecordingResult,
        )
        result = runner.run(suite)

        missing = self.driver._missing_tests(collected, result.started, result)
        self.assertTrue(missing, "the stopped class is still a truncation")
        self.assertTrue(
            all("Skipped" not in m for m in missing),
            f"a setUpClass skip must not be reported: {missing}",
        )
        self.assertTrue(missing[0].endswith("test_c"), missing)


class _WorkspaceCmds:
    """``maya.cmds`` as far as a project goes: ``workspace`` over one current
    root, remembering every root it was asked to open."""

    def __init__(self, root):
        self.root = root
        self.opened = []

    @staticmethod
    def about(**kwargs):
        """A batch session: ``EnvUtils.set_current_workspace`` opens a root with
        ``workspace -openWorkspace`` there (``setProject`` is the GUI's)."""
        return bool(kwargs.get("batch"))

    def workspace(self, *args, **kwargs):
        if kwargs.get("query") or kwargs.get("q"):
            return self.root
        if kwargs.get("openWorkspace"):
            self.root = args[0]
            self.opened.append(args[0])
        return None


class TestTheRunGivesTheProjectBack(DriverTestCase):
    """A run opens a throwaway project; it must reopen the one it found.

    The in-session harness (Script Editor: ``MayaTestRunner().run_tests``)
    runs ``run_suite`` inside the user's own Maya, which the run left on its
    ``policy="session"`` project -- a folder deleted when Maya exits, with
    whatever they had saved into its default folders in between.

    No Maya: ``maya.cmds`` is a stand-in holding one current root, and the
    run's other process-wide sandboxes are stood down for the test.
    """

    def setUp(self):
        from mayatk.env_utils import _env_utils

        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.project = os.path.normpath(project.name)
        self.cmds = _WorkspaceCmds(self.project)
        self.addCleanup(self._remove_sandboxes)
        maya = types.ModuleType("maya")
        maya.cmds = self.cmds
        for key, value in (("maya", maya), ("maya.cmds", self.cmds)):
            self._keep(sys.modules, key)
            sys.modules[key] = value
        self._keep(os.environ, "MAYATK_EXTENDED_TESTS")  # the run sets or clears it
        for patcher in (
            mock.patch.object(_env_utils, "cmds", self.cmds, create=True),
            mock.patch.object(
                self.driver, "_sandbox", lambda launched: contextlib.nullcontext()
            ),
            mock.patch.object(self.driver, "_ensure_sys_path"),
            mock.patch.object(self.driver, "_sandbox_shots_prefs"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        results = tempfile.TemporaryDirectory()
        self.addCleanup(results.cleanup)
        self.config = {
            "modules": [],
            "results_file": os.path.join(results.name, "results.txt"),
        }

    def _remove_sandboxes(self):
        for root in self.cmds.opened:
            if os.path.normpath(root) != self.project:
                shutil.rmtree(root, ignore_errors=True)

    def test_a_run_ends_on_the_project_it_found(self):
        self.driver.run_suite(self.config)

        self.assertEqual(len(self.cmds.opened), 2, self.cmds.opened)
        self.assertNotEqual(
            os.path.normpath(self.cmds.opened[0]), self.project, "no sandbox opened"
        )
        self.assertEqual(os.path.normpath(self.cmds.root), self.project)

    def test_an_aborted_run_still_gives_it_back(self):
        """An interrupt in the Script Editor, or a harness error between
        modules, ends the run as surely as its last module does."""
        self.config["modules"] = ["test_never_loaded"]
        with mock.patch.object(self.driver, "_progress", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.driver.run_suite(self.config)

        self.assertEqual(os.path.normpath(self.cmds.root), self.project)


class TestTheSandboxOutlivesOnlyALaunchedMaya(DriverTestCase):
    """Every run is sandboxed; only in a Maya the runner launched for good.

    A panel a test builds persists its widget state through QSettings, and the
    driver armed only pythontk's guards, so a run wrote its fixtures into the
    developer's live store. Measured 2026-10-04: a GUI pass of
    ``test_articulated_rig_panel`` rewrote ``chk_adjust``, ``txt_name`` and
    ``tbl_joints`` under ``HKCU\\Software\\uitk\\shared\\switchboard``.
    ``uitk.testing.TestSandbox.activate`` redirects that store for the rest of
    the process -- right for a process the runner launched (``launched``), and
    wrong for the user's own Maya (the in-session harness, ``--reuse``), which
    went on refusing browser launches and writing its settings, presets and
    temp files into a root deleted when it exits. There the run takes
    ``activated()``, every guard put back on the way out.

    No Qt: ``uitk.testing`` is a stand-in module and every activation a mock.
    """

    def setUp(self):
        testing = types.ModuleType("uitk.testing")
        testing.TestSandbox = mock.MagicMock()
        testing.TestSandbox.activate.return_value = ("<qsettings>", "<presets>")
        self.uitk_activate = testing.TestSandbox.activate
        self.uitk_activated = testing.TestSandbox.activated
        self.uitk_activated.return_value.__enter__.return_value = (
            "<qsettings>",
            "<presets>",
        )
        self._keep(sys.modules, "uitk.testing")
        sys.modules["uitk.testing"] = testing
        self._keep(os.environ, "MAYATK_EXTENDED_TESTS")  # the run sets or clears it
        for name in ("activate", "activated"):
            patcher = mock.patch(f"pythontk.core_utils.test_sandbox.TestSandbox.{name}")
            setattr(self, f"ptk_{name}", patcher.start())
            self.addCleanup(patcher.stop)
        for patcher in (
            mock.patch.object(self.driver, "_ensure_sys_path"),
            mock.patch.object(self.driver, "_sandbox_shots_prefs"),
            mock.patch.object(
                self.driver, "_sandbox_workspace", contextlib.nullcontext
            ),
            mock.patch.object(self.driver, "_run_modules", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, **config):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.driver.run_suite({"modules": [], "results_file": "-", **config})
        return printed.getvalue()

    def test_a_launched_maya_gets_the_settings_sandbox_for_good(self):
        self._run(launched=True)

        self.uitk_activate.assert_called_once_with()
        self.uitk_activated.assert_not_called()

    def test_the_users_own_session_gets_it_for_the_run_only(self):
        """Without the flag -- the in-session harness, ``--reuse``, or a config
        that predates it -- the driver assumes the user's own Maya."""
        self._run()
        self._run(launched=False)

        self.assertEqual(self.uitk_activated.call_count, 2)
        self.assertEqual(self.uitk_activated.return_value.__exit__.call_count, 2)
        self.uitk_activate.assert_not_called()
        self.ptk_activate.assert_not_called()

    def test_an_aborted_run_still_takes_the_sandbox_down(self):
        """An interrupt in the Script Editor, or a harness error between
        modules, ends the run as surely as its last module does."""
        self.driver._run_modules.side_effect = KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._run()

        self.uitk_activated.return_value.__exit__.assert_called_once()

    def test_without_uitk_the_run_keeps_pythontk_guards_and_says_so(self):
        sys.modules["uitk.testing"] = None  # the import raises; setUp restores it

        printed = self._run(launched=True)

        self.ptk_activate.assert_called_once_with()
        self.assertIn("settings sandbox unavailable", printed)

    def test_without_uitk_the_users_session_keeps_them_for_the_run_only(self):
        sys.modules["uitk.testing"] = None

        printed = self._run()

        self.ptk_activated.assert_called_once_with()
        self.ptk_activated.return_value.__exit__.assert_called_once()
        self.ptk_activate.assert_not_called()
        self.assertIn("settings sandbox unavailable", printed)


class TestTheRunGivesTheShotsPrefsBack(DriverTestCase):
    """A run points ``ShotStore``'s prefs at a scratch folder, then back.

    The prefs live in the user's cloud-synced store, so a run redirects them;
    the redirect used to stay, and in the user's own Maya (the in-session
    harness) their shot prefs went on landing in a folder deleted when Maya
    exits.
    """

    def setUp(self):
        from pythontk.core_utils.engines.shots.shot_model import ShotStore

        self.store = ShotStore
        self.addCleanup(
            setattr, ShotStore, "_prefs_dir_override", ShotStore._prefs_dir_override
        )
        self._keep(os.environ, "MAYATK_EXTENDED_TESTS")  # the run sets or clears it
        for patcher in (
            mock.patch.object(self.driver, "_ensure_sys_path"),
            mock.patch.object(
                self.driver, "_sandbox", lambda launched: contextlib.nullcontext()
            ),
            mock.patch.object(
                self.driver, "_sandbox_workspace", contextlib.nullcontext
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._remove_scratch)
        self.during = []

    def _remove_scratch(self):
        if self.driver._sandbox_dir:
            shutil.rmtree(self.driver._sandbox_dir, ignore_errors=True)

    def _run_modules(self, config):
        self.during.append(self.store._prefs_dir_override)
        return {}

    def _run(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.driver.run_suite({"modules": [], "results_file": "-"})

    def test_the_run_ends_on_the_prefs_it_found(self):
        self.store._prefs_dir_override = "<the user's own>"

        with mock.patch.object(self.driver, "_run_modules", self._run_modules):
            self._run()

        self.assertNotEqual(self.during, ["<the user's own>"], "never redirected")
        self.assertEqual(self.store._prefs_dir_override, "<the user's own>")

    def test_an_aborted_run_still_gives_them_back(self):
        self.store._prefs_dir_override = None

        with mock.patch.object(
            self.driver, "_run_modules", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._run()

        self.assertIsNone(self.store._prefs_dir_override)


class TestEachModuleImportsTheConftestBesideIt(DriverTestCase):
    """A module's ``import conftest`` resolves as pytest resolves it.

    The run's ``sys.path`` holds ``test/``, ``extended/`` and ``mock_tests/``
    (``_ensure_sys_path``), ``mock_tests/`` ahead, so a root module's
    ``from conftest import ...`` loaded the MOCK conftest, which plants mocked
    ``maya`` modules unconditionally, in a real Maya. Each module now gets the
    conftest nearest its own folder.
    """

    def setUp(self):
        tree = tempfile.TemporaryDirectory()
        self.addCleanup(tree.cleanup)
        self.root = Path(tree.name)
        for folder in ("mock_tests", "extended"):
            (self.root / folder).mkdir()
        (self.root / "conftest.py").write_text("WHICH = 'root'\n")
        (self.root / "mock_tests" / "conftest.py").write_text("WHICH = 'mock'\n")
        for module in ("test_root.py", "mock_tests/test_mock.py", "extended/test_x.py"):
            (self.root / module).write_text("from conftest import WHICH\n")
        self._keep(sys.modules, "conftest")
        # The driver's own order: mock_tests/ ahead of test/.
        for folder in (self.root, self.root / "mock_tests"):
            sys.path.insert(0, str(folder))
            self.addCleanup(sys.path.remove, str(folder))
        patcher = mock.patch.object(self.driver, "TEST_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _load(self, relative):
        path = str(self.root / relative)
        return self.driver._load_test_module(Path(relative).stem, path)

    def _which(self, relative):
        return self._load(relative).WHICH

    def _plant_from_the_mock_conftest(self):
        """Make the mock conftest plant a ``maya.cmds`` and its module report
        whether it sees it; returns the real modules' snapshot and the purge
        mock (the unit run must not drop the process's own mayatk)."""
        (self.root / "mock_tests" / "conftest.py").write_text(
            "import sys, types\n"
            "PLANTED = sys.modules['maya.cmds'] = types.ModuleType('maya.cmds')\n"
        )
        (self.root / "mock_tests" / "test_mock.py").write_text(
            "import sys\n"
            "from conftest import PLANTED\n"
            "ON_MOCKS = sys.modules.get('maya.cmds') is PLANTED\n"
        )
        keys = self.driver._REAL_MODULE_KEYS
        for key in keys:
            self._keep(sys.modules, key)
        patcher = mock.patch.object(self.driver, "_purge_mayatk_modules")
        purge = patcher.start()
        self.addCleanup(patcher.stop)
        return {k: sys.modules[k] for k in keys if k in sys.modules}, purge

    def test_a_conftest_that_plants_maya_runs_again_for_each_module(self):
        """The module loop puts the real ``maya`` back after every module
        (``_restore_real_maya_modules``), so mocks a conftest plants when it is
        imported must be planted again for the next module beside it: a copy
        cached for the run left every mock module after a chunk's first on the
        real ``maya``."""
        snapshot, _ = self._plant_from_the_mock_conftest()
        for run in ("first module", "second module"):
            self.assertTrue(self._load("mock_tests/test_mock.py").ON_MOCKS, run)
            self.driver._restore_real_maya_modules(snapshot)

    def test_a_conftest_that_plants_maya_has_mayatk_imported_again(self):
        """mayatk imported before the plant (``_reset_session_globals``, ahead
        of every module) is bound to the real ``maya``: a mock module then
        drove the real ``cmds`` -- 7 of test_script_job_manager's 44 failed
        under mayapy, all green in pytest. A conftest that plants nothing
        leaves mayatk alone."""
        snapshot, purge = self._plant_from_the_mock_conftest()
        self._load("mock_tests/test_mock.py")
        self.assertEqual(purge.call_count, 1)
        self.driver._restore_real_maya_modules(snapshot)
        self._load("test_root.py")
        self.assertEqual(purge.call_count, 1, "the root conftest purged mayatk")

    def test_the_module_s_own_fixtures_run(self):
        """unittest finds ``setUpModule`` / ``tearDownModule`` through
        ``sys.modules[test.__module__]`` and skips them silently when the module
        is not there: a module loaded by path but not registered ran without
        them (test_scene_data_sidecar's sandbox, the mock suites' skips)."""
        (self.root / "test_root.py").write_text(
            "import unittest\n"
            "RAN = []\n"
            "def setUpModule():\n    RAN.append('setUp')\n"
            "def tearDownModule():\n    RAN.append('tearDown')\n"
            "class T(unittest.TestCase):\n    def test(self):\n        pass\n"
        )
        self._keep(sys.modules, "test_root")
        module = self._load("test_root.py")
        tests = unittest.defaultTestLoader.loadTestsFromTestCase(module.T)
        unittest.TestSuite([tests]).run(unittest.TestResult())
        self.assertEqual(module.RAN, ["setUp", "tearDown"])

    def test_a_conftest_that_plants_nothing_loads_once(self):
        """The root conftest stands up the run's sandbox: one load per run."""
        (self.root / "test_root.py").write_text("import conftest\nCONFTEST = conftest\n")
        first = self._load("test_root.py").CONFTEST
        self.assertIs(self._load("test_root.py").CONFTEST, first)

    def test_a_root_module_gets_the_root_conftest(self):
        self.assertEqual(self._which("test_root.py"), "root")

    def test_a_mock_module_gets_its_own(self):
        self.assertEqual(self._which("mock_tests/test_mock.py"), "mock")
        self.assertEqual(self._which("test_root.py"), "root", "and back again")

    def test_a_folder_without_one_gets_the_nearest_above(self):
        self._which("mock_tests/test_mock.py")
        self.assertEqual(self._which("extended/test_x.py"), "root")


class TestTheUsersSessionGetsItsExportHooksBack(DriverTestCase):
    """The per-module reset takes the session's export hooks down; in the
    user's own Maya, the run puts them back.

    ``_reset_session_globals`` disables every FBX export producer and stager,
    the explicit auto-takes, and the active ShotStore before each module --
    in the in-session harness, the USER's: their scene's next export after a
    run lost its shot takes. So did ``MAYATK_EXTENDED_TESTS``, which the run
    set or cleared for good. Stand-ins for ``FbxUtils``/``ShotStore``; the
    driver's real reset runs between them.
    """

    def setUp(self):
        class FbxUtils:
            _session_producers = {"shots"}
            _session_stagers = {"takes": ("<prepare>", "<finish>")}
            _explicit_auto_takes = True
            synced = []

            @classmethod
            def disable_export_producer(cls, key):
                cls._session_producers.discard(key)

            @classmethod
            def unregister_export_stager(cls, name):
                cls._session_stagers.pop(name, None)

            @classmethod
            def disable_auto_takes(cls):
                cls._explicit_auto_takes = False

            @classmethod
            def _sync_auto_export_hook(cls):
                cls.synced.append(
                    (set(cls._session_producers), cls._explicit_auto_takes)
                )

            _bracket_state = staticmethod(dict)
            reset_takes = reset_export = staticmethod(lambda: None)

        class ShotStore:
            _active = "<the user's store>"
            _auto_export_disabled = True

            @classmethod
            def clear_active(cls):
                cls._active = None

            @classmethod
            def set_active(cls, store):
                cls._active = store

        self.fbx, self.shots = FbxUtils, ShotStore
        modules = {}
        for name, attr, value in (
            ("mayatk.env_utils.fbx_utils", "FbxUtils", FbxUtils),
            ("mayatk.anim_utils.shots._shots", "ShotStore", ShotStore),
        ):
            modules[name] = types.ModuleType(name)
            setattr(modules[name], attr, value)
        for patcher in (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(self.driver, "_ensure_sys_path"),
            mock.patch.object(
                self.driver,
                "_sandbox",
                lambda launched: contextlib.nullcontext(mock.Mock()),
            ),
            mock.patch.object(
                self.driver, "_sandbox_workspace", contextlib.nullcontext
            ),
            mock.patch.object(self.driver, "_sandbox_shots_prefs"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self._keep(os.environ, "MAYATK_EXTENDED_TESTS")
        os.environ["MAYATK_EXTENDED_TESTS"] = "the user's"
        self.during = {}

    def _modules(self, config):
        self.driver._reset_session_globals()  # as before every module
        self.during.update(
            producers=set(self.fbx._session_producers),
            active=self.shots._active,
            env=os.environ.get("MAYATK_EXTENDED_TESTS"),
        )
        return {}

    def _run(self, **config):
        with mock.patch.object(self.driver, "_run_modules", self._modules):
            with contextlib.redirect_stdout(io.StringIO()):
                self.driver.run_suite({"modules": [], "results_file": "-", **config})

    def _assert_given_back(self):
        self.assertEqual(self.fbx._session_producers, {"shots"})
        self.assertEqual(
            self.fbx._session_stagers, {"takes": ("<prepare>", "<finish>")}
        )
        self.assertTrue(self.fbx._explicit_auto_takes)
        self.assertEqual(self.fbx.synced[-1], ({"shots"}, True), "the hook follows")
        self.assertEqual(self.shots._active, "<the user's store>")
        self.assertTrue(self.shots._auto_export_disabled)
        self.assertEqual(os.environ.get("MAYATK_EXTENDED_TESTS"), "the user's")

    def test_an_in_session_run_gives_them_back(self):
        self._run()

        self.assertEqual(
            self.during, {"producers": set(), "active": None, "env": None}
        )  # the reset did run
        self._assert_given_back()

    def test_an_aborted_run_gives_them_back_too(self):
        def interrupted(config):
            self._modules(config)
            raise KeyboardInterrupt

        with mock.patch.object(self.driver, "_run_modules", interrupted):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    self.driver.run_suite({"modules": [], "results_file": "-"})
        self._assert_given_back()

    def test_a_launched_maya_is_left_as_the_reset_left_it(self):
        self._run(launched=True)

        self.assertEqual(self.fbx._session_producers, set())


class TestTheReloadKeepsTheTrashGuard(DriverTestCase):
    """An in-session run reloads pythontk inside its sandbox; the guards hold.

    The user's long-lived Maya may hold stale modules, so the in-session run
    reloads pythontk after the sandbox is up -- which re-executed ``FileUtils``
    without its trash guard, and a re-bake test sent scratch maps to the user's
    Recycle Bin. The driver re-arms the sandbox after the reload.

    A fresh interpreter, the real sandbox and the real reload (a reload here
    would fork every class this process holds); probed by guard identity,
    never by trashing a file.
    """

    PROBE = r"""
import contextlib, importlib.util, json, os, sys
sys.path[:0] = [{pythontk!r}, {uitk!r}, {mayatk!r}]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
spec = importlib.util.spec_from_file_location("_driver", {driver!r})
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
driver._ensure_sys_path = lambda: None
driver._sandbox_workspace = contextlib.nullcontext
driver._sandbox_shots_prefs = lambda temp_dir: contextlib.nullcontext()

def guard():
    from pythontk.file_utils._file_utils import FileUtils
    return FileUtils.__dict__["move_to_trash"].__func__.__name__

report = {{}}

def probe(config):
    import pythontk as ptk
    report["during"] = guard()
    report["active"] = ptk.TestSandbox.is_active()
    return {{}}

driver._run_modules = probe
with contextlib.redirect_stdout(sys.stderr):
    driver.run_suite({{"modules": [], "results_file": "-", "reload": True}})
report["after"] = guard()
print(json.dumps(report))
"""

    def test_the_trash_stays_guarded_after_the_reload_then_comes_back(self):
        import json
        import subprocess

        mono = TEST_DIR.parents[1]
        code = self.PROBE.format(
            pythontk=str(mono / "pythontk"),
            uitk=str(mono / "uitk"),
            mayatk=str(mono / "mayatk"),
            driver=str(TEST_DIR / "_suite_driver.py"),
        )
        from base_test import child_python

        proc = subprocess.run(
            [child_python(), "-c", code], capture_output=True, text=True, timeout=300
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        report = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(
            report, {"during": "no_trash", "active": True, "after": "move_to_trash"}
        )


class TestTheDriverBeatsPerTest(DriverTestCase):
    """Every test the driver runs leaves a ``TEST <id>`` heartbeat on the
    progress stream first -- what the runner's stall clocks follow, so a
    module longer than the stall window is not taken for a hung one."""

    def test_each_test_start_is_a_heartbeat(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        progress = Path(root.name) / "beats.progress"

        class _Two(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass

        result = unittest.TextTestRunner(
            stream=io.StringIO(),
            resultclass=self.driver.functools.partial(
                self.driver._RecordingResult, progress_file=str(progress)
            ),
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(_Two))
        self.assertTrue(result.wasSuccessful())
        beats = progress.read_text(encoding="utf-8").split()
        self.assertEqual(beats[0::2], ["TEST", "TEST"])
        self.assertEqual(
            [b.rsplit(".", 1)[-1] for b in beats[1::2]], ["test_a", "test_b"]
        )


if __name__ == "__main__":
    unittest.main()
