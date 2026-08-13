# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.MayaCancelProvider — Maya's host cancellation strategy.

The rollback guard is the load-bearing part: ``cmds.undo()`` pops whatever is
on top of the queue, so a cancelled slot that recorded nothing would otherwise
undo the user's *previous* action. These tests pin that behaviour against a
real scene.
"""
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase


class CancelProviderTestCase(MayaTkTestCase):
    """Shared setup: a live provider with the undo queue enabled."""

    def setUp(self):
        super().setUp()
        from uitk.managers.cancel_manager import CancelManager
        from mayatk.ui_utils.cancel_provider import MayaCancelProvider

        self.CancelManager = CancelManager
        self.provider = MayaCancelProvider()
        CancelManager.register(self.provider)
        cmds.undoInfo(state=True, infinity=True)

    def tearDown(self):
        # Never leave a bracket open — an unbalanced beginComputation leaves
        # Maya in a computing state for the rest of the session.
        for bracket in list(self.provider._brackets):
            self.provider.end(bracket)
        self.CancelManager.reset()
        super().tearDown()


class TestProviderWiring(CancelProviderTestCase):
    def test_install_registers_with_uitk(self):
        from mayatk.ui_utils.cancel_provider import MayaCancelProvider

        provider = MayaCancelProvider.install()
        self.assertIs(self.CancelManager.provider(), provider)

    def test_excludes_user_input(self):
        """Queued input must not dispatch a nested slot mid-mutation."""
        self.assertTrue(self.provider.exclude_user_input)

    def test_scope_gets_host_peek_and_key_fallback(self):
        scope = self.CancelManager.new_scope("job")
        self.assertEqual(len(scope._sources), 2)

    def test_interrupt_source_quiet_without_a_bracket(self):
        self.assertFalse(self.provider._interrupt_requested())

    def test_begin_starts_an_interruptible_computation(self):
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job")
        try:
            self.assertIsNotNone(token.computation)
            self.assertFalse(self.provider._interrupt_requested())
        finally:
            self.provider.end(token)

    def test_nested_brackets_unwind(self):
        scope = self.CancelManager.new_scope("job")
        outer = self.provider.begin(scope, "outer")
        inner = self.provider.begin(scope, "inner")
        self.assertEqual(len(self.provider._brackets), 2)
        self.provider.end(inner)
        self.provider.end(outer)
        self.assertEqual(len(self.provider._brackets), 0)

    def test_end_tolerates_a_junk_token(self):
        self.provider.end(None, cancelled=True, rollback=True)  # must not raise

    def test_tick_is_safe_in_batch(self):
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job")
        try:
            self.provider.tick(1, 10, "working")
            self.provider.tick(5, 10, "working")
        finally:
            self.provider.end(token)


class TestUndoTransaction(CancelProviderTestCase):
    def test_no_chunk_opened_without_rollback(self):
        """Undo granularity must be unchanged for slots that never opted in."""
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job", rollback=False)
        self.assertFalse(token.chunk_open)
        cmds.polyCube(name="madeDuringSlot")
        self.provider.end(token, cancelled=True, rollback=False)

        self.assertTrue(cmds.objExists("madeDuringSlot"))
        self.assertFalse(
            str(cmds.undoInfo(query=True, undoName=True)).startswith("uitkCancelable")
        )

    def test_cancelled_run_is_rolled_back(self):
        cmds.polyCube(name="userPrevious")
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job", rollback=True)
        self.assertTrue(token.chunk_open)
        cmds.polyCube(name="partialWork")
        cmds.polySphere(name="morePartialWork")
        self.provider.end(token, cancelled=True, rollback=True)

        self.assertFalse(cmds.objExists("partialWork"))
        self.assertFalse(cmds.objExists("morePartialWork"))
        self.assertTrue(cmds.objExists("userPrevious"))

    def test_empty_chunk_does_not_undo_the_previous_action(self):
        """The whole reason the rollback is guarded rather than blind."""
        cmds.polyCube(name="userPrevious")
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job", rollback=True)
        # Cancelled before touching the scene.
        self.provider.end(token, cancelled=True, rollback=True)

        self.assertTrue(cmds.objExists("userPrevious"))

    def test_completed_run_keeps_its_work(self):
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job", rollback=True)
        cmds.polyCube(name="finishedWork")
        self.provider.end(token, cancelled=False, rollback=True)

        self.assertTrue(cmds.objExists("finishedWork"))

    def test_rollback_ignored_when_not_requested_by_the_slot(self):
        scope = self.CancelManager.new_scope("job")
        token = self.provider.begin(scope, "job", rollback=True)
        cmds.polyCube(name="keptWork")
        self.provider.end(token, cancelled=True, rollback=False)

        self.assertTrue(cmds.objExists("keptWork"))

    def test_rollback_with_undo_disabled_is_reported(self):
        """Batch/standalone Maya starts with undo off — don't fail silently."""
        cmds.undoInfo(state=False)
        try:
            scope = self.CancelManager.new_scope("job")
            token = self.provider.begin(scope, "job", rollback=True)
            self.assertFalse(token.chunk_open)
            self.assertIsNone(token.chunk_name)
            self.provider.end(token, cancelled=True, rollback=True)
        finally:
            cmds.undoInfo(state=True, infinity=True)

    def test_chunk_names_are_unique_per_invocation(self):
        """A shared name could match a *previous* chunk and undo the wrong one."""
        scope = self.CancelManager.new_scope("job")
        first = self.provider.begin(scope, "a", rollback=True)
        self.provider.end(first)
        second = self.provider.begin(scope, "b", rollback=True)
        self.provider.end(second)
        self.assertNotEqual(first.chunk_name, second.chunk_name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
