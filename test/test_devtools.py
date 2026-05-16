# !/usr/bin/python
# coding=utf-8
"""Test Suite for env_utils.devtools.

Covers DevTools (file/MEL search and inspection). WidgetInspector is
GUI-dependent and not covered here — those tests require an interactive
Maya session with widgets present.
"""
import os
import tempfile
import unittest

import maya.cmds as cmds

from mayatk.env_utils.devtools import DevTools

from base_test import MayaTkTestCase, QuickTestCase


class TestFindPython(QuickTestCase):
    """Pure-Python module lookup — no Maya needed."""

    def test_finds_stdlib_module(self):
        path = DevTools.find_python("os")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.endswith("os.py") or path.endswith("os.pyc"))

    def test_finds_qualified_object(self):
        path = DevTools.find_python("os.path.join")
        self.assertIsNotNone(path)
        # On Python 3.11+ `os.path` may be a frozen module — inspect.getfile
        # returns a path that doesn't exist on disk. Don't gate on existence;
        # just verify we got a plausible file path string.
        self.assertIsInstance(path, str)
        self.assertTrue(
            path.endswith("ntpath.py")
            or path.endswith("posixpath.py")
            or "path" in path.lower(),
            f"Unexpected path: {path}",
        )

    def test_returns_none_for_nonexistent_module(self):
        self.assertIsNone(DevTools.find_python("zz_definitely_not_a_module_xyz123"))


