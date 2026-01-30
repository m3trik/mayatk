# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils.preview module

Tests for Preview class functionality including:
- Basic enable/disable/finalize operations
- Selection change detection and undo sync protection
- External undo detection
- Error resilience
- Undo queue integrity
"""
import unittest
import pymel.core as pm

try:
    from qtpy import QtWidgets
except ImportError:
    QtWidgets = None

from base_test import MayaTkTestCase
from mayatk.core_utils.preview import Preview


class MockOperation:
    """Mock operation class for testing Preview."""

    def __init__(self):
        self.operated_objects = set()
        self.perform_count = 0
        self.should_fail = False

    def perform_operation(self, objects):
        if self.should_fail:
            raise RuntimeError("Simulated failure")
        self.perform_count += 1
        for obj in objects:
            pm.move(obj, 0, 1, 0, r=True)


@unittest.skipIf(QtWidgets is None, "Qt not available")
class TestPreview(MayaTkTestCase):
    """Tests for Preview class undo sync protection."""

    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="test_cube")[0]
        self.sphere = pm.polySphere(name="test_sphere")[0]

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

    # -------------------------------------------------------------------------
    # Basic Operation Tests
    # -------------------------------------------------------------------------

    def test_preview_excluded_from_restore(self):
        """Verify that the preview checkbox is marked to skip state restoration.

        Bug: Preview checkbox state was being restored, causing previews to
        trigger unexpectedly on UI load.
        Fixed: 2026-01-30
        """
        # restore_state attribute is set during __init__
        self.assertTrue(
            hasattr(self.chk, "restore_state"),
            "Checkbox missing restore_state attribute",
        )
        self.assertFalse(
            self.chk.restore_state,
            "Checkbox should be excluded from state restoration (restore_state=False)",
        )

    def test_enable_disable_cycle(self):
        """Test basic enable/disable reverts the operation."""
        pm.select(self.cube)
        initial_pos = self.cube.getTranslation(space="world")

        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        current_pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(current_pos.y, initial_pos.y + 1, places=4)

        self.preview.disable()
        final_pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(final_pos.y, initial_pos.y, places=4)
        self.assertFalse(self.preview.needs_undo)

    def test_disable_always_undoes_operation(self):
        """Test that disable ALWAYS undoes the operation regardless of how it's called.

        This verifies that whether disable() is called from:
        - User unchecking the checkbox
        - Selection change detection
        - External undo detection
        The operation is properly reverted.
        """
        pm.select(self.cube)
        initial_pos = self.cube.getTranslation(space="world")

        self.preview.enable()
        self.assertTrue(self.preview.needs_undo)

        # Simulate selection change calling disable
        self.preview.disable()

        final_pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(
            final_pos.y,
            initial_pos.y,
            places=4,
            msg="Operation was NOT undone when disable() was called",
        )

    def test_finalize_preserves_changes(self):
        """Test finalize_changes keeps the operation applied."""
        pm.select(self.cube)
        initial_pos = self.cube.getTranslation(space="world")

        self.preview.enable()
        self.preview.finalize_changes()

        final_pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(final_pos.y, initial_pos.y + 1, places=4)

    def test_refresh_updates_preview(self):
        """Test refresh applies operation again after undo."""
        pm.select(self.cube)
        self.preview.enable()

        # First operation moves Y+1
        pos_after_enable = self.cube.getTranslation(space="world")

        # Refresh should undo then redo (net effect: still Y+1)
        self.preview.refresh()

        pos_after_refresh = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(pos_after_enable.y, pos_after_refresh.y, places=4)

    def test_multiple_refresh_calls_keep_checkbox_checked(self):
        """Test that calling refresh multiple times keeps preview enabled.

        This simulates changing slider values in the UI - each value change
        triggers refresh(), but the checkbox should remain checked.
        """
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # Simulate multiple value changes (like moving a slider)
        for i in range(5):
            self.preview.refresh()
            self.assertTrue(
                self.chk.isChecked(), f"Checkbox was unchecked after refresh #{i+1}"
            )

        # Verify operation is still undoable
        self.assertTrue(self.preview.needs_undo)

    def test_refresh_does_not_trigger_selection_change_disable(self):
        """Test that refresh doesn't incorrectly trigger selection change detection.

        Some Maya operations clear the selection. The preview should handle this
        by temporarily disabling selection monitoring during refresh.
        """
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # Mock an operation that clears selection (like polyBevel does)
        original_perform = self.op.perform_operation

        def selection_clearing_operation(objects):
            original_perform(objects)
            pm.select(clear=True)  # Simulate Maya clearing selection

        self.op.perform_operation = selection_clearing_operation

        # This should NOT uncheck the preview
        self.preview.refresh()

        self.assertTrue(
            self.chk.isChecked(),
            "Preview was disabled due to selection change during refresh",
        )

        # Restore original
        self.op.perform_operation = original_perform

    # -------------------------------------------------------------------------
    # Selection Change Tests
    # -------------------------------------------------------------------------

    def test_selection_change_disables_preview(self):
        """Test that changing selection disables preview."""
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # Simulate selection change by calling the handler directly
        pm.select(self.sphere)
        self.preview.disable_on_selection_change()

        self.assertFalse(self.chk.isChecked())

    def test_selection_cleared_disables_preview(self):
        """Test that clearing selection disables preview."""
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        pm.select(clear=True)
        self.preview.disable_on_selection_change()

        self.assertFalse(self.chk.isChecked())

    def test_same_selection_does_not_disable(self):
        """Test that re-selecting the same objects does not disable preview."""
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # Re-select same object
        pm.select(self.cube)
        self.preview.disable_on_selection_change()

        # Should still be enabled
        self.assertTrue(self.chk.isChecked())

    def test_selection_change_during_refresh_ignored(self):
        """Test that selection changes during refresh are ignored."""
        pm.select(self.cube)
        self.preview.enable()

        # Simulate being in refresh
        self.preview.is_refreshing = True
        pm.select(self.sphere)
        self.preview.disable_on_selection_change()

        # Should still be enabled because we were refreshing
        self.assertTrue(self.chk.isChecked())
        self.preview.is_refreshing = False

    def test_adding_to_selection_disables_preview(self):
        """Test that adding objects to selection disables preview."""
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # Add sphere to selection
        pm.select(self.sphere, add=True)
        self.preview.disable_on_selection_change()

        self.assertFalse(self.chk.isChecked())

    def test_removing_from_selection_disables_preview(self):
        """Test that removing objects from selection disables preview."""
        pm.select([self.cube, self.sphere])
        self.preview.enable()
        self.assertTrue(self.chk.isChecked())

        # Deselect sphere
        pm.select(self.sphere, deselect=True)
        self.preview.disable_on_selection_change()

        self.assertFalse(self.chk.isChecked())

    # -------------------------------------------------------------------------
    # External Undo Tests
    # -------------------------------------------------------------------------

    def test_external_undo_disables_preview(self):
        """Test that manual undo disables the preview."""
        pm.select(self.cube)
        self.preview.enable()
        self.preview.refresh()

        # Consume any expected events from refresh
        while self.preview.expected_undo_events > 0:
            self.preview.disable_on_external_undo()

        # Simulate manual undo
        pm.undo()
        self.preview.disable_on_external_undo()

        self.assertFalse(self.chk.isChecked())

    def test_internal_undo_does_not_disable(self):
        """Test that internal undo during refresh does not disable preview."""
        pm.select(self.cube)
        self.preview.enable()

        # Refresh triggers internal undo
        self.preview.refresh()

        # Should still be enabled
        self.assertTrue(self.chk.isChecked())

    def test_expected_undo_events_consumed(self):
        """Test that expected undo events are properly counted down."""
        pm.select(self.cube)
        self.preview.enable()

        # Manually set expected events
        self.preview.expected_undo_events = 2

        # First call should decrement
        self.preview.disable_on_external_undo()
        self.assertEqual(self.preview.expected_undo_events, 1)
        self.assertTrue(self.chk.isChecked())

        # Second call should decrement
        self.preview.disable_on_external_undo()
        self.assertEqual(self.preview.expected_undo_events, 0)
        self.assertTrue(self.chk.isChecked())

    # -------------------------------------------------------------------------
    # Error Resilience Tests
    # -------------------------------------------------------------------------

    def test_operation_failure_clears_needs_undo(self):
        """Test that failed operations don't leave stale undo state."""
        pm.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.preview.needs_undo)

        # Force failure
        self.op.should_fail = True
        self.preview.refresh()

        self.assertFalse(self.preview.needs_undo)

    def test_no_selection_shows_message(self):
        """Test that enabling with no selection shows message and disables."""
        pm.select(clear=True)
        messages = []
        self.preview.message_func = messages.append

        self.preview.enable()

        self.assertFalse(self.chk.isChecked())
        self.assertTrue(any("No objects" in msg for msg in messages))

    def test_operation_failure_reverts_position(self):
        """Test that failed operation leaves object at original position."""
        pm.select(self.cube)
        self.preview.enable()

        initial_y = 1.0  # After first successful operation

        self.op.should_fail = True
        self.preview.refresh()

        # Undo happened, but new op failed, so cube should be at 0
        pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(pos.y, 0, places=4)

    # -------------------------------------------------------------------------
    # Undo Queue Integrity Tests
    # -------------------------------------------------------------------------

    def test_undo_queue_not_cleared(self):
        """Ensure preview does not clear the undo queue."""
        pm.select(self.sphere)
        pm.move(self.sphere, 10, 0, 0)  # Sentinel action

        pm.select(self.cube)
        self.preview.enable()
        self.preview.disable()

        # Undo the select
        pm.undo()
        # Undo the sentinel move
        pm.undo()

        pos = self.sphere.getTranslation(space="world")
        self.assertAlmostEqual(
            pos.x, 0, places=4, msg="Undo queue was cleared by Preview"
        )

    def test_disable_restores_undo_state(self):
        """Test that disable restores previous undo info state."""
        pm.select(self.cube)

        # Check initial state
        initial_state = pm.undoInfo(q=True, state=True)

        self.preview.enable()
        self.preview.disable()

        final_state = pm.undoInfo(q=True, state=True)
        self.assertEqual(initial_state, final_state)

    # -------------------------------------------------------------------------
    # Widget State Tests
    # -------------------------------------------------------------------------

    def test_checkbox_toggled_enables_preview(self):
        """Test that toggling checkbox via signal enables preview."""
        pm.select(self.cube)

        # Simulate checkbox toggle (this triggers the toggled signal)
        self.chk.setChecked(True)

        self.assertTrue(self.chk.isChecked())
        # Operation should have been performed
        pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(pos.y, 1.0, places=4)

    def test_create_button_disabled_initially(self):
        """Test create button state management."""
        pm.select(self.cube)

        self.preview.enable()
        self.assertTrue(self.btn.isEnabled())

        self.preview.disable()
        self.assertFalse(self.btn.isEnabled())

    def test_create_button_finalizes(self):
        """Test that clicking create button finalizes changes."""
        pm.select(self.cube)
        self.preview.enable()

        # Simulate button click
        self.btn.click()

        # Should be disabled and changes preserved
        self.assertFalse(self.chk.isChecked())
        pos = self.cube.getTranslation(space="world")
        self.assertAlmostEqual(pos.y, 1.0, places=4)


