# !/usr/bin/python
# coding=utf-8
"""Tests for the compute-then-commit (PlannedShot) architecture.

Validates that _compute_plan produces correct positions and that
_execute_plan commits them faithfully. These tests run inside Maya
to exercise the real ShotStore and sequencer.
"""
import os
import struct
import sys
import unittest
import wave

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

test_dir = os.path.dirname(os.path.abspath(__file__))
if test_dir not in sys.path:
    sys.path.insert(0, test_dir)

try:
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError("These tests must run inside a Maya session.") from exc

from base_test import MayaTkTestCase
from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderStep,
    BuilderObject,
    ShotManifest,
    PlannedShot,
)
from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

_TEMP_DIR = os.path.join(scripts_dir, "mayatk", "test", "temp_tests")


def _make_wav(name, duration_sec=1.0):
    os.makedirs(_TEMP_DIR, exist_ok=True)
    path = os.path.join(_TEMP_DIR, f"{name}.wav").replace("\\", "/")
    sr = 22050
    n = int(sr * duration_sec)
    data = struct.pack(f"<{n}h", *([0] * n))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data)
    return path


def _step(step_id, objects=None, behaviors=None, audio_name=None, source_path=""):
    """Build a BuilderStep with optional scene/audio objects.

    ``objects`` accepts either a list of names (all get the same
    ``behaviors``) or a dict mapping ``{name: [behavior, ...]}``.
    """
    step = BuilderStep(
        step_id=step_id,
        section="A",
        section_title="Test",
        description="test step",
    )
    step._pass_through = {}
    if isinstance(objects, dict):
        for name, obj_behaviors in objects.items():
            step.objects.append(BuilderObject(name=name, behaviors=list(obj_behaviors)))
    else:
        for name in objects or []:
            obj_behaviors = behaviors or []
            step.objects.append(BuilderObject(name=name, behaviors=list(obj_behaviors)))
    if audio_name:
        step.objects.append(
            BuilderObject(
                name=audio_name,
                kind="audio",
                behaviors=["set_clip"],
                source_path=source_path,
            )
        )
    return step


