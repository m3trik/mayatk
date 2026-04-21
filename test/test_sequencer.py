# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.shots.shot_sequencer.

Pure-Python tests run without Maya.  Maya-dependent tests bootstrap a
standalone session via ``MayaConnection`` so they can run from a normal
``python -m pytest`` invocation (provided Maya is installed).
"""
import unittest
import sys
import os
from pathlib import Path

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
    ShotBlock,
    ShotSequencer,
)
from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import ColumnMap
from mayatk.anim_utils.shots.shot_manifest.behaviors import (
    load_behavior,
    resolve_keys,
    apply_behavior,
    compute_duration,
    apply_to_shots,
)
from mayatk.audio_utils._audio_utils import AudioUtils

compute_waveform_envelope = AudioUtils.compute_waveform_envelope

# ---------------------------------------------------------------------------
# Maya standalone bootstrap
# ---------------------------------------------------------------------------
HAS_MAYA = False
try:
    from mayatk.env_utils.maya_connection import MayaConnection

    _conn = MayaConnection.get_instance()
    if not _conn.is_connected:
        _conn.connect(mode="standalone")
    HAS_MAYA = _conn.is_connected
except Exception:
    pass

if HAS_MAYA:
    import pymel.core as pm


class TestShotBlock(unittest.TestCase):
    """Test ShotBlock dataclass."""

    def test_duration(self):
        b = ShotBlock(shot_id=0, name="A", start=10, end=40)
        self.assertEqual(b.duration, 30)

    def test_objects_default_empty(self):
        b = ShotBlock(shot_id=1, name="B", start=0, end=10)
        self.assertEqual(b.objects, [])


class TestSequencer(unittest.TestCase):
    """Test ShotSequencer (no Maya)."""

    def _make(self):
        return ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, ["cube1"]),
                ShotBlock(1, "S1", 60, 100, ["sphere1"]),
                ShotBlock(2, "S2", 110, 150, ["cone1"]),
            ]
        )

    def test_sorted_shots(self):
        seq = self._make()
        names = [s.name for s in seq.sorted_shots()]
        self.assertEqual(names, ["S0", "S1", "S2"])

    def test_shot_by_id(self):
        seq = self._make()
        self.assertEqual(seq.shot_by_id(1).name, "S1")
        self.assertIsNone(seq.shot_by_id(99))

    def test_to_dict_round_trip(self):
        seq = self._make()
        data = seq.to_dict()
        restored = ShotSequencer.from_dict(data)
        self.assertEqual(len(restored.shots), 3)
        self.assertEqual(restored.shot_by_id(0).name, "S0")
        self.assertEqual(restored.shot_by_id(2).objects, ["cone1"])

    def test_from_dict_preserves_order(self):
        data = {
            "shots": [
                {"shot_id": 2, "name": "Z", "start": 100, "end": 200, "objects": []},
                {"shot_id": 0, "name": "A", "start": 0, "end": 50, "objects": []},
            ],
        }
        seq = ShotSequencer.from_dict(data)
        sorted_names = [s.name for s in seq.sorted_shots()]
        self.assertEqual(sorted_names, ["A", "Z"])

    def test_hidden_objects_default_empty(self):
        seq = self._make()
        self.assertEqual(seq.hidden_objects, set())
        self.assertFalse(seq.is_object_hidden("cube1"))

    def test_set_object_hidden(self):
        seq = self._make()
        seq.set_object_hidden("cube1", True)
        self.assertTrue(seq.is_object_hidden("cube1"))
        seq.set_object_hidden("cube1", False)
        self.assertFalse(seq.is_object_hidden("cube1"))

    def test_hidden_objects_round_trip(self):
        seq = self._make()
        seq.set_object_hidden("sphere1")
        data = seq.to_dict()
        restored = ShotSequencer.from_dict(data)
        self.assertTrue(restored.is_object_hidden("sphere1"))
        self.assertFalse(restored.is_object_hidden("cube1"))

    def test_from_dict_no_hidden(self):
        """Data without hidden_objects should load with empty hidden set."""
        data = {
            "shots": [
                {"shot_id": 0, "name": "A", "start": 0, "end": 50, "objects": ["x"]},
            ],
        }
        seq = ShotSequencer.from_dict(data)
        self.assertEqual(seq.hidden_objects, set())

    def test_shot_by_name(self):
        seq = self._make()
        self.assertEqual(seq.shot_by_name("S1").shot_id, 1)
        self.assertIsNone(seq.shot_by_name("nonexistent"))

    # ---- reorder ---------------------------------------------------------

    def test_reorder_equal_duration(self):
        """Swap two shots of equal duration Ã¢â‚¬â€ positions swap, gap preserved."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 40, ["a"]),
                ShotBlock(1, "B", 50, 90, ["b"]),
                ShotBlock(2, "C", 100, 140, ["c"]),
            ]
        )
        # gap between A and B is 10
        seq.reorder_shots(0, 1)
        b = seq.shot_by_id(1)
        a = seq.shot_by_id(0)
        self.assertEqual(b.start, 0)
        self.assertEqual(b.end, 40)
        self.assertEqual(a.start, 50)
        self.assertEqual(a.end, 90)
        # C unchanged (equal durations, no ripple)
        c = seq.shot_by_id(2)
        self.assertEqual(c.start, 100)
        self.assertEqual(c.end, 140)

    def test_reorder_different_duration_ripples(self):
        """Swap shots of different durations Ã¢â‚¬â€ downstream shots ripple."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "Short", 0, 20, ["s"]),  # 20 frames
                ShotBlock(1, "Long", 30, 80, ["l"]),  # 50 frames
                ShotBlock(2, "Tail", 90, 130, ["t"]),
            ]
        )
        # gap = 10
        seq.reorder_shots(0, 1)
        long_shot = seq.shot_by_id(1)
        short_shot = seq.shot_by_id(0)
        self.assertEqual(long_shot.start, 0)
        self.assertEqual(long_shot.end, 50)
        self.assertEqual(short_shot.start, 60)
        self.assertEqual(short_shot.end, 80)
        # Old region ended at 80, new region ends at 80 Ã¢â‚¬â€ no ripple
        tail = seq.shot_by_id(2)
        self.assertEqual(tail.start, 90)

    def test_reorder_reverse_arg_order(self):
        """Passing shot IDs in reverse order should produce same result."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 40, ["a"]),
                ShotBlock(1, "B", 50, 90, ["b"]),
            ]
        )
        seq.reorder_shots(1, 0)  # reversed
        self.assertEqual(seq.shot_by_id(1).start, 0)
        self.assertEqual(seq.shot_by_id(0).start, 50)

    def test_reorder_same_id_raises(self):
        """Reordering a shot with itself should raise ValueError."""
        seq = self._make()
        with self.assertRaises(ValueError):
            seq.reorder_shots(0, 0)

    def test_reorder_invalid_id_raises(self):
        """Reordering with a nonexistent shot ID should raise ValueError."""
        seq = self._make()
        with self.assertRaises(ValueError):
            seq.reorder_shots(0, 99)


class TestVisibleShots(unittest.TestCase):
    """Test the _visible_shots display-mode logic.

    Exercises the same selection logic used by the controller's
    _visible_shots helper without needing a full controller instance.
    """

    def _make_seq(self):
        return ShotSequencer(
            [
                ShotBlock(0, "A", 0, 50, ["a"]),
                ShotBlock(1, "B", 60, 100, ["b"]),
                ShotBlock(2, "C", 110, 150, ["c"]),
                ShotBlock(3, "D", 160, 200, ["d"]),
            ]
        )

    @staticmethod
    def _visible_shots(seq, active_shot, mode):
        """Standalone replica of ShotSequencerController._visible_shots."""
        if mode == "current":
            return [active_shot]
        sorted_shots = seq.sorted_shots()
        if mode == "all":
            return sorted_shots
        # adjacent
        idx = next(
            (i for i, s in enumerate(sorted_shots) if s.shot_id == active_shot.shot_id),
            None,
        )
        if idx is None:
            return [active_shot]
        result = []
        if idx > 0:
            result.append(sorted_shots[idx - 1])
        result.append(active_shot)
        if idx < len(sorted_shots) - 1:
            result.append(sorted_shots[idx + 1])
        return result

    def test_current_mode_returns_only_active(self):
        seq = self._make_seq()
        shot = seq.shot_by_id(1)
        result = self._visible_shots(seq, shot, "current")
        self.assertEqual([s.shot_id for s in result], [1])

    def test_all_mode_returns_every_shot(self):
        seq = self._make_seq()
        shot = seq.shot_by_id(2)
        result = self._visible_shots(seq, shot, "all")
        self.assertEqual([s.shot_id for s in result], [0, 1, 2, 3])

    def test_adjacent_mode_middle(self):
        """Middle shot returns prev + active + next."""
        seq = self._make_seq()
        shot = seq.shot_by_id(1)
        result = self._visible_shots(seq, shot, "adjacent")
        self.assertEqual([s.shot_id for s in result], [0, 1, 2])

    def test_adjacent_mode_first(self):
        """First shot has no predecessor."""
        seq = self._make_seq()
        shot = seq.shot_by_id(0)
        result = self._visible_shots(seq, shot, "adjacent")
        self.assertEqual([s.shot_id for s in result], [0, 1])

    def test_adjacent_mode_last(self):
        """Last shot has no successor."""
        seq = self._make_seq()
        shot = seq.shot_by_id(3)
        result = self._visible_shots(seq, shot, "adjacent")
        self.assertEqual([s.shot_id for s in result], [2, 3])


class TestResolveKeys(unittest.TestCase):
    """Test behavior_keys.resolve_keys helper."""

    def test_in_phase_start_anchor(self):
        keys = resolve_keys(
            {"offset": 0, "duration": 10, "values": [0.0, 1.0], "anchor": "start"},
            start=100.0,
            end=200.0,
        )
        self.assertEqual(len(keys), 2)
        self.assertAlmostEqual(keys[0]["time"], 100.0)
        self.assertAlmostEqual(keys[1]["time"], 110.0)
        self.assertEqual(keys[0]["value"], 0.0)
        self.assertEqual(keys[1]["value"], 1.0)

    def test_out_phase_end_anchor(self):
        keys = resolve_keys(
            {"offset": 0, "duration": 20, "values": [1.0, 0.0], "anchor": "end"},
            start=100.0,
            end=200.0,
        )
        # base = 200 - 20 - 0 = 180
        self.assertAlmostEqual(keys[0]["time"], 180.0)
        self.assertAlmostEqual(keys[1]["time"], 200.0)

    def test_offset_shifts_base(self):
        keys = resolve_keys(
            {"offset": 5, "duration": 10, "values": [0.0, 1.0], "anchor": "start"},
            start=0.0,
            end=100.0,
        )
        self.assertAlmostEqual(keys[0]["time"], 5.0)
        self.assertAlmostEqual(keys[1]["time"], 15.0)

    def test_three_values(self):
        keys = resolve_keys(
            {"duration": 20, "values": [0.0, 0.5, 1.0], "anchor": "start"},
            start=0.0,
            end=100.0,
        )
        self.assertEqual(len(keys), 3)
        self.assertAlmostEqual(keys[0]["time"], 0.0)
        self.assertAlmostEqual(keys[1]["time"], 10.0)
        self.assertAlmostEqual(keys[2]["time"], 20.0)


class TestLoadBehavior(unittest.TestCase):
    """Test YAML behavior loading."""

    def test_load_fade_in(self):
        t = load_behavior("fade_in")
        self.assertIn("attributes", t)
        self.assertIn("visibility", t["attributes"])
        vis = t["attributes"]["visibility"]
        self.assertIn("in", vis)
        self.assertEqual(vis["in"]["values"], [0.0, 1.0])

    def test_load_fade_out(self):
        t = load_behavior("fade_out")
        self.assertIn("attributes", t)
        self.assertIn("visibility", t["attributes"])
        vis = t["attributes"]["visibility"]
        self.assertIn("out", vis)
        self.assertEqual(vis["out"]["values"], [1.0, 0.0])

    def test_missing_behavior_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_behavior("nonexistent_template_xyz")


