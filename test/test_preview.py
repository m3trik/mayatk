# !/usr/bin/python
# coding=utf-8
"""Test suite for mayatk.core_utils.preview (hermetic ``CleanupContract`` design).

Covers the Preview orchestration contract:
- enable / disable / refresh / finalize lifecycle + UI wiring
- the two rollback paths Preview drives: attr-snapshot restore
  (``contract.record_modification``) and node-diff cleanup
- no-selection / validation gating, error resilience
- undo-queue integrity (preview must not flush the user's history)

``perform_operation`` is ``(self, objects, contract)``; ``contract`` is the
:class:`CleanupContract` during preview and ``None`` during the commit replay.
Selection-change / external-undo "auto-disable" and scriptJobs were removed
when Preview moved to the hermetic snapshot/diff design, so those tests are
gone (see the module docstring of ``core_utils/preview.py``).
"""
import unittest

try:
    from qtpy import QtWidgets
except ImportError:
    QtWidgets = None

from base_test import MayaTkTestCase
from mayatk.core_utils.preview import Preview, apply_result_selection
import maya.cmds as cmds


class MockOperation:
    """Records its attr mutation then moves +1 in Y.

    Recording via ``contract.record_modification`` before the mutation is what
    lets the hermetic rollback revert a non-node-creating op (the documented
    contract for in-place attribute edits). ``contract`` is ``None`` on the
    commit replay, so the guard skips recording there.
    """

    def __init__(self):
        self.operated_objects = set()
        self.perform_count = 0
        self.should_fail = False

    def perform_operation(self, objects, contract):
        if self.should_fail:
            raise RuntimeError("Simulated failure")
        self.perform_count += 1
        for obj in objects:
            if contract:
                contract.record_modification(obj, "translateY")
            cmds.move(0, 1, 0, obj, r=True)


