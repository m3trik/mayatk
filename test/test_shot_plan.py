# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.shots._shot_plan.

Pure-Python, Maya-free.  Exercises the planning layer directly so the
collision-safe ordering, envelope computation, and pivot handling are
covered independently of any Maya-side executor.
"""
import unittest
import sys

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore
from mayatk.anim_utils.shots._shot_plan import (
    MovePlan,
    ShotMove,
    plan_respace,
    plan_ripple_downstream,
    plan_ripple_upstream,
    _INF,
)


def _store(shots):
    s = ShotStore(list(shots))
    s.snap_whole_frames = False
    return s


class TestPlanRespace(unittest.TestCase):
    def test_forward_shift_orders_back_to_front(self):
        """Gap growth shifts shots forward by varying deltas.  Executing
        back-to-front prevents a shot's move range from overlapping the
        still-unmoved next shot's source window."""
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 11, 20, []),
                ShotBlock(3, "C", 21, 30, []),
            ]
        )
        plan = plan_respace(store, gap=20, start_frame=1)
        self.assertEqual(plan.sequence, [3, 2, 1])

    def test_backward_shift_orders_front_to_back(self):
        store = _store(
            [
                ShotBlock(1, "A", 10, 20, []),
                ShotBlock(2, "B", 40, 50, []),
                ShotBlock(3, "C", 70, 80, []),
            ]
        )
        plan = plan_respace(store, gap=0, start_frame=0)
        self.assertEqual(plan.sequence, [1, 2, 3])

    def test_non_moving_shots_absent_from_sequence(self):
        """Shots whose new position equals their old position do not
        need to be executed and must be omitted from ``sequence``."""
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 20, 30, []),
            ]
        )
        # gap=10, start=0 → A stays, B stays.
        plan = plan_respace(store, gap=10, start_frame=0)
        self.assertEqual(plan.sequence, [])
        self.assertFalse(plan.moves[1].moves)
        self.assertFalse(plan.moves[2].moves)

    def test_envelope_extends_to_next_shot_start(self):
        """Each shot's envelope must cover up to the next shot's start
        so fade tails in the trailing gap travel with the owning shot."""
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 20, 30, []),
            ]
        )
        plan = plan_respace(store, gap=5, start_frame=0)
        a = plan.moves[1]
        b = plan.moves[2]
        self.assertEqual(a.env_start, 0)
        self.assertEqual(a.env_end, 20)  # up to B's old start, not A's old end
        self.assertEqual(b.env_start, 20)
        self.assertGreater(b.env_end, 1e8)  # last shot is unbounded

    def test_locked_gap_preserves_width(self):
        """A locked gap must keep its current width when respacing."""
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 25, 35, []),  # gap of 15
                ShotBlock(3, "C", 40, 50, []),  # gap of 5
            ]
        )
        store.lock_gap(1, 2)  # preserve 15-frame gap between A and B
        plan = plan_respace(store, gap=0, start_frame=0)
        self.assertAlmostEqual(plan.moves[1].new_start, 0)
        self.assertAlmostEqual(plan.moves[1].new_end, 10)
        self.assertAlmostEqual(plan.moves[2].new_start, 25)  # 10 + 15 (locked)
        self.assertAlmostEqual(plan.moves[3].new_start, 35)  # 25+10 (gap=0)

    def test_empty_store_returns_empty_plan(self):
        plan = plan_respace(_store([]), gap=5, start_frame=0)
        self.assertEqual(plan.moves, {})
        self.assertEqual(plan.sequence, [])

    def test_snap_applied_when_enabled(self):
        """store.snap must round new positions when snap_whole_frames=True."""
        store = ShotStore(
            [
                ShotBlock(1, "A", 0, 10.4, []),
                ShotBlock(2, "B", 15.7, 22.3, []),
            ]
        )
        store.snap_whole_frames = True
        plan = plan_respace(store, gap=3.6, start_frame=0.4)
        self.assertEqual(plan.moves[1].new_start, 0.0)
        # duration not snapped in-place but new_end is
        for m in plan.moves.values():
            self.assertEqual(m.new_start, round(m.new_start))
            self.assertEqual(m.new_end, round(m.new_end))


class TestPlanRipple(unittest.TestCase):
    def test_downstream_excludes_pivot_and_earlier(self):
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 20, 30, []),  # pivot
                ShotBlock(3, "C", 40, 50, []),
                ShotBlock(4, "D", 60, 70, []),
            ]
        )
        plan = plan_ripple_downstream(
            store, pivot_shot_id=2, after_frame=30, delta=5
        )
        self.assertNotIn(1, plan.moves)  # upstream of after_frame
        self.assertNotIn(2, plan.moves)  # pivot excluded
        self.assertIn(3, plan.moves)
        self.assertIn(4, plan.moves)
        # Forward shift → back-to-front order.
        self.assertEqual(plan.sequence, [4, 3])

    def test_upstream_excludes_pivot_and_later(self):
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 20, 30, []),
                ShotBlock(3, "C", 40, 50, []),  # pivot
                ShotBlock(4, "D", 60, 70, []),
            ]
        )
        plan = plan_ripple_upstream(
            store, pivot_shot_id=3, before_frame=40, delta=-5
        )
        self.assertIn(1, plan.moves)
        self.assertIn(2, plan.moves)
        self.assertNotIn(3, plan.moves)  # pivot excluded
        self.assertNotIn(4, plan.moves)  # downstream of before_frame
        # Backward shift → front-to-back order.
        self.assertEqual(plan.sequence, [1, 2])

    def test_zero_delta_returns_empty_plan(self):
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 20, 30, []),
            ]
        )
        plan = plan_ripple_downstream(store, 1, 10, 0)
        self.assertEqual(plan.moves, {})
        self.assertEqual(plan.sequence, [])


class TestShotMove(unittest.TestCase):
    def test_moves_flag_ignores_sub_epsilon_deltas(self):
        m = ShotMove(
            shot_id=1,
            old_start=0,
            old_end=10,
            new_start=1e-9,
            new_end=10 + 1e-9,
            env_start=0,
            env_end=20,
        )
        self.assertFalse(m.moves)

    def test_delta_reflects_new_minus_old(self):
        m = ShotMove(1, 5, 15, 8, 18, 5, 20)
        self.assertAlmostEqual(m.delta, 3.0)


class TestRespaceRoundTrip(unittest.TestCase):
    """Gap up then back down must restore original shot positions."""

    def test_gap_round_trip_restores_positions(self):
        store = _store(
            [
                ShotBlock(1, "A", 0, 10, []),
                ShotBlock(2, "B", 20, 30, []),
                ShotBlock(3, "C", 40, 50, []),
            ]
        )
        # Snapshot positions before any change.
        orig = {s.shot_id: (s.start, s.end) for s in store.sorted_shots()}

        # Apply respace plans in-memory by committing new_start/new_end,
        # skipping the Maya executor.  This is what the executor does
        # for the non-Maya code path and is sufficient to verify the
        # planner round-trips positions.
        def _apply(plan):
            for sid in plan.sequence:
                shot = store.shot_by_id(sid)
                m = plan.moves[sid]
                shot.start = m.new_start
                shot.end = m.new_end

        _apply(plan_respace(store, gap=30, start_frame=0))
        _apply(plan_respace(store, gap=10, start_frame=0))

        restored = {s.shot_id: (s.start, s.end) for s in store.sorted_shots()}
        self.assertEqual(restored, orig)


if __name__ == "__main__":
    unittest.main()
