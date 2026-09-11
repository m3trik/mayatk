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

Pure Python: the functions under test take ids, not a Maya session, so this
runs anywhere.
"""

import importlib.util
import inspect
import os
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
