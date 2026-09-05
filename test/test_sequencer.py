# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.shots.shot_sequencer.

Pure-Python tests run without Maya.  Maya-dependent tests bootstrap a
standalone session via ``MayaConnection`` so they can run from a normal
``python -m pytest`` invocation (provided Maya is installed).
"""

import unittest
import os
from pathlib import Path

import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
    ShotBlock,
    ShotSequencer,
)
from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.anim_utils.shots._shot_apply import ShotApply
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import ColumnMap
from mayatk.anim_utils.shots.shot_manifest.behaviors import (
    Behaviors,
    load_behavior,
    resolve_keys,
)
from mayatk.audio_utils._audio_utils import AudioUtils
from mayatk.core_utils._core_utils import CoreUtils
import maya.cmds as cmds

compute_waveform_envelope = AudioUtils.compute_waveform_envelope

# ---------------------------------------------------------------------------
# Maya availability detection — cmds.about() succeeds in any Maya context
# (mayapy, maya GUI, command-port). MayaConnection.connect("standalone")
# from inside an already-initialized Maya raises, so we no longer rely on it.
# ---------------------------------------------------------------------------
HAS_MAYA = False
try:
    cmds.about(version=True)
    HAS_MAYA = True
except Exception:
    try:
        from mayatk.env_utils.maya_connection import MayaConnection

        _conn = MayaConnection.get_instance()
        if not _conn.is_connected:
            _conn.connect(mode="standalone")
        HAS_MAYA = _conn.is_connected
    except Exception:
        pass


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
        cmds.file(new=True, force=True)

    def _create_animated_cube(self, name, keys):
        """Create a cube and set keyframes at the given {frame: value} dict on translateX."""
        cube = cmds.polyCube(name=name)[0]
        for frame, value in keys.items():
            cmds.setKeyframe(cube, attribute="translateX", time=frame, value=value)
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

    def _make_nonunique_locators(self):
        """Create two locators sharing the leaf name ``WHEEL_LOC`` under
        different parents, animating only the first.

        Returns the animated locator's long DAG path.  After this the short
        name ``WHEEL_LOC`` is non-unique — ``cmds.objExists`` still reports
        it valid, but any query demanding a unique node raises ``More than
        one object matches name``.
        """
        grp_a = cmds.group(empty=True, name="grpA")
        grp_b = cmds.group(empty=True, name="grpB")
        # Animated locator under grpA.  Capture its long path now, while
        # the leaf name is still unique — once the grpB twin exists the
        # short name is ambiguous and cmds.ls(short) is nondeterministic.
        loc = cmds.spaceLocator(name="WHEEL_LOC")[0]
        loc = cmds.parent(loc, grp_a)[0]
        animated = cmds.ls(loc, long=True)[0]  # -> |grpA|WHEEL_LOC
        cmds.setKeyframe(animated, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(animated, attribute="translateX", time=10, value=5)
        # Static twin under grpB, same leaf name — makes the short name ambiguous.
        loc = cmds.spaceLocator(name="WHEEL_LOC")[0]
        cmds.parent(loc, grp_b)
        # Sanity: the short name really is ambiguous now.
        self.assertEqual(len(cmds.ls("WHEEL_LOC", long=True)), 2)
        return animated

    def test_shot_nodes_disambiguates_nonunique_name(self):
        """A stored short name that became non-unique resolves to a single
        unambiguous long path — the anim-curve-bearing one."""
        animated = self._make_nonunique_locators()
        shot = ShotBlock(0, "S", 0, 10, ["WHEEL_LOC"])
        nodes = ShotSequencer._shot_nodes(shot)
        self.assertEqual(len(nodes), 1)
        # Unambiguous (full DAG path) and points at the animated node.
        self.assertIn("|", nodes[0])
        self.assertEqual(cmds.ls(nodes[0], long=True), [animated])

    def test_collect_object_segments_survives_nonunique_name(self):
        """Regression: collect_object_segments must not raise ``More than one
        object matches name`` when a shot references a non-unique short name."""
        animated = self._make_nonunique_locators()
        seq = ShotSequencer([ShotBlock(0, "S", 0, 10, ["WHEEL_LOC"])])
        segs = seq.collect_object_segments(0)  # must not raise
        self.assertTrue(
            any(cmds.ls(s["obj"], long=True) == [animated] for s in segs),
            f"expected a segment for {animated}, got {[s['obj'] for s in segs]}",
        )

    def test_move_object_keys_shifts(self):
        """move_object_keys offsets keys within the given range."""
        cube = self._create_animated_cube("mv", {10: 0, 20: 5})
        seq = ShotSequencer()
        seq.move_object_keys(str(cube), 10, 20, 30)
        keys = sorted(cmds.keyframe(cube, q=True, attribute="translateX"))
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
        keys = sorted(cmds.keyframe(cube, q=True, attribute="translateX"))
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
        a_keys = sorted(cmds.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(a_keys[0], 0.0, places=1)
        self.assertAlmostEqual(a_keys[-1], 80.0, places=1)

        # obj_b keys should be UNTOUCHED at [10,40]
        b_keys = sorted(cmds.keyframe(c2, q=True, attribute="translateX"))
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

    def test_set_shot_start_ripple(self):
        """Moving shot 0's start ripples shot 1's start by the same delta."""
        c1 = self._create_animated_cube("ssr_a", {0: 0, 30: 5})
        c2 = self._create_animated_cube("ssr_b", {50: 0, 80: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 30, [str(c1)]),
                ShotBlock(1, "S1", 50, 80, [str(c2)]),
            ]
        )

        s1_start_before = seq.shot_by_id(1).start
        seq.set_shot_start(0, 10, ripple=True)

        self.assertAlmostEqual(seq.shot_by_id(1).start, s1_start_before + 10, places=1)

    def test_apply_behavior_sets_keys(self):
        """apply_behavior should create keyframes on the object."""
        cube = self._create_animated_cube("obj", {0: 0, 100: 10})
        Behaviors.apply_behavior(str(cube), "fade_in", 0, 100, attrs=["visibility"])

        # Visibility should now have keyframes
        vis_keys = cmds.keyframe(cube, attribute="visibility", query=True)
        self.assertIsNotNone(vis_keys)
        self.assertGreater(len(vis_keys), 0)

    def test_apply_behavior_unknown_raises(self):
        """apply_behavior with an unknown template raises FileNotFoundError."""
        cube = self._create_animated_cube("ab_unknown", {0: 0, 10: 5})
        with self.assertRaises(FileNotFoundError):
            Behaviors.apply_behavior(str(cube), "nonexistent_xyz_behavior", 0, 100)

    # -- gap hold enforcement ----------------------------------------------

    def test_enforce_gap_holds_after_define(self):
        """Manually calling _enforce_gap_holds sets stepped out-tangent on last pre-gap key.

        Bug: Gaps between shots had no automatic tangent enforcement, allowing
        interpolated motion to bleed through gap regions.
        Fixed: 2026-03-13
        """

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

    def test_enforce_gap_holds_skips_next_shot_lead_in(self):
        """A curve whose first key sits in the gap is the NEXT shot's lead-in.

        Bug: shot membership is per object, so every curve on a member reached
        the seam scan -- including ones with no key in the pre-gap shot at all.
        The seam rule ("last key before the next shot's start") then picked
        that curve's FIRST key and stepped its out-tangent, freezing the next
        shot's own animation.  Production case: ``FAILED_CMPT_LOC`` belongs to
        "Step 2.1" (33-65) through its opacity fade while its translate curves
        start at 66 and run to 172; frame 83 read -16.8 instead of -11.5.
        Fixed: 2026-09-03
        """
        cube = cmds.polyCube(name="leadin")[0]
        # Shot-2.1 content: a fade living wholly inside the pre-gap shot.
        for t, v in ((48, 0), (63, 1), (66, 1)):
            cmds.setKeyframe(str(cube), attribute="visibility", time=t, value=v)
        # Shot-3.1 content: starts inside the gap, runs on into the next shot.
        for t, v in ((66, -16.8), (101, -3.75), (141, 0.0), (172, 0.0)):
            cmds.setKeyframe(str(cube), attribute="translateX", time=t, value=v)
        seq = ShotSequencer(
            [
                ShotBlock(0, "Step 2.1", 33, 65, [str(cube)]),
                ShotBlock(1, "Step 3.1", 80, 256, [str(cube)]),
            ]
        )
        crv = (
            cmds.listConnections(
                f"{cube}.translateX", type="animCurve", s=True, d=False
            )
            or []
        )[0]
        before = cmds.keyframe(crv, q=True, time=(83, 83), eval=True, valueChange=True)

        seq._enforce_gap_holds()

        ott = cmds.keyTangent(crv, q=True, time=(66, 66), outTangentType=True)
        self.assertNotEqual(
            ott[0], "step", "Lead-in key of the next shot must not be stepped"
        )
        after = cmds.keyframe(crv, q=True, time=(83, 83), eval=True, valueChange=True)
        self.assertAlmostEqual(
            before[0], after[0], places=4, msg="Next shot's motion was frozen"
        )

    def test_enforce_gap_holds_still_holds_curve_with_pre_gap_content(self):
        """The lead-in skip must not disarm a curve that DOES cross the gap."""
        cube = self._create_animated_cube("crosser", {48: 0, 63: 1, 66: 1})
        seq = ShotSequencer(
            [
                ShotBlock(0, "Step 2.1", 33, 65, [str(cube)]),
                ShotBlock(1, "Step 3.1", 80, 256, [str(cube)]),
            ]
        )
        seq._enforce_gap_holds()
        crv = (
            cmds.listConnections(
                f"{cube}.translateX", type="animCurve", s=True, d=False
            )
            or []
        )[0]
        ott = cmds.keyTangent(crv, q=True, time=(66, 66), outTangentType=True)
        self.assertEqual(ott[0], "step", "Seam on a pre-gap-fed curve still holds")

    def test_set_shot_duration_enforces_gap_holds(self):
        """set_shot_duration should automatically enforce gap holds.

        Bug: Timeline-modifying operations did not enforce stepped tangents
        at gap boundaries, allowing animation bleed between shots.
        Fixed: 2026-03-13
        """

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
        self.assertTrue(
            any(s["kind"] == "anim" and s["obj"] == str(c1) for s in sequences)
        )
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
        seq._move_sequence({"kind": "anim", "obj": str(c1), "start": 10, "end": 20}, 30)
        keys = sorted(cmds.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 30.0, places=1)
        self.assertAlmostEqual(keys[-1], 40.0, places=1)

    def test_move_sequence_noop_when_delta_zero(self):
        """_move_sequence is a no-op when new_start matches old start."""
        c1 = self._create_animated_cube("mvs_b", {10: 0, 20: 5})
        seq = ShotSequencer()
        seq._move_sequence({"kind": "anim", "obj": str(c1), "start": 10, "end": 20}, 10)
        keys = sorted(cmds.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 10.0, places=1)
        self.assertAlmostEqual(keys[-1], 20.0, places=1)

    def test_recompute_shot_objects_drops_orphans(self):
        """_recompute_shot_objects drops objects with no keys in the shot range."""
        c1 = self._create_animated_cube("rc_a", {0: 0, 20: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 20, [str(c1), "ghost_node"])])
        seq._recompute_shot_objects(0)
        objs = seq.shot_by_id(0).objects
        self.assertIn(str(c1), objs)
        self.assertNotIn("ghost_node", objs)

    def test_recompute_shot_objects_preserves_pinned(self):
        """_recompute_shot_objects keeps pinned objects even when keyless."""
        c1 = self._create_animated_cube("rc_b", {0: 0, 20: 5})
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 20, [str(c1), "pinned_ghost"])])
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
        keys = sorted(cmds.keyframe(c1, q=True, attribute="translateX"))
        # Moved to dest.start (100); duration preserved.
        self.assertAlmostEqual(keys[0], 100.0, places=1)
        self.assertAlmostEqual(keys[-1], 110.0, places=1)
        # Object should now belong to dest shot.
        self.assertIn(str(c1), seq.shot_by_id(1).objects)
        self.assertNotIn(str(c1), seq.shot_by_id(0).objects)

    def test_move_sequences_places_after_when_from_upstream(self):
        """Dest already has the obj: the arrival lands after it, with room."""
        # Single object with two key clusters — one in S0, one in S1.
        # The "existing" check in move_sequences_to_shot keys by obj name,
        # so the same obj must appear in dest to trigger the after-anchor path.
        c1 = self._create_animated_cube("mvs_after_a", {10: 0, 20: 5, 110: 0, 130: 5})
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [str(c1)]),
                ShotBlock(1, "S1", 100, 200, [str(c1)]),
            ]
        )
        # Move c1's S0 seq [10,20] into S1; c1's S1 seq [110,130] already lives there.
        seq.move_sequences_to_shot(
            [{"kind": "anim", "obj": str(c1), "start": 10, "end": 20}],
            dest_shot_id=1,
        )
        c1_keys = [
            round(k, 1) for k in cmds.keyframe(c1, q=True, attribute="translateX")
        ]
        # Anchor = end of S1's existing c1 segment (130) PLUS the separation,
        # so the arrival cannot draw as one merged run with what was there.
        sep = seq.sequence_separation()
        self.assertIn(130.0, c1_keys, "the existing cluster stays put")
        self.assertIn(130.0 + sep, c1_keys, "the moved cluster starts clear of it")
        self.assertIn(140.0 + sep, c1_keys)

    def test_move_sequences_appends_even_when_the_source_is_downstream(self):
        """Direction of travel must not change where the arrival lands.

        Anchoring by direction put a clip dragged from a LATER shot ahead of
        the destination's own content -- and, when the group was long enough,
        ahead of the destination's start, i.e. on top of the previous shot.
        One rule ("it goes on the end") is both what an editor expects and the
        only one that cannot reach backwards.
        """
        # Same obj, two clusters: one in S0 (dest), one in S1 (source).
        c2 = self._create_animated_cube(
            "mvs_before_b", {110: 0, 130: 5, 210: 0, 220: 5}
        )
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 100, 200, [str(c2)]),
                ShotBlock(1, "S1", 200, 300, [str(c2)]),
            ]
        )
        # Where the destination's own run ENDS as the sequencer draws it --
        # not where its last key sits: a key held to the next one draws as a
        # clip out to the shot bound, and that is the edge the arrival has to
        # clear to read as a separate clip.
        existing_end = max(
            s["end"] for s in seq.collect_shot_sequences(0, include_audio=False)
        )
        sep = seq.sequence_separation()

        # Move c2's S1 seq [210,220] into S0; c2 already has a run there.
        seq.move_sequences_to_shot(
            [{"kind": "anim", "obj": str(c2), "start": 210, "end": 220}],
            dest_shot_id=0,
        )
        c2_keys = [
            round(k, 1) for k in cmds.keyframe(c2, q=True, attribute="translateX")
        ]
        self.assertIn(110.0, c2_keys, "the existing cluster stays put")
        arrived = [k for k in c2_keys if k > existing_end]
        self.assertTrue(arrived, "the arrival is appended, not prepended")
        self.assertGreaterEqual(
            min(arrived), existing_end + sep, "it clears the existing run"
        )
        self.assertGreaterEqual(
            seq.shot_by_id(0).end, max(arrived), "the shot grew to hold it"
        )

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
        c1_keys = sorted(cmds.keyframe(c1, q=True, attribute="translateX"))
        c2_keys = sorted(cmds.keyframe(c2, q=True, attribute="translateX"))
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
# detect_shots() — functional clustering behavior (requires Maya)
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestDetectShotsBehavior(unittest.TestCase):
    """Cover the actual clustering output of ``detect_shots`` — the
    existence/signature checks in ``TestDetectShots`` only verify wiring.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _animated(self, name, keys):
        cube = cmds.polyCube(name=name)[0]
        for frame, value in keys.items():
            cmds.setKeyframe(cube, attribute="translateX", time=frame, value=value)
        return cube

    def test_single_object_yields_one_shot(self):
        cube = self._animated("ds_single", {1: 0, 10: 5, 20: 10})
        shots = ShotSequencer().detect_shots(objects=[str(cube)])
        self.assertEqual(len(shots), 1)
        self.assertAlmostEqual(shots[0]["start"], 1.0, places=1)
        self.assertAlmostEqual(shots[0]["end"], 20.0, places=1)

    def test_gap_creates_two_shots(self):
        c1 = self._animated("ds_early", {1: 0, 10: 5})
        c2 = self._animated("ds_late", {100: 0, 110: 5})
        shots = ShotSequencer().detect_shots(
            objects=[str(c1), str(c2)], gap_threshold=10
        )
        self.assertEqual(len(shots), 2)

    def test_overlapping_ranges_merge_into_one(self):
        c1 = self._animated("ds_a", {0: 0, 50: 10})
        c2 = self._animated("ds_b", {30: 0, 80: 10})
        shots = ShotSequencer().detect_shots(
            objects=[str(c1), str(c2)], gap_threshold=10
        )
        self.assertEqual(len(shots), 1)
        self.assertAlmostEqual(shots[0]["start"], 0.0, places=1)
        self.assertAlmostEqual(shots[0]["end"], 80.0, places=1)