@unittest.skipIf(QtWidgets is None, "Qt not available")
class TestPreview(MayaTkTestCase):
    """Lifecycle + rollback behaviour of the Preview orchestrator."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_cube")[0]
        self.sphere = cmds.polySphere(name="test_sphere")[0]

        self.window = QtWidgets.QMainWindow()
        self.chk = QtWidgets.QCheckBox("Preview")
        self.btn = QtWidgets.QPushButton("Create")

        self.op = MockOperation()
        self.preview = Preview(
            self.op, self.chk, self.btn, message_func=lambda msg: None
        )

    def tearDown(self):
        try:
            self.preview.cleanup()
        except Exception:
            pass
        if hasattr(self, "window"):
            self.window.close()
        super().tearDown()

    def _y(self, node):
        return cmds.xform(node, query=True, worldSpace=True, translation=True)[1]

    # ------------------------------------------------------------- basic API
    def test_preview_excluded_from_restore(self):
        """The preview checkbox must skip uitk state-restore so a previewed
        op doesn't auto-fire on UI load."""
        self.assertTrue(hasattr(self.chk, "restore_state"))
        self.assertFalse(self.chk.restore_state)

    def test_enable_applies_then_disable_reverts(self):
        """enable() runs the op (Y+1); disable() rolls it back to the original."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)

        self.preview.enable()
        self.assertTrue(self.chk.isChecked())
        self.assertTrue(self.preview.is_enabled)
        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)

        self.preview.disable()
        self.assertFalse(self.chk.isChecked())
        self.assertFalse(self.preview.is_enabled)
        self.assertAlmostEqual(self._y(self.cube), initial_y, places=4)

    def test_finalize_preserves_changes(self):
        """finalize_changes() commits the op (Y+1 persists) and disables preview."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)

        self.preview.enable()
        self.preview.finalize_changes()

        self.assertFalse(self.chk.isChecked())
        self.assertFalse(self.preview.is_enabled)
        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)

    def test_finalized_change_is_one_undo_chunk(self):
        """The committed op replays inside a single openChunk/closeChunk pair,
        so one Ctrl+Z reverts it (and only it)."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)

        self.preview.enable()
        self.preview.finalize_changes()
        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)

        cmds.undo()
        self.assertAlmostEqual(self._y(self.cube), initial_y, places=4)

    def test_refresh_does_not_accumulate(self):
        """refresh() rolls back the prior preview before re-running, so the
        net effect stays Y+1 no matter how many refreshes."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)
        self.preview.enable()

        for _ in range(5):
            self.preview.refresh()
            self.assertTrue(self.chk.isChecked(), "refresh must keep preview enabled")

        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)

    def test_refresh_with_selection_clearing_op_stays_enabled(self):
        """An op that clears the selection mid-preview (e.g. polyBevel) must not
        knock the preview out — the hermetic design has no selection watcher."""
        cmds.select(self.cube)
        self.preview.enable()

        original = self.op.perform_operation

        def clears_selection(objects, contract):
            original(objects, contract)
            cmds.select(clear=True)

        self.op.perform_operation = clears_selection
        try:
            self.preview.refresh()
            self.assertTrue(self.chk.isChecked())
        finally:
            self.op.perform_operation = original

    # --------------------------------------------------- node-diff rollback
    def test_created_nodes_rolled_back_then_kept_on_finalize(self):
        """The node-diff path: nodes a previewed op creates are deleted on
        disable and re-created (persisted) on finalize."""

        class _CreateOp:
            def perform_operation(self, objects, contract):
                cmds.polyCube(name="preview_probe")

        preview = Preview(
            _CreateOp(),
            QtWidgets.QCheckBox(),
            QtWidgets.QPushButton(),
            message_func=lambda m: None,
        )
        try:
            cmds.select(self.cube)
            self.assertEqual(cmds.ls("preview_probe", type="transform"), [])

            preview.enable()
            self.assertTrue(cmds.ls("preview_probe", type="transform"))

            preview.disable()
            self.assertEqual(cmds.ls("preview_probe", type="transform"), [])

            # The op's polyCube auto-selected the cube it created; disable's
            # rollback then deleted it, leaving nothing selected. A real op
            # works on the user's (persistent) selection — this bare-create op
            # doesn't — so re-select before the commit pass, which enable()
            # gates on a non-empty selection.
            cmds.select(self.cube)
            preview.enable()
            preview.finalize_changes()
            self.assertTrue(cmds.ls("preview_probe", type="transform"))
        finally:
            preview.cleanup()

    # ----------------------------------------------------- gating / messages
    def test_no_selection_shows_message_and_stays_disabled(self):
        cmds.select(clear=True)
        messages = []
        self.preview.message_func = messages.append

        self.preview.enable()

        self.assertFalse(self.chk.isChecked())
        self.assertFalse(self.preview.is_enabled)
        self.assertTrue(any("No objects" in m for m in messages))

    def test_validation_failure_prevents_enable(self):
        """A validation_func returning False blocks enable and messages."""
        messages = []
        preview = Preview(
            MockOperation(),
            QtWidgets.QCheckBox(),
            QtWidgets.QPushButton(),
            message_func=messages.append,
            validation_func=lambda objs: False,
        )
        try:
            cmds.select(self.cube)
            preview.enable()
            self.assertFalse(preview.is_enabled)
            self.assertTrue(any("validation" in m.lower() for m in messages))
        finally:
            preview.cleanup()

    def test_operation_failure_reverts_to_clean_state(self):
        """A failure during refresh rolls back the prior preview and the failed
        op applies nothing — the object is left at its original position."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)
        self.preview.enable()
        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)

        self.op.should_fail = True
        self.preview.refresh()

        self.assertAlmostEqual(self._y(self.cube), initial_y, places=4)

    # ------------------------------------------------- undo-queue integrity
    def test_undo_queue_not_cleared(self):
        """Preview suppresses recording WITHOUT flushing the user's history,
        so a pre-preview action is still undoable afterward."""
        cmds.select(self.sphere)
        cmds.move(10, 0, 0, self.sphere)  # sentinel action on the user's queue

        cmds.select(self.cube)
        self.preview.enable()
        self.preview.disable()

        cmds.undo()  # reverts "select cube"
        cmds.undo()  # reverts the sentinel move
        self.assertAlmostEqual(
            cmds.xform(self.sphere, q=True, ws=True, t=True)[0],
            0,
            places=4,
            msg="Undo queue was cleared by Preview",
        )

    def test_disable_restores_undo_state(self):
        cmds.select(self.cube)
        initial_state = cmds.undoInfo(q=True, state=True)

        self.preview.enable()
        self.preview.disable()

        self.assertEqual(initial_state, cmds.undoInfo(q=True, state=True))

    # ------------------------------------------------------- widget wiring
    def test_checkbox_toggled_enables_preview(self):
        """Toggling the checkbox (its signal) runs the op."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)

        self.chk.setChecked(True)

        self.assertTrue(self.chk.isChecked())
        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)

    def test_create_button_state_tracks_enabled(self):
        cmds.select(self.cube)
        self.preview.enable()
        self.assertTrue(self.btn.isEnabled())
        self.preview.disable()
        self.assertFalse(self.btn.isEnabled())

    def test_create_button_finalizes(self):
        """Clicking Create commits and disables the preview."""
        cmds.select(self.cube)
        initial_y = self._y(self.cube)
        self.preview.enable()

        self.btn.click()

        self.assertFalse(self.chk.isChecked())
        self.assertAlmostEqual(self._y(self.cube), initial_y + 1, places=4)