class TestComputePlan(MayaTkTestCase):
    """Test _compute_plan produces correct positions without store mutation."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.manifest = ShotManifest(self.store)

    def tearDown(self):
        super().tearDown()
        for f in os.listdir(_TEMP_DIR) if os.path.isdir(_TEMP_DIR) else []:
            if f.endswith(".wav"):
                os.remove(os.path.join(_TEMP_DIR, f))

    def test_plan_new_shots_sequential(self):
        """New shots get sequential positions from cursor."""
        steps = [
            _step("S1", objects=["cube1"], behaviors=["fade_in"]),
            _step("S2", objects=["cube2"], behaviors=["fade_out"]),
        ]
        plan = self.manifest._compute_plan(steps)
        creates = [p for p in plan if p.action == "created"]
        self.assertEqual(len(creates), 2)
        # S2 starts where S1 ends
        self.assertAlmostEqual(creates[1].start, creates[0].end, places=1)

    def test_plan_does_not_mutate_store(self):
        """_compute_plan must not create shots in the store."""
        steps = [_step("S1", objects=["cube1"])]
        self.manifest._compute_plan(steps)
        self.assertEqual(len(self.store.shots), 0, "Plan must not touch store")

    def test_plan_with_explicit_ranges(self):
        """User-provided ranges are honored in the plan."""
        steps = [_step("S1", objects=["cube1"])]
        ranges = {"S1": (10.0, 50.0)}
        plan = self.manifest._compute_plan(steps, ranges=ranges)
        creates = [p for p in plan if p.action == "created"]
        self.assertEqual(len(creates), 1)
        self.assertAlmostEqual(creates[0].start, 10.0)
        self.assertAlmostEqual(creates[0].end, 50.0)

    def test_plan_locked_shots_skipped(self):
        """Locked shots appear as 'locked' in the plan."""
        self.store.define_shot(
            name="S1",
            start=1,
            end=30,
            objects=["cube1"],
            metadata={},
            description="locked shot",
        )
        shot = self.store.shots[0]
        shot.locked = True

        steps = [_step("S1", objects=["cube1"])]
        plan = self.manifest._compute_plan(steps)
        self.assertEqual(plan[0].action, "locked")

    def test_plan_removal(self):
        """Shots not in CSV are planned for removal."""
        self.store.define_shot(
            name="OLD",
            start=1,
            end=30,
            objects=["cube1"],
            metadata={},
            description="old",
        )
        steps = [_step("S1", objects=["cube2"])]
        plan = self.manifest._compute_plan(steps, remove_missing=True)
        removed = [p for p in plan if p.action == "removed"]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0].step.step_id, "OLD")

    def test_plan_skip_unchanged(self):
        """Unchanged shots get 'skipped' action."""
        self.store.define_shot(
            name="S1",
            start=1,
            end=30,
            objects=["cube1"],
            metadata={
                "csv_objects": [{"name": "cube1", "kind": "scene"}],
                "behaviors": [
                    {
                        "name": "cube1",
                        "behavior": "fade_in",
                        "kind": "scene",
                        "source_path": "",
                    }
                ],
            },
            description="test step",
        )
        steps = [_step("S1", objects=["cube1"], behaviors=["fade_in"])]
        plan = self.manifest._compute_plan(steps)
        non_removed = [p for p in plan if p.action != "removed"]
        self.assertEqual(non_removed[0].action, "skipped")


class TestExecutePlan(MayaTkTestCase):
    """Test _execute_plan commits planned positions faithfully."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.manifest = ShotManifest(self.store)

    def test_execute_creates_at_planned_positions(self):
        """Store shots match plan positions exactly."""
        steps = [
            _step("S1", objects=["cube1"]),
            _step("S2", objects=["cube2"]),
        ]
        plan = self.manifest._compute_plan(steps)
        self.manifest._execute_plan(plan)

        shots = self.store.sorted_shots()
        self.assertEqual(len(shots), 2)
        creates = [p for p in plan if p.action == "created"]
        for ps, shot in zip(creates, shots):
            self.assertAlmostEqual(
                shot.start, ps.start, places=1, msg=f"{shot.name} start mismatch"
            )
            self.assertAlmostEqual(
                shot.end, ps.end, places=1, msg=f"{shot.name} end mismatch"
            )

    def test_execute_removal(self):
        """Planned removals are committed to the store."""
        self.store.define_shot(
            name="OLD",
            start=1,
            end=30,
            objects=["cube1"],
            metadata={},
            description="old",
        )
        self.assertEqual(len(self.store.shots), 1)
        steps = [_step("S1", objects=["cube2"])]
        plan = self.manifest._compute_plan(steps, remove_missing=True)
        self.manifest._execute_plan(plan, remove_missing=True)
        names = {s.name for s in self.store.shots}
        self.assertNotIn("OLD", names)
        self.assertIn("S1", names)

    def test_roundtrip_update_matches_plan(self):
        """update() (plan+execute) yields same results as plan alone."""
        steps = [
            _step("S1", objects=["cube1"]),
            _step("S2", objects=["cube2"]),
            _step("S3", objects=["cube3"]),
        ]
        # Compute plan on fresh store
        plan = self.manifest._compute_plan(steps)
        plan_positions = {
            p.step.step_id: (p.start, p.end) for p in plan if p.action == "created"
        }

        # Now run full update() which does plan+execute
        actions = self.manifest.update(steps)

        shots = {s.name: s for s in self.store.sorted_shots()}
        for step_id, (exp_start, exp_end) in plan_positions.items():
            self.assertAlmostEqual(
                shots[step_id].start,
                exp_start,
                places=1,
                msg=f"{step_id} start diverged from plan",
            )
            self.assertAlmostEqual(
                shots[step_id].end,
                exp_end,
                places=1,
                msg=f"{step_id} end diverged from plan",
            )

    def test_patched_shot_position_update(self):
        """Existing shot repositioned by user range is updated correctly."""
        self.store.define_shot(
            name="S1",
            start=1,
            end=30,
            objects=["cube1"],
            metadata={
                "csv_objects": [{"name": "cube1", "kind": "scene"}],
                "behaviors": [],
            },
            description="test",
        )
        steps = [_step("S1", objects=["cube1"])]
        ranges = {"S1": (100.0, 200.0)}
        actions = self.manifest.update(steps, ranges=ranges)
        self.assertEqual(actions["S1"], "patched")
        shot = self.store.sorted_shots()[0]
        self.assertAlmostEqual(shot.start, 100.0)
        self.assertAlmostEqual(shot.end, 200.0)

    def test_zero_duration_fallback(self):
        """Zero-duration fallback creates stacked shots correctly."""
        steps = [_step("S1"), _step("S2"), _step("S3")]
        actions = self.manifest.update(steps, zero_duration_fallback=True)
        shots = self.store.sorted_shots()
        self.assertEqual(len(shots), 3)
        for s in shots:
            self.assertEqual(actions[s.name], "created")
        # No two shots should overlap
        for i in range(len(shots) - 1):
            self.assertGreaterEqual(
                shots[i + 1].start,
                shots[i].end,
                f"Shot {shots[i+1].name} overlaps {shots[i].name}",
            )