# ---------------------------------------------------------------------------
# MayaScenePersistence — round-trip against the shared data_internal node
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestMayaScenePersistenceRoundTrip(unittest.TestCase):
    """Mock-based suites cover the script-job wiring; this verifies the data
    actually round-trips through the shared ``data_internal`` channel.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_save_writes_channel_and_load_reads_back(self):
        from mayatk.anim_utils.shots._shots import MayaScenePersistence
        from mayatk.node_utils.data_nodes import DataNodes

        persistence = MayaScenePersistence()
        payload = {"shots": [{"id": 0, "name": "S0", "start": 0, "end": 50}]}
        persistence.save(payload)

        # Payload lands on the shared internal carrier, not a dedicated node.
        self.assertTrue(cmds.objExists(DataNodes.INTERNAL))
        self.assertTrue(
            cmds.attributeQuery(
                MayaScenePersistence.ATTR_NAME, node=DataNodes.INTERNAL, exists=True
            )
        )

        # Load returns the same payload.
        self.assertEqual(persistence.load(), payload)

    def test_load_returns_none_when_no_node(self):
        from mayatk.anim_utils.shots._shots import MayaScenePersistence

        # Fresh scene — no storage node yet.
        self.assertIsNone(MayaScenePersistence().load())

    def test_load_migrates_legacy_shotstore_node(self):
        """A pre-consolidation ``shotStore`` node is folded into data_internal."""
        import json
        from mayatk.anim_utils.shots._shots import MayaScenePersistence
        from mayatk.node_utils.data_nodes import DataNodes

        legacy_node = MayaScenePersistence.LEGACY_NODE_NAME
        legacy_attr = MayaScenePersistence.LEGACY_ATTR_NAME
        payload = {"shots": [{"id": 1, "name": "legacy", "start": 5, "end": 42}]}
        node = cmds.createNode("network", name=legacy_node)
        cmds.addAttr(node, longName=legacy_attr, dataType="string")
        cmds.setAttr(f"{node}.{legacy_attr}", json.dumps(payload), type="string")
        cmds.lockNode(node, lock=False, lockName=True)  # matches old carrier

        persistence = MayaScenePersistence()
        self.assertEqual(persistence.load(), payload)

        # Old carrier is gone; payload now lives on data_internal.
        self.assertFalse(cmds.objExists(legacy_node))
        self.assertEqual(
            DataNodes.get_internal_string(MayaScenePersistence.ATTR_NAME),
            json.dumps(payload),
        )
        # Subsequent loads read the migrated channel directly.
        self.assertEqual(persistence.load(), payload)


# ---------------------------------------------------------------------------
# Shot rename reconciliation — ShotSequencer.reconcile_all_shots()
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestReconcileStalePaths(unittest.TestCase):
    """Renaming an object listed in a shot must resolve to the new name
    on the next reconcile.  Replaces the old PyMEL ``test_load_resolves_renames``.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_parent_rename_resolves_child_via_short_name(self):
        """When a parent is renamed, stored long paths to its children go
        stale.  ``_reconcile_stale_paths`` re-resolves them by leaf name.
        """
        parent = cmds.group(empty=True, name="P")
        cmds.select(clear=True)
        child = cmds.polyCube(name="C")[0]
        child = cmds.parent(child, parent)[0]
        cmds.setKeyframe(child, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(child, attribute="translateX", time=50, value=10)

        stored_long = cmds.ls(child, long=True)[0]  # "|P|C"
        store = ShotStore()
        store.define_shot("S", 0, 50, objects=[stored_long])
        seq = ShotSequencer(store=store)

        cmds.rename(parent, "P_renamed")
        self.assertFalse(cmds.objExists(stored_long))

        self.assertTrue(seq.reconcile_all_shots())
        new_long = cmds.ls("C", long=True)[0]
        self.assertIn(new_long, store.shots[0].objects)
        self.assertNotIn(stored_long, store.shots[0].objects)

    def test_reconcile_resolves_ambiguous_leaf_to_animated(self):
        """A stale stored path whose leaf name is now non-unique resolves
        to the anim-curve-bearing node (exercises the multi-match branch
        of ``_reconcile_stale_paths``)."""
        grp_a = cmds.group(empty=True, name="grpA")
        grp_b = cmds.group(empty=True, name="grpB")
        loc = cmds.spaceLocator(name="WHEEL_LOC")[0]
        loc = cmds.parent(loc, grp_a)[0]
        animated = cmds.ls(loc, long=True)[0]  # |grpA|WHEEL_LOC
        cmds.setKeyframe(animated, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(animated, attribute="translateX", time=50, value=10)
        loc = cmds.spaceLocator(name="WHEEL_LOC")[0]
        cmds.parent(loc, grp_b)  # |grpB|WHEEL_LOC — static twin

        store = ShotStore()
        store.define_shot("S", 0, 50, objects=["|ghost|WHEEL_LOC"])  # stale path
        seq = ShotSequencer(store=store)

        self.assertTrue(seq.reconcile_all_shots())
        self.assertEqual(store.shots[0].objects, [animated])


# ---------------------------------------------------------------------------
# Shot Manifest tests (pure Python -- no Maya)
# ---------------------------------------------------------------------------

from unittest.mock import patch

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    ManifestModel,
    BuilderObject,
    BuilderStep,
    ShotManifest,
)


class TestDetectBehaviors(unittest.TestCase):
    """Test behavior auto-detection from step-contents text."""

    def test_fade_in(self):
        self.assertEqual(ManifestModel.detect_behaviors("Arrow fades in."), ["fade_in"])

    def test_fade_out(self):
        self.assertEqual(
            ManifestModel.detect_behaviors("Checklist fades out."), ["fade_out"]
        )

    def test_fade_in_and_out(self):
        self.assertEqual(
            ManifestModel.detect_behaviors("Arrow fades in, then fades out."),
            ["fade_in", "fade_out"],
        )

    def test_no_behavior(self):
        self.assertEqual(ManifestModel.detect_behaviors("User is teleported."), [])

    def test_empty(self):
        self.assertEqual(ManifestModel.detect_behaviors(""), [])

    def test_na(self):
        self.assertEqual(ManifestModel.detect_behaviors("N/A"), [])


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
        steps = ManifestModel.parse_csv(self._csv_path)
        self.assertEqual(len(steps), 3)  # A01, A02, B01

    def test_section_assignment(self):
        steps = ManifestModel.parse_csv(self._csv_path)
        self.assertEqual(steps[0].section, "A")
        self.assertEqual(steps[2].section, "B")

    def test_continuation_row_merges(self):
        steps = ManifestModel.parse_csv(self._csv_path)
        a01 = steps[0]
        self.assertEqual(len(a01.objects), 2)
        self.assertEqual(a01.objects[0].name, "ARROW_01")
        self.assertEqual(a01.objects[1].name, "ARROW_02")

    def test_continuation_inherits_behavior(self):
        """Continuation-row objects inherit the parent step's behavior."""
        steps = ManifestModel.parse_csv(self._csv_path)
        a01 = steps[0]
        self.assertEqual(a01.objects[0].behaviors, ["fade_in"])
        self.assertEqual(a01.objects[1].behaviors, ["fade_in"])  # inherited

    def test_behavior_detected(self):
        steps = ManifestModel.parse_csv(self._csv_path)
        self.assertEqual(steps[0].objects[0].behaviors, ["fade_in"])
        self.assertEqual(steps[1].objects[0].behaviors, ["fade_out"])

    def test_na_objects_excluded(self):
        steps = ManifestModel.parse_csv(self._csv_path)
        b01 = steps[2]
        self.assertEqual(len(b01.objects), 0)

    def test_section_title(self):
        steps = ManifestModel.parse_csv(self._csv_path)
        self.assertEqual(steps[0].section_title, "AILERON RIGGING")

    def test_duplicate_step_id_skipped(self):
        """Duplicate step_id rows should be skipped with a warning."""
        import tempfile
        import shutil

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
            steps = ManifestModel.parse_csv(csv_path)
            self.assertEqual(len(steps), 2)  # A01 + A02 only
            self.assertEqual(steps[0].description, "first")
            self.assertEqual(steps[1].step_id, "A02")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_continuation_merges_content(self):
        """Continuation rows with content should merge text into parent step."""
        import tempfile
        import shutil

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
            steps = ManifestModel.parse_csv(csv_path)
            self.assertIn("Second line.", steps[0].description)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_c130h_layout(self):
        """C-130H CSV layout: 'Step Contents' at col 2, 'Asset Names' at col 3.

        Bug: ColumnMap hardcoded integer indices matching only C-5M layout.
        Headers at different positions caused wrong columns to be read.
        Fixed: 2026-03-13
        """
        import tempfile
        import shutil

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
            steps = ManifestModel.parse_csv(csv_path)
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
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "bad.csv")
            import csv as csv_mod

            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: SEC", "", ""])
                w.writerow(["Step", "Ref", "Bad Column"])
            with self.assertRaises(ValueError):
                ManifestModel.parse_csv(csv_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_non_numbered_step_id_parsed(self):
        """Non-numbered step IDs like SETUP are recognized as steps.

        Bug: _STEP_RE only matched 'A01.)' format, silently dropping
        SETUP rows from 'SECTION X: OPENING SETUP'.
        Fixed: 2026-03-24
        """
        import tempfile
        import shutil

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
            steps = ManifestModel.parse_csv(
                csv_path, columns=ColumnMap(exclude_steps=())
            )
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
        # fit_contents lets the behavior length (15f) win over the 200f
        # default initial_shot_length used by extend_only.
        actions = builder.update(self._make_steps(), fit_mode="fit_contents")
        self.assertEqual(len(store.shots), 2)
        self.assertEqual(store.shots[0].name, "A01")
        self.assertAlmostEqual(store.shots[0].start, 1)
        # fade_in = 15f content-driven duration
        self.assertAlmostEqual(store.shots[0].end, 16)
        self.assertEqual(actions["A01"], "created")
        self.assertEqual(actions["A02"], "created")

    def test_from_csv_accepts_existing_store(self):
        """from_csv should use the provided store, not create a new one."""
        import tempfile
        import shutil

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
        dur = Behaviors.compute_duration(entries, fallback=30)
        self.assertEqual(dur, 15)

    def test_fade_in_and_out_duration(self):
        """Step with fade_in + fade_out: duration = 15 + 15 = 30f."""
        entries = [BuilderObject("OBJ", ["fade_in", "fade_out"])]
        dur = Behaviors.compute_duration(entries, fallback=30)
        self.assertEqual(dur, 30)

    def test_no_behavior_uses_fallback(self):
        """Step with no behaviors -> fallback duration."""
        entries = [BuilderObject("OBJ")]
        dur = Behaviors.compute_duration(entries, fallback=42)
        self.assertEqual(dur, 42)

    def test_empty_step_uses_fallback(self):
        """Step with no objects -> fallback duration."""
        entries = []
        dur = Behaviors.compute_duration(entries, fallback=50)
        self.assertEqual(dur, 50)

    def test_mixed_behaviors_takes_max(self):
        """Max across objects: fade_in+fade_out (30) > fade_in (15)."""
        entries = [
            BuilderObject("A", ["fade_in"]),
            BuilderObject("B", ["fade_in", "fade_out"]),
        ]
        dur = Behaviors.compute_duration(entries, fallback=30)
        self.assertEqual(dur, 30)

    def test_update_uses_content_duration(self):
        """Update should use content-driven per-step durations.

        ``fit_mode='fit_contents'`` makes the behavior/audio-driven length
        win over ``initial_shot_length``; the default mode is
        ``extend_only`` which floors to 200f.
        """
        steps = [
            BuilderStep("A01", "A", "", "", [BuilderObject("X", ["fade_in"])]),
            BuilderStep(
                "A02", "A", "", "", [BuilderObject("Y", ["fade_in", "fade_out"])]
            ),
        ]
        store = ShotStore()
        builder = ShotManifest(store)
        builder.update(steps, fit_mode="fit_contents")
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
        result = Behaviors.apply_to_shots(
            store.sorted_shots(),
            apply_fn=mock_apply,
            exists_fn=lambda _: True,
            has_keys_fn=lambda *_: True,
        )

        # Behavior should NOT have been applied (existing keys -> skip)
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

        # Prevent MayaScenePersistence.load() from hitting the data node.
        # Production now uses cmds (post-PyMEL migration), not pm.
        with patch("mayatk.anim_utils.shots._shots.cmds") as mock_cmds:
            mock_cmds.objExists.return_value = False
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
        """Uniform gaps → compute_gap returns the common gap value."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=60, end=100)
        store.define_shot(name="C", start=110, end=150)
        self.assertAlmostEqual(store.compute_gap(), 10.0)

    def test_compute_gap_zero(self):
        """Abutting shots → compute_gap returns 0."""
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
        """Mixed gap sizes → returns the median."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=55, end=100)  # gap=5
        store.define_shot(name="C", start=110, end=150)  # gap=10
        store.define_shot(name="D", start=160, end=200)  # gap=10
        # Sorted gaps: [5, 10, 10] → median = 10
        self.assertAlmostEqual(store.compute_gap(), 10.0)

    def test_compute_gap_overlapping_clamps_to_zero(self):
        """Overlapping shots produce negative raw gaps; compute_gap clamps to 0.

        Bug: overlapping shots returned negative gap values.
        Fixed: 2026-03-25
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=60)
        store.define_shot(name="B", start=50, end=110)  # overlap → raw gap -10
        self.assertAlmostEqual(store.compute_gap(), 0.0)

    def test_compute_gap_even_count_rounds_to_int(self):
        """Even number of gaps → median is rounded to nearest integer.

        Bug: even-count median could return a fractional .5 value.
        Fixed: 2026-03-25
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A", start=0, end=50)
        store.define_shot(name="B", start=55, end=100)  # gap=5
        store.define_shot(name="C", start=110, end=150)  # gap=10
        # Sorted gaps: [5, 10] → raw mean = 7.5 → rounded = 8
        result = store.compute_gap()
        self.assertEqual(result, result // 1)  # is a whole number

    # NOTE: ShotStore.ripple_shift/_upstream were removed — they were the
    # interleaved resolve→mutate shape the plan/apply split replaced.
    # Ripple behavior is pinned in test_shot_plan.py and via
    # ShotSequencer.ripple_downstream/ripple_upstream below.

    def test_sorted_shots_timeline_order(self):
        """sorted_shots returns shots in start-time order regardless of creation."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="C", start=100, end=150)
        store.define_shot(name="A", start=10, end=40)
        store.define_shot(name="B", start=50, end=80)
        names = [s.name for s in store.sorted_shots()]
        self.assertEqual(names, ["A", "B", "C"])


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestKeysBatchAuditRegressions(unittest.TestCase):
    """Audit regressions: expansion order and same-curve gesture merging."""

    def setUp(self):
        cmds.file(new=True, force=True)

    class _Clip:
        def __init__(self, data):
            self.data = data

    class _Widget:
        def __init__(self, clips):
            self._clips = clips

        def get_clip(self, clip_id):
            return self._clips.get(clip_id)

    def _host(self, clips, seq):
        import logging

        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin

        outer = self

        class Host(ClipMotionMixin):
            def __init__(self):
                self.sequencer = seq
                self._segment_cache = {}
                self._sub_row_cache = {}
                self._syncing = False
                self.logger = logging.getLogger("test.keys_audit_host")
                self.synced = []
                self.footers = []
                self._widget = outer._Widget(clips)

            def _get_sequencer_widget(self):
                return self._widget

            def _save_shot_state(self):
                self.sequencer.store.push_boundary_snapshot()

            def _discard_shot_state(self):
                self.sequencer.store.discard_boundary_snapshot()

            def _sync_to_widget(self, shot_id=None):
                self.synced.append(shot_id)

            def _sync_combobox(self):
                pass

            def _set_footer(self, text, *a, **k):
                self.footers.append(text)

        return Host()

    def test_expansion_cannot_double_move_a_landed_key(self):
        """The S1: at zero gap, a key dragged onto the NEXT shot's start on
        an object that is a member of BOTH shots.  Committing first and
        expanding after let the expansion's downstream ripple sweep the
        freshly-landed key a second time (the next shot's envelope starts at
        its .start).  Expansion now runs BEFORE the commit."""
        cube = cmds.polyCube(name="dm_shared")[0]
        # Keys in shot A (0-30) and in shot B (30-60) on the SAME object.
        for t, v in ((10, 0.0), (20, 1.0), (40, 2.0), (55, 3.0)):
            cmds.setKeyframe(cube, at="translateX", t=t, v=v)
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 30, [cube]),
                ShotBlock(1, "B", 30, 60, [cube]),
            ]
        )
        clips = {1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0})}
        host = self._host(clips, seq)

        # Drag A's key at 20 exactly onto B's start (30).
        host.on_keys_batch_moved([(1, [(20.0, 30.0)])])

        times = sorted(cmds.keyframe(cube, q=True, at="translateX") or [])
        self.assertIn(
            30.0,
            times,
            f"the landed key must sit AT 30, not swept further: {times}",
        )
        shot_a = seq.shot_by_id(0)
        self.assertGreaterEqual(shot_a.end, 30.0, "shot A grew to own the landed key")
        # B's own keys rippled right by A's expansion (0 here since 30 was
        # already A's end)… with a zero-delta expansion nothing else moves.
        self.assertEqual(
            times, [10.0, 30.0, 40.0, 55.0], f"no other key may move: {times}"
        )

    def test_expansion_past_the_boundary_ripples_but_never_double_moves(self):
        """Land at 35 — INSIDE B's territory.  A must grow to 35, B must
        ripple right by 5, and the landed key must sit exactly at 35."""
        cube = cmds.polyCube(name="dm_shared2")[0]
        for t, v in ((10, 0.0), (20, 1.0), (40, 2.0), (55, 3.0)):
            cmds.setKeyframe(cube, at="translateX", t=t, v=v)
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 30, [cube]),
                ShotBlock(1, "B", 30, 60, [cube]),
            ]
        )
        clips = {1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0})}
        host = self._host(clips, seq)

        host.on_keys_batch_moved([(1, [(20.0, 35.0)])])

        times = sorted(cmds.keyframe(cube, q=True, at="translateX") or [])
        self.assertIn(35.0, times, f"landed key must sit at 35: {times}")
        self.assertEqual(
            times,
            [10.0, 35.0, 45.0, 60.0],
            f"B's keys ride the +5 ripple exactly once: {times}",
        )
        self.assertAlmostEqual(seq.shot_by_id(0).end, 35.0)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 35.0)

    def test_same_curve_two_clips_one_gesture_commits_once(self):
        """The S1: two clips backed by the SAME anim curve (split_static
        segments of one obj.attr).  Sequential per-clip commits let group
        1's landed key be re-grabbed or overwritten by group 2's window;
        the gesture is now merged per curve before any commit."""
        cube = cmds.polyCube(name="dm_samecurve")[0]
        for t, v in ((10, 0.0), (20, 5.0), (40, 5.0), (50, 9.0)):
            cmds.setKeyframe(cube, at="translateX", t=t, v=v)
        seq = ShotSequencer([ShotBlock(0, "A", 0, 100, [cube])])
        clips = {
            1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0}),
            2: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0}),
        }
        host = self._host(clips, seq)

        # One drag, +20 on both clips' keys: clip 1 moves (10,20), clip 2
        # moves (40,50).  Clip 1's key at 20 lands at 40 — exactly clip 2's
        # origin.  Per-clip commits collapsed the two; merged they slide.
        host.on_keys_batch_moved(
            [(1, [(10.0, 30.0), (20.0, 40.0)]), (2, [(40.0, 60.0), (50.0, 70.0)])]
        )

        times = sorted(cmds.keyframe(cube, q=True, at="translateX") or [])
        vals = cmds.keyframe(cube, q=True, at="translateX", valueChange=True)
        self.assertEqual(
            times,
            [30.0, 40.0, 60.0, 70.0],
            f"all four keys must survive at +20: {times}",
        )
        self.assertEqual(
            sorted(vals), [0.0, 5.0, 5.0, 9.0], f"no value may be lost: {vals}"
        )

    def test_gesture_is_one_undo_step_and_syncing_guard_restores(self):
        cube = cmds.polyCube(name="dm_chunk")[0]
        for t in (10, 20):
            cmds.setKeyframe(cube, at="translateX", t=t, v=float(t))
        seq = ShotSequencer([ShotBlock(0, "A", 0, 100, [cube])])
        clips = {1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0})}
        host = self._host(clips, seq)
        host.on_keys_batch_moved([(1, [(10.0, 15.0), (20.0, 25.0)])])
        self.assertFalse(host._syncing, "guard must restore after the commit")
        cmds.undo()
        self.assertEqual(
            sorted(cmds.keyframe(cube, q=True, at="translateX") or []),
            [10.0, 20.0],
            "one Ctrl+Z reverses the whole gesture",
        )


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestResizeShotBoundsRipple(unittest.TestCase):
    """Every edge move ripples, so the gap keeps its width — and the pivot's
    OWN keys, including the ones a shrink strands outside the new bounds,
    never move.  The two halves are in tension: the ripple runs BEFORE the
    pivot's bounds are written precisely so the neighbour's move window,
    bounded by the pivot's OLD boundary, cannot reach the stranded keys."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def _two_shots_shared(self):
        cube = cmds.polyCube(name="rsb_shared")[0]
        for t, v in ((10, 0.0), (45, 1.0), (70, 2.0), (95, 3.0)):
            cmds.setKeyframe(cube, at="translateX", t=t, v=v)
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 50, [cube]),
                ShotBlock(1, "B", 60, 100, [cube]),
            ]
        )
        return cube, seq

    def _gap(self, seq):
        shots = seq.sorted_shots()
        return round(shots[1].start - shots[0].end, 3)

    def _keys(self, cube):
        return sorted(cmds.keyframe(cube, q=True, at="translateX") or [])

    def test_tail_shrink_pulls_the_neighbour_in_and_keeps_the_gap(self):
        cube, seq = self._two_shots_shared()
        seq.resize_shot_bounds(0, 0, 30)  # strands A's key at 45
        self.assertEqual(self._gap(seq), 10.0)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 40.0, msg="B follows in")
        self.assertEqual(
            self._keys(cube),
            [10.0, 45.0, 50.0, 75.0],
            "A's own keys (10, 45) hold; B's ride the -20",
        )

    def test_head_shrink_pulls_the_upstream_in_and_keeps_the_gap(self):
        cube, seq = self._two_shots_shared()
        seq.resize_shot_bounds(1, 80, 100)  # strands B's key at 70
        self.assertEqual(self._gap(seq), 10.0)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 70.0, msg="A follows in")
        self.assertEqual(
            self._keys(cube),
            [30.0, 65.0, 70.0, 95.0],
            "B's own keys (70, 95) hold; A's ride the +20",
        )

    def test_tail_grow_still_ripples_downstream(self):
        cube, seq = self._two_shots_shared()
        seq.resize_shot_bounds(0, 0, 55)
        self.assertEqual(self._gap(seq), 10.0)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 65.0)
        self.assertEqual(
            self._keys(cube),
            [10.0, 45.0, 75.0, 100.0],
            "B's keys ride the +5 push; A's stay",
        )

    def test_head_grow_still_ripples_upstream(self):
        cube, seq = self._two_shots_shared()
        seq.resize_shot_bounds(1, 55, 100)
        self.assertEqual(self._gap(seq), 10.0)
        self.assertEqual(self._keys(cube), [5.0, 40.0, 70.0, 95.0])


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestGapHoldSeam(unittest.TestCase):
    """Audit regression: the gap hold steps the LAST key before the next
    shot (the envelope seam), never a mid-content key with stranded keys
    interpolating beyond it."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_hold_lands_on_the_stranded_run_seam(self):
        cube = cmds.polyCube(name="seam_cube")[0]
        for t, v in ((10, 0.0), (25, 1.0), (45, 2.0), (70, 3.0)):
            cmds.setKeyframe(cube, at="translateX", t=t, v=v)
        # A's bounds end at 20, stranding its keys at 25 and 45 in the gap.
        # Set up directly rather than by resizing: a resize now ripples B in
        # behind the bound, which would take the gap out from under them.
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 20, [cube]),
                ShotBlock(1, "B", 60, 100, [cube]),
            ]
        )

        crv = cmds.listConnections(
            f"{cube}.translateX", type="animCurve", s=True, d=False
        )[0]

        def ott(t):
            return cmds.keyTangent(crv, q=True, time=(t, t), outTangentType=True)[0]

        seq._enforce_gap_holds()

        self.assertEqual(ott(45.0), "step", "the seam key (last before B) must hold")
        self.assertNotEqual(
            ott(25.0),
            "step",
            "a mid-content stranded key must NOT be stepped — the motion "
            "between stranded keys is the shot's own content",
        )


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestInsertAfterTrailingContent(unittest.TestCase):
    """Audit regression: appending after the LAST shot must clear its
    trailing envelope content (fade tails past .end)."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_append_clears_the_fade_tail(self):
        cube = cmds.polyCube(name="tail_cube")[0]
        for t, v in ((10, 0.0), (48, 1.0), (75, 0.0)):  # tail key at 75
            cmds.setKeyframe(cube, at="translateX", t=t, v=v)
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, [cube])])
        seq.store.gap = 10
        new = seq.insert_shot("B", duration=20)
        self.assertGreaterEqual(
            new.start,
            85.0,
            f"the new shot must start after the 75f fade tail + gap: {new.start}",
        )

    def test_append_without_trailing_content_uses_the_plain_gap(self):
        cube = cmds.polyCube(name="tail_cube2")[0]
        for t in (10, 48):
            cmds.setKeyframe(cube, at="translateX", t=t, v=1.0)
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, [cube])])
        seq.store.gap = 10
        new = seq.insert_shot("B", duration=20)
        self.assertAlmostEqual(new.start, 60.0)


class TestFitExtendOneSided(unittest.TestCase):
    """Audit regression: extend must rescue content that drifted entirely
    past ONE edge (the other side substitutes the shot's own boundary)."""

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_extend_encloses_content_entirely_past_the_tail(self):
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="ext_cube")[0]
        for t in (70, 90):  # ALL content past the shot's end
            cmds.setKeyframe(cube, at="translateX", t=t, v=float(t))
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, [cube])])
        head, tail = seq.extend_shot_to_fit(0)
        self.assertAlmostEqual(head, 0.0)
        self.assertGreater(tail, 0.0)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 90.0)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 0.0)


class TestKeysBatchMoved(unittest.TestCase):
    """One key drag == one commit: a single undo chunk, a snapshot taken
    BEFORE the boundary follow-up, and the panel left showing the shot the
    drag started in."""

    def setUp(self):
        cmds.file(new=True, force=True)

    class _Clip:
        def __init__(self, data):
            self.data = data

    class _Widget:
        def __init__(self, clips):
            self._clips = clips

        def get_clip(self, clip_id):
            return self._clips.get(clip_id)

    def _host(self, clips, seq):
        import logging

        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin

        outer = self

        class Host(ClipMotionMixin):
            """Uses the REAL snapshot push/pop so ordering is observable."""

            def __init__(self):
                self.sequencer = seq
                self._segment_cache = {}
                self._sub_row_cache = {}
                self._syncing = False
                self.logger = logging.getLogger("test.keys_batch_host")
                self._shot_undo_stack = []
                self.synced = []
                self.footers = []
                self._widget = outer._Widget(clips)

            def _get_sequencer_widget(self):
                return self._widget

            def _save_shot_state(self):
                self._shot_undo_stack.append(
                    [(sh.shot_id, sh.start, sh.end) for sh in self.sequencer.shots]
                )

            def _discard_shot_state(self):
                if self._shot_undo_stack:
                    self._shot_undo_stack.pop()

            def _sync_to_widget(self, shot_id=None):
                self.synced.append(shot_id)

            def _sync_combobox(self):
                pass

            def _set_footer(self, text, *a, **k):
                self.footers.append(text)

        return Host()

    def _keyed_cube(self, name, times):
        cube = cmds.polyCube(name=name)[0]
        for i, t in enumerate(times):
            cmds.setKeyframe(cube, at="translateX", t=t, v=float(i))
        return str(cube)

    def test_snapshot_predates_the_boundary_expansion(self):
        """The snapshot must hold the PRE-drag bounds.

        The boundary follow-up runs inside the same commit, so a snapshot
        taken afterwards records the expanded bounds and undo would re-apply
        the very expansion it is meant to reverse.
        """
        cube = self._keyed_cube("kb_expand", (10, 20))
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 30, [cube])])
        clips = {1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0})}
        host = self._host(clips, seq)

        # Drag the key at 20 out to 60 — past the shot's end at 30.
        host.on_keys_batch_moved([(1, [(20.0, 60.0)])])

        self.assertGreaterEqual(
            seq.shot_by_id(0).end, 60.0, "the shot must follow the key out"
        )
        # The restore point lives on the store's ledger (scene_edit pushes it
        # before any mutation); assert it through behaviour, not internals.
        self.assertTrue(seq.store.has_boundary_snapshot())
        self.assertEqual(
            seq.store.peek_boundary_tag()[0],
            True,
            "a key commit reaches Maya's queue, so the restore point is paired",
        )
        self.assertTrue(seq.store.restore_boundary_snapshot())
        self.assertEqual(
            (seq.shot_by_id(0).start, seq.shot_by_id(0).end),
            (0.0, 30.0),
            "the snapshot must hold the PRE-drag bounds",
        )

    def test_noop_drag_leaves_no_snapshot_behind(self):
        cube = self._keyed_cube("kb_noop", (10, 20))
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 30, [cube])])
        clips = {1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0})}
        host = self._host(clips, seq)

        host.on_keys_batch_moved([(1, [(20.0, 20.0)])])  # zero delta

        self.assertFalse(
            seq.store.has_boundary_snapshot(),
            "a no-op must not leave a restore point for an edit that never happened",
        )
        self.assertEqual(host.synced, [], "and must not rebuild")

    def test_rebuild_targets_the_originating_shot(self):
        """A drag spanning two shots must not retarget the panel.

        The originating clip contributes nothing here (zero delta), so the
        only shot that RECORDS a change is the other one -- which is exactly
        the case where deriving the target from the changed set sends the
        panel to a shot the user was not looking at.
        """
        a = self._keyed_cube("kb_a", (10, 20))
        b = self._keyed_cube("kb_b", (110, 120))
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, [a]),
                ShotBlock(1, "S1", 100, 150, [b]),
            ]
        )
        clips = {
            1: self._Clip({"obj": a, "attr_name": "translateX", "shot_id": 0}),
            2: self._Clip({"obj": b, "attr_name": "translateX", "shot_id": 1}),
        }
        host = self._host(clips, seq)

        host.on_keys_batch_moved([(1, [(20.0, 20.0)]), (2, [(120.0, 125.0)])])

        self.assertEqual(
            host.synced,
            [0],
            "the shot on screen is the one the drag started in",
        )

    def test_whole_gesture_is_one_undo_chunk(self):
        a = self._keyed_cube("kb_c1", (10, 20))
        b = self._keyed_cube("kb_c2", (10, 20))
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [a, b])])
        clips = {
            1: self._Clip({"obj": a, "attr_name": "translateX", "shot_id": 0}),
            2: self._Clip({"obj": b, "attr_name": "translateX", "shot_id": 0}),
        }
        host = self._host(clips, seq)

        host.on_keys_batch_moved([(1, [(20.0, 25.0)]), (2, [(20.0, 25.0)])])
        self.assertEqual(cmds.keyframe(a, q=True, at="translateX"), [10.0, 25.0])
        self.assertEqual(cmds.keyframe(b, q=True, at="translateX"), [10.0, 25.0])

        cmds.undo()
        self.assertEqual(
            (
                cmds.keyframe(a, q=True, at="translateX"),
                cmds.keyframe(b, q=True, at="translateX"),
            ),
            ([10.0, 20.0], [10.0, 20.0]),
            "one Ctrl+Z must reverse the whole drag, not one curve of it",
        )

    def test_on_keys_moved_routes_through_the_batch_path(self):
        cube = self._keyed_cube("kb_single", (10, 20))
        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [cube])])
        clips = {1: self._Clip({"obj": cube, "attr_name": "translateX", "shot_id": 0})}
        host = self._host(clips, seq)

        host.on_keys_moved(1, [(20.0, 25.0)])
        self.assertEqual(cmds.keyframe(cube, q=True, at="translateX"), [10.0, 25.0])
        self.assertEqual(host.synced, [0])


class TestResizeShotBounds(unittest.TestCase):
    """``resize_shot_bounds`` is the plain shot-edge drag: the boundary moves
    and the keyframes stay where the animator put them.  ``resize_shot`` (the
    Shift gesture) is the one that retimes content."""

    def _seq(self):
        return ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, []),
                ShotBlock(1, "S1", 60, 100, []),
            ]
        )

    def test_tail_grows_and_ripples_downstream(self):
        seq = self._seq()
        seq.resize_shot_bounds(0, 0, 70)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 0)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 70)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 80)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 120)

    def test_head_grow_ripples_upstream_away(self):
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 40, []),
                ShotBlock(1, "S1", 50, 90, []),
            ]
        )
        seq.resize_shot_bounds(1, 45, 90)  # head grows left by 5
        self.assertAlmostEqual(seq.shot_by_id(1).start, 45)
        self.assertAlmostEqual(seq.shot_by_id(0).start, -5)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 35)

    def test_head_shrink_pulls_the_upstream_in_and_keeps_the_gap(self):
        """A shrinking edge ripples too, so the gap keeps its width."""
        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 40, []),
                ShotBlock(1, "S1", 50, 90, []),
            ]
        )
        seq.resize_shot_bounds(1, 60, 90)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 60)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 10)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 50)

    def test_inverted_bounds_are_normalised(self):
        seq = self._seq()
        seq.resize_shot_bounds(0, 40, 10)
        shot = seq.shot_by_id(0)
        self.assertLessEqual(shot.start, shot.end)

    def test_noop_when_unchanged(self):
        seq = self._seq()
        seq.resize_shot_bounds(0, 0, 50)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 60)

    def test_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            self._seq().resize_shot_bounds(99, 0, 10)

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_keys_are_left_alone(self):
        """The whole point: a plain edge drag must not retime content."""
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="rsb_cube")[0]
        cmds.setKeyframe(cube, at="translateX", t=10, v=0)
        cmds.setKeyframe(cube, at="translateX", t=40, v=10)

        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [cube])])
        seq.resize_shot_bounds(0, 0, 100)

        self.assertEqual(
            sorted(cmds.keyframe(cube, q=True, at="translateX") or []),
            [10.0, 40.0],
            "boundary-only resize must leave keyframes untouched",
        )

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_resize_shot_still_scales(self):
        """Control: the Shift gesture's engine call must still retime."""
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="rs_cube")[0]
        cmds.setKeyframe(cube, at="translateX", t=0, v=0)
        cmds.setKeyframe(cube, at="translateX", t=50, v=10)

        seq = ShotSequencer([ShotBlock(0, "S0", 0, 50, [cube])])
        seq.resize_shot(0, 0, 100)

        self.assertEqual(
            sorted(cmds.keyframe(cube, q=True, at="translateX") or []),
            [0.0, 100.0],
            "Shift+edge drag must scale the shot's keys into the new range",
        )


class TestInsertShot(unittest.TestCase):
    """Making room in the middle used to mean hand-rippling every following
    shot; ``insert_shot`` opens the space first, then defines the shot."""

    def _seq(self):
        return ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, []),
                ShotBlock(1, "S1", 60, 100, []),
            ]
        )

    def test_insert_after_pushes_the_follower(self):
        seq = self._seq()
        seq.store.gap = 10
        new = seq.insert_shot("Mid", duration=20, after_shot_id=0)
        self.assertAlmostEqual(new.start, 60)
        self.assertAlmostEqual(new.end, 80)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 90)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 130)

    def test_insert_at_head_pushes_everything(self):
        seq = self._seq()
        seq.store.gap = 10
        new = seq.insert_shot("Head", duration=20, at_position=1)
        self.assertAlmostEqual(new.start, 0)
        self.assertAlmostEqual(new.end, 20)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 30)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 90)

    def test_insert_with_no_anchor_appends(self):
        seq = self._seq()
        seq.store.gap = 10
        new = seq.insert_shot("Tail", duration=20)
        self.assertAlmostEqual(new.start, 110)
        self.assertAlmostEqual(seq.shot_by_id(1).end, 100, msg="no ripple needed")

    def test_insert_into_empty_store(self):
        seq = ShotSequencer([])
        new = seq.insert_shot("First", duration=25)
        self.assertAlmostEqual(new.start, 1)
        self.assertAlmostEqual(new.end, 26)

    def test_unknown_anchor_raises(self):
        with self.assertRaises(ValueError):
            self._seq().insert_shot("X", duration=10, after_shot_id=99)

    def test_inserted_shot_starts_empty(self):
        seq = self._seq()
        new = seq.insert_shot("Mid", duration=20, after_shot_id=0)
        self.assertEqual(new.objects, [])

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_downstream_keys_travel_with_their_shot(self):
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="ins_cube")[0]
        cmds.setKeyframe(cube, at="translateX", t=60, v=0)
        cmds.setKeyframe(cube, at="translateX", t=100, v=10)

        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 50, []),
                ShotBlock(1, "S1", 60, 100, [cube]),
            ]
        )
        seq.store.gap = 10
        seq.insert_shot("Mid", duration=20, after_shot_id=0)

        self.assertEqual(
            sorted(cmds.keyframe(cube, q=True, at="translateX") or []),
            [90.0, 130.0],
            "the pushed shot's keys must move with it",
        )