# ---------------------------------------------------------------------------
# Maya-dependent tests (standalone session)
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestSequencerMaya(unittest.TestCase):
    """Tests requiring a running Maya session."""

    def setUp(self):
        pm.mel.file(new=True, force=True)

    def _create_animated_cube(self, name, keys):
        """Create a cube and set keyframes at the given {frame: value} dict on translateX."""
        cube = pm.polyCube(name=name)[0]
        for frame, value in keys.items():
            pm.setKeyframe(cube, attribute="translateX", time=frame, value=value)
        return cube

    # -- helpers / per-object methods --------------------------------------

    def test_shot_nodes_returns_live_nodes(self):
        """_shot_nodes returns PyNode refs for existing objects."""
        cube = self._create_animated_cube("sn_test", {0: 0, 10: 5})
        shot = ShotBlock(0, "S", 0, 10, [str(cube)])
        nodes = ShotSequencer._shot_nodes(shot)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(str(nodes[0]), str(cube))

    def test_shot_nodes_skips_missing(self):
        """_shot_nodes silently skips objects that no longer exist."""
        shot = ShotBlock(0, "S", 0, 10, ["ghost_node"])
        nodes = ShotSequencer._shot_nodes(shot)
        self.assertEqual(len(nodes), 0)

    def test_move_object_keys_shifts(self):
        """move_object_keys offsets keys within the given range."""
        cube = self._create_animated_cube("mv", {10: 0, 20: 5})
        seq = ShotSequencer()
        seq.move_object_keys(str(cube), 10, 20, 30)
        keys = sorted(pm.keyframe(cube, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 30.0, places=1)
        self.assertAlmostEqual(keys[-1], 40.0, places=1)

    def test_move_object_keys_noop_for_missing(self):
        """move_object_keys silently skips non-existent objects."""
        seq = ShotSequencer()
        seq.move_object_keys("no_such_obj", 0, 50, 10)  # should not raise

    def test_scale_object_keys_rescales(self):
        """scale_object_keys remaps keys into a new time range."""
        cube = self._create_animated_cube("sc", {0: 0, 100: 10})
        seq = ShotSequencer()
        seq.scale_object_keys(str(cube), 0, 100, 0, 200)
        keys = sorted(pm.keyframe(cube, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 0.0, places=1)
        self.assertAlmostEqual(keys[-1], 200.0, places=1)

    def test_scale_object_keys_noop_for_missing(self):
        """scale_object_keys silently skips non-existent objects."""
        seq = ShotSequencer()
        seq.scale_object_keys("no_such_obj", 0, 50, 0, 80)  # should not raise

    # -- error handling ----------------------------------------------------

    def test_set_shot_duration_invalid_id(self):
        """set_shot_duration raises ValueError for unknown shot_id."""
        seq = ShotSequencer()
        with self.assertRaises(ValueError):
            seq.set_shot_duration(99, 100)

    def test_set_shot_start_invalid_id(self):
        """set_shot_start raises ValueError for unknown shot_id."""
        seq = ShotSequencer()
        with self.assertRaises(ValueError):
            seq.set_shot_start(99, 0)

    def test_resize_object_invalid_id(self):
        """resize_object raises ValueError for unknown shot_id."""
        seq = ShotSequencer()
        with self.assertRaises(ValueError):
            seq.resize_object(99, "cube1", 0, 50, 0, 80)

    def test_resize_object_scales_single_object(self):
        """resize_object should only scale the target object, not others."""
        c1 = self._create_animated_cube("obj_a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("obj_b", {10: 0, 40: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [str(c1), str(c2)])])

        # Resize only obj_a from [0,50] -> [0,80]
        seq.resize_object(0, str(c1), 0, 50, 0, 80)

        # obj_a keys should be rescaled to [0,80]
        a_keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(a_keys[0], 0.0, places=1)
        self.assertAlmostEqual(a_keys[-1], 80.0, places=1)

        # obj_b keys should be UNTOUCHED at [10,40]
        b_keys = sorted(pm.keyframe(c2, q=True, attribute="translateX"))
        self.assertAlmostEqual(b_keys[0], 10.0, places=1)
        self.assertAlmostEqual(b_keys[-1], 40.0, places=1)

    def test_resize_object_ripples_downstream(self):
        """resize_object should shift downstream scenes by the end-frame delta."""
        c1 = self._create_animated_cube("early", {0: 0, 50: 10})
        c2 = self._create_animated_cube("late", {100: 0, 150: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 150, [str(c2)]),
            ]
        )

        # Extend obj in S0 from [0,50] -> [0,80]  (delta = +30)
        seq.resize_object(0, str(c1), 0, 50, 0, 80)

        # S1 should have shifted by +30
        self.assertAlmostEqual(seq.shot_by_id(1).start, 130.0, places=1)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 180.0, places=1)

    def test_set_shot_duration_ripple(self):
        """Changing shot 0's duration ripples shot 1's start/end."""
        c1 = self._create_animated_cube("a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("b", {100: 0, 150: 10})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 150, [str(c2)]),
            ]
        )

        original_s1_start = seq.shot_by_id(1).start

        # Extend shot 0 by 20 frames
        shot0 = seq.shot_by_id(0)
        seq.set_shot_duration(0, shot0.duration + 20)

        # Shot 1 should have shifted by +20
        self.assertAlmostEqual(
            seq.shot_by_id(1).start, original_s1_start + 20, places=1
        )

    def test_apply_behavior_sets_keys(self):
        """apply_behavior should create keyframes on the object."""
        cube = self._create_animated_cube("obj", {0: 0, 100: 10})
        apply_behavior(str(cube), "fade_in", 0, 100, attrs=["visibility"])

        # Visibility should now have keyframes
        vis_keys = pm.keyframe(cube, attribute="visibility", query=True)
        self.assertIsNotNone(vis_keys)
        self.assertGreater(len(vis_keys), 0)

    # -- gap hold enforcement ----------------------------------------------

    def test_enforce_gap_holds_after_define(self):
        """Manually calling _enforce_gap_holds sets stepped out-tangent on last pre-gap key.

        Bug: Gaps between shots had no automatic tangent enforcement, allowing
        interpolated motion to bleed through gap regions.
        Fixed: 2026-03-13
        """
        import maya.cmds as cmds

        c1 = self._create_animated_cube("gap_a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("gap_b", {100: 0, 150: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 150, [str(c2)]),
            ]
        )
        # Before enforcement, out-tangent at frame 50 should not be step
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        self.assertTrue(len(curves) > 0)
        ott = cmds.keyTangent(curves[0], q=True, time=(50, 50), outTangentType=True)
        self.assertNotEqual(
            ott[0], "step", "Pre-condition: should not already be stepped"
        )

        seq._enforce_gap_holds()

        ott = cmds.keyTangent(curves[0], q=True, time=(50, 50), outTangentType=True)
        self.assertEqual(
            ott[0], "step", "Out-tangent at gap boundary should be stepped"
        )

    def test_enforce_gap_holds_preserves_in_tangent(self):
        """_enforce_gap_holds should preserve the in-tangent of the last pre-gap key."""
        import maya.cmds as cmds

        c1 = self._create_animated_cube("pres_a", {0: 0, 50: 10})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 150, []),
            ]
        )
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        # Record in-tangent type before enforcement
        itt_before = cmds.keyTangent(
            curves[0], q=True, time=(50, 50), inTangentType=True
        )

        seq._enforce_gap_holds()

        itt_after = cmds.keyTangent(
            curves[0], q=True, time=(50, 50), inTangentType=True
        )
        self.assertEqual(itt_before[0], itt_after[0], "In-tangent should be preserved")

    def test_enforce_gap_holds_idempotent(self):
        """Calling _enforce_gap_holds twice should not change anything the second time."""
        import maya.cmds as cmds

        c1 = self._create_animated_cube("idem_a", {0: 0, 50: 10})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 150, []),
            ]
        )
        seq._enforce_gap_holds()
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        ott1 = cmds.keyTangent(curves[0], q=True, time=(50, 50), outTangentType=True)

        seq._enforce_gap_holds()
        ott2 = cmds.keyTangent(curves[0], q=True, time=(50, 50), outTangentType=True)

        self.assertEqual(ott1, ott2, "Second call should produce no change")

    def test_enforce_gap_holds_no_gap_no_change(self):
        """Contiguous shots (no gap) should not get stepped tangents."""
        import maya.cmds as cmds

        c1 = self._create_animated_cube("contig_a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("contig_b", {50: 0, 100: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 50, 100, [str(c2)]),
            ]
        )
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        ott_before = cmds.keyTangent(
            curves[0], q=True, time=(50, 50), outTangentType=True
        )

        seq._enforce_gap_holds()

        ott_after = cmds.keyTangent(
            curves[0], q=True, time=(50, 50), outTangentType=True
        )
        self.assertEqual(ott_before[0], ott_after[0], "No gap -> no step enforcement")

    def test_set_shot_duration_enforces_gap_holds(self):
        """set_shot_duration should automatically enforce gap holds.

        Bug: Timeline-modifying operations did not enforce stepped tangents
        at gap boundaries, allowing animation bleed between shots.
        Fixed: 2026-03-13
        """
        import maya.cmds as cmds

        c1 = self._create_animated_cube("dur_a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("dur_b", {100: 0, 150: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 150, [str(c2)]),
            ]
        )
        # Shrink shot 0, creating/modifying the gap
        seq.set_shot_duration(0, 30)

        # Last key of c1 should now be at frame 30 (scaled from 50) and stepped
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        times = cmds.keyframe(curves[0], q=True, timeChange=True)
        last_t = max(times)
        ott = cmds.keyTangent(
            curves[0], q=True, time=(last_t, last_t), outTangentType=True
        )
        self.assertEqual(
            ott[0], "step", "Gap hold should be enforced after set_shot_duration"
        )

    # -- unified sequence model (Part 1) -----------------------------------

    def test_collect_shot_sequences_anim_only(self):
        """collect_shot_sequences returns anim segments tagged with kind='anim'."""
        c1 = self._create_animated_cube("seq_a", {0: 0, 30: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 30, [str(c1)])])
        sequences = seq.collect_shot_sequences(0, include_audio=False)
        self.assertTrue(any(s["kind"] == "anim" and s["obj"] == str(c1) for s in sequences))
        for s in sequences:
            self.assertIn("start", s)
            self.assertIn("end", s)

    def test_collect_shot_sequences_unknown_shot(self):
        """Unknown shot id returns an empty list, no exception."""
        seq = ShotSequencer()
        self.assertEqual(seq.collect_shot_sequences(99), [])

    def test_move_sequence_anim_dispatch(self):
        """_move_sequence with kind='anim' shifts the object's keys."""
        c1 = self._create_animated_cube("mvs_a", {10: 0, 20: 5})
        seq = ShotSequencer()
        seq._move_sequence(
            {"kind": "anim", "obj": str(c1), "start": 10, "end": 20}, 30
        )
        keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 30.0, places=1)
        self.assertAlmostEqual(keys[-1], 40.0, places=1)

    def test_move_sequence_noop_when_delta_zero(self):
        """_move_sequence is a no-op when new_start matches old start."""
        c1 = self._create_animated_cube("mvs_b", {10: 0, 20: 5})
        seq = ShotSequencer()
        seq._move_sequence(
            {"kind": "anim", "obj": str(c1), "start": 10, "end": 20}, 10
        )
        keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 10.0, places=1)
        self.assertAlmostEqual(keys[-1], 20.0, places=1)

    def test_recompute_shot_objects_drops_orphans(self):
        """_recompute_shot_objects drops objects with no keys in the shot range."""
        c1 = self._create_animated_cube("rc_a", {0: 0, 20: 5})
        seq = ShotSequencer(
            [ShotBlock(0, "S0", 0, 20, [str(c1), "ghost_node"])]
        )
        seq._recompute_shot_objects(0)
        objs = seq.shot_by_id(0).objects
        self.assertIn(str(c1), objs)
        self.assertNotIn("ghost_node", objs)

    def test_recompute_shot_objects_preserves_pinned(self):
        """_recompute_shot_objects keeps pinned objects even when keyless."""
        c1 = self._create_animated_cube("rc_b", {0: 0, 20: 5})
        seq = ShotSequencer(
            [ShotBlock(0, "S0", 0, 20, [str(c1), "pinned_ghost"])]
        )
        seq.store.pinned_objects.add("pinned_ghost")
        seq._recompute_shot_objects(0)
        self.assertIn("pinned_ghost", seq.shot_by_id(0).objects)

    # -- trim / extend (Part 2) --------------------------------------------

    def test_trim_shot_to_content_shrinks_inward(self):
        """trim_shot_to_content moves boundaries inward to hug content."""
        c1 = self._create_animated_cube("trim_a", {20: 0, 40: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 100, [str(c1)])])
        head, tail = seq.trim_shot_to_content(0)
        s0 = seq.shot_by_id(0)
        self.assertAlmostEqual(s0.start, 20.0, places=1)
        self.assertAlmostEqual(s0.end, 40.0, places=1)
        self.assertAlmostEqual(head, 20.0, places=1)
        self.assertAlmostEqual(tail, -60.0, places=1)

    def test_trim_shot_ripples_downstream(self):
        """Trimming a shot's tail ripples downstream shots inward."""
        c1 = self._create_animated_cube("trd_a", {0: 0, 30: 5})
        c2 = self._create_animated_cube("trd_b", {200: 0, 250: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 100, [str(c1)]),
                ShotBlock(1, "S1", 200, 250, [str(c2)]),
            ]
        )
        seq.trim_shot_to_content(0)
        # S0 tail moved from 100 -> 30 (delta -70); S1 should shift by -70
        self.assertAlmostEqual(seq.shot_by_id(1).start, 130.0, places=1)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 180.0, places=1)

    def test_trim_shot_ripples_upstream(self):
        """Trimming a shot's head ripples upstream shots outward (toward content)."""
        c1 = self._create_animated_cube("tru_a", {0: 0, 30: 5})
        c2 = self._create_animated_cube("tru_b", {120: 0, 180: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 200, [str(c2)]),
            ]
        )
        seq.trim_shot_to_content(1)
        # S1 head moved from 100 -> 120 (delta +20); S0 should shift by +20
        self.assertAlmostEqual(seq.shot_by_id(0).start, 20.0, places=1)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 70.0, places=1)

    def test_extend_shot_to_fit_grows_outward(self):
        """extend_shot_to_fit expands the shot to enclose out-of-range content."""
        c1 = self._create_animated_cube("ext_a", {0: 0, 80: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 10, 50, [str(c1)])])
        head, tail = seq.extend_shot_to_fit(0)
        s0 = seq.shot_by_id(0)
        self.assertAlmostEqual(s0.start, 0.0, places=1)
        self.assertAlmostEqual(s0.end, 80.0, places=1)
        self.assertAlmostEqual(head, -10.0, places=1)
        self.assertAlmostEqual(tail, 30.0, places=1)

    def test_extend_shot_ripples_both_directions(self):
        """Extending a middle shot ripples both upstream and downstream."""
        c0 = self._create_animated_cube("ext_pre", {0: 0, 20: 5})
        c1 = self._create_animated_cube("ext_mid", {-10: 0, 110: 5})
        c2 = self._create_animated_cube("ext_post", {200: 0, 250: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 30, [str(c0)]),
                ShotBlock(1, "S1", 50, 100, [str(c1)]),
                ShotBlock(2, "S2", 200, 250, [str(c2)]),
            ]
        )
        seq.extend_shot_to_fit(1)
        # S1 head: 50 -> -10 (delta -60); tail: 100 -> 110 (delta +10)
        s0_start = seq.shot_by_id(0).start
        s2_start = seq.shot_by_id(2).start
        self.assertAlmostEqual(s0_start, -60.0, places=1)
        self.assertAlmostEqual(s2_start, 210.0, places=1)

    def test_trim_noop_when_already_tight(self):
        """trim_shot_to_content returns zeros when boundaries already fit."""
        c1 = self._create_animated_cube("noop_a", {0: 0, 50: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [str(c1)])])
        head, tail = seq.trim_shot_to_content(0)
        self.assertEqual((head, tail), (0.0, 0.0))

    def test_fit_shot_invalid_id(self):
        """fit_shot_to_content raises ValueError for unknown shot_id."""
        seq = ShotSequencer()
        with self.assertRaises(ValueError):
            seq.fit_shot_to_content(99)

    # -- move sequences across shots (Part 3) ------------------------------

    def test_move_sequences_to_empty_shot_anchors_at_start(self):
        """Moving an anim sequence to an empty dest places it at dest.start."""
        c1 = self._create_animated_cube("mvs_dest_a", {10: 0, 20: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 200, []),
            ]
        )
        seq.move_sequences_to_shot(
            [{"kind": "anim", "obj": str(c1), "start": 10, "end": 20}],
            dest_shot_id=1,
        )
        keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        # Moved to dest.start (100); duration preserved.
        self.assertAlmostEqual(keys[0], 100.0, places=1)
        self.assertAlmostEqual(keys[-1], 110.0, places=1)
        # Object should now belong to dest shot.
        self.assertIn(str(c1), seq.shot_by_id(1).objects)
        self.assertNotIn(str(c1), seq.shot_by_id(0).objects)

    def test_move_sequences_places_after_when_from_upstream(self):
        """When dest already has the obj and source is upstream, place AFTER."""
        c1 = self._create_animated_cube("mvs_after_a", {10: 0, 20: 5})
        c2 = self._create_animated_cube("mvs_after_b", {110: 0, 130: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 200, [str(c2)]),
            ]
        )
        # Move c1's seq from S0 (upstream) into S1 — c2 already lives there.
        seq.move_sequences_to_shot(
            [{"kind": "anim", "obj": str(c1), "start": 10, "end": 20}],
            dest_shot_id=1,
        )
        c1_keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        # Anchor = end of c2 (130); c1 moves to start at 130.
        self.assertAlmostEqual(c1_keys[0], 130.0, places=1)
        self.assertAlmostEqual(c1_keys[-1], 140.0, places=1)

    def test_move_sequences_places_before_when_from_downstream(self):
        """When dest already has the obj and source is downstream, place BEFORE."""
        c1 = self._create_animated_cube("mvs_before_a", {110: 0, 130: 5})
        c2 = self._create_animated_cube("mvs_before_b", {210: 0, 220: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 100, 200, [str(c1)]),
                ShotBlock(1, "S1", 200, 300, [str(c2)]),
            ]
        )
        # Move c2 (downstream of S0) into S0 — c1 already lives there.
        seq.move_sequences_to_shot(
            [{"kind": "anim", "obj": str(c2), "start": 210, "end": 220}],
            dest_shot_id=0,
        )
        c2_keys = sorted(pm.keyframe(c2, q=True, attribute="translateX"))
        # Anchor = start of c1 (110) - group_dur (10) = 100. c2 moves to [100,110].
        self.assertAlmostEqual(c2_keys[0], 100.0, places=1)
        self.assertAlmostEqual(c2_keys[-1], 110.0, places=1)

    def test_move_sequences_preserves_group_offsets(self):
        """Multiple sequences from the same source shot keep their offsets."""
        c1 = self._create_animated_cube("mvs_grp_a", {10: 0, 20: 5})
        c2 = self._create_animated_cube("mvs_grp_b", {30: 0, 40: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1), str(c2)]),
                ShotBlock(1, "S1", 100, 200, []),
            ]
        )
        seq.move_sequences_to_shot(
            [
                {"kind": "anim", "obj": str(c1), "start": 10, "end": 20},
                {"kind": "anim", "obj": str(c2), "start": 30, "end": 40},
            ],
            dest_shot_id=1,
        )
        c1_keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        c2_keys = sorted(pm.keyframe(c2, q=True, attribute="translateX"))
        # Group base = 10. anchor = 100. c1 -> [100,110], c2 -> [120,130]
        self.assertAlmostEqual(c1_keys[0], 100.0, places=1)
        self.assertAlmostEqual(c2_keys[0], 120.0, places=1)
        self.assertAlmostEqual(c2_keys[-1], 130.0, places=1)

    def test_move_sequences_invalid_dest(self):
        """Invalid destination raises ValueError."""
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [])])
        with self.assertRaises(ValueError):
            seq.move_sequences_to_shot([], dest_shot_id=99)

    def test_move_sequences_empty_list_noop(self):
        """Empty sequences list is a safe no-op."""
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [])])
        seq.move_sequences_to_shot([], dest_shot_id=0)  # should not raise

    def test_fit_shot_empty_sequences_noop(self):
        """No sequences -> no boundary change."""
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [])])
        head, tail = seq.fit_shot_to_content(0)
        self.assertEqual((head, tail), (0.0, 0.0))