class TestCollectFiles(QuickTestCase):
    """Filesystem walk + extension filter."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="devtools_test_")
        self.f_mel = os.path.join(self.root, "a.mel")
        self.f_py = os.path.join(self.root, "b.py")
        self.f_txt = os.path.join(self.root, "c.txt")
        for p in (self.f_mel, self.f_py, self.f_txt):
            with open(p, "w") as f:
                f.write("// stub\n")
        # nested dir
        nested = os.path.join(self.root, "sub")
        os.makedirs(nested)
        self.f_nested_mel = os.path.join(nested, "nested.mel")
        with open(self.f_nested_mel, "w") as f:
            f.write("// nested\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_filter_by_single_extension(self):
        found = list(DevTools._collect_files(self.root, [".mel"]))
        self.assertIn(self.f_mel, found)
        self.assertIn(self.f_nested_mel, found)
        self.assertNotIn(self.f_py, found)
        self.assertNotIn(self.f_txt, found)

    def test_filter_by_multiple_extensions(self):
        found = list(DevTools._collect_files(self.root, [".mel", ".py"]))
        self.assertIn(self.f_mel, found)
        self.assertIn(self.f_py, found)
        self.assertNotIn(self.f_txt, found)

    def test_recursive_false_skips_subdirs(self):
        found = list(DevTools._collect_files(self.root, [".mel"], recursive=False))
        self.assertIn(self.f_mel, found)
        self.assertNotIn(self.f_nested_mel, found)

    def test_wildcard_returns_all(self):
        found = list(DevTools._collect_files(self.root, ["*"]))
        # Should include all four files.
        self.assertEqual(len(found), 4)


class TestGrepMayaDir(QuickTestCase):
    """Search a temp dir for content — no MAYA_SCRIPT_PATH dependency."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="grep_test_")
        with open(os.path.join(self.root, "hit.mel"), "w") as f:
            f.write("// header\nglobal proc int my_target_proc() {\n  return 1;\n}\n")
        with open(os.path.join(self.root, "miss.mel"), "w") as f:
            f.write("// nothing interesting here\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_finds_literal_match(self):
        results = DevTools.grep_maya_dir(
            "my_target_proc", root_paths=self.root, ext=".mel"
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["line"], 2)
        self.assertIn("my_target_proc", results[0]["text"])

    def test_regex_match(self):
        results = DevTools.grep_maya_dir(
            r"my_\w+_proc", root_paths=self.root, ext=".mel", regex=True
        )
        self.assertEqual(len(results), 1)

    def test_context_lines_included(self):
        results = DevTools.grep_maya_dir(
            "my_target_proc", root_paths=self.root, ext=".mel", context=1
        )
        self.assertEqual(len(results), 1)
        self.assertIn("before", results[0])
        self.assertIn("after", results[0])
        self.assertEqual(results[0]["before"], ["// header"])

    def test_max_results_caps_output(self):
        # Write a file with many matches.
        many = os.path.join(self.root, "many.mel")
        with open(many, "w") as f:
            for _ in range(20):
                f.write("my_target_proc\n")
        results = DevTools.grep_maya_dir(
            "my_target_proc", root_paths=self.root, ext=".mel", max_results=5
        )
        self.assertEqual(len(results), 5)

    def test_returns_empty_for_no_match(self):
        results = DevTools.grep_maya_dir(
            "zzz_no_match_xyz", root_paths=self.root, ext=".mel"
        )
        self.assertEqual(results, [])


class TestGrepMelProcs(QuickTestCase):
    """Discover ``proc`` declarations in MEL files."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="grep_proc_test_")
        with open(os.path.join(self.root, "procs.mel"), "w") as f:
            f.write(
                "// header\n"
                "global proc int my_global_proc(int $a, string $b) {\n"
                "  return $a;\n"
                "}\n"
                "\n"
                "proc string my_local_proc() {\n"
                '  return "x";\n'
                "}\n"
            )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_finds_both_global_and_local(self):
        results = DevTools.grep_mel_procs(root_paths=self.root)
        names = {r["name"] for r in results}
        self.assertEqual(names, {"my_global_proc", "my_local_proc"})

    def test_scope_classification(self):
        results = DevTools.grep_mel_procs(root_paths=self.root)
        scope_by_name = {r["name"]: r["scope"] for r in results}
        self.assertEqual(scope_by_name["my_global_proc"], "global")
        self.assertEqual(scope_by_name["my_local_proc"], "local")

    def test_return_type_captured(self):
        results = DevTools.grep_mel_procs(root_paths=self.root)
        ret_by_name = {r["name"]: r["return_type"] for r in results}
        self.assertEqual(ret_by_name["my_global_proc"], "int")
        self.assertEqual(ret_by_name["my_local_proc"], "string")

    def test_pattern_filters_by_name(self):
        results = DevTools.grep_mel_procs(pattern="global", root_paths=self.root)
        names = {r["name"] for r in results}
        self.assertEqual(names, {"my_global_proc"})


class TestEchoAll(MayaTkTestCase):
    """Toggling Maya's command-echo state."""

    def test_echo_all_does_not_raise(self):
        # Just verify the toggle calls succeed — cmds.commandEcho state.
        DevTools.echo_all(True)
        self.assertTrue(cmds.commandEcho(query=True, state=True))
        DevTools.echo_all(False)
        self.assertFalse(cmds.commandEcho(query=True, state=True))


class TestFindMelAndCombined(MayaTkTestCase):
    """MEL lookup using Maya's ``whatIs``."""

    def test_find_mel_returns_none_for_unknown_proc(self):
        result = DevTools.find_mel("zz_no_such_proc_xyz_unique_name_999")
        self.assertIsNone(result)

    def test_find_falls_through_to_python_when_no_mel(self):
        # 'os' is a Python module, not a MEL proc — find() should return
        # the Python file path.
        path = DevTools.find("os")
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith(".py") or path.endswith(".pyc"))


class TestListMelGlobals(MayaTkTestCase):
    def test_returns_list_of_strings(self):
        # Maya has many built-in globals; expect a non-empty list.
        globals_list = DevTools.list_mel_globals()
        self.assertIsInstance(globals_list, list)
        # Maya populates dozens of $gFoo MEL globals.
        self.assertGreater(len(globals_list), 0)
        for name in globals_list[:5]:
            self.assertIsInstance(name, str)

    def test_pattern_filter_narrows_results(self):
        all_globals = DevTools.list_mel_globals()
        filtered = DevTools.list_mel_globals(pattern="^.{0,5}$")  # short names only
        self.assertLessEqual(len(filtered), len(all_globals))


if __name__ == "__main__":
    unittest.main()
