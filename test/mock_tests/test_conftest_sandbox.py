# coding=utf-8
"""This directory's conftest keeps a direct run off the developer's live stores.

A direct run (``python mock_tests/test_x.py``) loads only this conftest, never
the parent one, so it must redirect both of uitk's per-user stores itself:
``QSettings`` (the registry on Windows) and the presets root. It redirected
the first and not the second, and the panel tests here save presets.

A fresh interpreter loads this conftest alone and reports where both stores
resolve; a ``QSettings`` that is only constructed reads and writes nothing.
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MONO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

PROBE = r"""
import importlib.util, json, os, sys
sys.path[:0] = [os.path.join({mono!r}, p) for p in ("pythontk", "uitk", "mayatk")]
spec = importlib.util.spec_from_file_location("conftest", {conftest!r})
spec.loader.exec_module(importlib.util.module_from_spec(spec))
import pythontk as ptk
from qtpy import QtCore

store = QtCore.QSettings("uitk", "shared")
print(json.dumps({{
    "root": ptk.TestSandbox.temp(),
    "presets": str(ptk.UserConfig.user_config_root()),
    "qsettings": store.fileName(),
}}))
"""


class TestADirectRunIsSandboxed(unittest.TestCase):
    def test_both_stores_resolve_inside_the_runs_temp_root(self):
        try:
            import qtpy  # noqa: F401
        except Exception:
            self.skipTest("no Qt binding: no QSettings store to reach")
        code = PROBE.format(mono=MONO, conftest=os.path.join(HERE, "conftest.py"))
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        env.pop("UITK_PRESETS_ROOT", None)  # nothing inherited from this run
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        report = json.loads(proc.stdout.splitlines()[-1])
        root = os.path.normcase(os.path.abspath(report["root"])) + os.sep
        for store in ("presets", "qsettings"):
            with self.subTest(store=store):
                where = os.path.normcase(os.path.abspath(report[store]))
                self.assertTrue(where.startswith(root), f"{store}: {report[store]}")


if __name__ == "__main__":
    unittest.main()