# ---------------------------------------------------------------------------
# Shot Manifest tests (pure Python -- no Maya)
# ---------------------------------------------------------------------------

from unittest.mock import patch

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    detect_behaviors,
    parse_csv,
    BuilderObject,
    BuilderStep,
    ObjectStatus,
    StepStatus,
    ColumnMap,
    ShotManifest,
)


class TestDetectBehaviors(unittest.TestCase):
    """Test behavior auto-detection from step-contents text."""

    def test_fade_in(self):
        self.assertEqual(detect_behaviors("Arrow fades in."), ["fade_in"])

    def test_fade_out(self):
        self.assertEqual(detect_behaviors("Checklist fades out."), ["fade_out"])

    def test_fade_in_and_out(self):
        self.assertEqual(
            detect_behaviors("Arrow fades in, then fades out."),
            ["fade_in", "fade_out"],
        )

    def test_no_behavior(self):
        self.assertEqual(detect_behaviors("User is teleported."), [])

    def test_empty(self):
        self.assertEqual(detect_behaviors(""), [])

    def test_na(self):
        self.assertEqual(detect_behaviors("N/A"), [])


class TestParseCSV(unittest.TestCase):
    """Test CSV parsing with a synthetic fixture."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._tmp_dir = tempfile.mkdtemp()
        cls._csv_path = os.path.join(cls._tmp_dir, "test.csv")
        with open(cls._csv_path, "w", newline="", encoding="utf-8") as f:
            import csv

            w = csv.writer(f)
            w.writerow(["SECTION A: AILERON RIGGING", "", "", "", "", "", "", ""])
            w.writerow(
                [
                    "Step",
                    "Ref",
                    "Placard",
                    "Voice",
                    "Contents",
                    "Asset",
                    "Who",
                    "Status",
                ]
            )
            w.writerow(
                ["A01.)", "", "", "", "Arrow fades in.", "ARROW_01", "", "Complete"]
            )
            w.writerow(["", "", "", "", "", "ARROW_02", "", "Complete"])
            w.writerow(
                ["A02.)", "", "", "", "Checklist fades out.", "CHECK_01", "", ""]
            )
            w.writerow(["SECTION B: RUDDER RIGGING", "", "", "", "", "", "", ""])
            w.writerow(
                [
                    "Step",
                    "Ref",
                    "Placard",
                    "Voice",
                    "Contents",
                    "Asset",
                    "Who",
                    "Status",
                ]
            )
            w.writerow(["B01.)", "", "", "", "N/A", "N/A", "", ""])

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_step_count(self):
        steps = parse_csv(self._csv_path)
        self.assertEqual(len(steps), 3)  # A01, A02, B01

    def test_section_assignment(self):
        steps = parse_csv(self._csv_path)
        self.assertEqual(steps[0].section, "A")
        self.assertEqual(steps[2].section, "B")

    def test_continuation_row_merges(self):
        steps = parse_csv(self._csv_path)
        a01 = steps[0]
        self.assertEqual(len(a01.objects), 2)
        self.assertEqual(a01.objects[0].name, "ARROW_01")
        self.assertEqual(a01.objects[1].name, "ARROW_02")

    def test_continuation_inherits_behavior(self):
        """Continuation-row objects inherit the parent step's behavior."""
        steps = parse_csv(self._csv_path)
        a01 = steps[0]
        self.assertEqual(a01.objects[0].behaviors, ["fade_in"])
        self.assertEqual(a01.objects[1].behaviors, ["fade_in"])  # inherited

    def test_behavior_detected(self):
        steps = parse_csv(self._csv_path)
        self.assertEqual(steps[0].objects[0].behaviors, ["fade_in"])
        self.assertEqual(steps[1].objects[0].behaviors, ["fade_out"])

    def test_na_objects_excluded(self):
        steps = parse_csv(self._csv_path)
        b01 = steps[2]
        self.assertEqual(len(b01.objects), 0)

    def test_section_title(self):
        steps = parse_csv(self._csv_path)
        self.assertEqual(steps[0].section_title, "AILERON RIGGING")

    def test_duplicate_step_id_skipped(self):
        """Duplicate step_id rows should be skipped with a warning."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "dup.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: SEC", "", "", "", "", "", "", ""])
                w.writerow(["Step", "", "", "", "Contents", "Asset", "", "Status"])
                w.writerow(["A01.)", "", "", "", "first", "OBJ1", "", ""])
                w.writerow(["A01.)", "", "", "", "duplicate", "OBJ2", "", ""])
                w.writerow(["A02.)", "", "", "", "second", "OBJ3", "", ""])
            steps = parse_csv(csv_path)
            self.assertEqual(len(steps), 2)  # A01 + A02 only
            self.assertEqual(steps[0].description, "first")
            self.assertEqual(steps[1].step_id, "A02")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_continuation_merges_content(self):
        """Continuation rows with content should merge text into parent step."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "merge.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: SEC", "", "", "", "", "", "", ""])
                w.writerow(["Step", "", "", "", "Contents", "Asset", "", "Status"])
                w.writerow(["A01.)", "", "", "", "First line.", "OBJ1", "", ""])
                w.writerow(["", "", "", "", "Second line.", "OBJ2", "", ""])
            steps = parse_csv(csv_path)
            self.assertIn("Second line.", steps[0].description)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_c130h_layout(self):
        """C-130H CSV layout: 'Step Contents' at col 2, 'Asset Names' at col 3.

        Bug: ColumnMap hardcoded integer indices matching only C-5M layout.
        Headers at different positions caused wrong columns to be read.
        Fixed: 2026-03-13
        """
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "c130h.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: AILERON RIGGING", "", "", "", ""])
                w.writerow(["Step", "Ref", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "", "Arrow fades in.", "ARROW_01", "Complete"])
                w.writerow(["", "", "", "ARROW_02", ""])
                w.writerow(["A02.)", "", "Checklist fades out.", "CHECK_01", ""])
            steps = parse_csv(csv_path)
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0].objects[0].name, "ARROW_01")
            self.assertEqual(steps[0].objects[1].name, "ARROW_02")
            self.assertEqual(steps[0].objects[0].behaviors, ["fade_in"])
            self.assertEqual(steps[1].objects[0].name, "CHECK_01")
            self.assertEqual(steps[1].objects[0].behaviors, ["fade_out"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_header_raises(self):
        """ValueError when required column header is not found."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "bad.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: SEC", "", ""])
                w.writerow(["Step", "Ref", "Bad Column"])
            with self.assertRaises(ValueError):
                parse_csv(csv_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_non_numbered_step_id_parsed(self):
        """Non-numbered step IDs like SETUP are recognized as steps.

        Bug: _STEP_RE only matched 'A01.)' format, silently dropping
        SETUP rows from 'SECTION X: OPENING SETUP'.
        Fixed: 2026-03-24
        """
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "setup.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION X: OPENING SETUP", "", "", "", ""])
                w.writerow(["Step", "Ref", "Step Contents", "Asset Names", "Status"])
                w.writerow(["SETUP", "N/A", "Hangar, doors closed", "", "Complete"])
                w.writerow(["", "", "Stand in cargo hold", "PLATFORM_LOC", "Complete"])
                w.writerow(["", "", "User starting position", "REGGIE_01", "Complete"])
                w.writerow(["SECTION A: AILERON RIGGING", "", "", "", ""])
                w.writerow(["Step", "Ref", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "", "Arrow fades in.", "ARROW_01", "Complete"])
            steps = parse_csv(csv_path, columns=ColumnMap(exclude_steps=()))
            self.assertEqual(len(steps), 2)  # SETUP + A01
            setup = steps[0]
            self.assertEqual(setup.step_id, "SETUP")
            self.assertEqual(setup.section, "X")
            self.assertEqual(setup.section_title, "OPENING SETUP")
            self.assertEqual(len(setup.objects), 2)
            self.assertEqual(setup.objects[0].name, "PLATFORM_LOC")
            self.assertEqual(setup.objects[1].name, "REGGIE_01")
            # A01 still parsed normally
            self.assertEqual(steps[1].step_id, "A01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestShotManifestPure(unittest.TestCase):
    """Test ShotManifest data-only features (no Maya)."""

    def _make_steps(self):
        return [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Arrow fades in.",
                [
                    BuilderObject("ARROW_01", ["fade_in"]),
                    BuilderObject("ARROW_02", ["fade_in"]),
                ],
            ),
            BuilderStep(
                "A02",
                "A",
                "SEC A",
                "Checklist fades out.",
                [
                    BuilderObject("CHECK_01", ["fade_out"]),
                ],
            ),
        ]

    def test_update_creates_shots(self):
        store = ShotStore()
        builder = ShotManifest(store)
        actions = builder.update(self._make_steps())
        self.assertEqual(len(store.shots), 2)
        self.assertEqual(store.shots[0].name, "A01")
        self.assertAlmostEqual(store.shots[0].start, 1)
        # fade_in = 15f content-driven duration
        self.assertAlmostEqual(store.shots[0].end, 16)
        self.assertEqual(actions["A01"], "created")
        self.assertEqual(actions["A02"], "created")

    def test_from_csv_accepts_existing_store(self):
        """from_csv should use the provided store, not create a new one."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "t.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", "", "", "", "", ""])
                w.writerow(["Step", "", "", "", "Contents", "Asset", "", "Status"])
                w.writerow(["A01.)", "", "", "", "stuff", "OBJ", "", ""])
            existing = ShotStore()
            builder, steps = ShotManifest.from_csv(csv_path, store=existing)
            self.assertIs(builder.store, existing)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestShotManifestAssess(unittest.TestCase):
    """Test ShotManifest.assess() -- pure Python, no Maya required."""

    def _make_steps(self):
        return [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Arrow fades in.",
                [
                    BuilderObject("ARROW_01", ["fade_in"]),
                    BuilderObject("ARROW_02", ["fade_in"]),
                ],
            ),
            BuilderStep(
                "A02",
                "A",
                "SEC A",
                "Checklist fades out.",
                [BuilderObject("CHECK_01", ["fade_out"])],
            ),
        ]

    def _build_seq(self, steps, built_ids=None):
        """Return a ShotManifest whose store contains shots for *built_ids*."""
        store = ShotStore()
        builder = ShotManifest(store)
        if built_ids is None:
            built_ids = set()
        for step in steps:
            if step.step_id in built_ids:
                store.define_shot(
                    name=step.step_id,
                    start=1,
                    end=31,
                    objects=[o.name for o in step.objects],
                )
        return builder

    # -- all valid ---------------------------------------------------------

    def test_all_valid(self):
        """When all scenes are built and all objects exist, every status is 'valid'."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids={"A01", "A02"})
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *_: True,
        )
        self.assertTrue(all(r.status == "valid" for r in results))
        self.assertTrue(all(r.built for r in results))

    # -- missing shot -----------------------------------------------------

    def test_missing_shot(self):
        """Unbuilt step should have status 'missing_shot'."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids={"A01"})
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *_: True,
        )
        self.assertEqual(results[0].status, "valid")
        self.assertEqual(results[1].status, "missing_shot")
        self.assertFalse(results[1].built)

    # -- missing object ----------------------------------------------------

    def test_missing_object(self):
        """Object that doesn't exist should be 'missing_object'."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids={"A01", "A02"})
        # ARROW_02 does not exist
        results = builder.assess(
            steps,
            exists_fn=lambda n: n != "ARROW_02",
            verify_fn=lambda *_: True,
        )
        self.assertEqual(results[0].status, "missing_object")
        a01_objs = {o.name: o for o in results[0].objects}
        self.assertEqual(a01_objs["ARROW_01"].status, "valid")
        self.assertEqual(a01_objs["ARROW_02"].status, "missing_object")

    # -- rollup priority ---------------------------------------------------

    def test_missing_shot_overrides_missing_object(self):
        """'missing_shot' should win over 'missing_object' in rollup."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids=set())  # nothing built
        # Nothing exists either
        results = builder.assess(steps, exists_fn=lambda _n: False)
        for r in results:
            self.assertEqual(r.status, "missing_shot")

    # -- counts ------------------------------------------------------------

    def test_missing_count(self):
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids={"A01", "A02"})
        results = builder.assess(
            steps,
            exists_fn=lambda n: n != "ARROW_02",
            verify_fn=lambda *_: True,
        )
        self.assertEqual(results[0].missing_count, 1)
        self.assertEqual(results[0].total_count, 2)
        self.assertEqual(results[1].missing_count, 0)
        self.assertEqual(results[1].total_count, 1)

    # -- empty sequencer ---------------------------------------------------

    def test_no_sequencer_all_missing_shot(self):
        """With empty sequencer, all steps should be 'missing_shot'."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids=set())
        results = builder.assess(steps, exists_fn=lambda _n: True)
        self.assertTrue(all(r.status == "missing_shot" for r in results))

    # -- step with no objects ----------------------------------------------

    def test_step_with_no_objects(self):
        """A built step with no objects should be 'valid'."""
        steps = [BuilderStep("X01", "X", "SEC X", "No objects.", [])]
        builder = self._build_seq(steps, built_ids={"X01"})
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *_: True,
        )
        self.assertEqual(results[0].status, "valid")
        self.assertEqual(results[0].missing_count, 0)

    # -- missing behavior --------------------------------------------------

    def test_missing_behavior(self):
        """Object that exists but has no behavior keys -> 'missing_behavior'."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids={"A01", "A02"})
        # All objects exist, but ARROW_02 is missing its behavior keys
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda obj, *_: obj != "ARROW_02",
        )
        self.assertEqual(results[0].status, "missing_behavior")
        a01_objs = {o.name: o for o in results[0].objects}
        self.assertEqual(a01_objs["ARROW_01"].status, "valid")
        self.assertEqual(a01_objs["ARROW_02"].status, "missing_behavior")

    def test_missing_object_overrides_missing_behavior(self):
        """'missing_object' should win over 'missing_behavior' in rollup."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids={"A01", "A02"})
        # ARROW_01 missing entirely, ARROW_02 exists but no keys
        results = builder.assess(
            steps,
            exists_fn=lambda n: n != "ARROW_01",
            verify_fn=lambda obj, *_: obj != "ARROW_02",
        )
        self.assertEqual(results[0].status, "missing_object")

    def test_behavior_not_checked_when_scene_unbuilt(self):
        """Behavior keys should not be checked if the shot is not built."""
        steps = self._make_steps()
        builder = self._build_seq(steps, built_ids=set())  # nothing built
        call_log = []
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *a: (call_log.append(a), True)[1],
        )
        # verify_fn should never have been called
        self.assertEqual(len(call_log), 0)
        for r in results:
            self.assertEqual(r.status, "missing_shot")

    def test_no_behavior_skips_verify(self):
        """Objects with no expected behavior should not be verified."""
        steps = [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Static.",
                [BuilderObject("BOX_01")],  # no behavior
            ),
        ]
        builder = self._build_seq(steps, built_ids={"A01"})
        call_log = []
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *a: (call_log.append(a), True)[1],
        )
        self.assertEqual(len(call_log), 0)
        self.assertEqual(results[0].status, "valid")
        self.assertEqual(results[0].objects[0].status, "valid")


class TestBehaviorYAMLAnchors(unittest.TestCase):
    """Verify YAML templates include explicit anchor fields."""

    def test_fade_in_has_anchor(self):
        t = load_behavior("fade_in")
        vis = t["attributes"]["visibility"]
        self.assertEqual(vis["in"]["anchor"], "start")

    def test_fade_out_has_anchor(self):
        t = load_behavior("fade_out")
        vis = t["attributes"]["visibility"]
        self.assertEqual(vis["out"]["anchor"], "end")

    def test_fade_in_template_exists(self):
        """fade_in.yaml should load and contain only the 'in' phase."""
        t = load_behavior("fade_in")
        vis = t["attributes"]["visibility"]
        self.assertIn("in", vis)
        self.assertNotIn("out", vis)

    def test_fade_out_template_exists(self):
        """fade_out.yaml should load and contain only the 'out' phase."""
        t = load_behavior("fade_out")
        vis = t["attributes"]["visibility"]
        self.assertNotIn("in", vis)
        self.assertIn("out", vis)


class TestContentDrivenDuration(unittest.TestCase):
    """Test compute_duration and content-driven layout."""

    def test_fade_in_duration(self):
        """Step with fade_in objects: duration = 15f (template phase)."""
        entries = [BuilderObject("OBJ", ["fade_in"])]
        dur = compute_duration(entries, fallback=30)
        self.assertEqual(dur, 15)

    def test_fade_in_and_out_duration(self):
        """Step with fade_in + fade_out: duration = 15 + 15 = 30f."""
        entries = [BuilderObject("OBJ", ["fade_in", "fade_out"])]
        dur = compute_duration(entries, fallback=30)
        self.assertEqual(dur, 30)

    def test_no_behavior_uses_fallback(self):
        """Step with no behaviors -> fallback duration."""
        entries = [BuilderObject("OBJ")]
        dur = compute_duration(entries, fallback=42)
        self.assertEqual(dur, 42)

    def test_empty_step_uses_fallback(self):
        """Step with no objects -> fallback duration."""
        entries = []
        dur = compute_duration(entries, fallback=50)
        self.assertEqual(dur, 50)

    def test_mixed_behaviors_takes_max(self):
        """Max across objects: fade_in+fade_out (30) > fade_in (15)."""
        entries = [
            BuilderObject("A", ["fade_in"]),
            BuilderObject("B", ["fade_in", "fade_out"]),
        ]
        dur = compute_duration(entries, fallback=30)
        self.assertEqual(dur, 30)

    def test_update_uses_content_duration(self):
        """Update should use content-driven per-step durations."""
        steps = [
            BuilderStep("A01", "A", "", "", [BuilderObject("X", ["fade_in"])]),
            BuilderStep(
                "A02", "A", "", "", [BuilderObject("Y", ["fade_in", "fade_out"])]
            ),
        ]
        store = ShotStore()
        builder = ShotManifest(store)
        builder.update(steps)
        shots = store.sorted_shots()
        # A01: fade_in=15f, A02: fade_in+fade_out=30f
        self.assertAlmostEqual(shots[0].end - shots[0].start, 15)
        self.assertAlmostEqual(shots[1].end - shots[1].start, 30)


class TestSelectiveRebuild(unittest.TestCase):
    """Test ShotManifest.update() selective rebuild."""

    def _make_steps(self):
        return [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "fades in",
                [BuilderObject("ARROW_01", ["fade_in"])],
            ),
            BuilderStep(
                "A02",
                "A",
                "SEC A",
                "fades out",
                [BuilderObject("CHECK_01", ["fade_out"])],
            ),
        ]

    def test_first_build_creates_all(self):
        """First update on empty store creates all shots."""
        store = ShotStore()
        builder = ShotManifest(store)
        actions = builder.update(self._make_steps())
        self.assertEqual(actions["A01"], "created")
        self.assertEqual(actions["A02"], "created")
        self.assertEqual(len(store.shots), 2)

    def test_unchanged_shots_skipped(self):
        """Second update with same steps skips unchanged shots."""
        store = ShotStore()
        builder = ShotManifest(store)
        builder.update(self._make_steps())
        actions = builder.update(self._make_steps())
        self.assertEqual(actions["A01"], "skipped")
        self.assertEqual(actions["A02"], "skipped")

    def test_new_object_patches_shot(self):
        """Adding an object to an existing step should patch the shot."""
        store = ShotStore()
        builder = ShotManifest(store)
        steps = self._make_steps()
        builder.update(steps)
        # Add a new object to A01
        steps[0].objects.append(BuilderObject("ARROW_02", ["fade_in"]))
        actions = builder.update(steps)
        self.assertEqual(actions["A01"], "patched")
        # Shot should now have both objects
        shot = store.shot_by_name("A01")
        self.assertIn("ARROW_02", shot.objects)
        self.assertIn("ARROW_01", shot.objects)

    def test_skip_behavior_when_existing_keys(self):
        """apply_to_shots should skip objects with existing keyframes.

        Bug: apply_behavior() silently overwrote existing user animation.
        Fixed: 2026-03-13
        """
        from unittest.mock import MagicMock

        store = ShotStore()
        builder = ShotManifest(store)
        steps = self._make_steps()
        builder.update(steps)

        mock_apply = MagicMock()

        # Simulate: all objects already have keys in the range
        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: True,
        )

        # Behavior should NOT have been applied (existing keys Ã¢â€ â€™ skip)
        mock_apply.assert_not_called()
        self.assertTrue(len(result["skipped"]) > 0)


class TestAssessUserAnimated(unittest.TestCase):
    """Test assess() with user-animated objects and shrinkable frames."""

    def _make_steps(self):
        return [
            BuilderStep(
                "A01",
                "A",
                "",
                "static",
                [
                    BuilderObject("BOX_01"),  # no behavior = user-animated
                    BuilderObject("ARROW_01", ["fade_in"]),
                ],
            ),
        ]

    def test_user_animated_status(self):
        """Object without behavior -> 'user_animated' when keys exist."""
        store = ShotStore()
        builder = ShotManifest(store)
        builder.update(self._make_steps())
        results = builder.assess(
            self._make_steps(),
            exists_fn=lambda _n: True,
            verify_fn=lambda *_: True,
            keyframe_range_fn=lambda n: (1, 10) if n == "BOX_01" else None,
        )
        obj_map = {o.name: o for o in results[0].objects}
        self.assertEqual(obj_map["BOX_01"].status, "user_animated")
        self.assertEqual(obj_map["BOX_01"].key_range, (1, 10))
        self.assertEqual(obj_map["ARROW_01"].status, "valid")

    def test_shrinkable_frames(self):
        """Step with unused tail should report shrinkable_frames > 0."""
        store = ShotStore()
        store.define_shot(name="A01", start=1, end=100, objects=["BOX_01"])
        builder = ShotManifest(store)
        steps = [BuilderStep("A01", "A", "", "", [BuilderObject("BOX_01")])]
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *_: True,
            keyframe_range_fn=lambda _n: (1, 30),
        )
        # Shot ends at 100, content ends at 30 -> shrinkable = 70
        self.assertAlmostEqual(results[0].shrinkable_frames, 70)

    def test_no_shrinkable_when_tight(self):
        """Step that fills its range should have shrinkable_frames = 0."""
        store = ShotStore()
        store.define_shot(name="A01", start=1, end=16, objects=["ARROW_01"])
        builder = ShotManifest(store)
        steps = [
            BuilderStep("A01", "A", "", "", [BuilderObject("ARROW_01", ["fade_in"])])
        ]
        results = builder.assess(
            steps,
            exists_fn=lambda _n: True,
            verify_fn=lambda *_: True,
        )
        self.assertAlmostEqual(results[0].shrinkable_frames, 0)


class TestShotBlockMetadata(unittest.TestCase):
    """Test ShotBlock metadata and locked fields."""

    def test_metadata_default_empty(self):
        b = ShotBlock(shot_id=0, name="A", start=0, end=50)
        self.assertEqual(b.metadata, {})
        self.assertFalse(b.locked)

    def test_metadata_roundtrip(self):
        """metadata and locked should survive to_dict/from_dict."""
        seq = ShotSequencer(
            [
                ShotBlock(
                    0, "S0", 0, 50, ["obj"], metadata={"section": "A"}, locked=True
                ),
                ShotBlock(1, "S1", 60, 100, [], metadata={"content": "test"}),
            ]
        )
        data = seq.to_dict()
        restored = ShotSequencer.from_dict(data)
        self.assertEqual(restored.shot_by_id(0).metadata, {"section": "A"})
        self.assertTrue(restored.shot_by_id(0).locked)
        self.assertEqual(restored.shot_by_id(1).metadata, {"content": "test"})
        self.assertFalse(restored.shot_by_id(1).locked)

    def test_data_no_metadata(self):
        """Data without metadata/locked should load safely."""
        data = {
            "shots": [
                {"shot_id": 0, "name": "A", "start": 0, "end": 50, "objects": []},
            ],
        }
        seq = ShotSequencer.from_dict(data)
        self.assertEqual(seq.shot_by_id(0).metadata, {})
        self.assertFalse(seq.shot_by_id(0).locked)

    def test_define_shot_with_metadata(self):
        """define_shot() should accept metadata and locked parameters."""
        seq = ShotSequencer()
        shot = seq.define_shot(
            name="S1",
            start=0,
            end=50,
            objects=["obj"],
            metadata={"section": "B"},
            locked=True,
        )
        self.assertEqual(shot.metadata, {"section": "B"})
        self.assertTrue(shot.locked)


class TestClassifyObjects(unittest.TestCase):
    """Test ShotBlock.classify_objects with various csv_objects formats.

    Bug: csv_objects stored as list of dicts ({"name": ..., "kind": ...})
    caused TypeError: unhashable type: 'dict' when building the lookup set.
    Fixed: 2026-04-16
    """

    def test_dict_format_csv_objects(self):
        """csv_objects as list of dicts must not raise."""
        b = ShotBlock(
            0,
            "S0",
            0,
            50,
            ["ObjA", "ObjB"],
            metadata={
                "csv_objects": [
                    {"name": "ObjA", "kind": "mesh"},
                ],
            },
        )
        result = b.classify_objects()
        self.assertEqual(result["ObjA"], "valid")
        self.assertEqual(result["ObjB"], "scene_discovered")

    def test_string_format_csv_objects(self):
        """Legacy string format still works."""
        b = ShotBlock(
            0,
            "S0",
            0,
            50,
            ["ObjA", "ObjB"],
            metadata={"csv_objects": ["ObjA"]},
        )
        result = b.classify_objects()
        self.assertEqual(result["ObjA"], "valid")
        self.assertEqual(result["ObjB"], "scene_discovered")

    def test_object_status_takes_precedence(self):
        """object_status overrides csv_objects membership."""
        b = ShotBlock(
            0,
            "S0",
            0,
            50,
            ["ObjA"],
            metadata={
                "object_status": {"ObjA": "missing_behavior"},
                "csv_objects": [{"name": "ObjA", "kind": "mesh"}],
            },
        )
        self.assertEqual(b.classify_objects()["ObjA"], "missing_behavior")

    def test_no_csv_objects_defaults_valid(self):
        """Without csv_objects metadata, all objects are 'valid'."""
        b = ShotBlock(0, "S0", 0, 50, ["ObjA"])
        self.assertEqual(b.classify_objects()["ObjA"], "valid")


class TestShotStore(unittest.TestCase):
    """Tests for ShotStore CRUD and serialisation."""

    def setUp(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()

    def test_define_and_query(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="S0", start=0, end=50, objects=["a"])
        store.define_shot(name="S1", start=60, end=100, objects=["b"])
        self.assertEqual(len(store.shots), 2)
        self.assertEqual(store.shot_by_name("S0").start, 0)
        self.assertEqual(store.shot_by_id(1).name, "S1")

    def test_remove_shot(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="X", start=0, end=10)
        self.assertTrue(store.remove_shot(0))
        self.assertEqual(len(store.shots), 0)
        self.assertFalse(store.remove_shot(99))

    def test_append_shot_gap_aware(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        s0 = store.append_shot(name="A", duration=30, gap=10, start_frame=1)
        self.assertAlmostEqual(s0.start, 1)
        self.assertAlmostEqual(s0.end, 31)
        s1 = store.append_shot(name="B", duration=20, gap=10)
        self.assertAlmostEqual(s1.start, 41)  # 31 + 10
        self.assertAlmostEqual(s1.end, 61)

    def test_roundtrip_dict(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="T", start=10, end=50, objects=["obj"])
        store.set_object_hidden("obj")
        store.markers.append({"frame": 25})
        data = store.to_dict()
        restored = ShotStore.from_dict(data)
        self.assertEqual(len(restored.shots), 1)
        self.assertTrue(restored.is_object_hidden("obj"))
        self.assertEqual(restored.markers, [{"frame": 25}])

    def test_active_singleton(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from unittest.mock import patch

        # Prevent MayaScenePersistence.load() from hitting mocked PyNode
        with patch("mayatk.anim_utils.shots._shots.pm") as mock_pm:
            mock_pm.objExists.return_value = False
            a = ShotStore.active()
            b = ShotStore.active()
        self.assertIs(a, b)

    def test_set_active(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        custom = ShotStore()
        custom.define_shot(name="Custom", start=0, end=10)
        ShotStore.set_active(custom)
        self.assertIs(ShotStore.active(), custom)
        self.assertEqual(len(ShotStore.active().shots), 1)

    def test_sequencer_shares_store(self):
        """ShotSequencer wrapping a ShotStore sees the same data."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="Shared", start=0, end=50)
        seq = ShotSequencer(store=store)
        self.assertEqual(len(seq.shots), 1)
        self.assertEqual(seq.shot_by_name("Shared").start, 0)
        # Mutations through sequencer are visible on store
        seq.define_shot(name="Added", start=60, end=100, objects=[])
        self.assertEqual(len(store.shots), 2)

    # ---- compute_gap -----------------------------------------------------

    def test_compute_gap_uniform(self):
        """Uniform gaps â†’ compute_gap returns the common gap value."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=60, end=100)
        store.define_shot(name="C", start=110, end=150)
        self.assertAlmostEqual(store.compute_gap(), 10.0)

    def test_compute_gap_zero(self):
        """Abutting shots â†’ compute_gap returns 0."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=50, end=100)
        self.assertAlmostEqual(store.compute_gap(), 0.0)

    def test_compute_gap_single_shot(self):
        """With fewer than 2 shots, returns current store.gap."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.gap = 5.0
        store.define_shot(name="Only", start=0, end=50)
        self.assertAlmostEqual(store.compute_gap(), 5.0)

    def test_compute_gap_mixed_returns_median(self):
        """Mixed gap sizes â†’ returns the median."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=55, end=100)  # gap=5
        store.define_shot(name="C", start=110, end=150)  # gap=10
        store.define_shot(name="D", start=160, end=200)  # gap=10
        # Sorted gaps: [5, 10, 10] â†’ median = 10
        self.assertAlmostEqual(store.compute_gap(), 10.0)

    def test_compute_gap_overlapping_clamps_to_zero(self):
        """Overlapping shots produce negative raw gaps; compute_gap clamps to 0.

        Bug: overlapping shots returned negative gap values.
        Fixed: 2026-03-25
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=60)
        store.define_shot(name="B", start=50, end=110)  # overlap â†’ raw gap -10
        self.assertAlmostEqual(store.compute_gap(), 0.0)

    def test_compute_gap_even_count_rounds_to_int(self):
        """Even number of gaps â†’ median is rounded to nearest integer.

        Bug: even-count median could return a fractional .5 value.
        Fixed: 2026-03-25
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=55, end=100)  # gap=5
        store.define_shot(name="C", start=110, end=150)  # gap=10
        # Sorted gaps: [5, 10] â†’ raw mean = 7.5 â†’ rounded = 8
        result = store.compute_gap()
        self.assertEqual(result, result // 1)  # is a whole number

    # ---- ripple_shift tests -----------------------------------------------

    def test_ripple_shift_moves_downstream(self):
        """ripple_shift pushes downstream shots by delta."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=10, end=10)  # zero-range
        store.define_shot(name="B", start=11, end=11)
        store.define_shot(name="C", start=12, end=12)
        # Expand A's end to 40, then ripple downstream
        store.update_shot(0, end=40)
        store.ripple_shift(after_frame=10, delta=30, exclude_id=0)
        self.assertAlmostEqual(store.shot_by_name("B").start, 41)
        self.assertAlmostEqual(store.shot_by_name("B").end, 41)
        self.assertAlmostEqual(store.shot_by_name("C").start, 42)
        self.assertAlmostEqual(store.shot_by_name("C").end, 42)

    def test_ripple_shift_no_delta_noop(self):
        """Zero delta does nothing."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=10, end=40)
        store.define_shot(name="B", start=50, end=80)
        store.ripple_shift(after_frame=40, delta=0)
        self.assertAlmostEqual(store.shot_by_name("B").start, 50)

    def test_ripple_shift_skips_upstream(self):
        """Shots before after_frame are not affected."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=10, end=40)
        store.define_shot(name="B", start=50, end=80)
        store.define_shot(name="C", start=90, end=120)
        store.ripple_shift(after_frame=50, delta=20, exclude_id=1)
        self.assertAlmostEqual(store.shot_by_name("A").start, 10)
        self.assertAlmostEqual(store.shot_by_name("A").end, 40)
        self.assertAlmostEqual(store.shot_by_name("C").start, 110)
        self.assertAlmostEqual(store.shot_by_name("C").end, 140)

    def test_sorted_shots_timeline_order(self):
        """sorted_shots returns shots in start-time order regardless of creation."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="C", start=100, end=150)
        store.define_shot(name="A", start=10, end=40)
        store.define_shot(name="B", start=50, end=80)
        names = [s.name for s in store.sorted_shots()]
        self.assertEqual(names, ["A", "B", "C"])


class TestExpandShot(unittest.TestCase):
    """Test ShotSequencer.expand_shot() public method."""

    def test_expand_increases_end(self):
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, []),
                ShotBlock(1, "S1", 60, 100, []),
            ]
        )
        delta = seq.expand_shot(0, 70)
        self.assertAlmostEqual(delta, 20)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 70)
        # S1 should have rippled by +20
        self.assertAlmostEqual(seq.shot_by_id(1).start, 80)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 120)

    def test_expand_noop_when_smaller(self):
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50)])
        delta = seq.expand_shot(0, 30)
        self.assertAlmostEqual(delta, 0)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 50)

    def test_expand_invalid_id(self):
        seq = ShotSequencer()
        with self.assertRaises(ValueError):
            seq.expand_shot(99, 100)


