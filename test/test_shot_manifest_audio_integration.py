# !/usr/bin/python
# coding=utf-8
"""Maya integration tests for shared shot sizing — audio and animation.

Design under test: shots size to their largest member at *resolve time*,
and on rebuild **grow only** to accommodate members (never shrink).
The same grow-to-fit behavior applies when a clip (audio OR animation)
is moved across shot boundaries in the sequencer.
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
    import maya.cmds as cmds  # noqa: F401
except ImportError as exc:
    raise RuntimeError("These tests must run inside a Maya session.") from exc

from base_test import MayaTkTestCase  # noqa: E402
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils  # noqa: E402
from mayatk.anim_utils.shots._shots import ShotStore  # noqa: E402
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (  # noqa: E402
    ShotSequencer,
)
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (  # noqa: E402
    BuilderStep,
    BuilderObject,
    ShotManifest,
)
from mayatk.anim_utils.shots.shot_manifest.range_resolver import (  # noqa: E402
    resolve_ranges,
)
from mayatk.anim_utils.shots.shot_manifest.behaviors import (  # noqa: E402
    compute_duration,
)


_TEMP_DIR = os.path.join(scripts_dir, "mayatk", "test", "temp_tests")


def _make_wav(name: str, duration_sec: float = 1.0) -> str:
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


def _make_audio_step(
    step_id: str, audio_name: str, source_path: str = ""
) -> BuilderStep:
    step = BuilderStep(
        step_id=step_id,
        section="A",
        section_title="Sec",
        description="d",
    )
    step.objects.append(
        BuilderObject(
            name=audio_name,
            kind="audio",
            behaviors=["set_clip"],
            source_path=source_path,
        )
    )
    return step


def _register_track(name: str, duration_sec: float, tag: str) -> None:
    """Create a WAV and register it as an audio_clips track (no DG node)."""
    wav = _make_wav(f"{tag}_{name}", duration_sec=duration_sec)
    tid = audio_utils.normalize_track_id(name)
    audio_utils.ensure_track_attr(tid)
    audio_utils.set_path(tid, wav)


# ---------------------------------------------------------------------------
# compute_duration — shared sizing function
# ---------------------------------------------------------------------------


class TestComputeDuration(MayaTkTestCase):
    """``compute_duration`` is the shared member-sizing primitive."""

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")  # 24 fps

    def test_reads_source_path_directly(self):
        wav = _make_wav("cd_src", duration_sec=2.0)  # ~48f at 24fps
        step = _make_audio_step("A01", "A01_Hello", source_path=wav)
        self.assertAlmostEqual(compute_duration(step.objects), 48.0, delta=1.0)

    def test_falls_back_to_registered_track_path(self):
        """BuilderObject with source_path='' resolves via the track registry."""
        _register_track("A01_Hello", 2.0, "cd_track")
        obj = BuilderObject(
            name="A01_Hello",
            kind="audio",
            behaviors=["set_clip"],
            source_path="",
        )
        self.assertAlmostEqual(compute_duration([obj]), 48.0, delta=1.0)

    def test_returns_fallback_when_unresolvable(self):
        obj = BuilderObject(
            name="missing_clip",
            kind="audio",
            behaviors=["set_clip"],
            source_path="",
        )
        self.assertEqual(compute_duration([obj]), 30)


# ---------------------------------------------------------------------------
# Resolver — audio must participate in shared sizing
# ---------------------------------------------------------------------------


class TestResolverSharedSizing(MayaTkTestCase):
    """The range resolver sizes each step to its largest member.

    When no scene animation is detected, the resolver historically handed
    every step a flat 200f default — ignoring audio clip lengths. The
    shared ``compute_duration`` path must override that default whenever
    a step has a resolvable behavior duration.
    """

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")

    def test_resolver_sizes_to_audio_when_no_animation(self):
        names = ["A01_Hello", "A02_World", "A03_Bye"]
        for n, dur in zip(names, [2.0, 1.5, 2.5]):
            _register_track(n, dur, "rslv")

        steps = [
            _make_audio_step(sid, n, source_path="")
            for sid, n in zip(["A01", "A02", "A03"], names)
        ]
        resolved = resolve_ranges(
            steps=steps,
            user_ranges={},
            gap_starts=[],  # no animation detected
            gap_end_map={},
            gap=0.0,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=200.0,
        )
        durs = [end - start for _sid, start, end, _u in resolved]
        for dur, exp in zip(durs, [48, 36, 60]):
            self.assertAlmostEqual(dur, exp, delta=4)

    def test_resolver_keeps_fallback_when_step_has_no_members(self):
        """Steps without behaviors still get the 200f placeholder."""
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="Sec",
            description="no members",
        )
        resolved = resolve_ranges(
            steps=[step],
            user_ranges={},
            gap_starts=[],
            gap_end_map={},
            gap=0.0,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=200.0,
        )
        _sid, start, end, _u = resolved[0]
        self.assertAlmostEqual(end - start, 200.0, delta=0.1)


# ---------------------------------------------------------------------------
# ShotManifest.update — grow-only invariant on rebuild
# ---------------------------------------------------------------------------


class TestShotManifestGrowOnly(MayaTkTestCase):
    """On rebuild, shots grow to fit members but never shrink."""

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")
        ShotStore._active = None
        self.store = ShotStore()

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    def test_shot_grows_when_audio_added_later(self):
        """Shot with 30f fallback grows to clip length once a track loads."""
        clip_name = "A01_Hello"
        ShotManifest(self.store).update(
            [_make_audio_step("A01", clip_name, source_path="")]
        )
        self.assertEqual(
            self.store.shots[0].end - self.store.shots[0].start, 30.0
        )

        _register_track(clip_name, 2.0, "grow")

        ShotManifest(self.store).update(
            [_make_audio_step("A01", clip_name, source_path="")]
        )
        shot = self.store.shots[0]
        self.assertAlmostEqual(shot.end - shot.start, 48.0, delta=1.0)

    def test_shot_does_not_shrink_when_audio_shorter(self):
        """A 200f shot with a 48f audio clip stays at 200f — grow-only."""
        clip_name = "A01_Hello"
        # Simulate a resolver-placed 200f shot (e.g. from a prior build).
        ShotManifest(self.store).update(
            [_make_audio_step("A01", clip_name, source_path="")],
            ranges={"A01": (1.0, 201.0)},
        )
        self.assertEqual(
            self.store.shots[0].end - self.store.shots[0].start, 200.0
        )

        _register_track(clip_name, 2.0, "noshrink")  # 48f < 200f

        # Incremental rebuild echoes current positions as ranges.
        ShotManifest(self.store).update(
            [_make_audio_step("A01", clip_name, source_path="")],
            ranges={"A01": (1.0, 201.0)},
        )
        shot = self.store.shots[0]
        self.assertAlmostEqual(shot.end - shot.start, 200.0, delta=0.1)

    def test_locked_shot_is_not_resized(self):
        """Locked shots are protected even when members exceed their range."""
        clip_name = "A01_Hello"
        step = _make_audio_step("A01", clip_name, source_path="")

        ShotManifest(self.store).update([step])
        shot = self.store.shots[0]
        self.store.update_shot(shot.shot_id, locked=True)
        locked_start, locked_end = shot.start, shot.end

        _register_track(clip_name, 2.0, "locked")

        ShotManifest(self.store).update(
            [step], ranges={"A01": (locked_start, locked_end)}
        )
        shot = self.store.shots[0]
        self.assertEqual(shot.start, locked_start)
        self.assertEqual(shot.end, locked_end)


# ---------------------------------------------------------------------------
# Sequencer — shared grow-to-fit behavior for moved sequences
# ---------------------------------------------------------------------------


class TestSequencerMoveResizeShared(MayaTkTestCase):
    """Moving an animation clip past the shot boundary grows the shot.

    This is the same primitive ``_expand_shot_for_clip`` uses for audio
    clips in the sequencer — the behavior is shared between kinds.
    """

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")
        ShotStore._active = None
        self.store = ShotStore()
        self.seq = ShotSequencer(store=self.store)

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    def _make_anim_cube(self, name: str, start: float, end: float) -> str:
        """Create a cube with tx keys at *start* and *end*."""
        cube = cmds.polyCube(name=name)[0]
        cmds.setKeyframe(cube, attribute="translateX", t=start, v=0.0)
        cmds.setKeyframe(cube, attribute="translateX", t=end, v=10.0)
        return cube

    def test_move_grows_containing_shot_end(self):
        """Moving a clip past shot.end grows the shot — no shrink."""
        cube = self._make_anim_cube("mover_A", 10.0, 30.0)
        shot_id = self.store.define_shot(
            name="A01", start=1.0, end=50.0, objects=[cube]
        ).shot_id

        # Move the clip to start at 80 → new end 100 (past shot.end=50).
        self.seq.move_object_in_shot(shot_id, cube, 10.0, 30.0, 80.0)

        shot = self.store.shot_by_id(shot_id)
        self.assertAlmostEqual(shot.end, 100.0, delta=0.1)
        self.assertAlmostEqual(shot.start, 1.0, delta=0.1)

    def test_move_into_next_shot_ripples_downstream(self):
        """Clip extended past shot A's end ripples shot B forward."""
        cube = self._make_anim_cube("mover_B", 10.0, 30.0)
        shot_a = self.store.define_shot(
            name="A01", start=1.0, end=50.0, objects=[cube]
        ).shot_id
        shot_b = self.store.define_shot(
            name="A02", start=51.0, end=100.0, objects=[]
        ).shot_id
        b_prior_start = self.store.shot_by_id(shot_b).start
        b_prior_end = self.store.shot_by_id(shot_b).end

        # Extend the clip to end at 120 — 70f past shot A's tail.
        self.seq.move_object_in_shot(shot_a, cube, 10.0, 30.0, 100.0)

        shot_a_after = self.store.shot_by_id(shot_a)
        shot_b_after = self.store.shot_by_id(shot_b)
        delta = shot_a_after.end - 50.0  # how far A grew

        self.assertGreater(delta, 0.0)
        self.assertAlmostEqual(
            shot_b_after.start, b_prior_start + delta, delta=0.1
        )
        self.assertAlmostEqual(
            shot_b_after.end, b_prior_end + delta, delta=0.1
        )
        # A and B remain back-to-back (no overlap).
        self.assertGreaterEqual(shot_b_after.start, shot_a_after.end - 1e-6)

    def test_move_earlier_grows_shot_start(self):
        """Moving a clip before shot.start grows the shot head."""
        cube = self._make_anim_cube("mover_C", 20.0, 40.0)
        shot_id = self.store.define_shot(
            name="A01", start=10.0, end=50.0, objects=[cube]
        ).shot_id

        # Move the clip to start at 2 → before shot.start=10.
        self.seq.move_object_in_shot(shot_id, cube, 20.0, 40.0, 2.0)

        shot = self.store.shot_by_id(shot_id)
        self.assertAlmostEqual(shot.start, 2.0, delta=0.1)
        self.assertAlmostEqual(shot.end, 50.0, delta=0.1)

    def test_resize_object_grows_shot_to_fit(self):
        """Resizing a clip past shot.end grows the shot."""
        cube = self._make_anim_cube("resizer", 10.0, 30.0)
        shot_id = self.store.define_shot(
            name="A01", start=1.0, end=50.0, objects=[cube]
        ).shot_id

        # Scale the clip from (10,30) to (10,90) — past the shot tail.
        self.seq.resize_object(shot_id, cube, 10.0, 30.0, 10.0, 90.0)

        shot = self.store.shot_by_id(shot_id)
        self.assertGreaterEqual(shot.end, 90.0 - 1e-6)