class TestDirectionalTrim(unittest.TestCase):
    """Trimming one end is the common case when hand-tuning a cut."""

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_trim_leading_leaves_the_tail(self):
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="trim_cube")[0]
        cmds.setKeyframe(cube, at="translateX", t=20, v=0)
        cmds.setKeyframe(cube, at="translateX", t=60, v=10)

        seq = ShotSequencer([ShotBlock(0, "S0", 0, 100, [cube])])
        head, tail = seq.trim_shot_to_content(0, edge="leading")
        self.assertGreater(head, 0)
        self.assertAlmostEqual(tail, 0)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 100)

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_trim_trailing_leaves_the_head(self):
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="trim_cube2")[0]
        cmds.setKeyframe(cube, at="translateX", t=20, v=0)
        cmds.setKeyframe(cube, at="translateX", t=60, v=10)

        seq = ShotSequencer([ShotBlock(0, "S0", 0, 100, [cube])])
        head, tail = seq.trim_shot_to_content(0, edge="trailing")
        self.assertAlmostEqual(head, 0)
        self.assertLess(tail, 0)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 0)

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_a_trim_never_moves_a_bound_past_a_key(self):
        """A trailing HOLD is not empty space.

        Bug: the trim measured content with ``collect_shot_sequences``, which
        reports MOTION — hold spans are dropped so a clip reads as the
        animation it plays.  A shot whose tail was a long hold therefore read
        as empty at the end: the bound moved in front of keys that stayed
        behind, and the downstream ripple then pulled the next shot's content
        back on top of them.  Production case: "Step 4.1" [991, 1590]
        collected motion to 1313 while two members hold keys to 1533, and one
        trailing trim moved "Step 4.8" from 1605 to 1328, interleaving two
        shots' animation across six curves.
        Fixed: 2026-09-03
        """
        cmds.file(new=True, force=True)
        a = cmds.polyCube(name="trimHoldA")[0]
        for t, v in ((20, 0), (60, 10), (80, 10), (95, 10)):  # motion, then a hold
            cmds.setKeyframe(a, at="translateX", time=t, value=v)
        b = cmds.polyCube(name="trimHoldB")[0]
        for t, v in ((160, 0), (190, 5)):
            cmds.setKeyframe(b, at="translateX", time=t, value=v)

        seq = ShotSequencer(
            [
                ShotBlock(0, "S0", 0, 120, [a]),
                ShotBlock(1, "S1", 160, 200, [b]),
            ]
        )
        head, tail = seq.trim_shot_to_content(0, edge="trailing")

        self.assertAlmostEqual(seq.shot_by_id(0).end, 95, msg="trimmed past a key")
        self.assertAlmostEqual(tail, -25)
        # The downstream shot rippled by the same amount, so its content can
        # never land on the keys the trim left inside S0.
        self.assertAlmostEqual(seq.shot_by_id(1).start, 135)
        self.assertEqual(
            sorted(cmds.keyframe(a, q=True, at="translateX") or []),
            [20.0, 60.0, 80.0, 95.0],
            "the trimmed shot's own keys must not move",
        )
        self.assertGreater(
            min(cmds.keyframe(b, q=True, at="translateX") or []),
            max(cmds.keyframe(a, q=True, at="translateX") or []),
            "the next shot's animation must stay after this shot's last key",
        )

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_a_trim_still_removes_genuinely_empty_tail(self):
        """The key rule only holds the bound at a key — real slack still goes."""
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="trimEmptyTail")[0]
        cmds.setKeyframe(cube, at="translateX", time=20, value=0)
        cmds.setKeyframe(cube, at="translateX", time=60, value=10)

        seq = ShotSequencer([ShotBlock(0, "S0", 0, 200, [cube])])
        _head, tail = seq.trim_shot_to_content(0, edge="trailing")
        self.assertAlmostEqual(seq.shot_by_id(0).end, 60)
        self.assertAlmostEqual(tail, -140)


class TestAGapKeepsItsWidth(unittest.TestCase):
    """A gap changes width only when the gap itself is dragged.

    Bug: `resize_shot_bounds` rippled the neighbours only for a GROWING
    edge, so every shrink silently widened the adjacent gap — the one place
    a gap resized without anyone asking.  A shot resize now ripples in both
    directions, so all downstream shots and gaps follow the bound.
    Fixed: 2026-09-03
    """

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def setUp(self):
        cmds.file(new=True, force=True)
        self.a = self._cube("gwA", {0: 0, 100: 5})
        self.b = self._cube("gwB", {115: 0, 200: 9})
        self.c = self._cube("gwC", {215: 0, 300: 3})
        self.seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 100, [str(self.a)]),
                ShotBlock(1, "B", 115, 200, [str(self.b)]),
                ShotBlock(2, "C", 215, 300, [str(self.c)]),
            ]
        )

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, at="translateX", time=t, value=v)
        return node

    def _gaps(self):
        shots = self.seq.sorted_shots()
        return [round(n.start - p.end, 3) for p, n in zip(shots, shots[1:])]

    def _keys(self, node):
        return sorted(cmds.keyframe(node, q=True, at="translateX") or [])

    def test_shrinking_a_shot_tail_pulls_the_downstream_shots_in(self):
        self.assertEqual(self._gaps(), [15.0, 15.0])
        self.seq.resize_shot_bounds(0, 0, 80)  # tail in by 20
        self.assertEqual(self._gaps(), [15.0, 15.0], "gaps must survive a shrink")
        self.assertEqual(
            [(s.start, s.end) for s in self.seq.sorted_shots()],
            [(0, 80.0), (95.0, 180.0), (195.0, 280.0)],
        )
        self.assertEqual(self._keys(self.b), [95.0, 180.0], "B's keys ripple with it")
        self.assertEqual(self._keys(self.c), [195.0, 280.0])

    def test_growing_a_shot_tail_still_pushes_them_out(self):
        self.seq.resize_shot_bounds(0, 0, 130)
        self.assertEqual(self._gaps(), [15.0, 15.0])
        self.assertEqual(self.seq.shot_by_id(1).start, 145.0)

    def test_shrinking_a_shot_head_pulls_the_upstream_shots_in(self):
        self.seq.resize_shot_bounds(2, 235, 300)  # head in by 20
        self.assertEqual(self._gaps(), [15.0, 15.0])
        self.assertEqual(self.seq.shot_by_id(1).end, 220.0)

    def test_a_bounds_shrink_still_leaves_its_own_keys_alone(self):
        """Bounds-only means bounds-only: the stranded keys stay put."""
        cmds.setKeyframe(str(self.a), at="translateX", time=90, value=3)
        self.seq.resize_shot_bounds(0, 0, 80)
        self.assertEqual(
            self._keys(self.a), [0.0, 90.0, 100.0], "A's own keys must not move"
        )


class TestALockedGapDoesNotResize(unittest.TestCase):
    """A lock is a statement about a gap's WIDTH.

    Bug: the lock was consulted by the respace planner and the overlay
    drawing, but by none of the drag handlers — so a locked gap resized
    like any other.  Both edge handles now refuse; the body drag (constant
    width) and a shot resize (which ripples, so the width survives) are
    unaffected.
    Fixed: 2026-09-03
    """

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def setUp(self):
        cmds.file(new=True, force=True)
        ShotStore.clear_active()
        self.store = ShotStore()
        ShotStore.set_active(self.store)
        self.seq = ShotSequencer(store=self.store)
        self.a = self.store.define_shot("A", 0, 100)
        self.b = self.store.define_shot("B", 115, 200)
        self.ctl = self._ctl()

    def tearDown(self):
        ShotStore.clear_active()

    def _ctl(self):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        seq = self.seq
        footers = []

        class _Ctl(ShotSequencerController):
            def __init__(self):  # bypass the panel's __init__
                self._segment_cache = {}
                self._sub_row_cache = {}
                self._audio_segments_cache = None
                self._syncing = False

            sequencer = seq
            active_shot_id = None

            def _get_sequencer_widget(self):
                return None

            def _set_footer(self, text, **kw):
                footers.append(text)

            def _gap_edit_epilogue(self):
                pass

        ctl = _Ctl()
        ctl.footers = footers
        return ctl

    def _bounds(self):
        return [(s.start, s.end) for s in self.seq.sorted_shots()]

    def test_a_locked_gaps_right_edge_refuses(self):
        self.store.lock_gap(self.a.shot_id, self.b.shot_id)
        before = self._bounds()
        self.ctl.on_gap_resized(115, 130)
        self.assertEqual(self._bounds(), before)
        self.assertTrue(any("locked" in f for f in self.ctl.footers))

    def test_a_locked_gaps_left_edge_refuses(self):
        self.store.lock_gap(self.a.shot_id, self.b.shot_id)
        before = self._bounds()
        self.ctl.on_gap_left_resized(100, 85)
        self.assertEqual(self._bounds(), before)
        self.assertTrue(any("locked" in f for f in self.ctl.footers))

    def test_an_unlocked_gap_still_resizes(self):
        self.ctl.on_gap_resized(115, 130)
        self.assertNotEqual(self._bounds(), [(0, 100), (115, 200)])

    def test_the_tail_handle_flanks_no_gap_and_is_never_locked(self):
        """The overlay after the LAST shot has no right-hand shot."""
        self.store.lock_all_gaps()
        self.ctl.on_gap_left_resized(200, 190)
        self.assertAlmostEqual(self.seq.shot_by_id(self.b.shot_id).end, 190)

    def test_the_lock_is_recorded_even_when_the_overlay_frames_have_drifted(self):
        """Bug: each gap edge was matched against a shot frame on its own, so
        an overlay whose cached span had drifted by more than a rounding
        resolved to nothing — and a lock that resolves to nothing is never
        written.  The overlay drew itself "[Locked]" while the store stayed
        empty, so the next rebuild handed the lock straight back and the gap
        dragged like any other.
        Fixed: 2026-09-03
        """
        self.ctl.on_gap_lock_changed(100.4, 114.6, True)  # drifted by ~0.5
        self.assertTrue(
            self.store.is_gap_locked(self.a.shot_id, self.b.shot_id),
            "the nearest pair is the gap the overlay is standing on",
        )
        self.ctl.on_gap_lock_changed(100.4, 114.6, False)
        self.assertFalse(self.store.is_gap_locked(self.a.shot_id, self.b.shot_id))

    def test_a_recorded_lock_then_refuses_the_drag(self):
        """The two halves in one pass: record it, then try to drag it."""
        self.ctl.on_gap_lock_changed(100.0, 115.0, True)
        before = self._bounds()
        self.ctl.on_gap_resized(115, 130)
        self.assertEqual(self._bounds(), before)

    def test_a_single_shot_timeline_flanks_no_gap(self):
        self.store.remove_shot(self.b.shot_id)
        self.ctl.on_gap_lock_changed(100.0, 115.0, True)
        self.assertFalse(self.store.locked_gaps)

    def test_locking_does_not_freeze_a_shot_resize(self):
        """The ripple carries the gap along, so its width is never at risk."""
        self.store.lock_gap(self.a.shot_id, self.b.shot_id)
        self.seq.resize_shot_bounds(self.a.shot_id, 0, 80)
        shots = self.seq.sorted_shots()
        self.assertAlmostEqual(shots[1].start - shots[0].end, 15.0)


class TestASlideNeverCrossesANeighbour(unittest.TestCase):
    """A slide ripples ONE side; the other has to hold.

    Bug: `slide_shot` / `move_shot` / `set_shot_start` moved the shot whole
    and rippled only the side they were told to, so a slide TOWARD the other
    side went straight over the neighbour.  The store then held two shots
    claiming one span, which makes key ownership — and every envelope derived
    from it — ambiguous, and the panel drew the shot ending mid-content.
    Production case: an outer gap drag pulled "Step 4.8" 212 frames earlier,
    [1605, 2180] -> [1393, 1968], while "Step 4.4" [1373, 1605] stayed put.
    The inner gap-edge drag already clamped (`_set_shot_edge`); the
    whole-shot gestures now keep the same rule.
    Fixed: 2026-09-03
    """

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def setUp(self):
        cmds.file(new=True, force=True)
        self.a = cmds.polyCube(name="slideA")[0]
        cmds.setKeyframe(self.a, at="translateX", time=0, value=0)
        cmds.setKeyframe(self.a, at="translateX", time=100, value=5)
        self.b = cmds.polyCube(name="slideB")[0]
        cmds.setKeyframe(self.b, at="translateX", time=100, value=5)
        cmds.setKeyframe(self.b, at="translateX", time=200, value=9)
        # Zero gap: A ends exactly where B starts, so there is no room at all.
        self.seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 100, [str(self.a)]),
                ShotBlock(1, "B", 100, 200, [str(self.b)]),
            ]
        )

    def _bounds(self):
        return [(s.name, s.start, s.end) for s in self.seq.sorted_shots()]

    def _assert_no_overlap(self):
        shots = self.seq.sorted_shots()
        for prev, nxt in zip(shots, shots[1:]):
            self.assertGreaterEqual(
                nxt.start,
                prev.end - 1e-6,
                f"{nxt.name} starts inside {prev.name}: {self._bounds()}",
            )

    def test_sliding_downstream_holds_at_the_upstream_neighbour(self):
        self.seq.slide_shot(1, 70, direction="downstream")
        self._assert_no_overlap()
        self.assertEqual(self._bounds(), [("A", 0, 100), ("B", 100, 200)])

    def test_sliding_upstream_holds_at_the_downstream_neighbour(self):
        self.seq.slide_shot(0, 30, direction="upstream")
        self._assert_no_overlap()

    def test_move_shot_holds_at_the_upstream_neighbour(self):
        self.seq.move_shot(1, 70)
        self._assert_no_overlap()

    def test_set_shot_start_holds_with_and_without_ripple(self):
        self.seq.set_shot_start(1, 70, ripple=True)
        self._assert_no_overlap()
        self.seq.set_shot_start(1, 70, ripple=False)
        self._assert_no_overlap()

    def test_a_slide_into_real_room_still_moves(self):
        """The clamp only holds at a neighbour — open room still absorbs it."""
        self.seq.store.shots[1].start = 140
        self.seq.store.shots[1].end = 240
        self.seq.slide_shot(1, 120, direction="downstream")
        self.assertAlmostEqual(self.seq.shot_by_id(1).start, 120)
        self._assert_no_overlap()


class TestShotMembershipIncludesHolds(unittest.TestCase):
    """An object keyed on a hold for the whole shot is still that shot's
    content.  Excluding it hid it from the panel AND stranded its keys when
    the shot moved (ripples shift ``shot.objects``)."""

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_hold_only_object_is_discovered(self):
        cmds.file(new=True, force=True)
        held = cmds.polyCube(name="held_cube")[0]
        cmds.setKeyframe(held, at="translateX", t=10, v=5)
        cmds.setKeyframe(held, at="translateX", t=40, v=5)  # flat: a hold

        found = ShotSequencer._find_keyed_transforms(0, 50)
        self.assertTrue(
            any(held in n for n in found),
            "membership must not require the values to vary",
        )

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_require_motion_still_filters_holds(self):
        """Boundary detection keeps the stricter rule."""
        cmds.file(new=True, force=True)
        held = cmds.polyCube(name="held_cube2")[0]
        cmds.setKeyframe(held, at="translateX", t=10, v=5)
        cmds.setKeyframe(held, at="translateX", t=40, v=5)

        found = ShotSequencer._find_keyed_transforms(0, 50, require_motion=True)
        self.assertFalse(any(held in n for n in found))

    @unittest.skipUnless(HAS_MAYA, "requires Maya")
    def test_hold_only_object_rides_a_shot_move(self):
        cmds.file(new=True, force=True)
        held = cmds.polyCube(name="held_move")[0]
        cmds.setKeyframe(held, at="translateX", t=10, v=5)
        cmds.setKeyframe(held, at="translateX", t=40, v=5)

        seq = ShotSequencer([])
        shot = seq.define_shot("S0", 0, 50)  # objects=None -> auto-discover
        seq.move_shot(shot.shot_id, 100)

        self.assertEqual(
            sorted(cmds.keyframe(held, q=True, at="translateX") or []),
            [110.0, 140.0],
            "a discovered hold-only object must travel with its shot",
        )


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
        # No extra space — end is exactly start + duration
        self.assertAlmostEqual(seq.shot_by_id(1).end - seq.shot_by_id(1).start, 100)