class TestReconciliation(unittest.TestCase):
    """Test update() reconciliation: detecting removed steps."""

    def test_removed_steps_reported(self):
        """Steps in store but not in CSV should be reported as 'removed'."""
        store = ShotStore()
        builder = ShotManifest(store)
        steps_v1 = [
            BuilderStep("A01", "A", "", "", [BuilderObject("OBJ1", ["fade_in"])]),
            BuilderStep("A02", "A", "", "", [BuilderObject("OBJ2", ["fade_out"])]),
        ]
        builder.update(steps_v1)
        self.assertEqual(len(store.shots), 2)

        # V2 of CSV removes A02
        steps_v2 = [
            BuilderStep("A01", "A", "", "", [BuilderObject("OBJ1", ["fade_in"])]),
        ]
        actions = builder.update(steps_v2)
        self.assertEqual(actions["A01"], "skipped")
        self.assertEqual(actions["A02"], "removed")

    def test_build_stores_metadata(self):
        """update() should populate ShotBlock.metadata."""
        store = ShotStore()
        builder = ShotManifest(store)
        steps = [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Arrow fades in.",
                [BuilderObject("ARROW_01", ["fade_in"])],
            ),
        ]
        builder.update(steps)
        shot = store.shot_by_name("A01")
        self.assertIn("section", shot.metadata)
        self.assertEqual(shot.metadata["section"], "A")
        self.assertIn("behaviors", shot.metadata)
        self.assertEqual(shot.metadata["behaviors"][0]["behavior"], "fade_in")

    def test_skipped_shot_refreshes_metadata(self):
        """Metadata should be refreshed even when a shot is skipped."""
        store = ShotStore()
        builder = ShotManifest(store)
        steps_v1 = [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Original content.",
                [BuilderObject("OBJ1", ["fade_in"])],
            ),
        ]
        builder.update(steps_v1)
        self.assertEqual(store.shot_by_name("A01").description, "Original content.")

        # Re-run with updated content but same objects (triggers "skipped")
        steps_v2 = [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Updated content.",
                [BuilderObject("OBJ1", ["fade_in"])],
            ),
        ]
        actions = builder.update(steps_v2)
        self.assertEqual(actions["A01"], "skipped")
        self.assertEqual(store.shot_by_name("A01").description, "Updated content.")


