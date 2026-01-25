import unittest
import sys
import os

try:
    from qtpy import QtWidgets, QtCore

    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)
except ImportError:
    pass

import pymel.core as pm

# Adjust path to find base_test
test_dir = r"O:\Cloud\Code\_scripts\mayatk\test"
if test_dir not in sys.path:
    sys.path.append(test_dir)
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.append(scripts_dir)

from base_test import MayaTkTestCase
from mayatk.core_utils.preview import Preview


class MockOperation:
    def __init__(self):
        self.perform_count = 0

    def perform_operation(self, objects):
        self.perform_count += 1
        for obj in objects:
            pm.move(obj, 0, 1, 0, r=True)


class TestPreviewInteractive(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="test_cube")[0]
        self.window = QtWidgets.QMainWindow()
        self.chk = QtWidgets.QCheckBox("Preview")
        self.btn = QtWidgets.QPushButton("Create")
        self.op = MockOperation()
        self.preview = Preview(
            self.op, self.chk, self.btn, message_func=lambda msg: None
        )

    def tearDown(self):
        try:
            self.preview.disable()
        except:
            pass
        if hasattr(self, "window"):
            self.window.close()
        super().tearDown()

    def test_interactive_refresh_does_not_disable(self):
        """
        Simulate an interactive refresh loop where undo event might fire late.
        The tool must NOT disable itself.
        """
        pm.select(self.cube)
        self.preview.enable()  # Y=1
        self.assertTrue(self.chk.isChecked())

        # Simulate interaction: Refresh called.
        # This performs Undo (Y=0), then New Op (Y=1).
        self.preview.refresh()

        # Verify maintained state
        self.assertTrue(self.chk.isChecked(), "Preview disabled itself during refresh!")
        self.assertAlmostEqual(self.cube.getTranslation(space="world").y, 1.0)

        # Simulate LATE ScriptJob
        # If refresh happened, redo stack should be empty because we pushed new ops.
        # So manual invocation of disable_on_external_undo should return EARLY.
        self.preview.disable_on_external_undo()

        # Verify still enabled
        self.assertTrue(
            self.chk.isChecked(), "Preview disabled itself upon late undo event!"
        )

    def test_manual_undo_still_disables(self):
        """
        Verify that ACTUAL manual undo still disables the tool.
        """
        pm.select(self.cube)
        self.preview.enable()
        self.preview.refresh()

        # In this test environment, scriptJobs might not fire automatically.
        # refresh() caused an internal undo, incrementing event counter.
        # We must simulate that event being consumed if it wasn't already.
        if self.preview.expected_undo_events > 0:
            self.preview.disable_on_external_undo()

        # Manual Undo
        pm.undo()
        # Trigger scriptjob (Simulation of the manual undo event)
        self.preview.disable_on_external_undo()

        # Should be disabled
        self.assertFalse(self.chk.isChecked())
        # Should NOT double undo (Y should be 0, not -1 or something, wait prev pos was 0)
        self.assertAlmostEqual(self.cube.getTranslation(space="world").y, 0.0)


if __name__ == "__main__":
    unittest.main()