# ---------------------------------------------------------------------------
# Build button flow — the scenario the user actually reports
# ---------------------------------------------------------------------------


class TestBuildButtonWithAudioClipsUI(MayaTkTestCase):
    """Reproduces: audio loaded via audio_clips UI, then Build pressed.

    Mirrors ``shot_manifest_slots.build()``'s range_map construction so
    the behavior under test is the same code path the Build button runs.
    """

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")
        ShotStore._active = None
        self.store = ShotStore()

    def tearDown(self):
        ShotStore._active = None
        super().tearDown()

    def _run_build(self, builder, steps, incremental: bool):
        """Same range_map logic as shot_manifest_slots.build()."""
        if incremental:
            range_map = {
                s.name: (s.start, s.end) for s in self.store.sorted_shots()
            }
        else:
            resolved = resolve_ranges(
                steps=steps,
                user_ranges={},
                gap_starts=[],
                gap_end_map={},
                gap=0.0,
                use_selected_keys=False,
                last_resolved=[],
                default_duration=200.0,
            )
            range_map = {
                sid: (s, e) for sid, s, e, _ in resolved if e is not None
            }
        return builder.sync(
            steps,
            ranges=range_map,
            remove_missing=True,
            zero_duration_fallback=incremental,
        )

    def _audio_keys(self, name: str):
        tid = audio_utils.normalize_track_id(name)
        keys = audio_utils.read_keys(tid) or []
        on = [f for f, v in keys if int(round(v)) == 1]
        off = [f for f, v in keys if int(round(v)) == 0]
        return on, off

    def test_build_places_audio_clips_at_their_shot_start(self):
        """After Build, every audio track's on-key == shot.start."""
        names = ["A01_Hello", "A02_World", "A03_Bye"]
        durs_sec = [2.0, 1.5, 2.5]  # ~48f, 36f, 60f at 24fps
        for n, d in zip(names, durs_sec):
            _register_track(n, d, "buildflow")

        steps = [
            _make_audio_step(sid, n, source_path="")
            for sid, n in zip(["A01", "A02", "A03"], names)
        ]
        builder = ShotManifest(self.store)
        self._run_build(builder, steps, incremental=False)

        shots = {s.name: s for s in self.store.sorted_shots()}
        self.assertEqual(set(shots), {"A01", "A02", "A03"})

        for sid, name, exp_dur in zip(
            ["A01", "A02", "A03"], names, [48.0, 36.0, 60.0]
        ):
            shot = shots[sid]
            on, off = self._audio_keys(name)
            self.assertTrue(on, f"No on-key for {name}")
            self.assertTrue(off, f"No off-key for {name}")
            self.assertAlmostEqual(
                on[0],
                shot.start,
                delta=0.5,
                msg=f"{name} on-key {on[0]} != shot.start {shot.start}",
            )
            self.assertAlmostEqual(
                off[0] - on[0],
                exp_dur,
                delta=1.0,
                msg=f"{name} clip span wrong",
            )

    def test_pressing_build_twice_does_not_move_shots_or_clips(self):
        """Rebuild must be a no-op on placement — no shrinking, no shifting."""
        names = ["A01_Hello", "A02_World"]
        for n, d in zip(names, [2.0, 1.5]):
            _register_track(n, d, "twice")

        steps = [
            _make_audio_step(sid, n, source_path="")
            for sid, n in zip(["A01", "A02"], names)
        ]
        builder = ShotManifest(self.store)
        self._run_build(builder, steps, incremental=False)

        before_shots = {
            s.name: (s.start, s.end) for s in self.store.sorted_shots()
        }
        before_keys = {n: self._audio_keys(n) for n in names}

        self._run_build(builder, steps, incremental=True)

        after_shots = {
            s.name: (s.start, s.end) for s in self.store.sorted_shots()
        }
        after_keys = {n: self._audio_keys(n) for n in names}

        self.assertEqual(
            before_shots, after_shots, "Shots moved across rebuilds"
        )
        self.assertEqual(
            before_keys, after_keys, "Audio clip keys moved across rebuilds"
        )

    def test_build_after_loading_audio_grows_existing_shots(self):
        """Shots built without audio must grow when audio loads, not shrink."""
        names = ["A01_Hello", "A02_World"]
        steps = [
            _make_audio_step(sid, n, source_path="")
            for sid, n in zip(["A01", "A02"], names)
        ]
        builder = ShotManifest(self.store)

        # First build: no audio registered yet. Shots use 30f fallback.
        self._run_build(builder, steps, incremental=False)
        first = {
            s.name: (s.start, s.end) for s in self.store.sorted_shots()
        }

        # Now load audio via the audio_clips UI workflow.
        for n, d in zip(names, [2.0, 1.5]):  # 48f, 36f
            _register_track(n, d, "grow_after")

        # Second build (what pressing Build does next).
        self._run_build(builder, steps, incremental=True)

        shots = {s.name: s for s in self.store.sorted_shots()}
        for sid, exp in zip(["A01", "A02"], [48.0, 36.0]):
            shot = shots[sid]
            self.assertGreaterEqual(
                shot.end - shot.start,
                exp - 1.0,
                msg=f"{sid} did not grow to fit audio",
            )
            # Grow-only: start unchanged.
            self.assertAlmostEqual(
                shot.start, first[sid][0], delta=0.1,
                msg=f"{sid}.start moved — should be grow-only",
            )

        # Audio keys must land at each shot's start.
        for sid, name in zip(["A01", "A02"], names):
            on, off = self._audio_keys(name)
            self.assertTrue(on and off)
            self.assertAlmostEqual(on[0], shots[sid].start, delta=0.5)


if __name__ == "__main__":
    unittest.main()