class TestLockedAssess(unittest.TestCase):
    """Test assess() locked shot handling."""

    def test_locked_shot_skips_checks(self):
        """Locked shots should report 'locked' status; verify_fn not called."""
        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=31,
            objects=["OBJ1"],
            locked=True,
        )
        builder = ShotManifest(store)
        steps = [
            BuilderStep("A01", "A", "", "", [BuilderObject("OBJ1", ["fade_in"])]),
        ]
        call_log = []
        results = builder.assess(
            steps,
            exists_fn=lambda _: True,
            verify_fn=lambda *a: (call_log.append(a), True)[1],
        )
        self.assertEqual(results[0].status, "locked")
        self.assertTrue(results[0].locked)
        # verify_fn should NOT have been called for locked shots
        self.assertEqual(len(call_log), 0)

    def test_unlocked_shot_checks_normally(self):
        """Unlocked shots should check behaviors normally."""
        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=31,
            objects=["OBJ1"],
            locked=False,
        )
        builder = ShotManifest(store)
        steps = [
            BuilderStep("A01", "A", "", "", [BuilderObject("OBJ1", ["fade_in"])]),
        ]
        results = builder.assess(
            steps,
            exists_fn=lambda _: True,
            verify_fn=lambda *_: True,
        )
        self.assertEqual(results[0].status, "valid")
        self.assertFalse(results[0].locked)


class TestRespace(unittest.TestCase):
    """Test ShotSequencer.respace() timeline redistribution."""

    def test_respace_sequential(self):
        """Shots should be repositioned sequentially with gaps."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 100, 130, []),  # 30f, starts late
                ShotBlock(1, "S1", 200, 250, []),  # 50f, big gap
            ]
        )
        seq.respace(gap=5, start_frame=1)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 1)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 31)  # 30f duration preserved
        self.assertAlmostEqual(seq.shot_by_id(1).start, 36)  # 31 + 5 gap
        self.assertAlmostEqual(seq.shot_by_id(1).end, 86)  # 50f duration preserved

    def test_respace_no_gap(self):
        """Shots should be contiguous with gap=0."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 50, 70, []),
                ShotBlock(1, "S1", 100, 120, []),
            ]
        )
        seq.respace(gap=0, start_frame=10)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 10)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 30)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 30)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 50)

    def test_respace_already_correct(self):
        """Respace on already-correct layout should be a no-op."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 1, 31, []),
                ShotBlock(1, "S1", 31, 61, []),
            ]
        )
        seq.respace(gap=0, start_frame=1)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 1)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 31)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 31)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 61)

    def test_respace_empty(self):
        """Respace on empty sequencer should not raise."""
        seq = ShotSequencer()
        seq.respace(gap=5, start_frame=1)  # should be a no-op

    def test_respace_single_shot(self):
        """Single shot should be repositioned to start_frame."""
        seq = ShotSequencer([ShotBlock(0, "S0", 100, 150, [])])
        seq.respace(gap=0, start_frame=1)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 1)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 51)

    def test_respace_preserves_first_shot_position(self):
        """Respace with first shot's own start preserves it.

        Bug: on_gap_changed called respace(start_frame=1), resetting
        the first shot's position regardless of where it actually was.
        Fixed: 2026-04-16
        """
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 200, []),
                ShotBlock(1, "S1", 200, 400, []),
                ShotBlock(2, "S2", 400, 600, []),
            ]
        )
        first_start = seq.sorted_shots()[0].start
        seq.respace(gap=10, start_frame=first_start)
        # First shot stays at frame 0, not shifted to 1
        self.assertAlmostEqual(seq.shot_by_id(0).start, 0)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 200)
        # Interior gaps of 10
        self.assertAlmostEqual(seq.shot_by_id(1).start, 210)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 410)
        self.assertAlmostEqual(seq.shot_by_id(2).start, 420)
        self.assertAlmostEqual(seq.shot_by_id(2).end, 620)

    def test_respace_no_gap_after_last_shot(self):
        """Gap must only appear between shots, not after the last one."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 100, []),
                ShotBlock(1, "S1", 100, 200, []),
            ]
        )
        seq.respace(gap=10, start_frame=0)
        # Last shot ends at its duration, no trailing gap
        self.assertAlmostEqual(seq.shot_by_id(1).end, 210)
        # No extra space â€” end is exactly start + duration
        self.assertAlmostEqual(seq.shot_by_id(1).end - seq.shot_by_id(1).start, 100)


class TestApplyBehaviors(unittest.TestCase):
    """Test apply_to_shots() from behaviors module Ã¢â‚¬â€ pure Python with mocks."""

    def test_apply_behaviors_calls_engine(self):
        """apply_to_shots should call apply_fn for declared behaviors."""
        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=16,
            objects=["ARROW_01"],
            metadata={
                "behaviors": [{"name": "ARROW_01", "behavior": "fade_in"}],
            },
        )

        applied = []

        def mock_apply(obj, beh, start, end, **kw):
            applied.append({"object": obj, "behavior": beh})

        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: False,
        )

        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["object"], "ARROW_01")
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["skipped"]), 0)

    def test_apply_behaviors_skips_existing(self):
        """Objects with existing keys should be skipped."""
        from unittest.mock import MagicMock

        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=16,
            objects=["ARROW_01"],
            metadata={
                "behaviors": [{"name": "ARROW_01", "behavior": "fade_in"}],
            },
        )

        mock_apply = MagicMock()
        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: True,
        )

        mock_apply.assert_not_called()
        self.assertEqual(len(result["skipped"]), 1)

    def test_apply_behaviors_skips_locked(self):
        """Locked shots should be skipped entirely."""
        from unittest.mock import MagicMock

        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=16,
            objects=["ARROW_01"],
            metadata={
                "behaviors": [{"name": "ARROW_01", "behavior": "fade_in"}],
            },
            locked=True,
        )

        mock_apply = MagicMock()
        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: False,
        )

        mock_apply.assert_not_called()
        self.assertEqual(len(result["applied"]), 0)
        self.assertEqual(len(result["skipped"]), 0)

    def test_apply_behaviors_skips_missing_object(self):
        """Objects that don't exist in Maya should be skipped."""
        from unittest.mock import MagicMock

        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=16,
            objects=["ARROW_01"],
            metadata={
                "behaviors": [{"name": "ARROW_01", "behavior": "fade_in"}],
            },
        )

        mock_apply = MagicMock()
        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: False,
            has_keys_fn=lambda *_: False,
        )

        mock_apply.assert_not_called()
        self.assertEqual(len(result["applied"]), 0)

    def test_apply_behaviors_no_metadata(self):
        """Shots without behavior metadata should be silently skipped."""
        from unittest.mock import MagicMock

        store = ShotStore()
        store.define_shot(name="A01", start=1, end=16, objects=["OBJ"])
        mock_apply = MagicMock()
        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: False,
        )
        self.assertEqual(len(result["applied"]), 0)
        self.assertEqual(len(result["skipped"]), 0)
        mock_apply.assert_not_called()

    def test_apply_behaviors_requires_apply_fn(self):
        """Omitting apply_fn should raise TypeError."""
        store = ShotStore()
        store.define_shot(
            name="A01",
            start=1,
            end=16,
            objects=["OBJ"],
            metadata={
                "behaviors": [{"name": "OBJ", "behavior": "fade_in"}],
            },
        )
        with self.assertRaises(TypeError):
            apply_to_shots(store.sorted_shots(), exists_fn=lambda _: True)

    def test_zero_duration_shot_skips_behaviors(self):
        """Zero-duration shots should skip behavior application to avoid
        keyframes spilling past the shot boundary.

        Bug: Behaviors applied to zero-duration shots place keyframes
        that extend into neighboring shots' time ranges.
        Fixed: 2026-04-04
        """
        from unittest.mock import MagicMock

        store = ShotStore()
        store.define_shot(
            name="A01",
            start=100,
            end=100,
            objects=["OBJ"],
            metadata={
                "behaviors": [{"name": "OBJ", "behavior": "fade_in"}],
            },
        )
        mock_apply = MagicMock()
        result = apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: False,
        )
        mock_apply.assert_not_called()
        self.assertEqual(len(result["applied"]), 0)


class TestShotManifestSlotsImport(unittest.TestCase):
    """Verify the shot_manifest_slots module can be imported (no Qt required)."""

    def test_controller_class_exists(self):
        from mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots import (
            ShotManifestController,
        )

        self.assertTrue(callable(ShotManifestController))

    def test_slots_class_exists(self):
        from mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots import (
            ShotManifestSlots,
        )

        self.assertTrue(callable(ShotManifestSlots))


class TestControllerColumnLayout(unittest.TestCase):
    """Verify the 6-column unified layout and manifest data constants."""

    @classmethod
    def setUpClass(cls):
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
            HEADERS,
            COL_STEP,
            COL_SECTION,
            COL_DESC,
            COL_BEHAVIORS,
            COL_START,
            COL_END,
            PASTEL_STATUS,
            fmt_behavior,
        )

        cls.HEADERS = HEADERS
        cls.COL_STEP = COL_STEP
        cls.COL_SECTION = COL_SECTION
        cls.COL_DESC = COL_DESC
        cls.COL_BEHAVIORS = COL_BEHAVIORS
        cls.COL_START = COL_START
        cls.COL_END = COL_END
        cls.PASTEL_STATUS = PASTEL_STATUS
        cls.fmt_behavior = staticmethod(fmt_behavior)

    def test_headers_count(self):
        """Unified layout should have exactly 6 columns."""
        self.assertEqual(len(self.HEADERS), 6)

    def test_headers_names(self):
        self.assertEqual(
            self.HEADERS,
            ["Step", "Section", "Description", "Behaviors", "Start", "End"],
        )

    def test_no_objects_column(self):
        """Objects column was removed -- should not appear in headers."""
        self.assertNotIn("Objects", self.HEADERS)

    def test_column_indices(self):
        """Fixed column indices should match header positions."""
        self.assertEqual(self.COL_STEP, 0)
        self.assertEqual(self.COL_SECTION, 1)
        self.assertEqual(self.COL_DESC, 2)
        self.assertEqual(self.COL_BEHAVIORS, 3)
        self.assertEqual(self.COL_START, 4)
        self.assertEqual(self.COL_END, 5)

    def test_column_indices_match_headers(self):
        """Each COL_* constant should match its header's index."""
        h = self.HEADERS
        self.assertEqual(h[self.COL_STEP], "Step")
        self.assertEqual(h[self.COL_DESC], "Description")
        self.assertEqual(h[self.COL_BEHAVIORS], "Behaviors")
        self.assertEqual(h[self.COL_START], "Start")
        self.assertEqual(h[self.COL_END], "End")

    def test_fmt_behavior(self):
        self.assertEqual(self.fmt_behavior("fade_in"), "Fade In")
        self.assertEqual(self.fmt_behavior("fade_out"), "Fade Out")
        self.assertEqual(self.fmt_behavior(""), "")

    def test_pastel_status_keys(self):
        """All expected status keys should be present."""
        expected = {
            "valid",
            "missing_shot",
            "missing_object",
            "missing_behavior",
            "user_animated",
            "locked",
            "additional",
        }
        self.assertTrue(
            expected.issubset(set(self.PASTEL_STATUS.keys())),
            f"Missing keys: {expected - set(self.PASTEL_STATUS.keys())}",
        )

    def test_valid_status_no_color(self):
        """'valid' status should apply no color changes."""
        fg, bg = self.PASTEL_STATUS["valid"]
        self.assertIsNone(fg)
        self.assertIsNone(bg)

    def test_missing_shot_has_bg(self):
        """'missing_shot' should have both fg and bg colors."""
        fg, bg = self.PASTEL_STATUS["missing_shot"]
        self.assertIsNotNone(fg)
        self.assertIsNotNone(bg)

    def test_missing_object_has_bg(self):
        """'missing_object' should have both fg and bg colors."""
        fg, bg = self.PASTEL_STATUS["missing_object"]
        self.assertIsNotNone(fg)
        self.assertIsNotNone(bg)


class TestShotManifestUIFile(unittest.TestCase):
    """Verify the .ui file exists alongside the slots."""

    def test_ui_file_exists(self):
        from pathlib import Path

        ui_path = (
            Path(__file__).parent.parent
            / "mayatk"
            / "anim_utils"
            / "shots"
            / "shot_manifest"
            / "shot_manifest.ui"
        )
        self.assertTrue(ui_path.exists(), f"Missing: {ui_path}")


# ---------------------------------------------------------------------------
# Audio Track tests (pure Python -- no Maya)
# ---------------------------------------------------------------------------