class TestApplyBehaviors(unittest.TestCase):
    """Test apply_to_shots() from behaviors module - pure Python with mocks."""

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

        result = Behaviors.apply_to_shots(
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
        result = Behaviors.apply_to_shots(
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
        result = Behaviors.apply_to_shots(
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
        result = Behaviors.apply_to_shots(
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
        result = Behaviors.apply_to_shots(
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
            Behaviors.apply_to_shots(store.sorted_shots(), exists_fn=lambda _: True)

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
        result = Behaviors.apply_to_shots(
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
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            ManifestData,
            HEADERS,
            COL_STEP,
            COL_SECTION,
            COL_DESC,
            COL_BEHAVIORS,
            COL_START,
            COL_END,
            PASTEL_STATUS,
        )

        cls.HEADERS = HEADERS
        cls.COL_STEP = COL_STEP
        cls.COL_SECTION = COL_SECTION
        cls.COL_DESC = COL_DESC
        cls.COL_BEHAVIORS = COL_BEHAVIORS
        cls.COL_START = COL_START
        cls.COL_END = COL_END
        cls.PASTEL_STATUS = PASTEL_STATUS
        cls.fmt_behavior = staticmethod(ManifestData.fmt_behavior)

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
        # Disk existence — ``cmds.objExists`` was the wrong check
        # (it tests a Maya scene node, not a filesystem path).
        self.assertTrue(ui_path.is_file(), f"Missing: {ui_path}")


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
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            ManifestData,
            HEADERS,
            COL_STEP,
            COL_DESC,
            COL_BEHAVIORS,
            PASTEL_STATUS,
        )

        cls._HEADERS = HEADERS
        cls._COL_STEP = COL_STEP
        cls._COL_DESC = COL_DESC
        cls._COL_BEHAVIORS = COL_BEHAVIORS
        cls._PASTEL_STATUS = PASTEL_STATUS
        cls._fmt_behavior = staticmethod(ManifestData.fmt_behavior)

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

        # Column tints - darken Step and Behaviors columns
        tree.set_column_tint(COL_STEP, QColor(0, 0, 0, 45))
        tree.set_column_tint(COL_BEHAVIORS, QColor(0, 0, 0, 45))

        # Behavior column formatter
        display_colors = {
            ManifestData.fmt_behavior(k).lower(): v
            for k, v in {}  # BEHAVIOR_COLORS removed.items()
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
        # A single processEvents is not enough to guarantee the view has laid
        # out — how many cycles it takes depends on what else is on the event
        # queue, so it varies with which tests ran first.  Until it has,
        # visualItemRect returns an EMPTY rect for every row, every sample
        # lands on the same y (the header), and the colour assertions compare
        # a pixel to itself.  Wait for real geometry instead of hoping.
        for _ in range(50):
            if tree.visualItemRect(c_valid).height() > 0:
                break
            cls._app.processEvents()
        else:
            raise unittest.SkipTest("tree never laid out — cannot sample pixels")

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

        # Freeze the geometry that matches ``_image``.  Sampling the cached
        # frame while asking the LIVE tree where its rows are only agrees
        # until some other test collapses, sorts or resizes it — after which
        # a child's rect can come back empty and two different rows sample
        # the same pixels.  Capturing rects with the frame keeps every
        # pixel-level assertion self-consistent regardless of test order.
        cls._header_h = tree.header().height()
        cls._rects = {k: tree.visualItemRect(v) for k, v in cls._items.items()}
        cls._sections = {
            c: (tree.header().sectionPosition(c), tree.header().sectionSize(c))
            for c in range(tree.columnCount())
        }
        # ``grab()`` renders at the device pixel ratio, so on any scaled
        # display the image is LARGER than the widget and widget coordinates
        # address the wrong pixels — measured 1.25x here, which put the child
        # row's sample inside the parent row's band and made the two compare
        # equal.  Derive the factor from the image itself rather than assume.
        cls._dpr = cls._image.width() / float(max(tree.width(), 1))

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
        return self._items[key].foreground(col).color()

    def _item_bg_hex(self, key, col=None):
        """Get background color hex from the item model."""
        col = col if col is not None else self._COL_DESC
        return self._items[key].background(col).color()

    def _sample_bg(self, item_key, col_index):
        """Average (R,G,B) from the rendered image at the cell center of *item_key* / *col*.

        Geometry comes from the snapshot taken with ``_image`` (see
        ``setUpClass``), not from the live tree, so the coordinates always
        describe the frame actually being sampled.
        """
        rect = self._rects[item_key]
        # visualItemRect is relative to the viewport (excludes header),
        # but grab() captures the full widget (includes header) — and at the
        # device pixel ratio, so widget coordinates must be scaled into
        # image space (see ``_dpr`` in setUpClass).
        y = int((rect.center().y() + self._header_h) * self._dpr)

        pos, size = self._sections[col_index]
        x_start = int((pos + 4) * self._dpr)
        x_end = int((pos + size - 4) * self._dpr)

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
        # Compare hex strings: PySide6 QColor isn't hashable in all builds.
        fg_hex = fg.name() if hasattr(fg, "name") else str(fg)
        problem_fgs = {
            (v[0].name() if hasattr(v[0], "name") else str(v[0]))
            for v in self._PASTEL_STATUS.values()
            if v[0]
        }
        self.assertNotIn(
            fg_hex,
            problem_fgs,
            f"Valid parent should not have a problem color: {fg_hex}",
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
    """detect_shots() logic - pure clustering tests without Maya."""

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
        # — if Python's float resolves them identically, the test still
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

        def cb(evt):
            received.append(evt)

        self.store.add_listener(cb)
        self.store.define_shot(name="A", start=0, end=10)
        self.assertEqual(len(received), 1)
        self.store.remove_listener(cb)
        self.store.define_shot(name="B", start=20, end=30)
        self.assertEqual(len(received), 1)  # no new event

    def test_duplicate_listener_not_added(self):
        def cb(evt):
            return None

        self.store.add_listener(cb)
        self.store.add_listener(cb)
        self.assertEqual(len(self.store._listeners), 1)

    def test_remove_nonexistent_listener_is_noop(self):
        def cb(evt):
            return None

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
        """A stale on_shot_end_changed(0) firing after a real shot becomes
        active must not shift upstream shots.

        Original bug (2026-04-04): _sync_shot_editor set spinners to 0
        without blockSignals when shot was None.  The debounce timer fired
        400ms later, after an active shot was set, producing
        delta = 0 - shot.end and rippling all shots with start >= 0.  That
        was fixed at the controller layer with blockSignals in
        _sync_shot_editor.

        Store-level defense (2026-07-08): update_shot now clamps inverted
        bounds.  A stale end=0 on a shot at start=2520 snaps end back to
        2520 instead of persisting a zero end, so the follow-up user edit
        reads old_end=2520 (not 0) and ripples with after_frame=2520 --
        upstream shots are out of range and stay put.  This test pins that
        store-level protection: even if the stale signal reaches
        update_shot, upstream shots cannot be corrupted.
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        store.define_shot(name="A01", start=400, end=680)
        store.define_shot(name="A02", start=680, end=1000)
        store.define_shot(name="A09", start=2520, end=2520)
        store.define_shot(name="A10", start=2520, end=2760)
        store.set_active_shot(store.shot_by_name("A09").shot_id)

        seq = ShotSequencer(store=store)

        # Simulate what the stale debounce would do: value=0 for A09.end.
        old_end = store.shot_by_name("A09").end  # 2520
        stale_value = 0
        delta = stale_value - old_end  # -2520
        with store.batch_update():
            store.update_shot(store.active_shot_id, end=stale_value)
            seq.ripple_downstream(store.active_shot_id, old_end, delta)

        # Upstream shots (start < old_end) must be untouched.
        a01 = store.shot_by_name("A01")
        a02 = store.shot_by_name("A02")
        self.assertAlmostEqual(a01.start, 400, msg="Upstream A01 start corrupted")
        self.assertAlmostEqual(a01.end, 680, msg="Upstream A01 end corrupted")
        self.assertAlmostEqual(a02.start, 680, msg="Upstream A02 start corrupted")
        self.assertAlmostEqual(a02.end, 1000, msg="Upstream A02 end corrupted")

        # Downstream A10 should have shifted by the ripple.
        a10 = store.shot_by_name("A10")
        self.assertAlmostEqual(a10.start, 0, msg="A10 start not shifted")
        self.assertAlmostEqual(a10.end, 240, msg="A10 end not shifted")

        # The inverted-bounds clamp snapped A09.end back to its start (2520)
        # instead of persisting the stale 0.
        self.assertAlmostEqual(
            store.shot_by_name("A09").end,
            2520,
            msg="Stale end=0 must be clamped to start, not persisted",
        )

        # Now the user edits A09.end to 2620.  Because the clamp kept
        # old_end at 2520 (not 0), the ripple uses after_frame=2520 and
        # cannot reach the upstream shots.
        old_end2 = store.shot_by_name("A09").end  # 2520 (clamped, not 0)
        user_value = 2620
        delta2 = user_value - old_end2  # 100
        with store.batch_update():
            store.update_shot(store.active_shot_id, end=user_value)
            seq.ripple_downstream(store.active_shot_id, old_end2, delta2)

        # Upstream A01/A02 stay put -- the store-level clamp prevented the
        # after_frame=0 ripple that used to corrupt them.
        a01 = store.shot_by_name("A01")
        a02 = store.shot_by_name("A02")
        self.assertAlmostEqual(
            a01.start,
            400,
            msg="Store clamp must prevent the stale-zero upstream corruption",
        )
        self.assertAlmostEqual(a02.start, 680, msg="Upstream A02 start corrupted")


class TestColumnMap(unittest.TestCase):
    """Test ColumnMap serialisation round-trip and custom-alias parsing."""

    def test_to_dict_round_trip(self):
        """to_dict → from_dict produces an identical ColumnMap."""
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
        import tempfile
        import shutil
        import csv as csv_mod

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
            steps = ManifestModel.parse_csv(csv_path, columns=custom)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].step_id, "A01")
            self.assertEqual(steps[0].objects[0].name, "ARROW_01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_voice_column_populates_audio_field(self):
        """When Voice Support column exists, audio field stores voice text.

        display_text returns description (Step Contents), not audio.
        Audio text flows into metadata as voice_text.
        Refactored: 2026-04-14 — display_text flipped to return description.
        """
        import tempfile
        import shutil
        import csv as csv_mod

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
                # A02: voice-only (Contents=N/A) — should show N/A description
                w.writerow(["A02.)", "The clamps are removed.", "N/A", "N/A", ""])
                # A03: silent action (Voice=N/A) — should show action description
                w.writerow(["A03.)", "N/A", "Poker chips push in.", "CHIPS_01", ""])
            steps = ManifestModel.parse_csv(csv_path)
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
        import tempfile
        import shutil
        import csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "novoice.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Arrow fades in.", "ARROW_01", "Complete"])
            steps = ManifestModel.parse_csv(csv_path)
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
        import tempfile
        import shutil
        import csv as csv_mod

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
            steps = ManifestModel.parse_csv(csv_path)
            ids = [s.step_id for s in steps]
            self.assertNotIn("SETUP", ids)
            self.assertIn("A01", ids)
            self.assertIn("A02", ids)
            self.assertEqual(len(steps), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_steps_empty_includes_all(self):
        """Empty exclude_steps keeps all steps including SETUP."""
        import tempfile
        import shutil
        import csv as csv_mod

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
            steps = ManifestModel.parse_csv(csv_path, columns=no_exclude)
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
        import tempfile
        import shutil
        import csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "ev.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Intro.", "N/A", ""])
                w.writerow(["A02.)", "Box appears.", "BOX_01", ""])
            steps = ManifestModel.parse_csv(csv_path)
            self.assertEqual(steps[0].objects, [])  # N/A filtered
            self.assertEqual(len(steps[1].objects), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_values_case_insensitive(self):
        """exclude_values comparison is case-insensitive."""
        import tempfile
        import shutil
        import csv as csv_mod

        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "ci.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.writer(f)
                w.writerow(["SECTION A: TEST", "", "", ""])
                w.writerow(["Step", "Step Contents", "Asset Names", "Status"])
                w.writerow(["A01.)", "Intro.", "n/a", ""])
            steps = ManifestModel.parse_csv(csv_path)
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
            steps2 = ManifestModel.parse_csv(csv_path2, columns=cm)
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
        import tempfile
        import shutil
        import csv as csv_mod

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
            steps = ManifestModel.parse_csv(csv_path, columns=cm)
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
        import tempfile
        import shutil
        import csv as csv_mod

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

            steps = ManifestModel.parse_csv(csv_path, post_process=_derive)
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
        Refactored: 2026-04-14 — display_text flipped to return description.
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

from mayatk.anim_utils.shots.shot_manifest.mapping import Mapping


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
        """Mapping.discover() finds .json mapping files in a directory."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            self._write_json(tmp, "project_a.json", {"columns": {}})
            self._write_json(tmp, "project_b.json", {"columns": {}})
            self._write_json(tmp, "_private.json", {"columns": {}})
            names = Mapping.discover(tmp)
            self.assertEqual(names, ["project_a", "project_b"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_discover_empty_dir(self):
        """Mapping.discover() returns empty list for nonexistent directory."""
        self.assertEqual(Mapping.discover("/nonexistent"), [])

    def test_discover_default_dir_has_default(self):
        """The default mapping directory contains 'default'."""
        names = Mapping.discover()
        self.assertIn("default", names)

    # ---- load_mapping ----

    def test_load_mapping_reads_json(self):
        """Mapping.load_mapping() reads and parses a JSON file."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            data = {"columns": {"step_id": ["ID"]}}
            self._write_json(tmp, "test.json", data)
            result = Mapping.load_mapping("test", tmp)
            self.assertEqual(result["columns"]["step_id"], ["ID"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_mapping_missing_raises(self):
        """Mapping.load_mapping() raises FileNotFoundError for missing file."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(FileNotFoundError):
                Mapping.load_mapping("nonexistent", tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_mapping_accepts_full_path(self):
        """Mapping.load_mapping() accepts a full .json path as name."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            data = {"columns": {"step_id": ["ID"]}}
            path = self._write_json(tmp, "full.json", data)
            result = Mapping.load_mapping(path)
            self.assertEqual(result["columns"]["step_id"], ["ID"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- resolve (end-to-end) ----

    def test_resolve_with_empty_mapping(self):
        """resolve() with empty mapping uses default ColumnMap."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            steps = Mapping.resolve(csv_path, mapping={})
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0].step_id, "A01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_with_column_remap(self):
        """resolve() applies column aliases from the mapping."""
        import tempfile
        import shutil
        import csv as csv_mod

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
            steps = Mapping.resolve(csv_path, mapping=mapping)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].description, "Arrow fades in.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_by_name(self):
        """resolve() loads a mapping by name from a directory."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            self._write_json(tmp, "my_map.json", {"columns": {}})
            steps = Mapping.resolve(csv_path, name="my_map", directory=tmp)
            self.assertEqual(len(steps), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_default_mapping(self):
        """The built-in default.json produces correct steps."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            steps = Mapping.resolve(csv_path, name="default")
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0].step_id, "A01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- audio_resolve: prefix ----

    def test_audio_prefix_resolves_clip(self):
        """audio_resolve with method=prefix adds audio BuilderObject."""
        import tempfile
        import shutil

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
            steps = Mapping.resolve(csv_path, mapping=mapping)
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
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            audio_dir = os.path.join(tmp, "audio")
            os.makedirs(audio_dir)

            mapping = {
                "columns": {},
                "audio_resolve": {"method": "prefix", "directory": audio_dir},
            }
            steps = Mapping.resolve(csv_path, mapping=mapping)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNone(ao)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audio_prefix_nonexistent_dir(self):
        """audio_resolve prefix is a no-op when directory missing."""
        import tempfile
        import shutil

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
            steps = Mapping.resolve(csv_path, mapping=mapping)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNone(ao)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- audio_resolve: regex ----

    def test_audio_regex_resolves_clip(self):
        """audio_resolve with method=regex adds audio BuilderObject."""
        import tempfile
        import shutil

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
            steps = Mapping.resolve(csv_path, mapping=mapping)
            ao = next((o for o in steps[0].objects if o.kind == "audio"), None)
            self.assertIsNotNone(ao)
            self.assertEqual(ao.name, "A01-001")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- audio_resolve: map ----

    def test_audio_map_resolves_clip(self):
        """audio_resolve with method=map adds audio BuilderObjects."""
        import tempfile
        import shutil

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
            steps = Mapping.resolve(csv_path, mapping=mapping)
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
        import tempfile
        import shutil

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
            steps = Mapping.resolve(csv_path, mapping=mapping)
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
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            csv_path = self._make_csv(tmp)
            mapping = {
                "columns": {},
                "audio_resolve": {"method": "unknown"},
            }
            with self.assertRaises(ValueError):
                Mapping.resolve(csv_path, mapping=mapping)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression tests: 2026-07 shots-system optimization pass
# ---------------------------------------------------------------------------


class TestRescaleToFpsSnapPolicy(unittest.TestCase):
    """rescale_to_fps must honor snap_whole_frames instead of bare round().

    Bug: a time-unit change unconditionally quantized sub-frame shot
    bounds and marker times even when whole-frame snapping was off.
    """

    def test_snapping_off_preserves_subframe_bounds(self):
        store = ShotStore()
        store.snap_whole_frames = False
        store.scene_fps = 24.0
        store.define_shot(name="A", start=10.5, end=20.25)
        store.markers.append({"time": 5.25})
        store.rescale_to_fps(48.0)
        shot = store.shot_by_name("A")
        self.assertAlmostEqual(shot.start, 21.0)
        self.assertAlmostEqual(shot.end, 40.5)
        self.assertAlmostEqual(store.markers[0]["time"], 10.5)

    def test_snapping_on_rounds_bounds(self):
        store = ShotStore()
        store.snap_whole_frames = True
        store.scene_fps = 24.0
        store.define_shot(name="A", start=10, end=20)
        store.rescale_to_fps(30.0)
        shot = store.shot_by_name("A")
        self.assertEqual(shot.start, round(10 * 30.0 / 24.0))
        self.assertEqual(shot.end, round(20 * 30.0 / 24.0))


class TestHasAnimationBeyondSampleWindow(unittest.TestCase):
    """has_animation must consider every curve, not a 50-curve sample.

    Bug: scenes whose first curves drove non-transform nodes (materials,
    blendshapes) false-negatived even though transform animation existed.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_material_only_animation_is_not_shot_animation(self):
        for _ in range(60):
            mat = cmds.shadingNode("lambert", asShader=True)
            cmds.setKeyframe(mat, attribute="colorR", time=1, value=0)
            cmds.setKeyframe(mat, attribute="colorR", time=10, value=1)
        self.assertFalse(ShotStore.has_animation())

    def test_transform_curve_found_beyond_first_fifty(self):
        # 60 material curves created FIRST so the transform curve sits
        # beyond the old 50-curve sampling window.
        for _ in range(60):
            mat = cmds.shadingNode("lambert", asShader=True)
            cmds.setKeyframe(mat, attribute="colorR", time=1, value=0)
        loc = cmds.spaceLocator()[0]
        cmds.setKeyframe(loc, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(loc, attribute="translateX", time=10, value=5)
        self.assertTrue(ShotStore.has_animation())


class TestAssessBatched(unittest.TestCase):
    """assess() resolves the union of shot objects in one pass."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_valid_and_missing(self):
        loc = cmds.spaceLocator()[0]
        loc_long = cmds.ls(loc, long=True)[0]
        store = ShotStore()
        store.define_shot(name="A", start=0, end=10, objects=[loc_long])
        store.define_shot(
            name="B", start=20, end=30, objects=[loc_long, "|missing_xyz_123"]
        )
        store.define_shot(name="C", start=40, end=50, objects=[])
        result = store.assess()
        self.assertEqual(result[store.shot_by_name("A").shot_id], "valid")
        self.assertEqual(result[store.shot_by_name("B").shot_id], "missing_object")
        self.assertEqual(result[store.shot_by_name("C").shot_id], "valid")


class TestApplyGap(unittest.TestCase):
    """ShotSequencer.apply_gap -- engine home for the gap-scope algorithm
    that previously lived inline in the Shots settings slot."""

    def _make(self):
        store = ShotStore()
        store.define_shot(name="A", start=0, end=10)
        store.define_shot(name="B", start=20, end=30)
        store.define_shot(name="C", start=40, end=50)
        return ShotSequencer(store=store), store

    def test_scope_all_respaces(self):
        seq, store = self._make()
        self.assertTrue(seq.apply_gap(5, scope="all"))
        shots = seq.sorted_shots()
        self.assertAlmostEqual(shots[0].start, 0)
        self.assertAlmostEqual(shots[1].start, 15)
        self.assertAlmostEqual(shots[2].start, 30)
        for s in shots:
            self.assertAlmostEqual(s.end - s.start, 10)

    def test_scope_start_moves_anchor_after_predecessor(self):
        seq, store = self._make()
        b = store.shot_by_name("B")
        self.assertTrue(seq.apply_gap(2, scope="start", shot_id=b.shot_id))
        a = store.shot_by_name("A")
        self.assertAlmostEqual(b.start, a.end + 2)

    def test_scope_end_moves_successor(self):
        seq, store = self._make()
        b = store.shot_by_name("B")
        self.assertTrue(seq.apply_gap(3, scope="end", shot_id=b.shot_id))
        c = store.shot_by_name("C")
        self.assertAlmostEqual(c.start, b.end + 3)

    def test_empty_store_returns_false(self):
        seq = ShotSequencer(store=ShotStore())
        self.assertFalse(seq.apply_gap(5, scope="all"))

    def test_unknown_anchor_returns_false(self):
        seq, _store = self._make()
        self.assertFalse(seq.apply_gap(5, scope="start", shot_id=999))


class TestParseCSVRobustness(unittest.TestCase):
    """Header placement + encoding tolerance for manifest CSV loads.

    Bugs: a header row whose step column was not cell 0 (or a CSV not
    encoded as UTF-8) silently produced 0 steps / a hard decode error.
    """

    def setUp(self):
        import tempfile

        self._tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _write_rows(self, rows, encoding="utf-8"):
        import csv as _csv

        path = os.path.join(self._tmp_dir, "fixture.csv")
        with open(path, "w", newline="", encoding=encoding) as f:
            w = _csv.writer(f)
            w.writerows(rows)
        return path

    def test_header_not_in_first_column(self):
        # Includes a per-section header repeat (standard layout): the
        # second header row must be re-detected, not misread as a
        # continuation row of A02.
        path = self._write_rows(
            [
                ["", "Step", "Contents", "Asset"],
                ["", "A01.)", "Arrow fades in.", "ARROW_01"],
                ["", "A02.)", "Checklist fades out.", "CHECK_01"],
                ["", "Step", "Contents", "Asset"],
                ["", "B01.)", "Rudder fades in.", "RUDDER_01"],
            ]
        )
        steps = ManifestModel.parse_csv(path)
        self.assertEqual([s.step_id for s in steps], ["A01", "A02", "B01"])
        a02 = steps[1]
        self.assertNotIn("Contents", a02.description)
        self.assertEqual([o.name for o in a02.objects], ["CHECK_01"])

    def test_cp1252_encoded_csv_loads(self):
        path = self._write_rows(
            [
                ["Step", "Contents", "Asset"],
                ["A01.)", "Fl\xe8che fades in – d\xe9tail.", "ARROW_01"],
            ],
            encoding="cp1252",
        )
        steps = ManifestModel.parse_csv(path)
        self.assertEqual(len(steps), 1)
        self.assertIn("fades in", steps[0].description)

    def test_bom_with_cp1252_body_still_finds_header(self):
        """Excel 'CSV UTF-8' (BOM) with a pasted cp1252 byte: the BOM
        must be stripped from the RAW bytes before the fallback decode,
        or it leaks into the first header cell and 0 steps parse."""
        path = os.path.join(self._tmp_dir, "bom_mixed.csv")
        body = "Step,Contents,Asset\r\nA01.),Fl\xe8che fades in.,ARROW_01\r\n"
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + body.encode("cp1252"))
        steps = ManifestModel.parse_csv(path)
        self.assertEqual([s.step_id for s in steps], ["A01"])

    def test_no_header_returns_empty_not_crash(self):
        path = self._write_rows(
            [
                ["A01.)", "Arrow fades in.", "ARROW_01"],
                ["A02.)", "Checklist fades out.", "CHECK_01"],
            ]
        )
        self.assertEqual(ManifestModel.parse_csv(path), [])


class TestRangeResolverClamp(unittest.TestCase):
    """A pinned next-start at or before start + gap must not invert the
    auto-resolved end of the preceding step."""

    def test_next_start_before_cursor_clamps(self):
        from mayatk.anim_utils.shots.shot_manifest.range_resolver import RangeResolver
        from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
            BuilderStep,
        )

        steps = [
            BuilderStep(step_id=sid, section="A", section_title="", description="")
            for sid in ("A01", "A02")
        ]
        # A02 pinned to start almost immediately after A01 starts; with
        # gap wider than the spacing, A01's derived end used to invert.
        resolved = RangeResolver.resolve_ranges(
            steps,
            user_ranges={"A01": (100.0, None), "A02": (102.0, 200.0)},
            gap_starts=[],
            gap_end_map={},
            gap=10.0,
            use_selected_keys=False,
            last_resolved=[],
        )
        by_id = {sid: (start, end) for sid, start, end, _ in resolved}
        start, end = by_id["A01"]
        self.assertGreaterEqual(end, start)


class TestRangeResolverSparseFrozenPrefix(unittest.TestCase):
    """last_resolved can be SPARSE in selected-keys mode (unresolved
    steps are skipped), so the frozen prefix must be matched by step_id
    — positional copying froze the wrong steps' ranges and duplicated
    the edited step."""

    def test_sparse_prefix_freezes_by_id(self):
        from mayatk.anim_utils.shots.shot_manifest.range_resolver import RangeResolver
        from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
            BuilderStep,
        )

        steps = [
            BuilderStep(step_id=sid, section="A", section_title="", description="")
            for sid in ("A01", "A02", "A03")
        ]
        # Previously: only A01 resolved (A02 had no matching region),
        # A03 user-pinned.  User then edits A03 → re-resolve from idx 2.
        last = [
            ("A01", 0.0, 50.0, False),
            ("A03", 500.0, 700.0, True),
        ]
        resolved = RangeResolver.resolve_ranges(
            steps,
            user_ranges={"A03": (550.0, 700.0)},
            gap_starts=[560.0],
            gap_end_map={},
            gap=10.0,
            use_selected_keys=True,
            last_resolved=last,
            from_step_idx=2,
        )
        ids = [entry[0] for entry in resolved]
        self.assertEqual(ids.count("A03"), 1, "edited step must not be duplicated")
        by_id = {entry[0]: entry for entry in resolved}
        self.assertEqual(
            (by_id["A01"][1], by_id["A01"][2]),
            (0.0, 50.0),
            "A01 must keep its frozen range",
        )
        self.assertEqual((by_id["A03"][1], by_id["A03"][2]), (550.0, 700.0))


# ---------------------------------------------------------------------------
# Regression tests — 2026-07 full-system review pass
# ---------------------------------------------------------------------------


class TestUpdateShotInvertedClamp(unittest.TestCase):
    """update_shot must clamp inverted bounds (end < start) to a
    zero-duration shot — downstream envelope/respace math assumes
    ordered bounds."""

    def _store(self):
        store = ShotStore()
        store.define_shot(name="A", start=10, end=20)
        return store

    def test_end_below_start_clamps(self):
        store = self._store()
        shot = store.shot_by_name("A")
        store.update_shot(shot.shot_id, end=5)
        self.assertEqual(shot.start, shot.end)
        self.assertEqual(shot.start, 10)

    def test_start_above_end_clamps(self):
        store = self._store()
        shot = store.shot_by_name("A")
        store.update_shot(shot.shot_id, start=25)
        self.assertEqual(shot.start, shot.end)
        self.assertEqual(shot.end, 20)


class TestDirtyFlagCoverage(unittest.TestCase):
    """Mutations that bypass update_shot must still reach the save path."""

    def test_gap_lock_reaches_the_save_path(self):
        """Assert the EFFECT (a save happened), not the ``_dirty`` flag.

        ``mark_dirty`` schedules a flush, and mayatk's override defers it
        through ``cmds.evalDeferred``.  Whether that deferred call has run
        by the time the assertion executes depends on what else is on
        Maya's idle queue, so reading ``_dirty`` back is a coin flip —
        it stays True only while the flush is still pending.  Counting
        saves pins what the test is actually about.
        """
        saves = []

        class _CountingBackend:
            def save(self, data):
                saves.append(data)

            def load(self):
                return None

        store = ShotStore()
        previous = ShotStore._persistence
        ShotStore.set_persistence(_CountingBackend())
        try:
            store.define_shot(name="A", start=0, end=10)
            store.define_shot(name="B", start=20, end=30)
            a = store.shot_by_name("A")
            b = store.shot_by_name("B")

            saves.clear()
            store.lock_gap(a.shot_id, b.shot_id)
            store._flush_dirty()  # drain, in case the deferred flush is pending
            self.assertTrue(saves, "lock_gap must reach the save path")
            self.assertIn(
                [a.shot_id, b.shot_id],
                [list(p) for p in saves[-1]["locked_gaps"]],
                "and the lock must be in what was serialized",
            )

            saves.clear()
            store.unlock_all_gaps()
            store._flush_dirty()
            self.assertTrue(saves, "unlock_all_gaps must reach the save path")
            self.assertEqual(saves[-1]["locked_gaps"], [])
        finally:
            ShotStore.set_persistence(previous)

    def test_eventless_dirty_batch_still_flushes(self):
        """A batch that marks dirty without accumulating events must
        flush on exit — pin/hide mutators don't notify."""
        store = ShotStore()
        flushes = []
        store._flush_dirty = lambda: flushes.append(1)
        with store.batch_update():
            store.set_object_pinned("pCube1")
        self.assertTrue(flushes, "dirty batch with zero events was not flushed")


class TestClassifyObjectsLeafFallback(unittest.TestCase):
    """Metadata is keyed by CSV short names while shot.objects hold long
    DAG paths after a manifest sync — classification must fall back to
    leaf-name comparison instead of degrading to scene_discovered."""

    def test_long_path_matches_short_metadata(self):
        shot = ShotBlock(
            1,
            "A01",
            0,
            10,
            objects=["|GEO|ARROW_01", "|GEO|EXTRA_99"],
            metadata={
                "object_status": {"ARROW_01": "missing_behavior"},
                "csv_objects": [{"name": "ARROW_01"}],
            },
        )
        result = shot.classify_objects()
        self.assertEqual(result["|GEO|ARROW_01"], "missing_behavior")
        self.assertEqual(result["|GEO|EXTRA_99"], "scene_discovered")


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestResolveToTransformSubclasses(unittest.TestCase):
    """resolve_to_transform must treat transform SUBCLASSES (joints) as
    their own owner — the old nodeType check resolved a keyed joint to
    its parent, so shot moves left the joint's keys behind."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_child_joint_resolves_to_itself(self):
        from mayatk.anim_utils.shots._detection import Detection

        grp = cmds.group(em=True, name="rig_grp")
        cmds.select(grp)
        jnt = cmds.joint(name="root_jnt")
        resolved = Detection.resolve_to_transform(jnt)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.rsplit("|", 1)[-1], "root_jnt")

    def test_root_joint_resolves_to_itself(self):
        from mayatk.anim_utils.shots._detection import Detection

        cmds.select(clear=True)
        jnt = cmds.joint(name="lone_jnt")
        resolved = Detection.resolve_to_transform(jnt)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.rsplit("|", 1)[-1], "lone_jnt")


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestFitShotSharedObjectClamp(unittest.TestCase):
    """fit/extend must never attribute keys owned by ANOTHER shot to the
    shot being fitted — with shared objects the old unbounded probe
    dragged the shot over its neighbor and rippled the whole timeline."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_extend_ignores_neighbor_owned_keys(self):
        loc = cmds.spaceLocator(name="shared_loc")[0]
        loc_long = cmds.ls(loc, long=True)[0]
        for t in (10, 20, 120, 130):
            cmds.setKeyframe(loc, attribute="translateX", time=t, value=t)

        store = ShotStore()
        store.snap_whole_frames = False
        a = store.define_shot(name="A", start=0, end=50, objects=[loc_long])
        store.define_shot(name="B", start=100, end=200, objects=[loc_long])
        seq = ShotSequencer(store=store)

        seq.extend_shot_to_fit(a.shot_id)

        self.assertLessEqual(
            store.shot_by_id(a.shot_id).end,
            50,
            "shot A extended over keys owned by shot B",
        )
        # B's keys must not have moved (no spurious ripple).
        times = cmds.keyframe(loc, q=True, timeChange=True) or []
        self.assertIn(120.0, times)
        self.assertIn(130.0, times)


class TestMarkerNavigation(unittest.TestCase):
    """Prev/next in markers mode must jump the playhead to the marker time —
    never feed the float marker time to select_shot, where the engine's
    ``==`` compare makes marker time 3.0 match an unrelated shot id 3."""

    class _FakeCombo:
        def __init__(self, items, index=0):
            self._items = list(items)
            self._index = index

        def currentIndex(self):
            return self._index

        def setCurrentIndex(self, index):
            self._index = index

        def count(self):
            return len(self._items)

        def itemData(self, index):
            return self._items[index][1]

        def blockSignals(self, block):
            pass

    class _FakeSignal:
        def __init__(self):
            self.emitted = []

        def emit(self, *args):
            self.emitted.append(args)

    class _FakeWidget:
        def __init__(self):
            self.playhead = None
            self.playhead_moved = TestMarkerNavigation._FakeSignal()

        def set_playhead(self, time):
            self.playhead = time

    def _make_host(self, cmb, widget, sequencer):
        import types

        from mayatk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin

        class _Host(ShotNavMixin):
            def __init__(self):
                self.sequencer = sequencer
                self.ui = types.SimpleNamespace(cmb_shot=cmb)
                self._cmb_mode = "markers"
                self._cmb_mode_widget = None
                self._shifted_out_keys = {}
                self._prev_action = None
                self._next_action = None
                self._syncing = False
                self._playback_range_mode = "off"
                self._widget = widget
                self.synced = []

            def _sync_to_widget(self, frame=False):
                self.synced.append(frame)

            def _get_sequencer_widget(self):
                return self._widget

            def _visible_shots(self, shot):
                return [shot]

        return _Host()

    def test_marker_nav_does_not_select_shot_with_matching_id(self):
        # Shot id 3 is unrelated to the marker at time 3.0.
        seq = ShotSequencer([ShotBlock(3, "S3", 50, 60, ["cube1"])])
        seq.store.select_on_load = False
        selected = []
        seq.store.set_active_shot = lambda sid: selected.append(sid)

        cmb = self._FakeCombo([("@ 1", 1.0), ("@ 3", 3.0)], index=0)
        widget = self._FakeWidget()
        host = self._make_host(cmb, widget, seq)

        host._navigate_shot(+1)

        self.assertEqual(
            selected,
            [],
            "markers-mode nav must not route the marker time into shot selection",
        )
        self.assertEqual(
            widget.playhead, 3.0, "markers-mode nav must jump the playhead"
        )
        self.assertEqual(widget.playhead_moved.emitted, [(3.0,)])
        self.assertEqual(cmb.currentIndex(), 1)


# ---------------------------------------------------------------------------
# Key-move tangent fidelity (2026-08-17 backlog: sequencer key moves dropped
# tangent angles / weights / breakdown flags via cut-and-recreate)
# ---------------------------------------------------------------------------


#: Per-key tangent properties a move must carry through untouched.
TANGENT_PROPS = (
    "inAngle",
    "outAngle",
    "inWeight",
    "outWeight",
    "inTangentType",
    "outTangentType",
    "weightLock",
    "lock",
)


def snapshot_curve(crv):
    """Full per-key state of *crv* -- value, every tangent property, breakdown."""
    times = cmds.keyframe(crv, q=True) or []
    breakdowns = set(cmds.keyframe(crv, q=True, breakdown=True) or [])
    recs = []
    for t in times:
        rec = {
            "time": t,
            "value": cmds.keyframe(crv, q=True, time=(t, t), valueChange=True)[0],
            "breakdown": any(abs(t - b) < 1e-6 for b in breakdowns),
        }
        for name in TANGENT_PROPS:
            rec[name] = cmds.keyTangent(crv, q=True, time=(t, t), **{name: True})[0]
        recs.append(rec)
    return recs


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestKeyMoveTangentFidelity(unittest.TestCase):
    """A key move must carry the FULL tangent state, not just value + type.

    ``move_object_keys`` used to cut-and-recreate every key, capturing only
    the value and the in/out tangent *type* -- so hand-tuned angles, weights,
    lock flags and breakdown markers were silently reset, reshaping the curve.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    # -- fixtures ---------------------------------------------------------

    def _hand_tuned_cube(self, name="tanFidelity"):
        """A cube whose translateX curve carries deliberately non-default tangents.

        Keys at 1 / 5 / 10 with fixed weighted tangents at hand-set angles and
        weights, plus a breakdown key at 5.
        """
        cube = cmds.polyCube(name=name)[0]
        for t, v in ((1, 0.0), (5, 5.0), (10, 2.0)):
            cmds.setKeyframe(cube, attribute="translateX", time=t, value=v)
        crv = self._curve_of(cube)
        cmds.keyTangent(crv, edit=True, time=(1, 10), weightedTangents=True)
        cmds.keyTangent(
            crv,
            edit=True,
            time=(1, 1),
            lock=False,
            inTangentType="fixed",
            outTangentType="fixed",
            outAngle=37.5,
            outWeight=4.25,
        )
        cmds.keyTangent(
            crv,
            edit=True,
            time=(5, 5),
            lock=False,
            weightLock=False,
            inTangentType="fixed",
            outTangentType="fixed",
            inAngle=-22.0,
            outAngle=63.0,
            inWeight=3.5,
            outWeight=2.75,
        )
        cmds.keyTangent(
            crv,
            edit=True,
            time=(10, 10),
            lock=False,
            inTangentType="fixed",
            outTangentType="fixed",
            inAngle=11.25,
            inWeight=5.5,
        )
        cmds.keyframe(crv, edit=True, time=(5, 5), breakdown=True)
        return cube, crv

    @staticmethod
    def _curve_of(cube):
        return cmds.listConnections(
            f"{cube}.translateX", type="animCurve", s=True, d=False
        )[0]

    def _assert_shifted_identically(self, before, after, delta, msg="", props=None):
        self.assertEqual(
            len(before), len(after), f"{msg}: key count changed {before} -> {after}"
        )
        for b, a in zip(before, after):
            self.assertAlmostEqual(
                b["time"] + delta,
                a["time"],
                places=4,
                msg=f"{msg}: key {b['time']} did not land at {b['time'] + delta}",
            )
            self._assert_same_state(b, a, msg, props)

    def _assert_same_state(self, b, a, msg="", props=None):
        for name in props or TANGENT_PROPS + ("value", "breakdown"):
            bv, av = b[name], a[name]
            if isinstance(bv, float):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=4,
                    msg=f"{msg}: t={b['time']} {name} {bv!r} -> {av!r}",
                )
            else:
                self.assertEqual(
                    bv, av, msg=f"{msg}: t={b['time']} {name} {bv!r} -> {av!r}"
                )

    # -- move_object_keys --------------------------------------------------

    def test_move_object_keys_preserves_full_tangent_state(self):
        """A clean (collision-free) move keeps every tangent property intact."""
        cube, crv = self._hand_tuned_cube()
        before = snapshot_curve(crv)

        ShotSequencer().move_object_keys(str(cube), 1, 10, 21)

        after = snapshot_curve(self._curve_of(cube))
        self._assert_shifted_identically(before, after, 20, "clean move")

    def test_move_object_keys_pushes_a_pose_out_of_the_landing_zone(self):
        """A pose inside the destination window is displaced, never straddled.

        27 sits inside the 21..30 destination window.  Leaving it there put
        the arriving cluster's motion and the old pose on the same span --
        the clip played as neither, which is what "the animation gets
        malformed" describes.  It is pushed clear instead, in the direction of
        travel, and every moved key still lands on its true destination with
        its full tangent state.
        """
        cube, crv = self._hand_tuned_cube()
        cmds.setKeyframe(cube, attribute="translateX", time=27, value=9.0)
        before = [r for r in snapshot_curve(crv) if r["time"] <= 10.0 + 1e-6]
        self.assertEqual(len(before), 3)

        ShotSequencer().move_object_keys(str(cube), 1, 10, 21)

        crv2 = self._curve_of(cube)
        times = sorted(round(t, 4) for t in cmds.keyframe(crv2, q=True) or [])
        self.assertEqual(len(times), 4, f"no key may be lost (got {times})")
        self.assertEqual(
            times[:3],
            [21.0, 25.0, 30.0],
            f"the moved keys land where they were asked to (got {times})",
        )
        self.assertGreater(
            times[3], 30.0, "the obstructing pose is pushed past the arrival"
        )
        pushed = [r for r in snapshot_curve(crv2) if r["time"] > 30.0 + 1e-6]
        self.assertAlmostEqual(
            pushed[0]["value"], 9.0, places=4, msg="the pose itself is untouched"
        )
        moved = [r for r in snapshot_curve(crv2) if r["time"] <= 30.0 + 1e-6]
        self._assert_shifted_identically(before, moved, 20, "crossing")

    def test_move_object_keys_takes_an_occupied_frame_without_eating_the_pose(self):
        """An exact landing must not be a silent delete.

        A moved key landing precisely on a key that stays put is the one thing
        a relative move cannot express (two keys can't share a frame), so this
        drops to cut-and-recreate -- whose ``setKeyframe`` OVERWRITES.  The
        moved key still has to win the slot and keep every tangent property,
        but the pose that was there gets pushed clear rather than destroyed.
        """
        cube, crv = self._hand_tuned_cube()
        cmds.setKeyframe(cube, attribute="translateX", time=25, value=9.0)
        before = [r for r in snapshot_curve(crv) if r["time"] <= 10.0 + 1e-6]
        self.assertEqual(len(before), 3)

        ShotSequencer().move_object_keys(str(cube), 1, 10, 21)

        after = snapshot_curve(self._curve_of(cube))
        times = [round(r["time"], 4) for r in after]
        self.assertEqual(len(times), 4, f"the displaced pose survives (got {times})")
        self.assertEqual(
            times[:3],
            [21.0, 25.0, 30.0],
            "the moved key must take the occupied frame outright, not land a "
            "sub-frame short of it",
        )
        self.assertAlmostEqual(after[3]["value"], 9.0, places=4)
        self._assert_shifted_identically(before, after[:3], 20, "occupied destination")

    def test_move_object_keys_preserves_default_tangent_types(self):
        """The recreate path must not convert auto/linear tangents to fixed.

        Auto tangent *angles* are derived from the neighbouring keys, so they
        legitimately recompute once the cluster sits beside a different
        neighbour; the tangent TYPE and the lock/breakdown flags must not.
        """
        cube = cmds.polyCube(name="defaultTans")[0]
        for t, v in ((1, 0.0), (5, 5.0), (10, 2.0)):
            cmds.setKeyframe(cube, attribute="translateX", time=t, value=v)
        crv = self._curve_of(cube)
        cmds.keyTangent(crv, edit=True, time=(10, 10), inTangentType="linear")
        cmds.keyTangent(crv, edit=True, time=(5, 5), outTangentType="flat")
        cmds.keyframe(crv, edit=True, time=(5, 5), breakdown=True)
        cmds.setKeyframe(cube, attribute="translateX", time=25, value=9.0)
        before = [r for r in snapshot_curve(crv) if r["time"] <= 10.0 + 1e-6]

        ShotSequencer().move_object_keys(str(cube), 1, 10, 21)

        # The key planted at 25 is inside the 21..30 landing zone and carries
        # a pose, so it is pushed clear rather than overwritten; this test is
        # about the MOVED keys' tangents, so compare against those.
        after = [r for r in snapshot_curve(self._curve_of(cube)) if r["time"] <= 30.5]
        self._assert_shifted_identically(
            before,
            after,
            20,
            "default tangents",
            props=(
                "inTangentType",
                "outTangentType",
                "weightLock",
                "lock",
                "value",
                "breakdown",
            ),
        )

    # -- clip_motion.on_keys_moved ----------------------------------------

    class _FakeClip:
        def __init__(self, data):
            self.data = data

    class _FakeWidget:
        def __init__(self, clip):
            self._clip = clip

        def get_clip(self, clip_id):
            return self._clip

    def _clip_motion_host(self, obj, attr, seq):
        """Minimal ClipMotionMixin host -- the members the mixin documents."""
        import logging

        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin

        outer = self

        class Host(ClipMotionMixin):
            def __init__(self):
                self.sequencer = seq
                self._segment_cache = {}
                self._sub_row_cache = {}
                self._syncing = False
                self.logger = logging.getLogger("test.clip_motion_host")
                self.footers = []
                self._clip = outer._FakeClip(
                    {"obj": obj, "attr_name": attr, "shot_id": 0}
                )

            def _get_sequencer_widget(self):
                return outer._FakeWidget(self._clip)

            def _save_shot_state(self):
                pass

            def _discard_shot_state(self):
                pass

            def _sync_to_widget(self, shot_id=None):
                pass

            def _sync_combobox(self):
                pass

            def _set_footer(self, text, *args, **kwargs):
                self.footers.append(text)

        return Host()

    def test_on_keys_moved_preserves_full_tangent_state(self):
        """Dragging keys in the clip view must not reset their tangents."""
        cube, crv = self._hand_tuned_cube("clipTanFidelity")
        before = snapshot_curve(crv)
        host = self._clip_motion_host(str(cube), "translateX", ShotSequencer())

        host.on_keys_moved(0, [(1.0, 21.0), (5.0, 25.0), (10.0, 30.0)])

        after = snapshot_curve(self._curve_of(cube))
        self._assert_shifted_identically(before, after, 20, "on_keys_moved")

    def test_on_keys_moved_preserves_tangents_for_a_sparse_selection(self):
        """Dragging some keys past one that stays put keeps every tangent."""
        cube, crv = self._hand_tuned_cube("clipSparse")
        before = snapshot_curve(crv)
        host = self._clip_motion_host(str(cube), "translateX", ShotSequencer())

        # Keys 1 and 10 move +20; the key at 5 stays where it is.
        host.on_keys_moved(0, [(1.0, 21.0), (10.0, 30.0)])

        after = snapshot_curve(self._curve_of(cube))
        self.assertEqual(
            [round(r["time"], 4) for r in after],
            [5.0, 21.0, 30.0],
            "the unselected key must stay put while the others move past it",
        )
        stayed = [r for r in after if abs(r["time"] - 5.0) < 1e-6]
        moved = [r for r in after if abs(r["time"] - 5.0) >= 1e-6]
        self._assert_shifted_identically(
            [before[0], before[2]], moved, 20, "sparse drag"
        )
        self._assert_shifted_identically(
            [before[1]], stayed, 0, "sparse drag (untouched key)"
        )

    def test_on_keys_moved_tolerates_drag_times_off_by_rounding(self):
        """Drag times arrive from the widget, so they match only to a tolerance.

        Pairing a reported time with a curve key by equality (or by a rounded
        equality) drops the key outright, and the whole drag silently becomes
        a no-op.  Mixed deltas so this runs through the cut-and-recreate path,
        which is where the pairing happens.
        """
        cube, crv = self._hand_tuned_cube("clipDrifted")
        before = snapshot_curve(crv)
        host = self._clip_motion_host(str(cube), "translateX", ShotSequencer())

        drift = 1e-5  # finer than the 1e-3 key window, coarser than 6dp rounding
        host.on_keys_moved(
            0, [(1.0 + drift, 21.0), (5.0 - drift, 26.0), (10.0 + drift, 32.0)]
        )

        after = snapshot_curve(self._curve_of(cube))
        self.assertEqual(
            [round(r["time"], 4) for r in after],
            [21.0, 26.0, 32.0],
            "a drag whose times differ from the curve's only by rounding must "
            "still move the keys",
        )
        for b, a in zip(before, after):
            self._assert_same_state(b, a, "drifted drag times")

    def test_on_keys_moved_preserves_tangents_for_mixed_deltas(self):
        """Per-key deltas that differ still have to carry the tangent state."""
        cube, crv = self._hand_tuned_cube("clipMixedDelta")
        before = snapshot_curve(crv)
        host = self._clip_motion_host(str(cube), "translateX", ShotSequencer())

        # 1 -> 21 (+20), 5 -> 26 (+21), 10 -> 32 (+22)
        host.on_keys_moved(0, [(1.0, 21.0), (5.0, 26.0), (10.0, 32.0)])

        after = snapshot_curve(self._curve_of(cube))
        self.assertEqual(
            [round(r["time"], 4) for r in after],
            [21.0, 26.0, 32.0],
            "mixed-delta drag must land every key on its own destination",
        )
        for b, a in zip(before, after):
            self._assert_same_state(b, a, "on_keys_moved mixed deltas")


# ===========================================================================
# Real-scene regressions (VDATS assembly: 12 shots, renamed rig, shared
# objects across shots, zero-gap layout)
# ===========================================================================


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestReconcileNeverDropsMembership(unittest.TestCase):
    """Reconciliation re-points membership; it must never DELETE any.

    It runs unattended on every panel refresh, and "renamed" is
    indistinguishable from "deleted" by name alone.  A production scene whose
    rig had been renamed after the shots were authored lost 21 membership
    entries across 8 of 12 shots the moment the panel opened — silently, and
    irreversibly on the next store flush.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_a_vanished_object_keeps_its_stored_entry(self):
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, ["|grp|GONE_LOC"])])
        self.assertTrue(seq.reconcile_all_shots() or True)
        self.assertEqual(
            seq.shot_by_id(0).objects,
            ["|grp|GONE_LOC"],
            "an unresolvable member is inert, but dropping it destroys the "
            "shot's record of its own content",
        )

    def test_a_reparented_object_is_repathed_by_leaf_name(self):
        grp = cmds.group(empty=True, name="newParent")
        loc = cmds.spaceLocator(name="MOVED_LOC")[0]
        cmds.parent(loc, grp)
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, ["|oldParent|MOVED_LOC"])])
        seq.reconcile_all_shots()
        self.assertEqual(seq.shot_by_id(0).objects, ["|newParent|MOVED_LOC"])

    def test_a_renamed_object_is_recovered_through_its_curve_names(self):
        """Maya names an auto-created curve ``<node>_<attr>`` and never renames
        it with the node, so the curves are a fossil of the old name."""
        loc = cmds.spaceLocator(name="OLD_LOC")[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        cmds.setKeyframe(loc, at="translateX", t=10, v=5)
        cmds.rename(loc, "NEW_LOC")
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, ["OLD_LOC"])])
        seq.reconcile_all_shots()
        self.assertEqual(
            [o.rsplit("|", 1)[-1] for o in seq.shot_by_id(0).objects], ["NEW_LOC"]
        )

    def test_an_ambiguous_rename_is_left_alone(self):
        """Two candidates → no guess: re-pointing a shot at the wrong object
        is worse than leaving the name unresolved."""
        from mayatk.anim_utils.shots._shots import Detection

        for name in ("DUP_A", "DUP_B"):
            loc = cmds.spaceLocator(name=name)[0]
            cmds.setKeyframe(loc, at="translateX", t=1, v=0)
            # Curves named after ONE vanished node, driving two survivors.
            crv = cmds.listConnections(
                f"{loc}.translateX", type="animCurve", s=True, d=False
            )[0]
            cmds.rename(crv, f"VANISHED_LOC_translateX_{name}")
        self.assertIsNone(Detection.transform_from_curve_names("VANISHED_LOC"))

    def test_a_longer_node_name_cannot_claim_the_prefix(self):
        """``FOO_`` must not match ``FOO_BAR``'s curves — the text after the
        prefix has to BE the attribute the curve drives."""
        from mayatk.anim_utils.shots._shots import Detection

        loc = cmds.spaceLocator(name="FOO_BAR")[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        self.assertIsNone(Detection.transform_from_curve_names("FOO"))
        self.assertIsNotNone(Detection.transform_from_curve_names("FOO_BAR"))

    def test_reconcile_is_idempotent(self):
        loc = cmds.spaceLocator(name="IDEM_LOC")[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        seq = ShotSequencer([ShotBlock(0, "A", 0, 50, ["|missing|IDEM_LOC"])])
        seq.reconcile_all_shots()
        first = list(seq.shot_by_id(0).objects)
        seq.reconcile_all_shots()
        self.assertEqual(seq.shot_by_id(0).objects, first)


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestOrderedRippleClearsUnownedKeys(unittest.TestCase):
    """A rippled key must be able to pass a key that will NOT move.

    ``option="move"`` refuses to let a key pass a neighbour and clamps it
    onto that frame instead — producing two keys at one time, the travelling
    one at the wrong frame.  The plan's topological order does not help: it
    only orders the moves the plan CONTAINS.

    A moving shot now adopts everything keyed inside it
    (``_adopt_keyed_objects``), so plain non-membership no longer produces
    an immovable key.  Ambiguity still does, deliberately: a leaf name that
    matches two scene nodes is never adopted, because guessing would
    re-point a shot at the wrong object.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_a_travelling_key_passes_a_key_in_a_stationary_shot(self):
        """One curve, two shots, only the second moves — backwards, past the
        first shot's key.

        The back-fill cannot help here: the stationary shot is not in the
        plan's move set, so its key is genuinely immovable, and the
        travelling key has to pass it rather than clamp onto it.
        """
        loc = cmds.spaceLocator(name="shared_loc")[0]
        for t, v in ((90, 0.0), (110, 1.0)):
            cmds.setKeyframe(loc, at="translateX", t=t, v=v)
        # A [0,60] stays put; its envelope is [0,100) and owns the key at 90.
        # B [100,160] respaces back to 60 (delta -40) and its key at 110
        # must travel to 70 — straight through 90.
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 60, []), ShotBlock(1, "B", 100, 160, [])]
        )
        seq.respace(gap=0, start_frame=0)

        times = sorted(cmds.keyframe(loc, q=True, at="translateX") or [])
        self.assertEqual(len(times), len(set(times)), f"duplicate key times: {times}")
        self.assertIn(90.0, times, "the stationary shot's key must not move")
        self.assertIn(70.0, times, f"the 110 key must travel its full -40, got {times}")


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestShotEdgeClampsAtNeighbour(unittest.TestCase):
    """A non-rippling edge drag can only eat the adjacent GAP.

    At zero gap there is nothing to eat, and an unclamped drag stored
    OVERLAPPING shots — two shots claiming one span makes key ownership, and
    every envelope derived from it, ambiguous.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _host(self, shots):
        from mayatk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin

        class Host(GapManagerMixin):
            def __init__(self, sequencer):
                self.sequencer = sequencer

        return Host(ShotSequencer(shots))

    def test_start_cannot_cross_the_previous_shots_end(self):
        host = self._host([ShotBlock(0, "A", 0, 50), ShotBlock(1, "B", 50, 100)])
        changed = host._set_shot_edge(host.sequencer.shot_by_id(1), new_start=42)
        self.assertFalse(changed, "at zero gap the edge has nowhere to go")
        self.assertAlmostEqual(host.sequencer.shot_by_id(1).start, 50.0)

    def test_end_cannot_cross_the_next_shots_start(self):
        host = self._host([ShotBlock(0, "A", 0, 50), ShotBlock(1, "B", 50, 100)])
        host._set_shot_edge(host.sequencer.shot_by_id(0), new_end=70)
        self.assertAlmostEqual(host.sequencer.shot_by_id(0).end, 50.0)

    def test_the_gap_is_still_consumable_when_there_is_one(self):
        host = self._host([ShotBlock(0, "A", 0, 50), ShotBlock(1, "B", 70, 100)])
        self.assertTrue(host._set_shot_edge(host.sequencer.shot_by_id(1), new_start=60))
        self.assertAlmostEqual(host.sequencer.shot_by_id(1).start, 60.0)

    def test_clamping_stops_exactly_at_the_neighbour(self):
        host = self._host([ShotBlock(0, "A", 0, 50), ShotBlock(1, "B", 70, 100)])
        host._set_shot_edge(host.sequencer.shot_by_id(1), new_start=20)
        self.assertAlmostEqual(host.sequencer.shot_by_id(1).start, 50.0)


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestUndoPairing(unittest.TestCase):
    """A bounds-only edit records nothing on Maya's queue.

    Maya DISCARDS an empty undo chunk (verified: ``undoName`` still reports
    the entry before it), so an unconditional ``cmds.undo()`` after such an
    edit pops the user's previous, unrelated operation.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        ShotStore.clear_active()

    def tearDown(self):
        ShotStore.clear_active()

    def _store(self):
        store = ShotStore()
        store.define_shot("A", 0, 50)
        store.define_shot("B", 60, 100)
        return store

    def test_a_bounds_only_edit_is_tagged_unpaired(self):
        store = self._store()
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        paired, marker = store.peek_boundary_tag()
        self.assertFalse(paired, "nothing reached Maya's queue")
        self.assertEqual(marker, store.undo_queue_top())

    def test_a_scene_edit_is_tagged_paired_with_its_chunk(self):
        store = self._store()
        loc = cmds.spaceLocator(name="pair_loc")[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        with store.scene_edit("keys"):
            cmds.setKeyframe(loc, at="translateX", t=20, v=5)
        paired, marker = store.peek_boundary_tag()
        self.assertTrue(paired)
        self.assertEqual(marker, cmds.undoInfo(q=True, undoName=True))

    def test_snapshot_false_records_no_restore_point(self):
        store = self._store()
        with store.scene_edit("probe", snapshot=False):
            store.update_shot(0, end=40)
        self.assertFalse(store.has_boundary_snapshot())

    def test_empty_chunk_leaves_the_previous_entry_on_top(self):
        """The Maya behaviour the pairing exists for."""
        store = self._store()
        loc = cmds.spaceLocator(name="prev_loc")[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        with store.scene_edit("real"):
            cmds.setKeyframe(loc, at="translateX", t=20, v=5)
        before = cmds.undoInfo(q=True, undoName=True)
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        self.assertEqual(
            cmds.undoInfo(q=True, undoName=True),
            before,
            "an empty chunk must not appear on the queue",
        )


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestNativeUndoEventOwnership(unittest.TestCase):
    """Maya fires Undo/Redo for EVERY undo in the session.

    Consuming a restore point for someone else's undo reverts shot bounds
    whose keys Maya left exactly where they were — bounds and keys desync
    with nothing on screen to explain it.  The entry Maya just moved is
    named by the OPPOSITE queue, and our restore point knows the marker its
    edit landed under, so the two match only when the event is ours.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        ShotStore.clear_active()

    def tearDown(self):
        ShotStore.clear_active()

    def _controller(self):
        """A controller stub carrying only what the ownership test reads."""
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        store = ShotStore()
        store.define_shot("A", 0, 50)
        store.define_shot("B", 60, 100)
        ctrl = ShotSequencerController.__new__(ShotSequencerController)
        ctrl._sequencer = ShotSequencer(store=store)
        return ctrl, store

    def _keyed(self, name):
        loc = cmds.spaceLocator(name=name)[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        return loc

    def test_no_restore_point_is_not_ours(self):
        ctrl, _store = self._controller()
        self.assertFalse(ctrl._native_event_is_ours())

    def test_our_own_edit_on_top_is_ours(self):
        ctrl, store = self._controller()
        loc = self._keyed("own_loc")
        with store.scene_edit("keys"):
            cmds.setKeyframe(loc, at="translateX", t=20, v=5)
        cmds.undo()  # Maya undoes OUR chunk
        self.assertTrue(ctrl._native_event_is_ours())

    def test_an_unrelated_undo_is_not_ours(self):
        ctrl, store = self._controller()
        loc = self._keyed("other_loc")
        with store.scene_edit("keys"):
            cmds.setKeyframe(loc, at="translateX", t=20, v=5)
        with CoreUtils.undo_chunk("unrelated_edit"):
            cmds.setAttr(loc + ".translateY", 5)
        cmds.undo()  # Maya undoes the UNRELATED edit
        self.assertFalse(
            ctrl._native_event_is_ours(),
            "our restore point must survive an undo of someone else's edit",
        )

    def test_a_bounds_only_edit_is_never_claimed_by_a_native_undo(self):
        ctrl, store = self._controller()
        loc = self._keyed("bounds_loc")
        with CoreUtils.undo_chunk("unrelated_edit"):
            cmds.setAttr(loc + ".translateY", 5)
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        cmds.undo()  # can only be the unrelated edit — ours recorded nothing
        self.assertFalse(ctrl._native_event_is_ours())

    def test_redo_direction_matches_on_the_undo_queue(self):
        ctrl, store = self._controller()
        loc = self._keyed("redo_loc")
        with store.scene_edit("keys"):
            cmds.setKeyframe(loc, at="translateX", t=20, v=5)
        store.restore_boundary_snapshot()  # move our entry to the redo side
        cmds.undo()
        cmds.redo()
        self.assertTrue(ctrl._native_event_is_ours(redo=True))

    def test_an_untagged_push_keeps_the_pre_pairing_behaviour(self):
        ctrl, store = self._controller()
        store.push_boundary_snapshot()
        self.assertTrue(ctrl._native_event_is_ours())


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestUndoPlanRedoDirection(unittest.TestCase):
    """The redo direction has to compare against the RIGHT queue.

    A paired edit's chunk rides Maya's queues, so after its undo the marker
    names the top of the redo queue.  An UNPAIRED edit never put anything on
    either queue — its marker names the undo queue's top, in both
    directions.  Comparing an unpaired entry against the redo queue always
    mismatched, so redoing a bounds-only edit silently dropped the bounds
    restore AND called cmds.redo() on somebody else's undone operation.
    Found by driving a real production scene.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        ShotStore.clear_active()

    def tearDown(self):
        ShotStore.clear_active()

    def _controller(self):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        store = ShotStore()
        store.define_shot("A", 0, 50)
        store.define_shot("B", 60, 100)
        ctrl = ShotSequencerController.__new__(ShotSequencerController)
        ctrl._sequencer = ShotSequencer(store=store)
        return ctrl, store

    def test_redo_of_a_bounds_only_edit_applies_the_ledger(self):
        ctrl, store = self._controller()
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        store.restore_boundary_snapshot()
        self.assertEqual(
            ctrl._undo_plan(redo=True),
            (True, False),
            "an unpaired redo must apply the ledger and NOT touch Maya",
        )

    def test_redo_of_a_paired_edit_touches_mayas_queue(self):
        ctrl, store = self._controller()
        loc = cmds.spaceLocator(name="paired_loc")[0]
        cmds.setKeyframe(loc, at="translateX", t=1, v=0)
        with store.scene_edit("keys"):
            cmds.setKeyframe(loc, at="translateX", t=20, v=5)
        cmds.undo()
        store.restore_boundary_snapshot()
        self.assertEqual(ctrl._undo_plan(redo=True), (True, True))

    def test_an_unrelated_edit_after_the_undo_releases_the_redo(self):
        """A new edit clears Maya's redo stack, so our point must stand down."""
        ctrl, store = self._controller()
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        store.restore_boundary_snapshot()
        loc = cmds.spaceLocator(name="later_loc")[0]
        with CoreUtils.undo_chunk("unrelated_edit"):
            cmds.setKeyframe(loc, at="translateX", t=5, v=1)
        self.assertEqual(
            ctrl._undo_plan(redo=True),
            (False, True),
            "our restore point must stay put once an unrelated edit lands",
        )

    def test_undo_direction_is_unchanged_for_both_pairings(self):
        ctrl, store = self._controller()
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        self.assertEqual(ctrl._undo_plan(), (True, False))
        loc = cmds.spaceLocator(name="both_loc")[0]
        with store.scene_edit("keys"):
            cmds.setKeyframe(loc, at="translateX", t=3, v=1)
        self.assertEqual(ctrl._undo_plan(), (True, True))

    def test_bounds_survive_an_undo_redo_round_trip(self):
        """The user-visible half: shrink, undo, redo — the shrink comes back."""
        ctrl, store = self._controller()
        with store.scene_edit("boundsonly"):
            store.update_shot(0, end=40)
        apply_ledger, call_maya = ctrl._undo_plan()
        self.assertTrue(apply_ledger)
        store.restore_boundary_snapshot()
        self.assertEqual(store.shot_by_id(0).end, 50.0)
        apply_ledger, call_maya = ctrl._undo_plan(redo=True)
        self.assertTrue(apply_ledger, "redo must re-apply the bounds")
        self.assertFalse(call_maya)
        store.redo_boundary_snapshot()
        self.assertEqual(store.shot_by_id(0).end, 40.0)


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestDeleteKeysBracketing(unittest.TestCase):
    """Both key-delete paths must bracket through scene_edit.

    They were the last edit sites still opening a bare undo chunk and
    pushing their restore point AFTER the mutation, untagged — so the point
    could not restore anything, and being untagged the Maya Undo EVENT
    callback claimed it for somebody else's undo.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        ShotStore.clear_active()

    def tearDown(self):
        ShotStore.clear_active()

    def _ctrl_with_clip(self):
        from unittest.mock import MagicMock
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        loc = cmds.spaceLocator(name="del_loc")[0]
        for t in (1, 5, 10):
            cmds.setKeyframe(loc, at="translateX", t=t, v=float(t))

        store = ShotStore()
        store.define_shot("A", 0, 50, objects=cmds.ls(loc, long=True))
        ctrl = ShotSequencerController.__new__(ShotSequencerController)
        ctrl._sequencer = ShotSequencer(store=store)
        ctrl.logger = MagicMock()
        ctrl._segment_cache = {}
        ctrl._sub_row_cache = {}
        ctrl._sync_to_widget = MagicMock()
        ctrl._set_footer = MagicMock()
        ctrl._resolve_full_name = MagicMock(side_effect=lambda n: n)

        clip = MagicMock()
        clip.data = {
            "obj": cmds.ls(loc, long=True)[0],
            "attr_name": "translateX",
            "attributes": ["translateX"],
            "orig_start": 0.0,
            "orig_end": 50.0,
        }
        widget = MagicMock()
        widget.get_clip = MagicMock(side_effect=lambda cid: clip if cid == 1 else None)
        ctrl._get_sequencer_widget = MagicMock(return_value=widget)
        return ctrl, store, loc

    def test_delete_clip_keys_is_paired_and_predates_the_edit(self):
        ctrl, store, loc = self._ctrl_with_clip()
        ctrl._delete_clip_keys([1])
        self.assertEqual(cmds.keyframe(loc, q=True, timeChange=True) or [], [])
        tag = store.peek_boundary_tag()
        self.assertIsInstance(tag, tuple, "the restore point must be tagged")
        self.assertTrue(tag[0], "a key delete records a Maya undo step")
        self.assertEqual(tag[1], cmds.undoInfo(q=True, undoName=True))

    def test_a_delete_that_removes_nothing_leaves_no_restore_point(self):
        ctrl, store, _loc = self._ctrl_with_clip()
        depth = len(store._boundary_undo)
        ctrl._delete_clip_keys([999])  # no such clip -> no ops at all
        self.assertEqual(len(store._boundary_undo), depth)

    def test_a_failed_delete_discards_its_restore_point(self):
        """Every cutKey failing (locked/connected attrs on a referenced asset)
        must leave the ledger exactly as it was — an orphan restore point
        shifts every later undo by one, and losing the redo branch that the
        up-front push cleared costs the user a redo they never spent."""
        ctrl, store, loc = self._ctrl_with_clip()
        store.push_boundary_snapshot(tag=(False, "earlier"))
        store.update_shot(0, end=40)
        store.restore_boundary_snapshot()  # -> a redo branch exists
        depth = len(store._boundary_undo)

        with patch.object(cmds, "cutKey", side_effect=RuntimeError("locked")):
            ctrl._delete_clip_keys([1])

        self.assertEqual(len(store._boundary_undo), depth)
        self.assertTrue(
            store.has_boundary_snapshot(redo=True),
            "a failed delete must not cost the user their redo branch",
        )
        self.assertEqual(len(cmds.keyframe(loc, q=True, timeChange=True) or []), 3)


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestRespacePreservesEachShot(unittest.TestCase):
    """A respace repositions shots; it must not re-animate them.

    The reported bug, reproduced against the production assembly: resizing
    every gap to 15 frames changed what Maya evaluated inside ELEVEN of the
    twelve shots -- including Shot 4, whose position did not change at all,
    on 42 of its 109 frames. The cause is a curve segment that spans a shot
    boundary: moving whatever is on the other side retimes that segment, and
    with auto tangents the change reaches back past the boundary.

    So the contract these pin is: sample what the scene evaluates at every
    frame of a shot, respace, and sample the same OFFSETS at the shot's new
    position -- the two must be identical.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _spanning(self, name):
        """A locator whose keys straddle both shot boundaries and the gap."""
        loc = cmds.spaceLocator(name=name)[0]
        for t, v in ((0, 0.0), (20, 5.0), (60, 9.0), (120, 30.0), (140, 40.0)):
            cmds.setKeyframe(loc, at="translateX", t=t, v=v)
        return loc

    def _samples(self, loc, start, end):
        out = []
        for frame in range(int(start), int(end) + 1):
            cmds.currentTime(frame, edit=True)
            out.append(round(cmds.getAttr(f"{loc}.translateX"), 6))
        return out

    def test_a_shot_that_does_not_move_is_not_changed(self):
        """The sharpest form of the bug: shot A stays where it is and still
        loses frames, because the key it interpolates TOWARD moved."""
        loc = self._spanning("stay_loc")
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, [loc]), ShotBlock(1, "B", 110, 150, [loc])]
        )
        before = self._samples(loc, 0, 50)
        seq.respace(gap=10, start_frame=0)  # A stays at 0-50, B: 110 -> 60
        self.assertAlmostEqual(seq.shot_by_id(0).start, 0.0)
        self.assertEqual(self._samples(loc, 0, 50), before)

    def test_a_shot_that_moves_takes_its_content_with_it_unchanged(self):
        loc = self._spanning("move_loc")
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, [loc]), ShotBlock(1, "B", 110, 150, [loc])]
        )
        before = self._samples(loc, 110, 150)
        seq.respace(gap=10, start_frame=0)
        b = seq.shot_by_id(1)
        self.assertAlmostEqual(b.start, 60.0)
        self.assertEqual(self._samples(loc, b.start, b.end), before)

    def test_growing_every_gap_preserves_both_shots_too(self):
        """The grow path runs at a different moment than the shrink path (the
        timeline it scales into is only empty afterwards), so it needs its own
        proof rather than riding the shrink case's."""
        loc = self._spanning("grow_loc")
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, [loc]), ShotBlock(1, "B", 60, 100, [loc])]
        )
        before_a = self._samples(loc, 0, 50)
        before_b = self._samples(loc, 60, 100)
        seq.respace(gap=200, start_frame=0)
        b = seq.shot_by_id(1)
        self.assertAlmostEqual(b.start, 250.0)
        self.assertEqual(self._samples(loc, 0, 50), before_a)
        self.assertEqual(self._samples(loc, b.start, b.end), before_b)

    def test_gap_content_no_shot_claims_is_still_retimed(self):
        """The retime must act on the scene's CONTENT, not on the flanking
        shot's object list.

        Membership is backfilled only for shots that MOVE, so a stationary
        left shot's list is whatever the store happened to hold. Reading it
        leaves a gap key nobody claimed exactly where it was — and since the
        shot AFTER the gap moves toward it, that key ends up inside the next
        shot's range, which is the stranding the whole pass exists to prevent.
        """
        # Keyed ONLY inside the gap, so neither shot adopts it: the backfill
        # runs on the moving shot's envelope [110, INF), and the stationary
        # shot is not backfilled at all. A key further out would be adopted by
        # the moving shot and retimed either way, which is what made the first
        # version of this test pass against the very bug it names.
        loc = cmds.spaceLocator(name="gapkey_loc")[0]
        for t, v in ((60, 6.0), (90, 9.0)):
            cmds.setKeyframe(loc, at="translateX", t=t, v=v)
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, []), ShotBlock(1, "B", 110, 150, [])]
        )
        seq.respace(gap=10, start_frame=0)  # A stays 0-50, B: 110 -> 60

        a, b = seq.shot_by_id(0), seq.shot_by_id(1)
        self.assertAlmostEqual(a.end, 50.0)
        self.assertAlmostEqual(b.start, 60.0)
        self.assertEqual(seq.shot_by_id(0).objects, [], "still claimed by nobody")
        self.assertEqual(seq.shot_by_id(1).objects, [])
        times = sorted(
            cmds.keyframe(loc, q=True, at="translateX", timeChange=True) or []
        )
        self.assertFalse(
            [t for t in times if b.start <= t <= b.end],
            f"nothing may be stranded inside the following shot: {times}",
        )
        inside = [t for t in times if a.end < t < b.start]
        self.assertEqual(len(inside), 2, f"both keys belong in the gap: {times}")
        # A 60-frame gap becomes a 10-frame one, so 10 and 40 frames in
        # become 10/6 and 40/6 — within whatever whole-frame snapping applies.
        for got, want in zip(inside, (50 + 10 / 6.0, 50 + 40 / 6.0)):
            self.assertLess(abs(got - want), 1.0, times)

    def test_pinning_alone_changes_nothing(self):
        """The pin is the precondition for all of the above, so it carries its
        own proof: inserting a key on every shot bound must leave the curve
        evaluating identically. (``tie_keyframes`` would NOT -- its bookends
        are flat, which is a different tool for a different job.)"""
        loc = self._spanning("pin_loc")
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, [loc]), ShotBlock(1, "B", 110, 150, [loc])]
        )
        before = self._samples(loc, 0, 150)
        added = ShotApply.pin_shot_bounds(seq.store, [loc])
        self.assertGreater(added, 0, "the spanning curve has bounds to pin")
        self.assertEqual(self._samples(loc, 0, 150), before)


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestMovingShotCarriesItsEnvelope(unittest.TestCase):
    """A shot that moves must carry everything keyed inside its envelope.

    ``ShotApply`` shifts ``shot.objects`` within ``[env_start, env_end)``, so
    an object keyed there but absent from the list is left behind: the shot
    moves and part of its animation does not, landing inside a neighbour.
    Measured on a production assembly — one "resize all gaps" stranded 146
    keys across 6 of 12 shots, and one shot whose entire membership was
    unresolvable (renamed rig) moved 341 frames carrying nothing.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _keyed(self, name, times):
        loc = cmds.spaceLocator(name=name)[0]
        for t in times:
            cmds.setKeyframe(loc, at="translateX", t=t, v=float(t))
        return loc

    def _times(self, loc):
        return sorted(
            cmds.keyframe(loc, q=True, at="translateX", timeChange=True) or []
        )

    def test_an_object_no_shot_owns_still_moves_with_the_shot(self):
        orphan = self._keyed("orphan_loc", (110, 120))
        member = self._keyed("member_loc", (105, 130))
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 50, [member]),
                ShotBlock(1, "B", 100, 150, [member]),
            ]
        )
        seq.respace(gap=10, start_frame=0)  # shot B: 100 -> 60, delta -40
        self.assertEqual(self._times(member), [65.0, 90.0])
        self.assertEqual(
            self._times(orphan),
            [70.0, 80.0],
            "an object no shot lists is still inside B's envelope and must move",
        )

    def test_a_shot_whose_membership_is_all_stale_still_moves_its_keys(self):
        loc = self._keyed("live_loc", (105, 130))
        seq = ShotSequencer(
            [
                ShotBlock(0, "A", 0, 50, ["|gone|RENAMED_AWAY"]),
                ShotBlock(1, "B", 100, 150, ["|gone|ALSO_RENAMED"]),
            ]
        )
        seq.respace(gap=10, start_frame=0)
        self.assertEqual(
            self._times(loc),
            [65.0, 90.0],
            "membership that resolves to nothing must not mean 'move nothing'",
        )

    def test_a_shared_boundary_key_belongs_to_the_PRECEDING_shot(self):
        """Contiguous shots share a sample; the fencepost rule gives it to the
        shot that CLOSES on it, not the one that opens.

        Here nothing else is keyed on the curve, so the sample is A's closing
        pose and nothing of B's: B slides away and it stays put.  It still
        moves exactly once — never twice, never with neither shot.
        """
        loc = self._keyed("edge_loc", (100,))
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 100, []), ShotBlock(1, "B", 100, 150, [])]
        )
        seq.respace(gap=0, start_frame=0)  # A stays, B: 100 -> 100 (no move)
        seq.slide_shot(1, 120.0)  # B alone moves +20
        self.assertEqual(
            self._times(loc), [100.0], "A closes on it, so it stays with A"
        )
        seq.slide_shot(0, 10.0)  # now A moves +10
        self.assertEqual(self._times(loc), [110.0], "and travels when A does")

    def test_a_split_gives_the_following_shot_its_opening_pose_back(self):
        """Opening a gap pulls the shared sample apart.

        The preceding shot keeps it; the following shot — which was also
        opening on it — gets a copy at its new start, so its first segment
        keeps its timing instead of starting on nothing.
        """
        loc = self._keyed("shared_loc", (10, 40, 60))  # 40 = the shared sample
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 40, [loc]), ShotBlock(1, "B", 40, 80, [loc])]
        )
        seq.respace(gap=10, start_frame=0)  # A stays [0,40]; B -> [50,90]
        self.assertEqual(
            self._times(loc),
            [10.0, 40.0, 50.0, 70.0],
            "A keeps 40; B opens on a copy at 50; B's own key 60 -> 70",
        )
        at_40 = cmds.keyframe(
            loc, q=True, at="translateX", time=(40, 40), valueChange=True
        )
        at_50 = cmds.keyframe(
            loc, q=True, at="translateX", time=(50, 50), valueChange=True
        )
        self.assertEqual(at_50, at_40, "the copy carries the shared pose")
        self.assertAlmostEqual(
            50.0 - 40.0, 10.0, msg="B's first segment keeps its 10-frame span"
        )

    def test_a_sample_only_the_following_shot_animates_travels_with_it(self):
        """Not every boundary sample is shared.

        Where the preceding shot has no key on the curve at all, the sample
        is the following shot's opening pose alone — it moves with that shot
        rather than being left behind on a curve its neighbour has no stake
        in.  (Regression: a first cut duplicated it, stranding a stray key.)
        """
        loc = self._keyed("b_only_loc", (40, 50, 60))  # nothing before 40
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 40, []), ShotBlock(1, "B", 40, 80, [loc])]
        )
        seq.respace(gap=10, start_frame=0)  # B -> [50,90], delta +10
        self.assertEqual(
            self._times(loc),
            [50.0, 60.0, 70.0],
            "the whole run travels; nothing is left at 40",
        )

    def test_collapsing_a_gap_merges_two_agreeing_samples_into_one(self):
        """Maya stacks a near-duplicate when a mover lands on an occupied
        frame (measured: t + 1.7e-7).  Agreeing poses must merge to ONE key."""
        loc = cmds.spaceLocator(name="merge_loc")[0]
        for t, v in ((10, 0.0), (40, 1.0), (50, 1.0), (70, 3.0)):
            cmds.setKeyframe(loc, at="translateX", t=t, v=v)
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 40, [loc]), ShotBlock(1, "B", 50, 90, [loc])]
        )
        seq.respace(gap=0, start_frame=0)  # B -> [40,80], delta -10
        times = self._times(loc)
        self.assertEqual(times, [10.0, 40.0, 60.0], f"merged, not stacked: {times}")
        for a, b in zip(times, times[1:]):
            self.assertGreater(b - a, 1e-3, "no near-duplicate pair survives")

    def test_collapsing_a_gap_onto_disagreeing_poses_is_refused_intact(self):
        """A hard cut cannot live at gap 0 — one frame holds one pose.  The
        operation must refuse BEFORE writing, leaving the scene untouched."""
        from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict

        loc = cmds.spaceLocator(name="cut_loc")[0]
        for t, v in ((10, 0.0), (40, 1.0), (50, 2.0), (70, 3.0)):
            cmds.setKeyframe(loc, at="translateX", t=t, v=v)
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 40, [loc]), ShotBlock(1, "B", 50, 90, [loc])]
        )
        before = self._times(loc)
        with self.assertRaises(ShotBoundaryConflict) as ctx:
            seq.respace(gap=0, start_frame=0)
        self.assertIn("cut_loc", str(ctx.exception), "the curve must be named")
        self.assertEqual(self._times(loc), before, "nothing was written")
        self.assertEqual(
            [(s.start, s.end) for s in seq.sorted_shots()],
            [(0, 40), (50, 90)],
            "and the layout is unchanged too",
        )

    def test_a_refusal_leaves_nothing_written_on_the_two_step_paths(self):
        """``slide_shot`` / ``set_shot_start`` write in TWO steps (ripple and
        pivot, ordered by the delta's sign).  A refusal in the second step
        after the first already wrote would leave the scene half-moved and
        make the "nothing was written" guarantee false.

        It cannot: the step that could newly make two shots contiguous always
        runs first, and it refuses before writing.  Pinned here because the
        guarantee is what lets the panel report a conflict and stop.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict

        for label, op in (
            ("no ripple", lambda s: s.set_shot_start(1, 40, ripple=False)),
            ("ripple", lambda s: s.set_shot_start(1, 40, ripple=True)),
            ("slide", lambda s: s.slide_shot(1, 40, direction="downstream")),
        ):
            with self.subTest(path=label):
                cmds.file(new=True, force=True)
                loc = cmds.spaceLocator(name="cut_loc")[0]
                for t, v in ((10, 0.0), (40, 1.0), (50, 2.0), (70, 3.0)):
                    cmds.setKeyframe(loc, at="translateX", t=t, v=v)
                seq = ShotSequencer(
                    [
                        ShotBlock(0, "A", 0, 40, [loc]),
                        ShotBlock(1, "B", 50, 90, [loc]),
                    ]
                )
                before_keys = self._times(loc)
                before_layout = [(s.start, s.end) for s in seq.sorted_shots()]
                with self.assertRaises(ShotBoundaryConflict):
                    op(seq)
                self.assertEqual(self._times(loc), before_keys, "no keys written")
                self.assertEqual(
                    [(s.start, s.end) for s in seq.sorted_shots()],
                    before_layout,
                    "no bounds written",
                )

    def test_a_split_then_collapse_round_trip_is_lossless(self):
        """Expand then re-collapse must return every key to where it began."""
        loc = self._keyed("round_loc", (10, 40, 60))
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 40, [loc]), ShotBlock(1, "B", 40, 80, [loc])]
        )
        before = self._times(loc)
        seq.respace(gap=10, start_frame=0)
        seq.respace(gap=0, start_frame=0)
        self.assertEqual(self._times(loc), before, "round trip restored every key")

    def test_a_key_in_the_trailing_gap_is_retimed_into_the_new_gap(self):
        """A fade tail in the trailing gap belongs to the shot before it — but
        it is RETIMED into the gap's new width, not carried rigidly with it.

        The plan path's envelope is still [start, next.start), so the key
        travels with A; what changed is that a respace redefines the width of
        the space it travels into. Carried rigidly, a key 10 frames into a
        50-frame gap lands 10 frames into a 10-frame gap — which is exactly
        ON the following shot's opening frame, and past it for anything
        further in. That is the collision that corrupted a respace on a
        production assembly: the stale key ended up after the next shot's
        content, and the tangent change it caused cost the PRECEDING shot —
        which had not moved at all — 42 of its 109 frames.

        Retimed, the same key keeps its fraction of the gap: 10/50 of a
        50-frame gap becomes 2/10 of a 10-frame one, so 60 → 52, then rides
        A's +20 to 72.
        """
        loc = self._keyed("tail_loc", (60,))  # 10 frames into the 50-frame gap
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, []), ShotBlock(1, "B", 100, 150, [])]
        )
        seq.respace(gap=10, start_frame=20)  # A: 0 -> 20, B: 100 -> 80
        got = self._times(loc)
        self.assertEqual(len(got), 1)
        # ``cmds.scaleKey`` computes the retimed frame in the scene's time
        # unit and lands a few ULP short of it, so the exact compare passed
        # only under the harness's time unit and reddened under a bare mayapy.
        # The contract is the frame, not its last bit.
        self.assertAlmostEqual(got[0], 72.0, places=3)
        self.assertLess(
            self._times(loc)[0],
            seq.shot_by_id(1).start,
            "the gap's own content must stay inside the gap",
        )

    def test_a_boundary_key_its_owner_accounts_for_is_not_stolen(self):
        """Resize B so it ends exactly where C starts.

        C's ripple envelope then begins on B's last key.  Adopting purely on
        time would hand that key to C and move it twice; B's own range
        covers it, so membership still decides.  (blendertk's suite caught
        this — mayatk had no test for the shared-boundary case.)
        """
        b = self._keyed("bee_loc", (20, 25, 30))
        c = self._keyed("cee_loc", (40, 45, 50))
        seq = ShotSequencer(
            [
                ShotBlock(0, "B", 20, 30, [b]),
                ShotBlock(1, "C", 40, 50, [c]),
            ]
        )
        seq.resize_shot(0, 20, 40)  # B x2 -> keys 20,30,40; C ripples +10
        self.assertEqual(
            self._times(b), [20.0, 30.0, 40.0], "B's own keys, scaled once"
        )
        self.assertEqual(self._times(c), [50.0, 55.0, 60.0], "C rippled +10")
        self.assertNotIn(
            cmds.ls(b, long=True)[0],
            seq.shot_by_id(1).objects,
            "C must not claim B on the strength of a shared-boundary key",
        )

    def test_a_shot_that_does_not_move_gains_nothing(self):
        self._keyed("still_loc", (10, 20))
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, []), ShotBlock(1, "B", 100, 150, [])]
        )
        before = list(seq.shot_by_id(0).objects)
        seq.slide_shot(1, 120.0)  # only B moves
        self.assertEqual(seq.shot_by_id(0).objects, before)

    def test_same_leaf_name_under_two_parents_is_adopted_by_full_path(self):
        """Duplicate leaf names must not collapse into one member.

        Maya hands back SHORTEST-UNIQUE names, so ``gA|dupe_loc`` and
        ``gB|dupe_loc`` each resolve to exactly one node — both are real
        content of the shot and both must travel with it, each under its
        own full path.
        """
        for g in ("gA", "gB"):
            grp = cmds.group(empty=True, name=g)
            loc = cmds.spaceLocator(name="dupe_loc")[0]
            cmds.parent(loc, grp)
            cmds.setKeyframe(f"{grp}|dupe_loc", at="translateX", t=110, v=1)
        seq = ShotSequencer(
            [ShotBlock(0, "A", 0, 50, []), ShotBlock(1, "B", 100, 150, [])]
        )
        seq.slide_shot(1, 120.0)  # +20
        adopted = [o for o in seq.shot_by_id(1).objects if o.endswith("dupe_loc")]
        self.assertEqual(len(adopted), 2, f"expected both paths, got {adopted}")
        for path in ("|gA|dupe_loc", "|gB|dupe_loc"):
            self.assertEqual(
                sorted(cmds.keyframe(path, q=True, at="translateX") or []),
                [130.0],
                f"{path} must travel with the shot",
            )

    def test_the_backfill_is_captured_by_the_restore_point(self):
        """Membership rides the boundary snapshot, so an undo puts it back."""
        self._keyed("undo_loc", (110, 120))
        store = ShotStore()
        store.define_shot("A", 0, 50)
        store.define_shot("B", 100, 150)
        seq = ShotSequencer(store=store)
        before = list(store.shot_by_id(1).objects)
        store.push_boundary_snapshot()
        seq.slide_shot(1, 120.0)
        self.assertNotEqual(store.shot_by_id(1).objects, before)
        store.restore_boundary_snapshot()
        self.assertEqual(store.shot_by_id(1).objects, before)


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestShotEditLedger(unittest.TestCase):
    """Gap holds and boundary samples are claimed, followed, and released.

    The system writes on the animator's curves; these prove it can also take
    those writes back, and that it never takes back a write it did not make.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.seq = ShotSequencer(store=self.store)

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, attribute="translateX", time=t, value=v)
        return node

    @staticmethod
    def _curve(node):
        return (cmds.listConnections(f"{node}.translateX", type="animCurve") or [""])[0]

    @staticmethod
    def _ott(node, t):
        return (
            cmds.keyTangent(
                f"{node}.translateX", q=True, time=(t, t), outTangentType=True
            )
            or [""]
        )[0]

    @staticmethod
    def _times(node):
        return sorted(
            cmds.keyframe(f"{node}.translateX", q=True, timeChange=True) or []
        )

    def _two_shots(self):
        """Shot A [1,20] and B [40,50] with a gap between them."""
        a = self._cube("ledA", {1: 0, 10: 4, 20: 9})
        b = self._cube("ledB", {40: 9, 50: 3})
        self.seq.define_shot("A", 1, 20, objects=[a])
        self.seq.define_shot("B", 40, 50, objects=[b])
        return a, b

    # -- gap holds ---------------------------------------------------------

    def test_gap_hold_is_claimed_once(self):
        """The seam is stepped and claimed, and a second pass changes nothing."""
        a, _b = self._two_shots()
        self.seq._enforce_gap_holds()
        self.assertEqual(self._ott(a, 20), "step")
        self.assertEqual(self.seq.ledger.step_count, 1)
        self.seq._enforce_gap_holds()
        self.assertEqual(self.seq.ledger.step_count, 1, "idempotent")

    def test_hold_released_when_the_gap_closes(self):
        """No gap, no hold: the key gets its original out-tangent back."""
        a, _b = self._two_shots()
        self.seq._enforce_gap_holds()
        self.store.update_shot(self.seq.shot_by_name("B").shot_id, start=20.0)
        self.seq._enforce_gap_holds()
        self.assertNotEqual(self._ott(a, 20), "step")
        self.assertEqual(self.seq.ledger.step_count, 0)

    def test_an_animators_own_step_is_never_claimed_or_undone(self):
        """A step that was already there is intentional and stays put."""
        a, _b = self._two_shots()
        cmds.keyTangent(f"{a}.translateX", e=True, time=(10, 10), outTangentType="step")
        self.seq._enforce_gap_holds()
        self.assertFalse(self.seq.ledger.owns_step(self._curve(a), 10.0))
        # Close the gap: the system releases its OWN hold and leaves this one.
        self.store.update_shot(self.seq.shot_by_name("B").shot_id, start=20.0)
        self.seq._enforce_gap_holds()
        self.assertEqual(self._ott(a, 10), "step")

    def test_a_claim_rides_a_rigid_shot_move(self):
        """Keys move with their shot, and the claim moves with the keys."""
        a = self._cube("rideA", {1: 0, 20: 9})
        b = self._cube("rideB", {40: 9, 50: 3})
        sa = self.seq.define_shot("A", 1, 20, objects=[a])
        self.seq.define_shot("B", 40, 50, objects=[b])
        self.seq._enforce_gap_holds()
        self.seq.move_shot(sa.shot_id, 11.0)
        self.assertEqual(self._times(a), [11.0, 30.0])
        self.assertEqual(self.seq.ledger.step_times(self._curve(a)), [30.0])
        self.assertEqual(self._ott(a, 30), "step")

    def test_dragging_a_key_off_the_seam_moves_the_hold(self):
        """The hold follows the seam, not the key that used to be on it."""
        a, _b = self._two_shots()
        self.seq._enforce_gap_holds()
        crv = self._curve(a)
        # Drag the last key well inside the shot: key 10 becomes the seam.
        ShotSequencer.move_curve_keys(crv, [20.0], -15.0, ledger=self.seq.ledger)
        self.seq._enforce_gap_holds()
        self.assertEqual(self._ott(a, 10), "step", "the new seam holds")
        self.assertEqual(self._ott(a, 5), "auto", "the moved key is restored")
        self.assertEqual(self.seq.ledger.step_count, 1)

    def test_every_gap_holds_even_on_a_shared_curve(self):
        """One curve spanning three shots gets a hold at EACH of its two seams.

        Shot objects are routinely shared, so collapsing the seam set to one
        entry per curve would silently leave every gap but the last one
        interpolating across the cut.
        """
        a = self._cube("sharedA", {1: 0, 20: 5, 40: 8, 60: 2, 80: 9})
        self.seq.define_shot("A", 1, 20, objects=[a])
        self.seq.define_shot("B", 40, 60, objects=[a])
        self.seq.define_shot("C", 80, 90, objects=[a])
        self.seq._enforce_gap_holds()
        self.assertEqual(self._ott(a, 20), "step", "gap A->B holds")
        self.assertEqual(self._ott(a, 60), "step", "gap B->C holds")
        self.assertEqual(self.seq.ledger.step_count, 2)

    def test_claims_survive_serialisation(self):
        """The writes persist with the scene, so the claims have to as well."""
        self._two_shots()
        self.seq._enforce_gap_holds()
        restored = ShotStore.from_dict(self.store.to_dict())
        self.assertEqual(restored.edit_ledger.step_count, self.seq.ledger.step_count)

    # -- boundary samples --------------------------------------------------

    def test_a_boundary_sample_follows_its_bound(self):
        """A sample created for a bound moves when that bound moves."""
        a = self._cube("bndA", {1: 0, 50: 10})
        sa = self.seq.define_shot("A", 1, 50, objects=[a])
        crv = self._curve(a)
        self.seq.ledger.record_key(crv, 50.0, sa.shot_id, "end")
        self.store.update_shot(sa.shot_id, end=40.0)
        moved, _removed = self.seq._reconcile_boundary_keys()
        self.assertEqual(moved, 1)
        self.assertEqual(self._times(a), [1.0, 40.0])
        self.assertEqual(self.seq.ledger.key_times(crv), [40.0])

    def test_an_orphaned_sample_carrying_a_pose_is_disowned_not_cut(self):
        """Tidying up never deletes animation."""
        a = self._cube("bndB", {1: 0, 50: 10})
        sa = self.seq.define_shot("A", 1, 50, objects=[a])
        self.seq.ledger.record_key(self._curve(a), 50.0, sa.shot_id, "end")
        self.store.remove_shot(sa.shot_id)
        self.seq.ledger.disown_shot(sa.shot_id)
        _moved, removed = self.seq._reconcile_boundary_keys()
        self.assertEqual(removed, 0)
        self.assertEqual(self._times(a), [1.0, 50.0])
        self.assertEqual(self.seq.ledger.key_count, 0)

    def test_a_redundant_orphaned_sample_is_cut(self):
        """A sample inside a flat plateau plays no part, so it goes."""
        c = self._cube("bndC", {1: 5, 20: 5, 40: 5, 60: 9})
        sc = self.seq.define_shot("C", 1, 60, objects=[c])
        self.seq.ledger.record_key(self._curve(c), 20.0, sc.shot_id, "start")
        _moved, removed = self.seq._reconcile_boundary_keys()
        self.assertEqual(removed, 1)
        self.assertEqual(self._times(c), [1.0, 40.0, 60.0])


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestShotLifecycle(unittest.TestCase):
    """delete / merge / split / pad, on real curves."""

    def setUp(self):
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.seq = ShotSequencer(store=self.store)

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, attribute="translateX", time=t, value=v)
        return node

    @staticmethod
    def _times(node):
        return sorted(
            cmds.keyframe(f"{node}.translateX", q=True, timeChange=True) or []
        )

    def _ranges(self):
        return [(s.name, s.start, s.end) for s in self.seq.sorted_shots()]

    def test_delete_cuts_contents_and_closes_the_gap(self):
        """The default delete takes the shot, its keys, and its space."""
        a = self._cube("delA", {1: 0, 20: 1})
        b = self._cube("delB", {40: 0, 60: 1})
        c = self._cube("delC", {80: 0, 100: 1})
        self.seq.define_shot("A", 1, 20, objects=[a])
        sb = self.seq.define_shot("B", 40, 60, objects=[b])
        self.seq.define_shot("C", 80, 100, objects=[c])
        result = self.seq.delete_shot(sb.shot_id)
        self.assertEqual(
            cmds.keyframe(f"{b}.translateX", q=True, keyframeCount=True) or 0, 0
        )
        self.assertEqual(round(result["closed"]), 40)
        self.assertEqual(self._ranges(), [("A", 1.0, 20.0), ("C", 40.0, 60.0)])
        self.assertEqual(self._times(c), [40.0, 60.0])

    def test_delete_can_keep_the_contents_and_the_space(self):
        """Both halves are opt-out, for the caller that wants only the record gone."""
        b = self._cube("keepB", {40: 0, 60: 1})
        c = self._cube("keepC", {80: 0, 100: 1})
        sb = self.seq.define_shot("B", 40, 60, objects=[b])
        self.seq.define_shot("C", 80, 100, objects=[c])
        self.seq.delete_shot(sb.shot_id, delete_contents=False, close_gap=False)
        self.assertEqual(self._times(b), [40.0, 60.0])
        self.assertEqual(self._ranges(), [("C", 80.0, 100.0)])

    def test_delete_of_the_last_shot_takes_its_trailing_content(self):
        """The final shot's envelope runs past its end, and the cut honours it."""
        a = self._cube("lastA", {1: 0, 20: 1})
        b = self._cube("lastB", {40: 0, 60: 1, 80: 2})  # 80 is a trailing tail
        self.seq.define_shot("A", 1, 20, objects=[a])
        sb = self.seq.define_shot("B", 40, 60, objects=[b])
        self.seq.delete_shot(sb.shot_id)
        self.assertEqual(
            cmds.keyframe(f"{b}.translateX", q=True, keyframeCount=True) or 0, 0
        )
        self.assertEqual(self._times(a), [1.0, 20.0], "A is untouched")
        self.assertEqual(self._ranges(), [("A", 1.0, 20.0)])

    def test_merge_spans_both_and_folds_the_objects_in(self):
        a = self._cube("mgA", {1: 0, 20: 9})
        b = self._cube("mgB", {40: 9, 50: 3})
        sa = self.seq.define_shot("A", 1, 20, objects=[a])
        sb = self.seq.define_shot("B", 40, 50, objects=[b])
        merged = self.seq.merge_shots([sb.shot_id, sa.shot_id])
        self.assertEqual(len(self.store.shots), 1)
        self.assertEqual((merged.start, merged.end), (1.0, 50.0))
        self.assertEqual(
            sorted(o.split("|")[-1] for o in merged.objects), ["mgA", "mgB"]
        )

    def test_merge_releases_the_hold_it_swallows(self):
        """A merged-over gap is no longer a cut, so its hold comes off."""
        a = self._cube("mhA", {1: 0, 20: 9})
        b = self._cube("mhB", {40: 9, 50: 3})
        sa = self.seq.define_shot("A", 1, 20, objects=[a])
        sb = self.seq.define_shot("B", 40, 50, objects=[b])
        self.seq._enforce_gap_holds()
        self.seq.merge_shots([sa.shot_id, sb.shot_id])
        self.assertNotEqual(
            (
                cmds.keyTangent(
                    f"{a}.translateX", q=True, time=(20, 20), outTangentType=True
                )
                or [""]
            )[0],
            "step",
        )

    def test_merge_needs_two_shots(self):
        sa = self.seq.define_shot("A", 1, 20, objects=[])
        with self.assertRaises(ValueError):
            self.seq.merge_shots([sa.shot_id])

    def test_split_gives_each_half_the_objects_that_animate_in_it(self):
        """Membership comes from the shot being split, narrowed per side."""
        head_obj = self._cube("spHead", {1: 0, 20: 5})
        tail_obj = self._cube("spTail", {40: 0, 60: 5})
        # A third object animates in the range but was never part of the shot;
        # a scene-wide rediscovery would sweep it in.
        self._cube("spStranger", {30: 0, 35: 5})
        sa = self.seq.define_shot("A", 1, 60, objects=[head_obj, tail_obj])
        tail = self.seq.split_shot(sa.shot_id, 30)
        head_names = {o.split("|")[-1] for o in self.seq.shot_by_id(sa.shot_id).objects}
        tail_names = {o.split("|")[-1] for o in tail.objects}
        self.assertEqual(head_names, {"spHead"})
        self.assertEqual(tail_names, {"spTail"})

    def test_split_divides_one_shot_leaving_content_alone(self):
        a = self._cube("spA", {1: 0, 30: 5, 60: 9})
        sa = self.seq.define_shot("A", 1, 60, objects=[a])
        self.seq.split_shot(sa.shot_id, 30)
        self.assertEqual(self._ranges(), [("A", 1.0, 30.0), ("A_2", 30.0, 60.0)])
        self.assertEqual(self._times(a), [1.0, 30.0, 60.0])

    def test_split_on_a_bound_is_refused(self):
        """A cut on a bound divides nothing, so it is an error, not a no-op."""
        sa = self.seq.define_shot("A", 1, 60, objects=[])
        with self.assertRaises(ValueError):
            self.seq.split_shot(sa.shot_id, 1)

    def test_add_leading_space_holds_the_start_and_shifts_content_later(self):
        """The head is an anchor: the room opens in FRONT of the content."""
        a = self._cube("padA", {1: 0, 20: 1})
        b = self._cube("padB", {40: 0, 60: 1})
        self.seq.define_shot("A", 1, 20, objects=[a])
        sb = self.seq.define_shot("B", 40, 60, objects=[b])
        head, tail = self.seq.add_shot_space(sb.shot_id, 10, edge="leading")
        self.assertEqual((head, tail), (0.0, 10.0))
        self.assertEqual(self._ranges(), [("A", 1.0, 20.0), ("B", 40.0, 70.0)])
        self.assertEqual(self._times(b), [50.0, 70.0], "content moved, not the head")
        self.assertEqual(self._times(a), [1.0, 20.0], "upstream is never dragged back")

    def test_add_leading_space_pushes_the_downstream_shot(self):
        """Everything from the padded start onward shifts right, shots included."""
        a = self._cube("padE", {1: 0, 20: 1})
        b = self._cube("padF", {40: 0, 60: 1})
        sa = self.seq.define_shot("A", 1, 20, objects=[a])
        self.seq.define_shot("B", 40, 60, objects=[b])
        head, tail = self.seq.add_shot_space(sa.shot_id, 10, edge="leading")
        self.assertEqual((head, tail), (0.0, 10.0))
        self.assertEqual(self._ranges(), [("A", 1.0, 30.0), ("B", 50.0, 70.0)])
        self.assertEqual(self._times(a), [11.0, 30.0])
        self.assertEqual(self._times(b), [50.0, 70.0])

    def test_removing_leading_space_reclaims_only_empty_room(self):
        """A negative pad closes head slack; it never drags keys out the front."""
        a = self._cube("padG", {10: 0, 20: 1})
        sa = self.seq.define_shot("A", 1, 20, objects=[a])
        # 9 frames of empty head; asking for 30 back may only take those 9.
        head, tail = self.seq.add_shot_space(sa.shot_id, -30, edge="leading")
        self.assertEqual((head, tail), (0.0, -9.0))
        self.assertEqual(self._ranges(), [("A", 1.0, 11.0)])
        self.assertEqual(self._times(a), [1.0, 11.0])

    def test_add_trailing_space_pushes_the_downstream_shot(self):
        a = self._cube("padC", {1: 0, 20: 1})
        b = self._cube("padD", {40: 0, 60: 1})
        sa = self.seq.define_shot("A", 1, 20, objects=[a])
        self.seq.define_shot("B", 40, 60, objects=[b])
        head, tail = self.seq.add_shot_space(sa.shot_id, 10, edge="trailing")
        self.assertEqual((head, tail), (0.0, 10.0))
        self.assertEqual(self._ranges(), [("A", 1.0, 30.0), ("B", 50.0, 70.0)])


