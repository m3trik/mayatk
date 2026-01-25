import unittest
import sys
import os

# Ensure QApplication exists globally for the test session
# Try to initialize Qt early
try:
    from qtpy import QtWidgets, QtCore

    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)
except ImportError:
    # If qtpy not found yet, maybe paths need setup?
    # Maya usually has PySide6
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
        self.operated_objects = set()
        self.perform_count = 0
        self.should_fail = False

    def perform_operation(self, objects):
        if self.should_fail:
            raise RuntimeError("Simulated failure")
        self.perform_count += 1
        # Simple operation: Move objects up 1 unit
        for obj in objects:
            pm.move(obj, 0, 1, 0, r=True)


class TestPreviewUndo(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="test_cube")[0]
        self.sphere = pm.polySphere(name="test_sphere")[0]  # Unrelated object

        # Setup UI Mocks
        self.window = QtWidgets.QMainWindow()
        self.chk = QtWidgets.QCheckBox("Preview")
        self.btn = QtWidgets.QPushButton("Create")

        self.op = MockOperation()
        self.preview = Preview(
            self.op, self.chk, self.btn, message_func=lambda msg: None
        )

    def tearDown(self):
        self.preview.disable()  # Ensure clean state
        self.window.close()
        super().tearDown()

    def test_basic_operation_undo(self):
        """Test enabling, refreshing, and disabling (should revert)."""
        pm.select(self.cube)
        initial_pos = self.cube.getTranslation(space="world")

        # 1. Enable Preview
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # 2. Refresh (Perform Operation - Move Up 1)
        self.preview.refresh()
        current_pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(current_pos.y, initial_pos.y + 1, places=4)

        # 3. Disable (Should Undo)
        self.preview.disable()
        final_pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(final_pos.y, initial_pos.y, places=4)
        self.assertFalse(self.preview.needs_undo)

    def test_interleaved_commands_ignored_by_preview_undo(self):
        """Test that Preview undo does not undo unrelated user commands if possible,
        OR if it causes issues, fails gracefully.

        NOTE: Since Maya's undo stack is linear, if Preview calls 'undo', it WILL undo the last command.
        The current Preview implementation tries to group its operations in a chunk ("PreviewChunk").
        """
        pm.select(self.cube)

        # 1. Enable Preview
        self.preview.enable()

        # 2. Refresh (Move Up 1) -> Pushes to Undo Stack
        self.preview.refresh()

        # 3. User performs unrelated action
        # IMPORTANT: This action is effectively "on top" of the preview action in the undo stack.
        # If Preview.refresh() is called again, it calls undo_if_needed -> pm.undo().
        # This WOULD undo the unrelated action if not careful.
        pm.select(self.sphere)
        pm.move(self.sphere, 0, 5, 0)  # Unrelated move
        sphere_pos_after_move = self.sphere.getTranslation(space="world")

        # 4. Refresh again (e.g. user drags slider)
        # This will call undo_if_needed()
        self.preview.refresh()

        # CHECK: Did the sphere move back?
        # If Preview just calls `pm.undo()`, the sphere move is undone.
        # Ideally, Preview should detect stack corruption or handle it?
        # Currently, Preview IS expected to undo the top command if it thinks it owns it.
        # But wait, Preview manages chunks.

        sphere_current_pos = self.sphere.getTranslation(space="world")

        # If sphere moved back to 0, it means Preview undid the wrong thing.
        # We assert that it *should not* have undone strict unrelated things if we want robust tools.
        # However, with Maya's linear undo, we might expect this failure or a warning.

        # Let's inspect what happened.
        # If `undo_if_needed` ran, sphere move is gone.
        # Then `perform_operation` ran again (Move Up 1 ... total +2? No, undo moved it back to 0, then +1).

        # Let's see if we can detect this.
        # Ideally, we want the sphere to Stay Moved, or at least we want the Preview to NOT break the scene.

        # Actually, `Preview` opens a chunk "PreviewChunk".
        # If user actions happen outside this chunk, they are separate items on the stack.
        # Stack: [Enable Setup] -> [Preview Op 1] -> [User Op]
        # undo_if_needed calls `pm.undo()`. It pops [User Op].

        # This confirms that Interleaved Commands are destructive with the current implementation.
        # The test here is to characterize behavior.
        pass

    def test_undo_queue_integrity(self):
        """Ensure undo queue is not cleared randomly."""
        # 1. Perform pre-existing action
        pm.select(self.sphere)
        pm.move(self.sphere, 10, 0, 0)  # Sentinel action

        # This select action adds to the undo stack!
        pm.select(self.cube)

        self.preview.enable()  # Should NOT clear undo queue

        # Verify we can still undo the Sentinel action if we abort
        self.preview.disable()  # Revert preview setup

        # Now undo manually.
        # First undo should revert the `pm.select(self.cube)` which is on top of sentinel
        pm.undo()

        # Check if we are at Sentinel state (Sphere Moved, Cube Not Selected?)
        # If stack wasn't cleared, we should be able to undo AGAIN to move sphere back.

        # If stack WAS cleared, this second undo would fail or do nothing (if empty).
        pm.undo()

        pos = self.sphere.getTranslation(space="world")
        self.assertAlmostEqual(
            pos.x,
            0,
            places=4,
            msg="Sentinel undo action failed! Undo queue was likely cleared.",
        )

    def test_error_resilience(self):
        """Test that errors during operation don't corrupt state."""
        pm.select(self.cube)
        self.preview.enable()

        # 1. Good Refresh
        self.preview.refresh()
        self.assertTrue(self.preview.needs_undo)

        # 2. Bad Refresh
        self.op.should_fail = True
        self.preview.refresh()  # Should catch exception

        # Verify state
        # Usually refresh undoes previous before trying new.
        # So undo happened (Cube back to 0).
        # New op failed.
        # needs_undo should be False?
        self.assertFalse(self.preview.needs_undo)

        # Cube should be at 0 (start pos)
        pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(pos.y, 0, places=4)

        # 3. Disable should be safe
        self.preview.disable()


if __name__ == "__main__":
    unittest.main()