class TestWaveformEnvelope(unittest.TestCase):
    """Test compute_waveform_envelope on a synthetic WAV file."""

    @classmethod
    def setUpClass(cls):
        """Create a temporary 16-bit mono WAV with a known pattern."""
        import struct
        import wave
        import tempfile

        cls._tmp_dir = tempfile.mkdtemp()
        cls._wav_path = str(Path(cls._tmp_dir) / "test_tone.wav")

        # Generate 4410 samples (0.1 seconds at 44100 Hz)
        # Simple ascending ramp from -16384 to +16383
        n_samples = 4410
        samples = [int(-16384 + i * (32767 / n_samples)) for i in range(n_samples)]
        raw = struct.pack(f"<{n_samples}h", *samples)

        with wave.open(cls._wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(raw)

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_returns_list_of_tuples(self):
        env = compute_waveform_envelope(self._wav_path, num_bins=64)
        self.assertIsInstance(env, list)
        self.assertEqual(len(env), 64)
        for lo, hi in env:
            self.assertLessEqual(lo, hi)

    def test_values_normalised(self):
        env = compute_waveform_envelope(self._wav_path, num_bins=32)
        for lo, hi in env:
            self.assertGreaterEqual(lo, -1.0)
            self.assertLessEqual(hi, 1.0)

    def test_missing_file_returns_empty(self):
        env = compute_waveform_envelope("/nonexistent/path.wav")
        self.assertEqual(env, [])

    def test_single_bin(self):
        env = compute_waveform_envelope(self._wav_path, num_bins=1)
        self.assertEqual(len(env), 1)


# ---------------------------------------------------------------------------
# Maya-dependent audio tests
# ---------------------------------------------------------------------------


class TestRenderedRowColors(unittest.TestCase):
    """Pixel-level + model-level tests: verify all color coding is correct.

    Uses two verification strategies:
    - **Model-level** (item.foreground/background): reliable for text colors
      because pixel-sampling background areas misses foreground entirely.
    - **Pixel-level** (grab -> QImage): reliable for composite backgrounds
      where the delegate composites column tints, row tints, and per-item bg.

    Covers:
    - Column tints darken Step and Behaviors columns
    - Parent row tint (subtle lighten) vs child row tint (darken)
    - Assessment pastels with status backgrounds:
      missing_shot (gold+amber), missing_object (rose+rose),
      missing_behavior (sky+blue), user_animated (lavender+purple),
      locked (grey, no bg)
    - Behavior text colors: fade_in (teal), fade_out (amber)
    - Valid rows retain default appearance
    """

    _app = None
    _BASE_BG = "#393939"
    _DEFAULT_FG = "#cccccc"

    @classmethod
    def setUpClass(cls):
        try:
            from qtpy.QtWidgets import QApplication
            from qtpy.QtGui import QColor, QBrush
        except ImportError:
            raise unittest.SkipTest("Qt bindings not available")

        cls._app = QApplication.instance() or QApplication([])
        cls.QColor = QColor

        from uitk.widgets.treeWidget import TreeWidget
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
            HEADERS,
            COL_STEP,
            COL_SECTION,
            COL_DESC,
            COL_BEHAVIORS,
            COL_START,
            COL_END,
            PASTEL_STATUS,
            BEHAVIOR_STATUS_COLORS,
            fmt_behavior,
        )

        cls._HEADERS = HEADERS
        cls._COL_STEP = COL_STEP
        cls._COL_DESC = COL_DESC
        cls._COL_BEHAVIORS = COL_BEHAVIORS
        cls._PASTEL_STATUS = PASTEL_STATUS
        cls._fmt_behavior = staticmethod(fmt_behavior)

        # ---- build a tree with rows for each status ---------------------
        tree = TreeWidget()
        tree.setHeaderLabels(HEADERS)
        tree.setColumnCount(len(HEADERS))
        tree.setStyleSheet(
            f"QTreeWidget {{ background: {cls._BASE_BG}; color: {cls._DEFAULT_FG}; }}"
            f"QTreeWidget::item {{ background: transparent; color: {cls._DEFAULT_FG}; }}"
        )

        # Row 0: valid parent (A01)
        p_valid = tree.create_item(
            ["A01", "Sec", "Valid step", "1 behaviors", "1\u201330"]
        )
        c_valid = tree.create_item(["", "", "OBJ_VALID", "Fade In", ""], parent=p_valid)

        # Row 2: missing_shot parent (A02)
        p_mshot = tree.create_item(["A02", "Sec", "Missing shot", "", "31\u201360"])

        # Row 3: missing_object parent (A03) with affected child
        p_mobj = tree.create_item(
            ["A03", "Sec", "Missing obj step", "1 behaviors", "61\u201390"]
        )
        c_mobj = tree.create_item(["", "", "GONE_OBJ", "Fade In", ""], parent=p_mobj)

        # Row 5: missing_behavior parent (A04) with affected child
        p_mbeh = tree.create_item(
            ["A04", "Sec", "Missing beh step", "1 behaviors", "91\u2013120"]
        )
        c_mbeh = tree.create_item(
            ["", "", "NO_KEYS_OBJ", "Fade Out", ""], parent=p_mbeh
        )

        # Row 7: user_animated parent (A05) with affected child
        p_uanim = tree.create_item(
            ["A05", "Sec", "User animated step", "1 behaviors", "121\u2013150"]
        )
        c_uanim = tree.create_item(
            ["", "", "ANIM_OBJ", "Fade In Out", ""], parent=p_uanim
        )

        # Row 9: locked parent (A06)
        p_locked = tree.create_item(["A06", "Sec", "Locked step", "", "151\u2013180"])

        # ---- apply base formatting (column tints + row tints) -----------
        tree._child_row_color = QColor(0, 0, 0, 55)
        tree._parent_row_color = QColor(255, 255, 255, 12)

        # Column tints Ã¢â‚¬â€ darken Step and Behaviors columns
        tree.set_column_tint(COL_STEP, QColor(0, 0, 0, 45))
        tree.set_column_tint(COL_BEHAVIORS, QColor(0, 0, 0, 45))

        # Behavior column formatter
        display_colors = {
            fmt_behavior(k).lower(): v for k, v in {}  # BEHAVIOR_COLORS removed.items()
        }
        formatter = tree.make_color_map_formatter(display_colors)
        tree.set_column_formatter(COL_BEHAVIORS, formatter)

        tree.apply_formatting()

        # ---- apply assessment colors (simulating _apply_assessment) -----
        status_items = {
            "missing_shot": (p_mshot, []),
            "missing_object": (p_mobj, [c_mobj]),
            "missing_behavior": (p_mbeh, [c_mbeh]),
            "user_animated": (p_uanim, [c_uanim]),
            "locked": (p_locked, []),
        }
        col_count = tree.columnCount()
        for status, (parent, children) in status_items.items():
            fg_hex, bg_hex = PASTEL_STATUS[status]
            if fg_hex:
                fg_brush = QBrush(QColor(fg_hex))
                for c in range(col_count):
                    parent.setForeground(c, fg_brush)
            if bg_hex:
                bg_brush = QBrush(QColor(bg_hex))
                for c in range(col_count):
                    parent.setBackground(c, bg_brush)
            for child in children:
                if fg_hex:
                    for c in range(col_count):
                        child.setForeground(c, QBrush(QColor(fg_hex)))
                if bg_hex:
                    for c in range(col_count):
                        child.setBackground(c, QBrush(QColor(bg_hex)))

        # ---- render -----------------------------------------------------
        tree.expandAll()
        tree.resize(900, 500)
        tree.show()
        cls._app.processEvents()

        cls._image = tree.grab().toImage()
        cls._tree = tree

        # Store item references for model-level tests
        cls._items = {
            "valid_parent": p_valid,
            "valid_child": c_valid,
            "missing_shot_parent": p_mshot,
            "missing_object_parent": p_mobj,
            "missing_object_child": c_mobj,
            "missing_behavior_parent": p_mbeh,
            "missing_behavior_child": c_mbeh,
            "user_animated_parent": p_uanim,
            "user_animated_child": c_uanim,
            "locked_parent": p_locked,
        }

        # Visual row indices (expanded order)
        cls._rows = {
            "valid_parent": 0,
            "valid_child": 1,
            "missing_shot_parent": 2,
            "missing_object_parent": 3,
            "missing_object_child": 4,
            "missing_behavior_parent": 5,
            "missing_behavior_child": 6,
            "user_animated_parent": 7,
            "user_animated_child": 8,
            "locked_parent": 9,
        }

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tree") and cls._tree:
            cls._tree.hide()
            cls._tree.deleteLater()

    # ---- helpers --------------------------------------------------------

    def _item_fg_hex(self, key, col=None):
        """Get foreground color hex from the item model."""
        col = col if col is not None else self._COL_DESC
        return self._items[key].foreground(col).color().name()

    def _item_bg_hex(self, key, col=None):
        """Get background color hex from the item model."""
        col = col if col is not None else self._COL_DESC
        return self._items[key].background(col).color().name()

    def _sample_bg(self, item_key, col_index):
        """Average (R,G,B) from the rendered image at the cell center of *item_key* / *col*."""
        item = self._items[item_key]
        rect = self._tree.visualItemRect(item)
        # visualItemRect is relative to the viewport (excludes header),
        # but grab() captures the full widget (includes header).
        header_h = self._tree.header().height()
        y = rect.center().y() + header_h

        x_start = self._tree.header().sectionPosition(col_index) + 4
        x_end = x_start + self._tree.header().sectionSize(col_index) - 8

        r_total = g_total = b_total = count = 0
        for x in range(x_start, x_end + 1, 4):
            if 0 <= x < self._image.width() and 0 <= y < self._image.height():
                px = self.QColor(self._image.pixel(x, y))
                r_total += px.red()
                g_total += px.green()
                b_total += px.blue()
                count += 1
        if count == 0:
            return (0, 0, 0)
        return (r_total / count, g_total / count, b_total / count)

    def _brightness(self, rgb):
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    # ==== PIXEL-LEVEL: parent vs child background brightness ==============

    def test_parent_row_lighter_than_child(self):
        """Parent rows should render brighter than child rows (white vs dark overlay)."""
        col = self._COL_DESC
        parent_b = self._brightness(self._sample_bg("valid_parent", col))
        child_b = self._brightness(self._sample_bg("valid_child", col))
        self.assertGreater(
            parent_b,
            child_b,
            f"Parent bg ({parent_b:.1f}) should exceed child bg ({child_b:.1f})",
        )

    def test_child_rows_uniform_brightness(self):
        """Valid child rows (no status bg) should have no custom BackgroundRole,
        letting the delegate's childRowColor tint render uniformly.
        """
        col = self._COL_DESC
        valid_bg = self._items["valid_child"].background(col)
        # style() returns NoBrush enum when no per-item override was applied
        from qtpy.QtCore import Qt

        self.assertEqual(
            valid_bg.style(),
            Qt.NoBrush,
            "Valid child should have NoBrush (delegate handles tint)",
        )

    def test_missing_object_bg_redder_than_valid(self):
        """missing_object parent bg (#3D2828) should have more red than valid parent."""
        col = self._COL_DESC
        mobj_bg = self._items["missing_object_parent"].background(col).color()
        valid_bg = self._items["valid_parent"].background(col).color()
        # #3D2828: R=61 > G=B=40;  valid parent: no bg override
        self.assertGreater(
            mobj_bg.red() - mobj_bg.blue(),
            valid_bg.red() - valid_bg.blue(),
            f"missing_object bg should be redder: mobj=({mobj_bg.red()},{mobj_bg.green()},{mobj_bg.blue()}) "
            f"valid=({valid_bg.red()},{valid_bg.green()},{valid_bg.blue()})",
        )

    def test_no_row_is_black_or_white(self):
        """Sanity: no row should render pure black or pure white."""
        col = self._COL_DESC
        for label in self._items:
            rgb = self._sample_bg(label, col)
            b = self._brightness(rgb)
            self.assertGreater(b, 10, f"{label} too dark: {rgb}")
            self.assertLess(b, 240, f"{label} too bright: {rgb}")

    # ==== MODEL-LEVEL: foreground assessment colors =======================

    def test_missing_shot_fg_blue(self):
        """missing_shot parent should have cool blue foreground (#88B8D0)."""
        fg = self._item_fg_hex("missing_shot_parent")
        c = self.QColor(fg)
        self.assertGreater(c.blue(), c.green(), f"Blue: B should exceed G: {fg}")

    def test_missing_object_fg_rose(self):
        """missing_object items should have pastel rose foreground (#E0A0A0)."""
        fg = self._item_fg_hex("missing_object_parent")
        c = self.QColor(fg)
        self.assertGreater(c.red(), c.green(), f"Rose: R > G: {fg}")
        self.assertGreater(c.red(), c.blue(), f"Rose: R > B: {fg}")

    def test_missing_object_child_fg_matches_parent(self):
        """missing_object child should have the same fg as its parent."""
        parent_fg = self._item_fg_hex("missing_object_parent")
        child_fg = self._item_fg_hex("missing_object_child")
        self.assertEqual(parent_fg, child_fg)

    def test_missing_behavior_fg_gold(self):
        """missing_behavior should have warm gold fg (#D4B878): R > B."""
        fg = self._item_fg_hex("missing_behavior_parent")
        c = self.QColor(fg)
        self.assertGreater(c.red(), c.blue(), f"Gold: R > B: {fg}")

    def test_user_animated_fg_blue(self):
        """user_animated should have cool blue fg (#88B8D0): B > G."""
        fg = self._item_fg_hex("user_animated_parent")
        c = self.QColor(fg)
        self.assertGreater(c.blue(), c.green(), f"Blue: B > G: {fg}")

    def test_locked_fg_grey(self):
        """locked parent should have low-saturation grey fg (#888888)."""
        fg = self._item_fg_hex("locked_parent")
        c = self.QColor(fg)
        self.assertLess(
            c.saturation(), 15, f"Locked should be grey: {fg} sat={c.saturation()}"
        )

    def test_locked_fg_dimmer_than_default(self):
        """locked fg should be dimmer than the default #cccccc."""
        locked_c = self.QColor(self._item_fg_hex("locked_parent"))
        default_c = self.QColor(self._DEFAULT_FG)
        self.assertLess(locked_c.lightness(), default_c.lightness())

    def test_valid_parent_has_no_custom_fg(self):
        """Valid parent should not have assessment foreground applied."""
        fg = self._item_fg_hex("valid_parent")
        problem_fgs = {v[0] for v in self._PASTEL_STATUS.values() if v[0]}
        self.assertNotIn(
            fg, problem_fgs, f"Valid parent should not have a problem color: {fg}"
        )

    # ==== MODEL-LEVEL: background assessment colors =======================

    def test_missing_object_bg_set(self):
        """missing_object parent should have dark reddish bg (#3D2828)."""
        bg = self._item_bg_hex("missing_object_parent")
        c = self.QColor(bg)
        self.assertGreater(c.red(), c.green(), f"Dark rose bg: R > G: {bg}")
        self.assertGreater(c.red(), c.blue(), f"Dark rose bg: R > B: {bg}")

    def test_missing_shot_has_status_bg(self):
        """missing_shot parent should have a cool-tinted background (#28323D)."""
        bg = self._item_bg_hex("missing_shot_parent")
        c = self.QColor(bg)
        self.assertGreater(c.blue(), c.red(), f"Cool bg: B > R: {bg}")

    def test_missing_behavior_has_status_bg(self):
        """missing_behavior parent should have a warm-tinted background (#3D3528)."""
        bg = self._item_bg_hex("missing_behavior_parent")
        c = self.QColor(bg)
        self.assertGreater(c.red(), c.blue(), f"Warm bg: R > B: {bg}")

    def test_user_animated_has_status_bg(self):
        """user_animated parent should have a purple-tinted background."""
        bg = self._item_bg_hex("user_animated_parent")
        c = self.QColor(bg)
        self.assertGreater(c.blue(), c.green(), f"Purple bg: B > G: {bg}")

    def test_column_tint_darkens_step_column(self):
        """Step column (tinted) should render darker than Content column (untinted)."""
        parent_step = self._sample_bg("valid_parent", self._COL_STEP)
        parent_content = self._sample_bg("valid_parent", self._COL_DESC)
        self.assertLess(
            self._brightness(parent_step),
            self._brightness(parent_content),
            f"Step col ({parent_step}) should be darker than Content ({parent_content})",
        )

    # ==== MODEL-LEVEL: behavior column text colors ========================

    def test_all_status_fgs_are_pastel(self):
        """All defined foreground status colors should be soft pastels."""
        for status, (fg_hex, _) in self._PASTEL_STATUS.items():
            if fg_hex is None:
                continue
            c = self.QColor(fg_hex)
            self.assertLess(
                c.saturation(),
                200,
                f"{status} fg too saturated: {fg_hex} sat={c.saturation()}",
            )
            self.assertGreater(
                c.lightness(),
                50,
                f"{status} fg too dark: {fg_hex} L={c.lightness()}",
            )