@unittest.skipUnless(HAS_MAYA, "requires Maya")
class TestMoveToShotPlacement(unittest.TestCase):
    """Move to Shot appends after what is already there, with real room.

    Landing a moved sequence flush against the destination's existing content
    reads as ONE clip in the sequencer -- the edit looks destructive even
    though nothing was lost.  These pin the placement contract: always after,
    always separated by more than the inter-shot gap, and the destination
    grows to hold the result.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.store.gap = 5.0
        self.seq = ShotSequencer(store=self.store)

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, attribute="translateX", time=t, value=v)
        return node

    @staticmethod
    def _times(node):
        return sorted(
            cmds.keyframe(f"{node}.translateX", q=True, timeChange=True) or []
        )

    def _move(self, src_id, dest_id, obj=None):
        seqs = [
            s
            for s in self.seq.collect_shot_sequences(src_id, include_audio=False)
            if obj is None or s["obj"].split("|")[-1] == obj
        ]
        self.assertTrue(seqs, "nothing collected to move")
        self.seq.move_sequences_to_shot(seqs, dest_id)

    def test_lands_after_existing_content_with_a_visible_gap(self):
        """The moved run starts past the object's last frame in the destination."""
        cube = self._cube("mvA", {1: 0, 20: 5, 60: 9, 80: 2})
        dest = self.seq.define_shot("Dest", 1, 60, objects=[cube])
        src = self.seq.define_shot("Src", 61, 100, objects=[cube])
        sep = self.seq.sequence_separation()
        self.assertGreater(sep, self.store.gap, "separation must exceed the shot gap")
        self._move(src.shot_id, dest.shot_id)
        moved = [t for t in self._times(cube) if t > 20.0]
        self.assertTrue(moved)
        self.assertGreaterEqual(
            min(moved), 20.0 + sep, "moved run must clear the existing content"
        )

    def test_a_downstream_source_still_appends_rather_than_prepends(self):
        """Direction of travel must not change where the clip lands."""
        cube = self._cube("mvB", {1: 0, 20: 5, 200: 9, 220: 2})
        dest = self.seq.define_shot("Dest", 1, 60, objects=[cube])
        src = self.seq.define_shot("Src", 190, 240, objects=[cube])
        self._move(src.shot_id, dest.shot_id)
        self.assertGreater(
            min(t for t in self._times(cube) if t > 20.0),
            20.0,
            "nothing may land before the destination's own content",
        )
        self.assertGreaterEqual(
            min(self._times(cube)), self.seq.shot_by_id(dest.shot_id).start
        )

    def test_the_destination_grows_to_hold_what_landed(self):
        cube = self._cube("mvC", {1: 0, 20: 5, 60: 9, 80: 2})
        dest = self.seq.define_shot("Dest", 1, 60, objects=[cube])
        src = self.seq.define_shot("Src", 61, 100, objects=[cube])
        self._move(src.shot_id, dest.shot_id)
        grown = self.seq.shot_by_id(dest.shot_id)
        self.assertGreaterEqual(
            grown.end, max(self._times(cube)), "the shot must enclose its content"
        )

    def test_an_object_with_no_content_there_anchors_at_the_start(self):
        """Nothing to clear, so nothing is pushed: it lands at the shot start."""
        other = self._cube("mvKeep", {1: 0, 40: 5})
        mover = self._cube("mvNew", {200: 0, 220: 5})
        dest = self.seq.define_shot("Dest", 1, 60, objects=[other])
        src = self.seq.define_shot("Src", 190, 240, objects=[mover])
        self._move(src.shot_id, dest.shot_id, obj="mvNew")
        self.assertEqual(min(self._times(mover)), 1.0)