class TestCumulativeRipple(MayaTkTestCase):
    """Test that cumulative_ripple tracking works in the plan."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.manifest = ShotManifest(self.store)

    def test_new_shots_after_audio_grow_account_for_ripple(self):
        """When an existing shot grows via audio, later new shots
        must shift downstream by the cumulative ripple delta."""
        # Create two existing shots
        self.store.define_shot(
            name="S1",
            start=1,
            end=30,
            objects=["cube1"],
            metadata={
                "csv_objects": [{"name": "cube1", "kind": "scene"}],
                "behaviors": [],
            },
            description="",
        )
        self.store.define_shot(
            name="S2",
            start=30,
            end=60,
            objects=["cube2"],
            metadata={
                "csv_objects": [{"name": "cube2", "kind": "scene"}],
                "behaviors": [],
            },
            description="",
        )

        # Build steps where S1 now has audio that's longer than its
        # current duration. We mock compute_duration for this.
        from unittest.mock import patch

        steps = [
            _step("S1", objects=["cube1"], audio_name="narr_s1"),
            _step("S2", objects=["cube2"]),
            _step("S3", objects=["cube3"]),  # new shot after the two
        ]

        # Mock compute_duration to return 50 frames for audio objects
        # (S1 is currently 29 frames, so it should grow by 21)
        original_compute = compute_duration

        def mock_compute(entries, fallback=30, fps=None):
            for e in entries:
                kind = (
                    getattr(e, "kind", "")
                    if not isinstance(e, dict)
                    else e.get("kind", "")
                )
                if kind == "audio":
                    return 50.0
            return original_compute(entries, fallback=fallback, fps=fps)

        with patch(
            "mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration",
            side_effect=mock_compute,
        ):
            plan = self.manifest._compute_plan(steps)

        # S1 should grow from (1,30) to (1,51) (start + 50)
        s1_plan = next(p for p in plan if p.step.step_id == "S1")
        self.assertAlmostEqual(s1_plan.start, 1.0)
        self.assertAlmostEqual(s1_plan.end, 51.0)
        self.assertGreater(s1_plan.ripple_delta, 0)

        # S2 should be shifted downstream by the ripple
        s2_plan = next(p for p in plan if p.step.step_id == "S2")
        self.assertGreater(
            s2_plan.start, 30.0, "S2 must shift downstream after S1 audio grow"
        )

        # S3 (new) should start after S2's shifted end
        s3_plan = next(p for p in plan if p.step.step_id == "S3")
        self.assertGreaterEqual(
            s3_plan.start, s2_plan.end, "S3 must start after shifted S2"
        )

    def test_no_double_ripple_on_execute(self):
        """Bug: _execute_plan must not call ripple_shift because the plan
        already has absolute positions. Double-shifting would push
        downstream shots twice as far as intended.
        """
        from unittest.mock import patch

        # S1(1-30), S2(30-60), S3(60-90)
        for name, s, e in [("S1", 1, 30), ("S2", 30, 60), ("S3", 60, 90)]:
            self.store.define_shot(
                name=name,
                start=s,
                end=e,
                objects=[f"obj_{name}"],
                metadata={
                    "csv_objects": [{"name": f"obj_{name}", "kind": "scene"}],
                    "behaviors": [],
                },
                description="",
            )

        steps = [
            _step("S1", objects=["obj_S1"], audio_name="audio_s1"),
            _step("S2", objects=["obj_S2"]),
            _step("S3", objects=["obj_S3"]),
        ]

        original_compute = compute_duration

        def mock_compute(entries, fallback=30, fps=None):
            for e in entries:
                kind = (
                    getattr(e, "kind", "")
                    if not isinstance(e, dict)
                    else e.get("kind", "")
                )
                if kind == "audio":
                    return 50.0  # S1 grows from 29 to 50 frames (+21)
            return original_compute(entries, fallback=fallback, fps=fps)

        with patch(
            "mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration",
            side_effect=mock_compute,
        ):
            actions = self.manifest.update(steps)

        shots = {s.name: s for s in self.store.sorted_shots()}

        # S1 grew by 21 frames (1 + 50 = 51)
        self.assertAlmostEqual(shots["S1"].start, 1.0)
        self.assertAlmostEqual(shots["S1"].end, 51.0)

        # S2 shifts by exactly 21 (30+21=51, 60+21=81)
        self.assertAlmostEqual(
            shots["S2"].start,
            51.0,
            msg="S2 start should shift by exactly the ripple delta",
        )
        self.assertAlmostEqual(
            shots["S2"].end, 81.0, msg="S2 end should shift by exactly the ripple delta"
        )

        # S3 shifts by exactly 21 (60+21=81, 90+21=111)
        self.assertAlmostEqual(shots["S3"].start, 81.0, msg="S3 start double-shifted!")
        self.assertAlmostEqual(shots["S3"].end, 111.0, msg="S3 end double-shifted!")


class TestComputeDurationPhaseLayout(MayaTkTestCase):
    """Test compute_duration accounts for phase-based layout.

    Bug: compute_duration took MAX across objects, ignoring that
    "in"-phase behaviors (start-anchored) and "out"-phase behaviors
    (end-anchored) on *different* objects must not overlap. The shot
    needs at least max_in_dur + max_out_dur.
    Fixed: 2026-04-20
    """

    def test_single_fade_in(self):
        """One object with fade_in → 15 frames."""
        objs = [BuilderObject(name="A", behaviors=["fade_in"])]
        self.assertEqual(compute_duration(objs), 15.0)

    def test_single_fade_out(self):
        """One object with fade_out → 15 frames."""
        objs = [BuilderObject(name="A", behaviors=["fade_out"])]
        self.assertEqual(compute_duration(objs), 15.0)

    def test_same_object_both_phases(self):
        """One object with fade_in + fade_out → 30 frames (sum)."""
        objs = [BuilderObject(name="A", behaviors=["fade_in", "fade_out"])]
        self.assertEqual(compute_duration(objs), 30.0)

    def test_different_objects_opposite_phases(self):
        """Object A: fade_in, Object B: fade_out → 30 frames (not 15).

        Bug: previously returned max(15, 15) = 15, causing the fade_out
        to collide with the fade_in at the start of the shot.
        """
        objs = [
            BuilderObject(name="A", behaviors=["fade_in"]),
            BuilderObject(name="B", behaviors=["fade_out"]),
        ]
        dur = compute_duration(objs)
        self.assertEqual(dur, 30.0, "Shot must fit in-phase (15) + out-phase (15)")

    def test_multiple_objects_mixed_phases(self):
        """Object A: fade_in+fade_out (30), B: fade_in (15) → 30 frames.

        A already needs 30 so the phase-layout floor doesn't increase it.
        """
        objs = [
            BuilderObject(name="A", behaviors=["fade_in", "fade_out"]),
            BuilderObject(name="B", behaviors=["fade_in"]),
        ]
        self.assertEqual(compute_duration(objs), 30.0)

    def test_no_behavior_objects_ignored_in_phase_total(self):
        """Plain objects (no behaviors) must not affect phase_total.

        Common CSV pattern: some objects have behaviors, some are
        plain user-animated props with no behavior templates.
        """
        objs = [
            BuilderObject(name="REGGIE", behaviors=[]),
            BuilderObject(name="ARROW_A", behaviors=["fade_out"]),
            BuilderObject(name="ARROW_B", behaviors=["fade_in"]),
        ]
        # Only the behavior objects matter: in=15 + out=15 = 30
        self.assertEqual(compute_duration(objs), 30.0)

    def test_single_behavior_among_plain_objects(self):
        """One behavior object among several plain objects → behavior wins."""
        objs = [
            BuilderObject(name="A", behaviors=[]),
            BuilderObject(name="B", behaviors=[]),
            BuilderObject(name="C", behaviors=["fade_in"]),
        ]
        self.assertEqual(compute_duration(objs), 15.0)

    def test_no_behaviors_returns_fallback(self):
        """Objects without behaviors return the fallback (30)."""
        objs = [BuilderObject(name="A", behaviors=[])]
        self.assertEqual(compute_duration(objs), 30.0)

    def test_empty_list_returns_fallback(self):
        """No entries at all returns the fallback."""
        self.assertEqual(compute_duration([]), 30.0)


class TestAnchorSingleBehavior(MayaTkTestCase):
    """Test apply_to_shots uses template anchors for single-behavior objects.

    Bug: single-behavior objects always got anchor_override=0.0, forcing
    fade_out to the start of the shot instead of the end.
    Fixed: 2026-04-20
    """

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.manifest = ShotManifest(self.store)

    def test_fade_out_placed_at_end(self):
        """A fade_out on a single-behavior object must key at the END."""
        from mayatk.anim_utils.shots.shot_manifest.behaviors import apply_to_shots

        # Create a scene object
        cube = cmds.polyCube(name="test_cube")[0]

        # Build a shot with one fade_out behavior
        self.store.define_shot(
            name="S1",
            start=1,
            end=31,
            objects=[cube],
            metadata={
                "behaviors": [
                    {
                        "name": cube,
                        "behavior": "fade_out",
                        "kind": "scene",
                        "source_path": "",
                    },
                ],
            },
            description="test",
        )
        shot = self.store.sorted_shots()[0]

        from mayatk.anim_utils.shots.shot_manifest.behaviors import apply_behavior

        result = apply_to_shots([shot], apply_fn=apply_behavior)
        self.assertTrue(len(result["applied"]) > 0, "Behavior must be applied")

        # Fade_out should key at the END of the shot (frames 16-31),
        # not the start (frames 1-16). Check visibility keys.
        keys = (
            cmds.keyframe(cube, attribute="visibility", query=True, timeChange=True)
            or []
        )
        self.assertTrue(len(keys) > 0, "Expected visibility keyframes")
        # The last key should be at shot.end (31)
        self.assertAlmostEqual(
            max(keys), 31.0, places=1, msg="fade_out last key must be at shot end"
        )
        # The first key of the behavior should NOT be at shot start (1)
        self.assertGreater(
            min(keys), 1.0, msg="fade_out keys must not start at frame 1"
        )

    def test_fade_in_still_at_start(self):
        """A fade_in on a single-behavior object remains at the START."""
        from mayatk.anim_utils.shots.shot_manifest.behaviors import (
            apply_to_shots,
            apply_behavior,
        )

        cube = cmds.polyCube(name="test_cube_in")[0]

        self.store.define_shot(
            name="S1",
            start=1,
            end=31,
            objects=[cube],
            metadata={
                "behaviors": [
                    {
                        "name": cube,
                        "behavior": "fade_in",
                        "kind": "scene",
                        "source_path": "",
                    },
                ],
            },
            description="test",
        )
        shot = self.store.sorted_shots()[0]

        apply_to_shots([shot], apply_fn=apply_behavior)

        keys = (
            cmds.keyframe(cube, attribute="visibility", query=True, timeChange=True)
            or []
        )
        self.assertTrue(len(keys) > 0, "Expected visibility keyframes")
        self.assertAlmostEqual(
            min(keys), 1.0, places=1, msg="fade_in first key must be at shot start"
        )

    def test_multi_behavior_object_still_distributed(self):
        """Objects with 2+ behaviors still get positional distribution."""
        from mayatk.anim_utils.shots.shot_manifest.behaviors import (
            apply_to_shots,
            apply_behavior,
        )

        cube = cmds.polyCube(name="test_cube_multi")[0]

        self.store.define_shot(
            name="S1",
            start=1,
            end=31,
            objects=[cube],
            metadata={
                "behaviors": [
                    {
                        "name": cube,
                        "behavior": "fade_in",
                        "kind": "scene",
                        "source_path": "",
                    },
                    {
                        "name": cube,
                        "behavior": "fade_out",
                        "kind": "scene",
                        "source_path": "",
                    },
                ],
            },
            description="test",
        )
        shot = self.store.sorted_shots()[0]

        # Bypass the has-keys guard so both behaviors are applied.
        result = apply_to_shots(
            [shot],
            apply_fn=apply_behavior,
            has_keys_fn=lambda *a, **kw: False,
        )
        self.assertEqual(len(result["applied"]), 2, "Both behaviors must be applied")

        # The primary channel is opacity (dual-keying creates it).
        # Check that keys span the full shot range, confirming both
        # behaviors were placed at distinct positions.
        keys = (
            cmds.keyframe(cube, attribute="opacity", query=True, timeChange=True) or []
        )
        if not keys:
            keys = (
                cmds.keyframe(cube, attribute="visibility", query=True, timeChange=True)
                or []
            )
        self.assertTrue(
            len(keys) >= 2, f"Expected at least 2 keyframes, got {len(keys)}"
        )
        # Should span the full shot range
        self.assertAlmostEqual(min(keys), 1.0, places=1)
        self.assertAlmostEqual(max(keys), 31.0, places=1)


class TestBuildDurationEncompasses(MayaTkTestCase):
    """End-to-end: build must produce shots that fully encompass
    all member behaviors (in-phase and out-phase) without overlap.

    Bug: steps with fade_in on one object and fade_out on another
    got 15-frame shots, causing overlapping behavior keyframes.
    Fixed: 2026-04-20
    """

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.store = ShotStore()
        self.manifest = ShotManifest(self.store)

    def test_mixed_phase_objects_chain_correctly(self):
        """Two steps with opposite-phase behaviors chain sequentially."""
        steps = [
            _step("S1", objects={"obj_a": ["fade_in"], "obj_b": ["fade_out"]}),
            _step("S2"),
        ]

        actions = self.manifest.update(steps)
        shots = self.store.sorted_shots()

        # S1 should be 30 frames (fade_in=15 + fade_out=15)
        s1 = shots[0]
        self.assertAlmostEqual(
            s1.end - s1.start, 30.0, msg="S1 must be 30 frames for both phases"
        )

        # S2 should start exactly where S1 ends
        s2 = shots[1]
        self.assertAlmostEqual(s2.start, s1.end, msg="S2 must start where S1 ends")

    def test_three_sequential_shots_ripple(self):
        """Three steps with varying durations chain correctly."""
        steps = [
            _step("S1", objects=["a"], behaviors=["fade_in"]),  # 15 frames
            _step("S2", objects={"x": ["fade_in"], "y": ["fade_out"]}),  # 30 frames
            _step("S3", objects=["b", "c"]),  # no behaviors = 30 fallback
        ]

        self.manifest.update(steps)
        shots = self.store.sorted_shots()

        self.assertEqual(len(shots), 3)
        # S1: 15 frames
        self.assertAlmostEqual(shots[0].end - shots[0].start, 15.0)
        # S2: 30 frames (in-phase + out-phase)
        self.assertAlmostEqual(shots[1].end - shots[1].start, 30.0)
        # S3: 30 frames (fallback, no behaviors)
        self.assertAlmostEqual(shots[2].end - shots[2].start, 30.0)
        # Each shot starts where the previous ends
        self.assertAlmostEqual(shots[1].start, shots[0].end)
        self.assertAlmostEqual(shots[2].start, shots[1].end)


if __name__ == "__main__":
    unittest.main()