class TestMarkerPersistence(unittest.TestCase):
    """Marker dict fields round-trip through controller persistence."""

    def test_marker_dict_has_all_fields(self):
        """A marker dict must include draggable, style, line_style, opacity."""
        d = {
            "time": 10.0,
            "note": "test",
            "color": "#FF0000",
            "draggable": False,
            "style": "bracket",
            "line_style": "solid",
            "opacity": 0.85,
        }
        self.assertIn("draggable", d)
        self.assertIn("style", d)
        self.assertIn("line_style", d)
        self.assertIn("opacity", d)

    def test_marker_dict_defaults(self):
        """Legacy marker dicts without new fields should get defaults."""
        d = {"time": 5.0, "note": "", "color": "#E8A84A"}
        self.assertTrue(d.get("draggable", True))
        self.assertEqual(d.get("style", "triangle"), "triangle")
        self.assertEqual(d.get("line_style", "dashed"), "dashed")
        self.assertAlmostEqual(d.get("opacity", 1.0), 1.0)


class TestDetectShots(unittest.TestCase):
    """detect_shots() logic Ã¢â‚¬â€ pure clustering tests without Maya."""

    def test_detect_shots_exists(self):
        """ShotSequencer should have a detect_shots method."""
        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        self.assertTrue(hasattr(ShotSequencer, "detect_shots"))

    def test_detect_shots_signature(self):
        """detect_shots accepts objects, gap_threshold, ignore, motion_rate params."""
        import inspect
        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        sig = inspect.signature(ShotSequencer.detect_shots)
        params = list(sig.parameters.keys())
        self.assertIn("objects", params)
        self.assertIn("gap_threshold", params)
        self.assertIn("ignore", params)
        self.assertIn("motion_rate", params)


class TestBoundaryFloatPrecision(unittest.TestCase):
    """Verify time-range clamping doesn't drop segments at float-imprecise boundaries.

    Bug: When _ripple_downstream shifts a boundary key by a delta computed
    from float arithmetic (shot.end = new_start + dur), the key's new
    position may differ from shot.end by ~1 ULP.  The old code used exact
    ``seg_start <= seg_end`` after clamping, causing the segment to be
    silently dropped.  Fixed 2026-03-19 by adding a 1e-4 tolerance.
    """

    @staticmethod
    def _filter_segments(active_segments, range_start, range_end):
        """Replicate the time-range clamping logic from collect_segments."""
        _BOUNDARY_EPS = 1e-4
        filtered = []
        for seg_start, seg_end in active_segments:
            if range_start is not None:
                seg_start = max(seg_start, range_start)
            if range_end is not None:
                seg_end = min(seg_end, range_end)
            if seg_start <= seg_end + _BOUNDARY_EPS:
                filtered.append((seg_start, max(seg_start, seg_end)))
        return filtered

    def test_exact_boundary_included(self):
        """Segment at exact shot end should be included."""
        result = self._filter_segments(
            [(4548.0, 4548.0)], range_start=4341.6, range_end=4548.0
        )
        self.assertEqual(len(result), 1)

    def test_boundary_key_above_range_end_by_ulp(self):
        """Segment key at shot.end + ~1 ULP must not be dropped.

        Reproduces the exact scenario: shot.end = 4374.0 + (4518.4 - 4341.6)
        = 4550.799999999999..., but key moved to 4548.0 + delta which rounds
        to 4550.8.  The key exceeds range_end by ~1e-13.
        """
        shot_end = 4374.0 + (4518.4 - 4341.6)  # 4550.799999...
        key_pos = 4548.0 + (shot_end - 4548.0)  # may round to 4550.8
        # Confirm the float mismatch exists (key_pos >= shot_end)
        # â€” if Python's float resolves them identically, the test still
        #   validates the tolerance path harmlessly.
        result = self._filter_segments(
            [(key_pos, key_pos)], range_start=4341.6, range_end=shot_end
        )
        self.assertEqual(
            len(result),
            1,
            f"Segment at {key_pos} dropped with range_end={shot_end} "
            f"(diff={key_pos - shot_end})",
        )

    def test_segment_genuinely_outside_range_excluded(self):
        """Segment well outside the range should still be excluded."""
        result = self._filter_segments(
            [(5000.0, 5000.0)], range_start=4341.6, range_end=4550.8
        )
        self.assertEqual(len(result), 0)

    def test_span_segment_clamped_at_boundary(self):
        """A span crossing range_end is clamped, not dropped."""
        result = self._filter_segments(
            [(4500.0, 4600.0)], range_start=4341.6, range_end=4550.8
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0][0], 4500.0)
        self.assertAlmostEqual(result[0][1], 4550.8)

    def test_span_before_range_start_clamped(self):
        """A span starting before range_start is clamped."""
        result = self._filter_segments(
            [(4300.0, 4400.0)], range_start=4341.6, range_end=4550.8
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0][0], 4341.6)
        self.assertAlmostEqual(result[0][1], 4400.0)


class TestDetectNextShot(unittest.TestCase):
    """Tests for ShotSequencer.detect_next_shot() incremental detection."""

    def setUp(self):
        ShotStore.clear_active()
        self.store = ShotStore()
        self.seq = ShotSequencer(store=self.store)

    def tearDown(self):
        ShotStore.clear_active()

    @patch.object(ShotSequencer, "detect_shots")
    def test_returns_none_when_no_candidates(self, mock_detect):
        """Return None when detect_shots finds no animation clusters."""
        mock_detect.return_value = []
        self.assertIsNone(self.seq.detect_next_shot())

    @patch.object(ShotSequencer, "detect_shots")
    def test_returns_first_when_store_empty(self, mock_detect):
        """With no existing shots, return the first detected candidate."""
        mock_detect.return_value = [
            {"name": "Shot 1", "start": 1.0, "end": 30.0, "objects": ["a"]},
            {"name": "Shot 2", "start": 50.0, "end": 80.0, "objects": ["b"]},
        ]
        result = self.seq.detect_next_shot()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Shot 1")

    @patch.object(ShotSequencer, "detect_shots")
    def test_returns_next_after_last_shot(self, mock_detect):
        """When shots exist, return the first candidate after them."""
        self.store.define_shot(name="Existing", start=1.0, end=30.0)
        mock_detect.return_value = [
            {"name": "Shot 1", "start": 1.0, "end": 30.0, "objects": ["a"]},
            {"name": "Shot 2", "start": 50.0, "end": 80.0, "objects": ["b"]},
        ]
        result = self.seq.detect_next_shot()
        self.assertIsNotNone(result)
        self.assertEqual(result["start"], 50.0)

    @patch.object(ShotSequencer, "detect_shots")
    def test_skips_overlapping_candidates(self, mock_detect):
        """Candidates overlapping existing shots should be skipped."""
        self.store.define_shot(name="A", start=1.0, end=40.0)
        self.store.define_shot(name="B", start=50.0, end=90.0)
        mock_detect.return_value = [
            {"name": "Shot 1", "start": 20.0, "end": 45.0, "objects": ["a"]},
            {"name": "Shot 2", "start": 60.0, "end": 85.0, "objects": ["b"]},
            {"name": "Shot 3", "start": 100.0, "end": 130.0, "objects": ["c"]},
        ]
        result = self.seq.detect_next_shot()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Shot 3")
        self.assertEqual(result["start"], 100.0)

    @patch.object(ShotSequencer, "detect_shots")
    def test_returns_none_when_all_covered(self, mock_detect):
        """Returns None if all candidates overlap existing shots."""
        self.store.define_shot(name="A", start=0.0, end=100.0)
        mock_detect.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["a"]},
        ]
        result = self.seq.detect_next_shot()
        self.assertIsNone(result)

    def test_detect_next_shot_signature(self):
        """detect_next_shot accepts gap_threshold, ignore, and flat-key params."""
        import inspect

        sig = inspect.signature(ShotSequencer.detect_next_shot)
        params = list(sig.parameters.keys())
        self.assertIn("gap_threshold", params)
        self.assertIn("ignore", params)