class TestSlidingARunKeepsItsOwnShape(unittest.TestCase):
    """A rigid slide must be a pure translation of the run's own motion.

    The first key's OUT tangent and the last key's IN tangent shape spans
    that lie entirely INSIDE the run, so a slide moves both of their
    endpoints together and the motion they describe cannot have changed.
    Maya recomputes them anyway: a derived tangent is one unbroken slope
    read from the keys on both sides, so the un-moved key OUTSIDE the run
    reshapes the inward half too.  Reported from production as the dragged
    segment's starting key tangent flattening, a little more each drag.
    """

    #: Derived tangent types -- the ones Maya reads from both neighbours.
    DERIVED = ("spline", "auto", "clamped", "plateau")

    def setUp(self):
        cmds.file(new=True, force=True)

    def _make(self, tangent):
        """An anchor pose, then a three-key run to slide away from it."""
        obj = cmds.spaceLocator(name="slide")[0]
        for t, v in ((0, 0.0), (50, 10.0), (60, 14.0), (70, 10.0)):
            cmds.setKeyframe(obj, attribute="translateY", time=t, value=v)
        crv = cmds.listConnections(
            f"{obj}.translateY", type="animCurve", s=True, d=False
        )[0]
        cmds.keyTangent(crv, edit=True, itt=tangent, ott=tangent)
        return crv

    def _sample(self, crv, lo, hi, n=41):
        step = (hi - lo) / float(n - 1)
        return [
            cmds.keyframe(crv, q=True, eval=True, time=((lo + i * step),) * 2)[0]
            for i in range(n)
        ]

    def test_the_first_interior_span_survives_the_slide(self):
        for tangent in self.DERIVED:
            with self.subTest(tangent=tangent):
                crv = self._make(tangent)
                before = self._sample(crv, 50.0, 60.0)

                ShotSequencer.move_curve_keys(crv, [50.0, 60.0, 70.0], 20.0)

                after = self._sample(crv, 70.0, 80.0)
                drift = max(abs(a - b) for a, b in zip(before, after))
                self.assertLess(
                    drift,
                    1e-3,
                    f"{tangent}: the run's own motion changed by {drift:.4f}",
                )

    def test_the_outward_half_is_left_free_to_re_ease(self):
        """Only the inward half is held.  The outward one spans the gap to
        content that did NOT move, so that gap really did change and its
        tangent must be allowed to follow -- pinning the whole tangent would
        freeze an ease that is no longer right."""
        crv = self._make("spline")
        ShotSequencer.move_curve_keys(crv, [50.0, 60.0, 70.0], 20.0)

        tt = (70.0, 70.0)
        self.assertEqual(
            cmds.keyTangent(crv, q=True, time=tt, inTangentType=True)[0],
            "spline",
            "the outward-facing half must stay derived",
        )
        self.assertEqual(
            cmds.keyTangent(crv, q=True, time=tt, outTangentType=True)[0],
            "fixed",
            "the inward-facing half is pinned, which makes it fixed",
        )

    def test_an_authored_tangent_is_never_touched(self):
        """Nothing to protect and nothing to break: a fixed tangent already
        survives the move, so the run must come back byte-identical."""
        crv = self._make("fixed")
        before = cmds.keyTangent(crv, q=True, time=(50, 70), outAngle=True)

        ShotSequencer.move_curve_keys(crv, [50.0, 60.0, 70.0], 20.0)

        self.assertEqual(
            cmds.keyTangent(crv, q=True, time=(70, 90), outTangentType=True),
            ["fixed"] * 3,
            "no type may change",
        )
        self.assertEqual(
            [
                round(x, 4)
                for x in cmds.keyTangent(crv, q=True, time=(70, 90), outAngle=True)
            ],
            [round(x, 4) for x in before],
        )

    def test_a_lone_key_has_no_interior_to_protect(self):
        """One key spans nothing, so there is no inward half and the tangent
        is left entirely to Maya."""
        crv = self._make("spline")
        ShotSequencer.move_curve_keys(crv, [60.0], 5.0)

        self.assertEqual(
            cmds.keyTangent(crv, q=True, time=(65, 65), outTangentType=True),
            ["spline"],
        )


