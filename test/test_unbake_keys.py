# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.anim_utils.unbake_keys module.

Covers the pure-Python decision helpers (`_find_essential_keys`,
`_preserve_hold_boundaries`) and the no-op guard paths of the public
`unbake_animation*` functions.
"""
import unittest

import maya.cmds as cmds

from mayatk.anim_utils.unbake_keys import (
    unbake_animation,
    unbake_animation_direction_based,
    unbake_animation_smart,
    _find_essential_keys,
    _preserve_hold_boundaries,
)

from base_test import MayaTkTestCase, QuickTestCase


def _key(index, time, value):
    return {"index": index, "time": float(time), "value": float(value)}


class TestFindEssentialKeys(QuickTestCase):
    """_find_essential_keys is pure Python — no Maya needed."""

    def test_short_curves_return_endpoints(self):
        keys = [_key(0, 0, 0)]
        self.assertEqual(_find_essential_keys(keys, 0.01), [0, 0])

        keys = [_key(0, 0, 0), _key(1, 1, 1)]
        self.assertEqual(_find_essential_keys(keys, 0.01), [0, 1])

    def test_first_and_last_always_essential(self):
        keys = [_key(0, 0, 0), _key(1, 5, 5), _key(2, 10, 10)]
        result = _find_essential_keys(keys, 0.01)
        self.assertIn(0, result)
        self.assertIn(2, result)

    def test_direction_change_is_kept(self):
        # Up-down peak — middle key is a local maximum
        keys = [_key(0, 0, 0), _key(1, 5, 10), _key(2, 10, 0)]
        result = _find_essential_keys(keys, 0.01)
        self.assertIn(1, result)

    def test_linear_intermediate_can_be_removed(self):
        # Three keys on a perfectly linear ramp — middle is redundant
        keys = [_key(0, 0, 0), _key(1, 5, 5), _key(2, 10, 10)]
        result = _find_essential_keys(keys, 0.01)
        # The middle key should NOT be marked as a direction change
        # First/last always kept; middle on a straight line is non-essential
        self.assertNotIn(1, result)

    def test_animation_start_keeps_key(self):
        # First key static (a→a), then change (a→b)
        keys = [_key(0, 0, 5), _key(1, 5, 5), _key(2, 10, 10)]
        result = _find_essential_keys(keys, 0.01)
        # The middle key is the start of motion
        self.assertIn(1, result)

    def test_animation_stop_keeps_key(self):
        # Motion (a→b) then static (b→b)
        keys = [_key(0, 0, 0), _key(1, 5, 5), _key(2, 10, 5)]
        result = _find_essential_keys(keys, 0.01)
        self.assertIn(1, result)


class TestPreserveHoldBoundaries(QuickTestCase):
    """_preserve_hold_boundaries — collapses runs of equal values."""

    def test_empty_input_no_op(self):
        essential = set()
        _preserve_hold_boundaries([], essential, 0.01)
        self.assertEqual(essential, set())

    def test_three_key_hold_keeps_first_and_last(self):
        keys = [_key(0, 0, 5), _key(1, 5, 5), _key(2, 10, 5)]
        essential = set()
        _preserve_hold_boundaries(keys, essential, 0.01)
        # 3-key hold: first and last preserved
        self.assertIn(0, essential)
        self.assertIn(2, essential)

    def test_two_key_hold_no_action(self):
        # 2-key hold isn't a "hold of 3+", so neither is added
        keys = [_key(0, 0, 5), _key(1, 5, 5)]
        essential = set()
        _preserve_hold_boundaries(keys, essential, 0.01)
        # 2-key hold doesn't trigger boundary preservation
        self.assertEqual(essential, set())


class TestUnbakeNoOpGuards(MayaTkTestCase):
    """All three unbake variants share a no-objects/no-selection guard."""

    def test_unbake_no_selection_returns_zero(self):
        cmds.select(clear=True)
        self.assertEqual(unbake_animation(), 0)

    def test_unbake_direction_no_selection_returns_zero(self):
        cmds.select(clear=True)
        self.assertEqual(unbake_animation_direction_based(), 0)

    def test_unbake_smart_no_selection_returns_zero(self):
        cmds.select(clear=True)
        self.assertEqual(unbake_animation_smart(), 0)

    def test_unbake_with_object_no_animation_returns_zero(self):
        # Cube with no keys — nothing to unbake on each attr
        cube = cmds.polyCube(name="unbake_cube")[0]
        # Each variant should run without raising and return 0
        result = unbake_animation([cube])
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