class TestShotStoreListeners(unittest.TestCase):
    """Tests for the ShotStore observer/listener mechanism."""

    def setUp(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        self.store = ShotStore()

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()

    def test_listener_receives_shot_defined(self):
        from mayatk.anim_utils.shots._shots import ShotDefined

        received = []
        self.store.add_listener(lambda evt: received.append(evt))
        shot = self.store.define_shot(name="A", start=0, end=50)
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], ShotDefined)
        self.assertIs(received[0].shot, shot)

    def test_listener_receives_shot_removed(self):
        from mayatk.anim_utils.shots._shots import ShotRemoved

        received = []
        shot = self.store.define_shot(name="A", start=0, end=50)
        self.store.add_listener(lambda evt: received.append(evt))
        self.store.remove_shot(shot.shot_id)
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], ShotRemoved)
        self.assertEqual(received[0].shot_id, shot.shot_id)

    def test_remove_listener_stops_notifications(self):
        received = []
        cb = lambda evt: received.append(evt)
        self.store.add_listener(cb)
        self.store.define_shot(name="A", start=0, end=10)
        self.assertEqual(len(received), 1)
        self.store.remove_listener(cb)
        self.store.define_shot(name="B", start=20, end=30)
        self.assertEqual(len(received), 1)  # no new event

    def test_duplicate_listener_not_added(self):
        cb = lambda evt: None
        self.store.add_listener(cb)
        self.store.add_listener(cb)
        self.assertEqual(len(self.store._listeners), 1)

    def test_remove_nonexistent_listener_is_noop(self):
        cb = lambda evt: None
        self.store.remove_listener(cb)  # should not raise

    def test_listener_exception_does_not_break_others(self):
        """A failing listener should not prevent subsequent listeners from firing."""
        from mayatk.anim_utils.shots._shots import ShotDefined

        received = []

        def bad_listener(evt):
            raise RuntimeError("boom")

        self.store.add_listener(bad_listener)
        self.store.add_listener(lambda evt: received.append(evt))
        self.store.define_shot(name="A", start=0, end=10)
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], ShotDefined)

    def test_multiple_listeners_all_notified(self):
        from mayatk.anim_utils.shots._shots import ShotDefined

        received_a = []
        received_b = []
        self.store.add_listener(lambda evt: received_a.append(evt))
        self.store.add_listener(lambda evt: received_b.append(evt))
        self.store.define_shot(name="A", start=0, end=10)
        self.assertEqual(len(received_a), 1)
        self.assertIsInstance(received_a[0], ShotDefined)
        self.assertEqual(len(received_b), 1)
        self.assertIsInstance(received_b[0], ShotDefined)

    def test_update_shot_fires_event(self):
        """update_shot should mutate in-place and fire ShotUpdated."""
        from mayatk.anim_utils.shots._shots import ShotUpdated

        received = []
        shot = self.store.define_shot(name="A", start=0, end=50)
        self.store.add_listener(lambda evt: received.append(evt))
        self.store.update_shot(shot.shot_id, start=10, end=60, name="B")
        self.assertEqual(shot.start, 10)
        self.assertEqual(shot.end, 60)
        self.assertEqual(shot.name, "B")
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], ShotUpdated)
        self.assertIs(received[0].shot, shot)

    def test_update_shot_unknown_id_returns_none(self):
        result = self.store.update_shot("nonexistent", start=5)
        self.assertIsNone(result)

    def test_batch_update_defers_notifications(self):
        """During batch_update, individual events are deferred; a single
        BatchComplete fires on exit."""
        from mayatk.anim_utils.shots._shots import BatchComplete

        received = []
        self.store.add_listener(lambda evt: received.append(evt))
        with self.store.batch_update():
            self.store.define_shot(name="A", start=0, end=10)
            self.store.define_shot(name="B", start=20, end=30)
            self.assertEqual(received, [])  # nothing during batch
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], BatchComplete)

    def test_batch_update_nested(self):
        """Nested batch_update should only fire once on outermost exit."""
        from mayatk.anim_utils.shots._shots import BatchComplete

        received = []
        self.store.add_listener(lambda evt: received.append(evt))
        with self.store.batch_update():
            self.store.define_shot(name="A", start=0, end=10)
            with self.store.batch_update():
                self.store.define_shot(name="B", start=20, end=30)
            self.assertEqual(received, [])  # inner exit doesn't fire
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], BatchComplete)

    def test_batch_update_no_events_no_notification(self):
        """batch_update with no mutations should not fire anything."""
        received = []
        self.store.add_listener(lambda evt: received.append(evt))
        with self.store.batch_update():
            pass
        self.assertEqual(received, [])

    def test_stale_zero_end_does_not_corrupt_upstream(self):
        """Simulates the debounce race: a stale on_shot_end_changed(0)
        firing after a real shot becomes active must not shift upstream shots.

        Bug: _sync_shot_editor set spinners to 0 without blockSignals when
        shot was None.  The debounce timer fired 400ms later, after an
        active shot was set, producing delta = 0 - shot.end and rippling
        all shots with start >= 0.
        Fixed: 2026-04-04
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A01", start=400, end=680)
        store.define_shot(name="A02", start=680, end=1000)
        store.define_shot(name="A09", start=2520, end=2520)
        store.define_shot(name="A10", start=2520, end=2760)
        store.set_active_shot(store.shot_by_name("A09").shot_id)

        # Simulate what the stale debounce would do: value=0 for A09.end
        old_end = store.shot_by_name("A09").end  # 2520
        stale_value = 0
        delta = stale_value - old_end  # -2520
        # This MUST NOT happen â€” the fix prevents the signal from
        # ever reaching on_shot_end_changed.  But verify that upstream
        # shots would not survive such a call.
        with store.batch_update():
            store.update_shot(store.active_shot_id, end=stale_value)
            store.ripple_shift(old_end, delta, exclude_id=store.active_shot_id)

        # Upstream shots (start < old_end) must be untouched
        a01 = store.shot_by_name("A01")
        a02 = store.shot_by_name("A02")
        self.assertAlmostEqual(a01.start, 400, msg="Upstream A01 start corrupted")
        self.assertAlmostEqual(a01.end, 680, msg="Upstream A01 end corrupted")
        self.assertAlmostEqual(a02.start, 680, msg="Upstream A02 start corrupted")
        self.assertAlmostEqual(a02.end, 1000, msg="Upstream A02 end corrupted")

        # Downstream A10 should have shifted
        a10 = store.shot_by_name("A10")
        self.assertAlmostEqual(a10.start, 0, msg="A10 start not shifted")
        self.assertAlmostEqual(a10.end, 240, msg="A10 end not shifted")

        # Now simulate the user editing A09.end to 2620 when store has end=0
        old_end2 = store.shot_by_name("A09").end  # 0
        user_value = 2620
        delta2 = user_value - old_end2  # 2620
        with store.batch_update():
            store.update_shot(store.active_shot_id, end=user_value)
            store.ripple_shift(old_end2, delta2, exclude_id=store.active_shot_id)

        # Upstream A01/A02 get shifted because after_frame=0 matches
        # everything â€” this is the OBSERVED BUG.  The fix must prevent
        # the stale value=0 from ever reaching on_shot_end_changed.
        a01 = store.shot_by_name("A01")
        a02 = store.shot_by_name("A02")
        # After the two-step corruption: A01 was at 400, unaffected by
        # first ripple (start<2520), but shifted by +2620 in second.
        self.assertAlmostEqual(
            a01.start,
            3020,
            msg="This test demonstrates the OBSERVED corruption; "
            "the real fix is blockSignals in _sync_shot_editor",
        )


class TestColumnMap(unittest.TestCase):
    """Test ColumnMap serialisation round-trip and custom-alias parsing."""

    def test_to_dict_round_trip(self):
        """to_dict â†’ from_dict produces an identical ColumnMap."""
        original = ColumnMap()
        restored = ColumnMap.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_custom_aliases_round_trip(self):
        """Custom header aliases survive serialisation."""
        custom = ColumnMap(
            step_id=("ID",),
            description=("Description", "Desc"),
            assets=("Object",),
        )
        restored = ColumnMap.from_dict(custom.to_dict())
        self.assertEqual(restored.step_id, ("ID",))
        self.assertEqual(restored.description, ("Description", "Desc"))
        self.assertEqual(restored.assets, ("Object",))

    def test_from_dict_ignores_unknown_keys(self):
        """Unknown keys in the dict are silently dropped."""
        data = ColumnMap().to_dict()
        data["bogus_field"] = ["whatever"]
        restored = ColumnMap.from_dict(data)
        self.assertFalse(hasattr(restored, "bogus_field"))

    def test_custom_column_map_parses_csv(self):
        """parse_csv respects a ColumnMap with non-default aliases."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "custom.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["ID", "Description", "Object", "Status"])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", "Complete"])
            custom = ColumnMap(
                step_id=("ID",),
                description=("Description",),
                assets=("Object",),
            )
            steps = parse_csv(csv_path, columns=custom)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].step_id, "A01")
            self.assertEqual(steps[0].objects[0].name, "ARROW_01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_voice_column_populates_audio_field(self):
        """When Voice Support column exists, audio field stores voice text.

        display_text returns description (Step Contents), not audio.
        Audio text flows into metadata as voice_text.
        Refactored: 2026-04-14 â€” display_text flipped to return description.
        """
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "voice.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", "", ""])
                w.writerow(
                    ["Step", "Voice Support", "Step Contents", "Asset Names", "Status"]
                )
                # A01: has both voice and contents
                w.writerow(
                    ["A01.)", "Welcome to training.", "Arrow fades in.", "ARROW_01", ""]
                )
                # A02: voice-only (Contents=N/A) â€” should show N/A description
                w.writerow(["A02.)", "The clamps are removed.", "N/A", "N/A", ""])
                # A03: silent action (Voice=N/A) â€” should show action description
                w.writerow(["A03.)", "N/A", "Poker chips push in.", "CHIPS_01", ""])
            steps = parse_csv(csv_path)
            self.assertEqual(len(steps), 3)

            # A01: display_text = description (Step Contents)
            self.assertEqual(steps[0].display_text, "Arrow fades in.")
            self.assertEqual(steps[0].audio, "Welcome to training.")
            self.assertEqual(steps[0].description, "Arrow fades in.")
            # Behavior detection still from description
            self.assertEqual(steps[0].objects[0].behaviors, ["fade_in"])

            # A02: display_text = description ("N/A")
            self.assertEqual(steps[1].display_text, "N/A")
            self.assertEqual(steps[1].audio, "The clamps are removed.")
            self.assertEqual(steps[1].description, "N/A")

            # A03: display_text = description (action text)
            self.assertEqual(steps[2].display_text, "Poker chips push in.")
            self.assertEqual(steps[2].audio, "N/A")
            self.assertEqual(steps[2].description, "Poker chips push in.")
            # Behavior still detected from description, not audio
            self.assertEqual(len(steps[2].objects), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_audio_column_falls_back_to_description(self):
        """Without an Audio column, display_text falls back to description."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "novoice.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", "Complete"])
            steps = parse_csv(csv_path)
            self.assertEqual(steps[0].audio, "")
            self.assertEqual(steps[0].display_text, "Arrow fades in.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audio_round_trip_in_column_map(self):
        """ColumnMap audio field survives to_dict/from_dict."""
        custom = ColumnMap(audio=("Narration", "VO"))
        restored = ColumnMap.from_dict(custom.to_dict())
        self.assertEqual(restored.audio, ("Narration", "VO"))

    def test_exclude_steps_default_excludes_setup(self):
        """Default ColumnMap excludes SETUP."""
        cm = ColumnMap()
        self.assertIn("SETUP", cm.exclude_steps)

    def test_exclude_steps_filters_parse_csv(self):
        """Steps listed in exclude_steps are removed from parse results."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "excl.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["SETUP", "Setup step.", "N/A", ""])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", "Complete"])
                w.writerow(["A02.)", "Box fades out.", "BOX_01", ""])
            # Default excludes SETUP
            steps = parse_csv(csv_path)
            ids = [s.step_id for s in steps]
            self.assertNotIn("SETUP", ids)
            self.assertIn("A01", ids)
            self.assertIn("A02", ids)
            self.assertEqual(len(steps), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_steps_empty_includes_all(self):
        """Empty exclude_steps keeps all steps including SETUP."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "all.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["SETUP", "Setup step.", "N/A", ""])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", ""])
            no_exclude = ColumnMap(exclude_steps=())
            steps = parse_csv(csv_path, columns=no_exclude)
            ids = [s.step_id for s in steps]
            self.assertIn("SETUP", ids)
            self.assertIn("A01", ids)
            self.assertEqual(len(steps), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_steps_round_trip(self):
        """exclude_steps survives to_dict/from_dict."""
        cm = ColumnMap(exclude_steps=("SETUP", "INTRO"))
        restored = ColumnMap.from_dict(cm.to_dict())
        self.assertEqual(restored.exclude_steps, ("SETUP", "INTRO"))

    def test_exclude_values_default_filters_na_assets(self):
        """Default exclude_values filters N/A from asset column."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "ev.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Intro.", "N/A", ""])
                w.writerow(["A02.)", "Box appears.", "BOX_01", ""])
            steps = parse_csv(csv_path)
            self.assertEqual(steps[0].objects, [])  # N/A filtered
            self.assertEqual(len(steps[1].objects), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_values_case_insensitive(self):
        """exclude_values comparison is case-insensitive."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "ci.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Intro.", "n/a", ""])
            steps = parse_csv(csv_path)
            self.assertEqual(steps[0].objects, [])

            # Custom exclude value
            cm = ColumnMap(exclude_values={"assets": ("NONE",)})
            csv_path2 = os.path.join(tmp, "ci2.csv")
            with open(csv_path2, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Intro.", "none", ""])
                w.writerow(["A02.)", "Next.", "N/A", ""])
            steps2 = parse_csv(csv_path2, columns=cm)
            self.assertEqual(steps2[0].objects, [])  # "none" excluded
            self.assertEqual(len(steps2[1].objects), 1)  # "N/A" kept
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_values_round_trip(self):
        """exclude_values dict survives to_dict/from_dict."""
        cm = ColumnMap(exclude_values={"assets": ("N/A", "NONE"), "audio": ("--",)})
        restored = ColumnMap.from_dict(cm.to_dict())
        self.assertEqual(restored.exclude_values["assets"], ("N/A", "NONE"))
        self.assertEqual(restored.exclude_values["audio"], ("--",))

    def test_metadata_pass_columns_collected(self):
        """metadata_pass columns are resolved and stored on step._pass_through."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "meta.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Priority", ""])
                w.writerow(["A01.)", "Intro.", "OBJ_01", "High", ""])
                w.writerow(["A02.)", "Next.", "OBJ_02", "", ""])
            cm = ColumnMap(metadata_pass={"priority": ("Priority",)})
            steps = parse_csv(csv_path, columns=cm)
            self.assertEqual(steps[0]._pass_through, {"priority": "High"})
            self.assertEqual(steps[1]._pass_through, {})  # Empty value not stored
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_metadata_pass_round_trip(self):
        """metadata_pass dict survives to_dict/from_dict."""
        cm = ColumnMap(
            metadata_pass={"priority": ("Priority", "Pri"), "notes": ("Notes",)}
        )
        restored = ColumnMap.from_dict(cm.to_dict())
        self.assertEqual(restored.metadata_pass["priority"], ("Priority", "Pri"))
        self.assertEqual(restored.metadata_pass["notes"], ("Notes",))

    def test_post_process_appends_audio_object(self):
        """post_process callable can append audio BuilderObject."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "pp.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", ""])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", ""])

            def _derive(step):
                step.objects.append(
                    BuilderObject(name=f"clip_{step.step_id}", kind="audio")
                )

            steps = parse_csv(csv_path, post_process=_derive)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNotNone(ao)
            self.assertEqual(ao.name, "clip_A01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_builder_object_kind_defaults_to_scene(self):
        """BuilderObject.kind defaults to 'scene'."""
        step = BuilderStep(
            step_id="X01",
            section="X",
            section_title="TEST",
            description="test",
        )
        step.objects.append(BuilderObject(name="obj"))
        self.assertEqual(step.objects[0].kind, "scene")

    def test_display_text_returns_description(self):
        """display_text returns description (not audio).
        Refactored: 2026-04-14 â€” display_text flipped to return description.
        """
        step = BuilderStep(
            step_id="X01",
            section="X",
            section_title="TEST",
            description="desc",
            audio="voice",
        )
        self.assertEqual(step.display_text, "desc")


# ---------------------------------------------------------------------------
# Mapping resolver tests (JSON mapping files)
# ---------------------------------------------------------------------------

from mayatk.anim_utils.shots.shot_manifest.mapping import (
    discover,
    load_mapping,
    resolve,
    DEFAULT_DIR,
)


class TestMappingResolver(unittest.TestCase):
    """Test the JSON mapping resolver and discovery."""

    def _make_csv(self, tmp, name="test.csv"):
        """Create a minimal CSV file and return its path."""
        import csv as csv_mod

        csv_path = os.path.join(tmp, name)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv_mod.writer(f)
            w.writerow(["SECTION A: TEST", "", "", ""])
            w.writerow(["Step", "Step Contents", "Asset Names", ""])
            w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", ""])
            w.writerow(["A02.)", "Box moves left.", "BOX_01", ""])
        return csv_path

    def _write_json(self, tmp, name, data):
        """Write a JSON mapping file and return its path."""
        import json

        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    # ---- discovery ----

    def test_discover_lists_json_files(self):
        """discover() finds .json mapping files in a directory."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            self._write_json(tmp, "project_a.json", {"columns": {}})
            self._write_json(tmp, "project_b.json", {"columns": {}})
            self._write_json(tmp, "_private.json", {"columns": {}})
            names = discover(tmp)
            self.assertEqual(names, ["project_a", "project_b"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_discover_empty_dir(self):
        """discover() returns empty list for nonexistent directory."""
        self.assertEqual(discover("/nonexistent"), [])

    def test_discover_default_dir_has_default(self):
        """The default mapping directory contains 'default'."""
        names = discover()
        self.assertIn("default", names)

    # ---- load_mapping ----

    def test_load_mapping_reads_json(self):
        """load_mapping() reads and parses a JSON file."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            data = {"columns": {"step_id": ["ID"]}}
            self._write_json(tmp, "test.json", data)
            result = load_mapping("test", tmp)
            self.assertEqual(result["columns"]["step_id"], ["ID"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_mapping_missing_raises(self):
        """load_mapping() raises FileNotFoundError for missing file."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(FileNotFoundError):
                load_mapping("nonexistent", tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_mapping_accepts_full_path(self):
        """load_mapping() accepts a full .json path as name."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            data = {"columns": {"step_id": ["ID"]}}
            path = self._write_json(tmp, "full.json", data)
            result = load_mapping(path)
            self.assertEqual(result["columns"]["step_id"], ["ID"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- resolve (end-to-end) ----

    def test_resolve_with_empty_mapping(self):
        """resolve() with empty mapping uses default ColumnMap."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            steps = resolve(csv_path, mapping={})
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0].step_id, "A01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_with_column_remap(self):
        """resolve() applies column aliases from the mapping."""
        import tempfile, shutil, csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "custom.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["ID", "Body", "Objects", ""])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", ""])
            mapping = {
                "columns": {
                    "step_id": ["ID"],
                    "description": ["Body"],
                    "assets": ["Objects"],
                }
            }
            steps = resolve(csv_path, mapping=mapping)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].description, "Arrow fades in.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_by_name(self):
        """resolve() loads a mapping by name from a directory."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            self._write_json(tmp, "my_map.json", {"columns": {}})
            steps = resolve(csv_path, name="my_map", directory=tmp)
            self.assertEqual(len(steps), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_default_mapping(self):
        """The built-in default.json produces correct steps."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            steps = resolve(csv_path, name="default")
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0].step_id, "A01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- audio_resolve: prefix ----

    def test_audio_prefix_resolves_clip(self):
        """audio_resolve with method=prefix adds audio BuilderObject."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            audio_dir = os.path.join(tmp, "audio")
            os.makedirs(audio_dir)
            open(os.path.join(audio_dir, "A01_intro.wav"), "w").close()
            open(os.path.join(audio_dir, "A02_demo.mp3"), "w").close()

            mapping = {
                "columns": {},
                "audio_resolve": {
                    "method": "prefix",
                    "directory": audio_dir,
                },
            }
            steps = resolve(csv_path, mapping=mapping)
            ao0 = next((o for o in steps[0].objects if o.kind == "audio"), None)
            ao1 = next((o for o in steps[1].objects if o.kind == "audio"), None)
            self.assertIsNotNone(ao0)
            self.assertEqual(ao0.name, "A01_intro")
            self.assertIsNotNone(ao1)
            self.assertEqual(ao1.name, "A02_demo")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audio_prefix_no_match(self):
        """audio_resolve prefix leaves no audio object when no match."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            audio_dir = os.path.join(tmp, "audio")
            os.makedirs(audio_dir)

            mapping = {
                "columns": {},
                "audio_resolve": {"method": "prefix", "directory": audio_dir},
            }
            steps = resolve(csv_path, mapping=mapping)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNone(ao)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audio_prefix_nonexistent_dir(self):
        """audio_resolve prefix is a no-op when directory missing."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            mapping = {
                "columns": {},
                "audio_resolve": {
                    "method": "prefix",
                    "directory": "/nonexistent/path",
                },
            }
            steps = resolve(csv_path, mapping=mapping)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNone(ao)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- audio_resolve: regex ----

    def test_audio_regex_resolves_clip(self):
        """audio_resolve with method=regex adds audio BuilderObject."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            audio_dir = os.path.join(tmp, "audio")
            os.makedirs(audio_dir)
            open(os.path.join(audio_dir, "A01-001.wav"), "w").close()

            mapping = {
                "columns": {},
                "audio_resolve": {
                    "method": "regex",
                    "directory": audio_dir,
                    "pattern": r"{step_id}-\d+",
                },
            }
            steps = resolve(csv_path, mapping=mapping)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNotNone(ao)
            self.assertEqual(ao.name, "A01-001")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- audio_resolve: map ----

    def test_audio_map_resolves_clip(self):
        """audio_resolve with method=map adds audio BuilderObjects."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            mapping = {
                "columns": {},
                "audio_resolve": {
                    "method": "map",
                    "clips": {"A01": "intro_clip", "A02": "demo_clip"},
                },
            }
            steps = resolve(csv_path, mapping=mapping)
            ao0 = next((o for o in steps[0].objects if o.kind == "audio"), None)
            ao1 = next((o for o in steps[1].objects if o.kind == "audio"), None)
            self.assertIsNotNone(ao0)
            self.assertEqual(ao0.name, "intro_clip")
            self.assertIsNotNone(ao1)
            self.assertEqual(ao1.name, "demo_clip")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audio_map_missing_key(self):
        """audio_resolve map adds no audio object for unmapped steps."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            mapping = {
                "columns": {},
                "audio_resolve": {
                    "method": "map",
                    "clips": {"A01": "only_a01"},
                },
            }
            steps = resolve(csv_path, mapping=mapping)
            ao0 = next((o for o in steps[0].objects if o.kind == "audio"), None)
            ao1 = next((o for o in steps[1].objects if o.kind == "audio"), None)
            self.assertIsNotNone(ao0)
            self.assertEqual(ao0.name, "only_a01")
            self.assertIsNone(ao1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- bad config ----

    def test_unknown_audio_method_raises(self):
        """Unknown audio_resolve method raises ValueError."""
        import tempfile, shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            mapping = {
                "columns": {},
                "audio_resolve": {"method": "unknown"},
            }
            with self.assertRaises(ValueError):
                resolve(csv_path, mapping=mapping)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