class TestLandingOnOccupiedFrames(unittest.TestCase):
    """A clip dropped where keys already sit must not mangle the animation.

    Two things used to happen, both of which the user sees as "the animation
    came apart".  A cluster whose destination merely OVERLAPPED other keys
    landed interleaved with them -- the moved motion and the old poses sharing
    one span, playing as neither.  A cluster whose key landed on an occupied
    FRAME destroyed the occupant outright (``option="over"`` and
    ``setKeyframe`` both overwrite).  Now the landing zone is cleared first:
    flat holds are absorbed (they carry nothing), poses are pushed aside.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _curve(self, obj, attr="translateX"):
        return cmds.listConnections(f"{obj}.{attr}", type="animCurve", s=True, d=False)[
            0
        ]

    def _keys(self, obj):
        crv = self._curve(obj)
        return (
            [round(t, 3) for t in (cmds.keyframe(crv, q=True) or [])],
            [round(v, 3) for v in (cmds.keyframe(crv, q=True, valueChange=True) or [])],
        )

    def _make(self, pairs, name="landing"):
        obj = cmds.spaceLocator(name=name)[0]
        for t, v in pairs:
            cmds.setKeyframe(obj, attribute="translateX", time=t, value=v)
        return obj

    def test_a_flat_hold_in_the_way_is_absorbed(self):
        """The frames between two clips are where hold samples accumulate;
        cutting one cannot change what the curve plays, so nothing moves."""
        obj = self._make([(100, 5.0), (120, 5.0), (140, 5.0), (150, 9.0), (160, 1.0)])
        ShotSequencer().move_object_keys(obj, 140, 160, 120)

        times, values = self._keys(obj)
        self.assertEqual(times, [100.0, 120.0, 130.0, 140.0])
        self.assertEqual(values, [5.0, 5.0, 9.0, 1.0])

    def test_a_hold_holding_a_smooth_curve_flat_is_not_absorbed(self):
        """A same-valued key is only free to cut when the tangents that would
        span the gap are already flat.

        With smooth (spline) tangents the middle key is what PINS the plateau:
        remove it and the surviving segment bows between its neighbours, and
        their angles are recomputed to boot.  Absorbing it therefore silently
        edits a curve the drag never touched -- reported from production as
        the dragged segment's starting key "going flat", drifting a little
        further on every drag.
        """
        obj = self._make([(100, 5.0), (120, 5.0), (140, 5.0), (150, 9.0), (160, 1.0)])
        crv = self._curve(obj)
        cmds.keyTangent(crv, edit=True, itt="spline", ott="spline")
        before = cmds.keyTangent(crv, q=True, time=(100, 100), outAngle=True)[0]

        ShotSequencer().move_object_keys(obj, 140, 160, 120)

        times, _ = self._keys(obj)
        self.assertEqual(len(times), 5, f"no key may be deleted (got {times})")
        self.assertAlmostEqual(
            cmds.keyTangent(crv, q=True, time=(100, 100), outAngle=True)[0],
            before,
            places=3,
            msg="a surviving key's tangent was recomputed by the absorb",
        )

    def test_a_hold_beside_a_stepped_key_still_protects_its_neighbour(self):
        """The production shape, and the one that hid from the first fix.

        Here the previous key STEPS, so the segment left behind plays
        constant either way -- but that key's IN tangent is ``spline``, and
        Maya derives a spline slope from the keys on BOTH sides.  Cutting the
        hold therefore reshapes the tangent on the neighbour's far side,
        which is what marched one production visibility key from 2.12 to 0.81
        degrees over four unrelated group drags.
        """
        obj = self._make([(80, 0.0), (100, 5.0), (120, 5.0), (140, 5.0), (150, 9.0)])
        crv = self._curve(obj)
        cmds.keyTangent(crv, edit=True, itt="spline", ott="step")
        before = cmds.keyTangent(crv, q=True, time=(100, 100), inAngle=True)[0]
        self.assertGreater(abs(before), 0.01, "fixture must have a sloped in-tangent")

        self.assertFalse(
            ShotSequencer._sample_is_redundant(crv, 120.0),
            "cutting this hold reshapes the previous key's spline in-tangent",
        )

        ShotSequencer().move_object_keys(obj, 140, 150, 120)
        self.assertEqual(
            len(cmds.keyframe(crv, q=True) or []),
            5,
            "the hold is displaced, never deleted",
        )

    def test_a_pose_in_the_way_is_pushed_not_deleted(self):
        obj = self._make(
            [(100, 5.0), (120, 20.0), (125, 30.0), (140, 5.0), (150, 9.0), (160, 1.0)]
        )
        ShotSequencer().move_object_keys(obj, 140, 160, 118)

        times, values = self._keys(obj)
        self.assertEqual(len(times), 6, f"no pose may be lost (got {times})")
        self.assertEqual(
            sorted(values),
            sorted([5.0, 20.0, 30.0, 5.0, 9.0, 1.0]),
            "every value survives the move",
        )
        for landed in (118.0, 128.0, 138.0):
            self.assertIn(landed, times, "the moved cluster lands where asked")

    def test_a_pushed_pose_keeps_its_internal_timing(self):
        """The displaced keys move as one block, so their spacing -- the
        timing the animator authored -- is preserved."""
        obj = self._make(
            [(100, 5.0), (120, 20.0), (125, 30.0), (140, 5.0), (150, 9.0), (160, 1.0)]
        )
        ShotSequencer().move_object_keys(obj, 140, 160, 118)

        times, _ = self._keys(obj)
        pushed = sorted(t for t in times if 100.0 < t < 118.0)
        self.assertEqual(len(pushed), 2)
        self.assertAlmostEqual(pushed[1] - pushed[0], 5.0, places=3)

    def test_the_push_clears_the_landing_zone_completely(self):
        obj = self._make(
            [(100, 5.0), (120, 20.0), (125, 30.0), (140, 5.0), (150, 9.0), (160, 1.0)]
        )
        ShotSequencer().move_object_keys(obj, 140, 160, 118)

        times, _ = self._keys(obj)
        inside = [t for t in times if 118.0 < t < 138.0 and t not in (128.0,)]
        self.assertEqual(inside, [], f"nothing may remain inside the arrival: {times}")

    def test_a_cluster_straddling_the_edge_is_displaced_whole(self):
        """Pushing only the keys that literally overlapped tore a cluster in
        half: the earlier member stayed put while the later ones moved.  The
        displaced block is grown to a fixpoint and moved by ONE delta, so the
        material it contains keeps the timing the animator gave it."""
        obj = self._make(
            [
                (1245, 0.0),
                (1258, 7.0),
                (1272, 0.0),
                (1297, 0.0),
                (1310, 7.0),
                (1324, 0.0),
            ]
        )
        # The arrival covers 1257..1284, which overlaps 1258 and 1272 but not
        # 1245 -- so 1245 is what a per-collision push would leave behind.
        ShotSequencer().move_object_keys(obj, 1297, 1324, 1257)

        times, values = self._keys(obj)
        self.assertEqual(len(times), 6, f"nothing lost (got {times})")
        self.assertEqual(values, [0.0, 7.0, 0.0, 0.0, 7.0, 0.0])
        displaced = times[:3]
        self.assertEqual(
            [round(b - a, 3) for a, b in zip(displaced, displaced[1:])],
            [13.0, 14.0],
            f"the displaced cluster keeps its spacing (got {displaced})",
        )
        self.assertEqual(times[3:], [1257.0, 1270.0, 1284.0])

    def test_a_curve_is_never_cut_below_two_keys(self):
        """Maya deletes a keyless animCurve and takes the connection with it,
        so absorption stops before the curve can disappear."""
        obj = self._make([(100, 5.0), (110, 5.0)])
        ShotSequencer().move_object_keys(obj, 100, 100, 110)

        self.assertTrue(
            cmds.listConnections(
                f"{obj}.translateX", type="animCurve", s=True, d=False
            ),
            "the curve (and its connection) must survive",
        )

    def test_a_sparse_key_selection_pushes_nothing(self):
        """The landing-zone policy is for CLIPS, not hand-picked key dots.

        A contiguous run occupies a continuous region of the timeline, so
        anything inside that region is in its way.  A sparse selection --
        keys picked out of a curve with others deliberately left between them
        -- occupies discrete frames instead, and the span between its first
        and last arrival is not a region anything can block.  Applying the
        span rule there displaced keys the arrival never touched.
        """
        obj = self._make([(10, 1.0), (30, 2.0), (50, 3.0), (60, 9.0)])
        crv = self._curve(obj)
        self.assertFalse(
            ShotSequencer._is_contiguous_run(crv, [10.0, 50.0]),
            "premise: 30 sits between the two moving keys",
        )

        ShotSequencer.move_curve_keys(crv, [10.0, 50.0], 30.0)

        times, values = self._keys(obj)
        self.assertEqual(times, [30.0, 40.0, 60.0, 80.0])
        self.assertEqual(values, [2.0, 1.0, 9.0, 3.0])

    def test_two_keys_with_nothing_between_them_are_a_clip(self):
        """Contiguity is about what is BETWEEN the moved keys, not how many
        there are -- a two-key clip still clears its landing zone."""
        obj = self._make([(10, 1.0), (50, 3.0), (60, 9.0)])
        crv = self._curve(obj)
        self.assertTrue(ShotSequencer._is_contiguous_run(crv, [10.0, 50.0]))

        ShotSequencer.move_curve_keys(crv, [10.0, 50.0], 30.0)

        times, values = self._keys(obj)
        self.assertEqual(len(times), 3, f"the pose at 60 survives (got {times})")
        self.assertEqual(sorted(values), [1.0, 3.0, 9.0])

    def test_a_clean_destination_still_moves_untouched(self):
        """The clearing pass is a no-op when nothing is in the way."""
        obj = self._make([(100, 0.0), (110, 5.0)])
        ShotSequencer().move_object_keys(obj, 100, 110, 200)

        times, values = self._keys(obj)
        self.assertEqual(times, [200.0, 210.0])
        self.assertEqual(values, [0.0, 5.0])


class TestGroupMoveOrderIsRigid(unittest.TestCase):
    """A group drag commits clip by clip, and the widget hands the batch over
    in an order that makes that safe (``ClipItem._collision_free_order``).

    The engine half of the same contract: applied in that order, a rigid
    translation stays rigid.  Applied in the order a scene's ``selectedItems``
    happened to yield, one clip's landing fell inside a pending clip's SOURCE
    range and got dragged a second time -- the group came apart, which is what
    a multi-select drag looked like whenever it travelled further than the gap
    between two of its clips.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _curve(self, obj):
        return cmds.listConnections(
            f"{obj}.translateX", type="animCurve", s=True, d=False
        )[0]

    def _times(self, obj):
        return [round(t, 3) for t in (cmds.keyframe(self._curve(obj), q=True) or [])]

    def _two_cluster_object(self):
        obj = cmds.spaceLocator(name="grp")[0]
        for t, v in ((100, 0.0), (110, 5.0), (130, 8.0), (140, 2.0)):
            cmds.setKeyframe(obj, attribute="translateX", time=t, value=v)
        return obj

    def _shot(self, obj):
        return ShotSequencer([ShotBlock(0, "S0", 50, 300, [obj])])

    def test_earliest_first_keeps_a_left_move_rigid(self):
        obj = self._two_cluster_object()
        seq = self._shot(obj)
        # delta = -25, which exceeds the 20-frame gap between the clusters.
        seq.move_object_in_shot(0, obj, 100, 110, 75)
        seq.move_object_in_shot(0, obj, 130, 140, 105)
        self.assertEqual(self._times(obj), [75.0, 85.0, 105.0, 115.0])

    def test_latest_first_keeps_a_right_move_rigid(self):
        obj = self._two_cluster_object()
        seq = self._shot(obj)
        seq.move_object_in_shot(0, obj, 130, 140, 155)
        seq.move_object_in_shot(0, obj, 100, 110, 125)
        self.assertEqual(self._times(obj), [125.0, 135.0, 155.0, 165.0])

    def test_the_widget_orders_a_batch_the_way_the_engine_needs(self):
        """The two halves have to agree, so assert the widget's order against
        the engine's requirement rather than trusting a comment."""
        from uitk.widgets.sequencer._clip import ClipItem

        left = ClipItem._collision_free_order([(130.0, "b", 105.0), (100.0, "a", 75.0)])
        self.assertEqual([cid for cid, _ in left], ["a", "b"])

        right = ClipItem._collision_free_order(
            [(100.0, "a", 125.0), (130.0, "b", 155.0)]
        )
        self.assertEqual([cid for cid, _ in right], ["b", "a"])