@unittest.skipIf(QtWidgets is None, "Qt not available")
class TestPreviewEdgeCases(MayaTkTestCase):
    """Constructor guards + cleanup."""

    def test_missing_perform_operation_raises(self):
        class BadOperation:
            pass

        with self.assertRaises(ValueError) as ctx:
            Preview(BadOperation(), QtWidgets.QCheckBox(), QtWidgets.QPushButton())
        self.assertIn("perform_operation", str(ctx.exception))

    def test_none_checkbox_raises(self):
        with self.assertRaises(ValueError):
            Preview(MockOperation(), None, QtWidgets.QPushButton())

    def test_none_button_raises(self):
        with self.assertRaises(ValueError):
            Preview(MockOperation(), QtWidgets.QCheckBox(), None)

    def test_cleanup_disables_active_preview_and_deregisters(self):
        """cleanup() rolls back an active preview and drops the instance from
        the class registry (replacing the old scriptJob-teardown contract)."""
        cube = cmds.polyCube(name="cleanup_cube")[0]
        chk = QtWidgets.QCheckBox()
        btn = QtWidgets.QPushButton()
        preview = Preview(MockOperation(), chk, btn, message_func=lambda m: None)

        cmds.select(cube)
        preview.enable()
        self.assertTrue(preview.is_enabled)
        self.assertIn(preview, Preview._instances)
        y_enabled = cmds.xform(cube, q=True, ws=True, t=True)[1]
        self.assertAlmostEqual(y_enabled, 1.0, places=4)

        preview.cleanup()

        self.assertFalse(preview.is_enabled)
        self.assertNotIn(preview, Preview._instances)
        # disable() during cleanup rolled the move back.
        self.assertAlmostEqual(
            cmds.xform(cube, q=True, ws=True, t=True)[1], 0.0, places=4
        )

    def test_cleanup_all_instances_clears_registry(self):
        chk = QtWidgets.QCheckBox()
        btn = QtWidgets.QPushButton()
        preview = Preview(MockOperation(), chk, btn, message_func=lambda m: None)
        self.assertIn(preview, Preview._instances)

        Preview.cleanup_all_instances()
        self.assertEqual(len(Preview._instances), 0)


