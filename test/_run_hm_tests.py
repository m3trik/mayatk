"""Minimal runner for test_hierarchy_manager.py under mayapy."""
import sys
import os
import io
import unittest

# Set CWD to mayatk root
MAYATK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(MAYATK_ROOT)

# Ensure packages are importable
sys.path.insert(0, MAYATK_ROOT)
for dep in ("pythontk", "uitk"):
    dep_path = os.path.join(MAYATK_ROOT, "..", dep)
    if os.path.isdir(dep_path):
        sys.path.insert(0, os.path.abspath(dep_path))

# Create QApplication before Maya standalone
from qtpy import QtWidgets

if QtWidgets.QApplication.instance() is None:
    QtWidgets.QApplication([])

# Import the test module directly from path
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "test_hierarchy_manager",
    os.path.join(MAYATK_ROOT, "test", "test_hierarchy_manager.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Load all test classes from the module
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for name in dir(_mod):
    obj = getattr(_mod, name)
    if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase:
        suite.addTests(loader.loadTestsFromTestCase(obj))

buf = io.StringIO()
runner = unittest.TextTestRunner(stream=buf, verbosity=0)
res = runner.run(suite)

print(
    f"SUMMARY: Tests={res.testsRun} Fail={len(res.failures)} "
    f"Err={len(res.errors)} Skip={len(res.skipped)}",
    flush=True,
)
for t, tb in res.failures[:5]:
    print(f"FAIL: {t}", flush=True)
    print(tb[-400:], flush=True)
for t, tb in res.errors[:5]:
    print(f"ERR: {t}", flush=True)
    print(tb[-400:], flush=True)