@unittest.skipIf(QtWidgets is None, "Qt not available")
class TestPreviewEdgeCases(MayaTkTestCase):
    """Edge case tests for Preview class."""

    def test_missing_perform_operation_raises(self):
        """Test that missing perform_operation method raises ValueError."""

        class BadOperation:
            pass

        window = QtWidgets.QMainWindow()
        chk = QtWidgets.QCheckBox("Preview")
        btn = QtWidgets.QPushButton("Create")

        with self.assertRaises(ValueError) as ctx:
            Preview(BadOperation(), chk, btn)

        self.assertIn("perform_operation", str(ctx.exception))
        window.close()

    def test_none_checkbox_raises(self):
        """Test that None checkbox raises ValueError."""
        op = MockOperation()
        btn = QtWidgets.QPushButton("Create")

        with self.assertRaises(ValueError):
            Preview(op, None, btn)

        btn.close()

    def test_none_button_raises(self):
        """Test that None button raises ValueError."""
        op = MockOperation()
        chk = QtWidgets.QCheckBox("Preview")

        with self.assertRaises(ValueError):
            Preview(op, chk, None)

        chk.close()

    def test_cleanup_removes_scriptjobs(self):
        """Test that cleanup() removes all scriptJobs."""
        window = QtWidgets.QMainWindow()
        chk = QtWidgets.QCheckBox("Preview")
        btn = QtWidgets.QPushButton("Create")
        op = MockOperation()

        preview = Preview(op, chk, btn, message_func=lambda msg: None)
        job_ids = preview.script_jobs.copy()

        # Verify jobs exist
        for job_id in job_ids:
            self.assertTrue(pm.scriptJob(exists=job_id))

        # Explicit cleanup
        preview.cleanup()

        # Jobs should be cleaned up
        for job_id in job_ids:
            self.assertFalse(
                pm.scriptJob(exists=job_id), f"ScriptJob {job_id} was not cleaned up"
            )

        # script_jobs list should be empty
        self.assertEqual(len(preview.script_jobs), 0)

        window.close()


if __name__ == "__main__":
    unittest.main()