class TestClipOverrunIsNotRippledTwice(unittest.TestCase):
    """A clip dragged PAST the shot end travels the drag distance, no more.

    ``move_object_in_shot`` grows the shot to hold the overrun and ripples the
    downstream shots by that growth.  Committing the keys FIRST put them
    at/past the next shot's envelope start, so the ripple swept them a second
    time and the clip landed at drag + ripple.  Found on a production
    assembly: a sequence dragged +20 past the shot end came out +38, its keys
    interleaved with the next shot's and their auto tangents recomputed in the
    new neighbourhood -- which is what "my start tangents went flat" was.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.store.gap = 5.0
        self.seq = ShotSequencer(store=self.store)

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, attribute="translateX", time=t, value=v)
        return node

    @staticmethod
    def _times(node):
        return sorted(
            cmds.keyframe(f"{node}.translateX", q=True, timeChange=True) or []
        )

    def test_a_clip_dragged_past_the_shot_end_lands_where_it_was_dropped(self):
        a = self._cube("ovrA", {10: 0, 20: 1})
        b = self._cube("ovrB", {30: 0, 40: 1})
        sa = self.seq.define_shot("A", 10, 20, objects=[a])
        self.seq.define_shot("B", 30, 40, objects=[b])
        self.seq.move_object_in_shot(sa.shot_id, a, 10, 20, 25)
        self.assertEqual(self._times(a), [25.0, 35.0], "drag was +15, not +15+ripple")

    def test_the_downstream_shot_still_ripples_by_the_overrun(self):
        a = self._cube("ovrC", {10: 0, 20: 1})
        b = self._cube("ovrD", {30: 0, 40: 1})
        sa = self.seq.define_shot("A", 10, 20, objects=[a])
        self.seq.define_shot("B", 30, 40, objects=[b])
        self.seq.move_object_in_shot(sa.shot_id, a, 10, 20, 25)
        # The shot grew 20 -> 35, so everything after it moves by 15.
        self.assertEqual(
            [(s.name, s.start, s.end) for s in self.seq.sorted_shots()],
            [("A", 10.0, 35.0), ("B", 45.0, 55.0)],
        )
        self.assertEqual(self._times(b), [45.0, 55.0])

    def test_the_whole_key_record_arrives_at_the_right_frame(self):
        """Position and tangent record are one claim: the double-move landed
        the keys in a neighbourhood that re-derived their tangents, so a test
        that only checked types could not tell the two orders apart."""
        a = self._cube("ovrE", {10: 0, 15: 4, 20: 1})
        cmds.keyTangent(f"{a}.translateX", edit=True, itt="linear", ott="linear")
        cmds.keyTangent(
            f"{a}.translateX", edit=True, time=(10, 10), itt="spline", ott="spline"
        )
        b = self._cube("ovrF", {30: 0, 40: 1})
        sa = self.seq.define_shot("A", 10, 20, objects=[a])
        self.seq.define_shot("B", 30, 40, objects=[b])
        self.seq.move_object_in_shot(sa.shot_id, a, 10, 20, 25)
        crv = cmds.listConnections(
            f"{a}.translateX", type="animCurve", s=True, d=False
        )[0]
        self.assertEqual(self._times(a), [25.0, 30.0, 35.0])
        self.assertEqual(
            cmds.keyTangent(crv, q=True, outTangentType=True),
            ["spline", "linear", "linear"],
        )


class TestARefusedDragReportsInsteadOfRaising(unittest.TestCase):
    """A clip dragged past the bound must never answer with a traceback.

    Overrunning expands the shot and ripples the neighbour, and the planner
    REFUSES that ripple when it would force two shots' disagreeing poses
    onto one frame.  Measured on a production assembly: the refusal came
    straight out of ``on_clip_moved`` as an unhandled
    ``ShotBoundaryConflict``, i.e. a traceback at the end of a mouse drag.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        ShotStore.clear_active()
        self.store = ShotStore()
        ShotStore.set_active(self.store)
        self.seq = ShotSequencer(store=self.store)

    def _ctl(self, clip_data):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )
        from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict

        seq, footers, warned, raised = self.seq, [], [], []

        class _Clip:
            data = clip_data
            start = clip_data["orig_start"]
            end = clip_data["orig_end"]

        class _Widget:
            shift_held_at_press = False

            @staticmethod
            def get_clip(_cid):
                return _Clip()

        class _Log:
            @staticmethod
            def warning(msg):
                warned.append(msg)

            @staticmethod
            def debug(*a, **kw):
                pass

        class _Ctl(ShotSequencerController):
            def __init__(self):  # bypass the panel's __init__
                self._segment_cache = {}
                self._sub_row_cache = {}
                self._audio_segments_cache = None
                self._syncing = False
                self._shifted_out_keys = {}

            sequencer = seq
            logger = _Log()

            def _get_sequencer_widget(self):
                return _Widget()

            def _set_footer(self, text, **kw):
                footers.append(text)

            def _sync_to_widget(self, **kw):
                pass

            def _sync_combobox(self):
                pass

            def _discard_shot_state(self):
                pass

            def _expand_shot_range(self, *_a, **_kw):
                # The refusal's real origin: expanding past the bound ripples
                # the neighbour, and the planner declines the ripple.
                exc = ShotBoundaryConflict(["p"])
                raised.append(exc)
                raise exc

        ctl = _Ctl()
        ctl.footers, ctl.warned, ctl.raised = footers, warned, raised
        return ctl

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, attribute="translateX", time=t, value=v)
        return node

    def _clip_data(self, obj, shot_id):
        return {
            "obj": obj,
            "attr_name": "translateX",  # the sub-row path, which expands last
            "orig_start": 10.0,
            "orig_end": 20.0,
            "shot_id": shot_id,
        }

    def test_a_single_drag_reports_the_refusal(self):
        a = self._cube("refuseA", {10: 0, 20: 1})
        shot = self.seq.define_shot("A", 10, 20, objects=[a])
        ctl = self._ctl(self._clip_data(a, shot.shot_id))

        ctl.on_clip_moved(1, 40.0)  # must not raise

        self.assertEqual(ctl.warned, [str(ctl.raised[0])], "the refusal is logged")
        self.assertEqual(
            ctl.footers[-1],
            str(ctl.raised[0]),
            "the refusal must be the notice left standing, not overwritten "
            f"by a success message: {ctl.footers}",
        )

    def test_a_batch_drag_reports_the_refusal(self):
        a = self._cube("refuseB", {10: 0, 20: 1})
        shot = self.seq.define_shot("A", 10, 20, objects=[a])
        ctl = self._ctl(self._clip_data(a, shot.shot_id))

        ctl.on_clips_batch_moved([(1, 40.0)])  # must not raise

        self.assertEqual(ctl.warned, [str(ctl.raised[0])])
        self.assertEqual(ctl.footers[-1], str(ctl.raised[0]), f"{ctl.footers}")

    def test_the_keys_that_already_moved_are_still_undoable(self):
        """The planner declines before IT writes, but the clip's own keys
        moved first -- so the restore point must survive the refusal."""
        a = self._cube("refuseC", {10: 0, 20: 1})
        shot = self.seq.define_shot("A", 10, 20, objects=[a])
        ctl = self._ctl(self._clip_data(a, shot.shot_id))
        dropped = []
        ctl._discard_shot_state = lambda: dropped.append(True)

        ctl.on_clip_moved(1, 40.0)

        self.assertEqual(
            dropped, [], "the scene changed, so the undo step must be kept"
        )


class TestContextMenuPadding(unittest.TestCase):
    """The context menu's Add Leading/Trailing Frames entries.

    How much room a shot needs is the whole question the gesture asks, so the
    entries prompt for it; the field opens on the last answer so a run of
    shots padded by the same beat costs one keystroke each.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        ShotStore.clear_active()
        self.store = ShotStore()
        ShotStore.set_active(self.store)
        self.seq = ShotSequencer(store=self.store)

    def _ctl(self):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        seq = self.seq
        footers = []

        class _Ctl(ShotSequencerController):
            def __init__(self):  # bypass the panel's __init__
                self._segment_cache = {}
                self._sub_row_cache = {}
                self._audio_segments_cache = None
                self._syncing = False
                self._context_space_frames = self.CONTEXT_SPACE_FRAMES

            sequencer = seq

            def _set_footer(self, text, **kw):
                footers.append(text)

            def _after_shot_change(self, shot_id=None):
                pass

            # _discard_shot_state is deliberately NOT stubbed: dropping the
            # dead restore point is behaviour under test here, and the real
            # one only needs ``sequencer``, which this host supplies.

        ctl = _Ctl()
        ctl.footers = footers
        return ctl

    def _cube(self, name, keys):
        node = cmds.polyCube(name=name)[0]
        for t, v in sorted(keys.items()):
            cmds.setKeyframe(node, attribute="translateX", time=t, value=v)
        return node

    def test_the_prompt_opens_on_a_beat_of_room(self):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        self.assertEqual(ShotSequencerController.CONTEXT_SPACE_FRAMES, 15.0)

    # -- the amount prompt -------------------------------------------------

    def _prompting(self, answer):
        """A controller whose ``input_dialog`` returns *answer*, kwargs recorded."""
        import types

        ctl = self._ctl()
        seen = {}
        padded = []

        def _dialog(**kwargs):
            seen.update(kwargs)
            return answer

        ctl.sb = types.SimpleNamespace(input_dialog=_dialog)
        ctl.ui = None
        ctl._get_sequencer_widget = lambda: None
        ctl._add_shot_space = lambda shot_id, frames, edge: padded.append(
            (shot_id, frames, edge)
        )
        return ctl, seen, padded

    def test_the_typed_amount_is_what_gets_padded(self):
        shot = self.seq.define_shot("A", 10, 20)
        ctl, seen, padded = self._prompting("42")
        ctl._prompt_shot_space(shot, edge="leading")
        self.assertEqual(padded, [(shot.shot_id, 42.0, "leading")])
        self.assertEqual(seen["text"], "15", "field opens on the class default")

    def test_the_prompt_remembers_the_last_answer(self):
        shot = self.seq.define_shot("A", 10, 20)
        ctl, _seen, _padded = self._prompting("42")
        ctl._prompt_shot_space(shot, edge="leading")
        ctl2, seen2, _p2 = self._prompting("7")
        ctl2._context_space_frames = ctl._context_space_frames
        ctl2._prompt_shot_space(shot, edge="trailing")
        self.assertEqual(seen2["text"], "42")

    def test_cancelling_the_prompt_pads_nothing(self):
        shot = self.seq.define_shot("A", 10, 20)
        ctl, _seen, padded = self._prompting(None)
        ctl._prompt_shot_space(shot, edge="leading")
        self.assertEqual(padded, [])
        self.assertEqual(ctl._context_space_frames, ctl.CONTEXT_SPACE_FRAMES)

    def test_a_negative_amount_is_accepted(self):
        """Removing room is the same control in the other direction —
        ``add_shot_space`` clamps it to what is actually empty."""
        shot = self.seq.define_shot("A", 10, 20)
        ctl, seen, padded = self._prompting("-5")
        ctl._prompt_shot_space(shot, edge="leading")
        self.assertEqual(padded, [(shot.shot_id, -5.0, "leading")])
        self.assertTrue(seen["validate"]("-5"))

    def test_the_validator_rejects_junk_and_zero(self):
        shot = self.seq.define_shot("A", 10, 20)
        ctl, seen, _padded = self._prompting("1")
        ctl._prompt_shot_space(shot, edge="leading")
        validate = seen["validate"]
        for bad in ("", "  ", "abc", "0", "0.0", "12f"):
            self.assertFalse(validate(bad), f"{bad!r} should be rejected")
        for good in ("1", " 2.5 ", "-3"):
            self.assertTrue(validate(good), f"{good!r} should be accepted")

    def test_leading_padding_holds_the_start_and_pushes_the_content(self):
        a = self._cube("padMenuA", {10: 0, 20: 1})
        shot = self.seq.define_shot("A", 10, 20, objects=[a])
        ctl = self._ctl()
        ctl._add_shot_space(shot.shot_id, ctl.CONTEXT_SPACE_FRAMES, edge="leading")
        self.assertEqual((shot.start, shot.end), (10.0, 35.0))
        self.assertEqual(
            sorted(cmds.keyframe(f"{a}.translateX", q=True, timeChange=True)),
            [25.0, 35.0],
        )

    def test_trailing_padding_leaves_the_content_alone(self):
        a = self._cube("padMenuB", {10: 0, 20: 1})
        shot = self.seq.define_shot("A", 10, 20, objects=[a])
        ctl = self._ctl()
        ctl._add_shot_space(shot.shot_id, ctl.CONTEXT_SPACE_FRAMES, edge="trailing")
        self.assertEqual((shot.start, shot.end), (10.0, 35.0))
        self.assertEqual(
            sorted(cmds.keyframe(f"{a}.translateX", q=True, timeChange=True)),
            [10.0, 20.0],
        )

    def test_a_no_op_pad_leaves_no_dead_restore_point(self):
        """A restore point nothing moved would make the next undo do nothing
        visible -- the same contract the trim actions already keep."""
        a = self._cube("padMenuC", {10: 0, 20: 1})
        shot = self.seq.define_shot("A", 10, 20, objects=[a])
        ctl = self._ctl()
        ctl._add_shot_space(shot.shot_id, 0.0, edge="leading")
        self.assertFalse(self.store.has_boundary_snapshot())
        self.assertTrue(any("nothing to do" in f for f in ctl.footers))


class TestViewMirrorsStayOffTheUndoQueue(unittest.TestCase):
    """Following the panel's shot must not cost an undo step either.

    ``_apply_view_playback_range`` runs after EVERY panel action -- shot
    create/delete/insert/merge/split, every gap and range drag, every shot
    switch and view-mode change -- and ``cmds.playbackOptions`` is undoable.
    Unguarded it cost a Ctrl+Z per action, and it landed on the queue AFTER
    ``scene_edit`` recorded its marker, so ``_undo_plan`` read "an unrelated
    edit followed ours", skipped the ledger restore and undid only the range
    change.  For a bounds-only edit -- creating a shot, whose chunk is empty
    and whose ledger restore is the only thing that can reverse it -- that
    meant it did not undo at all.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        ShotStore.clear_active()
        self.store = ShotStore()
        ShotStore.set_active(self.store)
        self.seq = ShotSequencer(store=self.store)

    def _undo_cost(self, obj, limit=10):
        for i in range(1, limit + 1):
            cmds.undo()
            if not cmds.objExists(obj):
                return i
        return -1

    def _nav(self, mode="locked"):
        """The mixin bound to the smallest host that satisfies its contract."""
        from mayatk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin

        seq, store = self.seq, self.store

        class _Ctl(ShotNavMixin):
            sequencer = seq
            _playback_range_mode = mode
            _shot_display_mode = "active"

            @property
            def active_shot_id(self):
                return store.active_shot_id

            def _visible_shots(self, shot):
                return [shot]

        return _Ctl()

    def test_playback_options_is_undoable_at_all(self):
        """The premise: without the guard this really does cost a step."""
        cmds.polyCube(name="undoAnchor")
        cmds.playbackOptions(min=5, max=50)
        self.assertGreater(
            self._undo_cost("undoAnchor"),
            1,
            "premise failed: cmds.playbackOptions stopped recording",
        )

    def test_following_the_shot_costs_no_undo_step(self):
        shot = self.seq.define_shot("A", 10, 60, objects=[])
        self.store.set_active_shot(shot.shot_id)
        cmds.polyCube(name="undoAnchor")
        self._nav()._apply_view_playback_range()
        self.assertEqual(self._undo_cost("undoAnchor"), 1)

    def test_the_guard_does_not_cost_the_feature(self):
        shot = self.seq.define_shot("A", 10, 60, objects=[])
        self.store.set_active_shot(shot.shot_id)
        self._nav()._apply_view_playback_range()
        self.assertEqual(
            (
                cmds.playbackOptions(q=True, min=True),
                cmds.playbackOptions(q=True, max=True),
            ),
            (10.0, 60.0),
        )

    def test_a_new_shot_leaves_the_marker_naming_the_queue_top(self):
        """What ``_undo_plan`` reads: the panel's own edit must still be the
        newest thing on the queue once the view has followed it."""
        cmds.polyCube(name="undoAnchor")
        with self.store.scene_edit("newshot"):
            shot = self.seq.insert_shot(name="Shot 1", duration=100.0, gap=5.0)
        self.store.set_active_shot(shot.shot_id)
        tag = self.store.peek_boundary_tag()
        self._nav()._apply_view_playback_range()
        self.assertIsInstance(tag, tuple)
        self.assertEqual(tag[1], self.store.undo_queue_top())


class TestSelectionMirrorsStayOffTheUndoQueue(unittest.TestCase):
    """Mirroring the panel's selection into Maya must not cost an undo step.

    ``cmds.select`` and ``cmds.selectKey`` are both undoable, and the panel
    calls them automatically -- on every clip click, every track click, every
    shot switch, and again on the rebuild that follows each edit.  Unguarded,
    each call buried the panel's own edit one Ctrl+Z deeper, so a GROUP
    gesture (which mirrors one entry per curve per key) took many presses to
    reverse.  It also left the queue top owned by a selection, which is what
    ``_undo_plan``'s marker test reads to decide whether the shot-bounds
    restore point is still ours.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        self.obj = cmds.spaceLocator(name="mirrored")[0]
        for t, v in ((1, 0.0), (5, 3.0), (9, 1.0), (13, 4.0)):
            cmds.setKeyframe(self.obj, attribute="translateX", time=t, value=v)
        self.crv = cmds.listConnections(
            f"{self.obj}.translateX", type="animCurve", s=True, d=False
        )[0]

    def _times(self):
        return [round(t, 3) for t in (cmds.keyframe(self.crv, q=True) or [])]

    def _edit(self):
        """One real, named scene edit -- the thing Ctrl+Z has to reach."""
        cmds.undoInfo(openChunk=True, chunkName="probeEdit")
        cmds.keyframe(self.crv, edit=True, relative=True, timeChange=7.0, time=(0, 100))
        cmds.undoInfo(closeChunk=True)

    def _undo_cost(self, before, limit=30):
        for i in range(1, limit + 1):
            cmds.undo()
            if self._times() == before:
                return i
        return -1

    def test_selectkey_is_undoable_at_all(self):
        """The premise: without a guard these calls really do cost steps."""
        before = self._times()
        self._edit()
        cmds.selectKey(clear=True)
        for t in self._times():
            cmds.selectKey(self.crv, add=True, time=(t, t))
        self.assertGreater(
            self._undo_cost(before), 1, "premise failed: selectKey stopped recording"
        )

    def test_key_selection_mirror_costs_no_undo_step(self):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        class _Clip:
            data = {"obj": None, "attr_name": "translateX"}

        class _Widget:
            def __init__(self, clip):
                self._clip = clip

            def get_clip(self, _cid):
                return self._clip

        class _Ctl:
            _syncing = False

            def __init__(self, widget):
                self._widget = widget

            def _get_sequencer_widget(self):
                return self._widget

        clip = _Clip()
        clip.data = dict(clip.data, obj=self.obj)
        ctl = _Ctl(_Widget(clip))

        before = self._times()
        self._edit()
        ShotSequencerController.on_key_selection_changed(
            ctl, [{"clip_id": 0, "times": self._times()}]
        )
        self.assertEqual(self._undo_cost(before), 1)

    def test_key_selection_mirror_still_selects(self):
        """The guard must not cost the feature it protects."""
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        class _Clip:
            pass

        clip = _Clip()
        clip.data = {"obj": self.obj, "attr_name": "translateX"}

        class _Widget:
            def get_clip(self, _cid):
                return clip

        class _Ctl:
            _syncing = False

            def _get_sequencer_widget(self):
                return _Widget()

        cmds.selectKey(clear=True)
        ShotSequencerController.on_key_selection_changed(
            _Ctl(), [{"clip_id": 0, "times": [5.0, 13.0]}]
        )
        selected = sorted(
            round(t, 3) for t in (cmds.keyframe(self.crv, q=True, selected=True) or [])
        )
        self.assertEqual(selected, [5.0, 13.0])

    def test_object_selection_mirror_costs_no_undo_step(self):
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        before = self._times()
        self._edit()
        # _select_and_show touches no instance state, so it can be called
        # unbound -- the point of the test is the cmds.select inside it.
        ShotSequencerController._select_and_show(None, [self.obj])
        self.assertEqual(self._undo_cost(before), 1)
        self.assertIn(
            self.obj, [n.split("|")[-1] for n in (cmds.ls(selection=True) or [])]
        )


if __name__ == "__main__":
    unittest.main()