@unittest.skipIf(QtWidgets is None, "Qt not available")
class TestPreviewSelectResult(MayaTkTestCase):
    """First-class *Select Result* on the Preview orchestrator.

    A panel passes ``select_result_checkbox`` + ``result_provider`` and Preview
    owns the whole feature -- applying it after every preview build and on
    commit (per the toggle) and wiring the toggle live -- so a panel gets it for
    free, the way Curve to Tube / Curtain used to hand-wire. The lower-level
    selection mechanics live in ``apply_result_selection`` (tested directly at
    the end for the toggle-reread and missing-widget paths).
    """

    def _preview(self, result_provider, checkbox=None):
        chk = checkbox if checkbox is not None else QtWidgets.QCheckBox()
        preview = Preview(
            MockOperation(),
            QtWidgets.QCheckBox(),
            QtWidgets.QPushButton(),
            message_func=lambda m: None,
            select_result_checkbox=chk,
            result_provider=result_provider,
        )
        self.addCleanup(preview.cleanup)
        return preview, chk

    def test_apply_selects_on_and_deselects_off(self):
        mesh = cmds.polyPlane(name="sr_onoff")[0]
        preview, chk = self._preview(lambda: mesh)
        chk.setChecked(True)
        cmds.select(clear=True)
        preview._apply_select_result(defer=False)
        self.assertIn(mesh, cmds.ls(selection=True) or [])

        chk.setChecked(False)
        cmds.select(mesh)
        preview._apply_select_result(defer=False)
        self.assertNotIn(mesh, cmds.ls(selection=True) or [])

    def test_toggle_applies_live(self):
        # Toggling the checkbox (de)selects the current result immediately,
        # without re-running the operation -- Preview wires it in __init__.
        mesh = cmds.polyPlane(name="sr_live")[0]
        preview, chk = self._preview(lambda: mesh)
        cmds.select(clear=True)
        chk.setChecked(True)
        self.assertIn(mesh, cmds.ls(selection=True) or [])
        chk.setChecked(False)
        self.assertNotIn(mesh, cmds.ls(selection=True) or [])

    def test_off_clears_leftover_component_selection(self):
        # An op can leave the result in COMPONENT selection (polySoftEdge's
        # edges); 'off' must still fully clear it (object_mode collapses the
        # component selection onto the object, then deselects).
        mesh = cmds.polyPlane(name="sr_comp")[0]
        chk = QtWidgets.QCheckBox()
        chk.setChecked(False)
        preview, _ = self._preview(lambda: mesh, checkbox=chk)
        cmds.select(f"{mesh}.e[0:5]")
        preview._apply_select_result(defer=False)
        self.assertEqual(cmds.ls(selection=True) or [], [])

    def test_unconfigured_is_noop(self):
        # No checkbox/provider -> the feature is inert and never touches the
        # selection (backward compatible for panels that don't opt in).
        preview = Preview(
            MockOperation(),
            QtWidgets.QCheckBox(),
            QtWidgets.QPushButton(),
            message_func=lambda m: None,
        )
        self.addCleanup(preview.cleanup)
        mesh = cmds.polyPlane(name="sr_noop")[0]
        cmds.select(mesh)
        preview._apply_select_result(defer=False)
        self.assertIn(mesh, cmds.ls(selection=True) or [])

    def test_provider_error_is_safe(self):
        def boom():
            raise RuntimeError("provider blew up")

        chk = QtWidgets.QCheckBox()
        chk.setChecked(True)
        preview, _ = self._preview(boom, checkbox=chk)
        preview._apply_select_result(defer=False)  # must not raise

    def test_honored_through_full_lifecycle(self):
        """Real enable -> commit: Select Result governs the selection on the
        live preview AND after commit, including clearing the op's leftover
        component selection -- proving Preview applies after the operation
        (preview) and after finalize_func (commit)."""
        target = cmds.polyCube(name="sr_target")[0]

        class _Op:
            def __init__(self):
                self.operated_objects = set()
                self.result = None

            def perform_operation(self, objects, contract):
                self.result = cmds.polyPlane(name="sr_built")[0]
                cmds.select(f"{self.result}.e[0:3]")  # leftover component sel

        op = _Op()
        chk = QtWidgets.QCheckBox()  # starts OFF
        preview = Preview(
            op,
            QtWidgets.QCheckBox(),
            QtWidgets.QPushButton(),
            message_func=lambda m: None,
            select_result_checkbox=chk,
            result_provider=lambda: op.result,
        )
        self.addCleanup(preview.cleanup)

        cmds.select(target)
        preview.enable()
        # OFF on the live preview -> result not selected, no leftover components.
        self.assertNotIn(op.result, cmds.ls(selection=True) or [])
        self.assertFalse(cmds.filterExpand(cmds.ls(selection=True), sm=(31, 32, 34)) or [])

        # Turn ON, commit -> the committed result is selected.
        chk.setChecked(True)
        preview.finalize_changes()
        built = op.result
        self.assertTrue(cmds.objExists(built))
        self.assertIn(built, cmds.ls(selection=True) or [])

    # ------------------------------------------- primitive (apply_result_selection)
    def test_primitive_missing_widget_falls_back_to_select(self):
        # A None/dead checkbox falls back to selecting (the documented default).
        mesh = cmds.polyPlane(name="sr_prim")[0]
        cmds.select(clear=True)
        apply_result_selection(None, mesh, object_mode=True)
        self.assertIn(mesh, cmds.ls(selection=True) or [])

    def test_primitive_deferred_reassert_rereads_settled_toggle(self):
        """Regression (interactive marking-menu state-restore): the checkbox can
        read its stale .ui default on the immediate apply and settle to the saved
        value just after. The deferred idle re-assert must RE-READ the toggle so
        the settled value wins, not a commit-time snapshot."""
        mesh = cmds.polyPlane(name="sr_settle")[0]
        mesh_long = cmds.ls(mesh, long=True)[0]

        class _SettlingCheck:
            def __init__(self):
                self._reads = 0

            def isChecked(self):  # True first (stale), then False (settled)
                self._reads += 1
                return self._reads == 1

        captured = []
        orig = cmds.evalDeferred
        cmds.evalDeferred = lambda fn, **kw: captured.append(fn)
        try:
            cmds.select(clear=True)
            apply_result_selection(_SettlingCheck(), mesh, object_mode=True, defer=True)
        finally:
            cmds.evalDeferred = orig

        self.assertIn(mesh_long, cmds.ls(selection=True, long=True) or [])  # stale True
        self.assertTrue(captured, "defer must schedule an idle re-assert")
        for fn in captured:
            fn()
        self.assertNotIn(mesh_long, cmds.ls(selection=True, long=True) or [])  # settled


if __name__ == "__main__":
    unittest.main()
