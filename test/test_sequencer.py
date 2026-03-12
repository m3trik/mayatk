# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.anim_utils.sequencer.

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

from mayatk.anim_utils.sequencer._sequencer import (
    SceneBlock,
    Sequencer,
)
from mayatk.anim_utils.behavior_keys import (
    load_behavior,
    resolve_keys,
    apply_behavior,
)
from mayatk.anim_utils.sequencer._audio_tracks import (
    AudioClipInfo,
    compute_waveform_envelope,
)

# Try importing AudioTrackManager (requires Maya modules at import time)
try:
    from mayatk.anim_utils.sequencer._audio_tracks import AudioTrackManager
except Exception:
    AudioTrackManager = None

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


class TestSceneBlock(unittest.TestCase):
    """Test SceneBlock dataclass."""

    def test_duration(self):
        b = SceneBlock(scene_id=0, name="A", start=10, end=40)
        self.assertEqual(b.duration, 30)

    def test_objects_default_empty(self):
        b = SceneBlock(scene_id=1, name="B", start=0, end=10)
        self.assertEqual(b.objects, [])


class TestSequencer(unittest.TestCase):
    """Test Sequencer (no Maya)."""

    def _make(self):
        return Sequencer(
            [
                SceneBlock(0, "S0", 0, 50, ["cube1"]),
                SceneBlock(1, "S1", 60, 100, ["sphere1"]),
                SceneBlock(2, "S2", 110, 150, ["cone1"]),
            ]
        )

    def test_sorted_scenes(self):
        seq = self._make()
        names = [s.name for s in seq.sorted_scenes()]
        self.assertEqual(names, ["S0", "S1", "S2"])

    def test_scene_by_id(self):
        seq = self._make()
        self.assertEqual(seq.scene_by_id(1).name, "S1")
        self.assertIsNone(seq.scene_by_id(99))

    def test_to_dict_round_trip(self):
        seq = self._make()
        data = seq.to_dict()
        restored = Sequencer.from_dict(data)
        self.assertEqual(len(restored.scenes), 3)
        self.assertEqual(restored.scene_by_id(0).name, "S0")
        self.assertEqual(restored.scene_by_id(2).objects, ["cone1"])

    def test_from_dict_preserves_order(self):
        data = [
            {"scene_id": 2, "name": "Z", "start": 100, "end": 200, "objects": []},
            {"scene_id": 0, "name": "A", "start": 0, "end": 50, "objects": []},
        ]
        seq = Sequencer.from_dict(data)
        sorted_names = [s.name for s in seq.sorted_scenes()]
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
        restored = Sequencer.from_dict(data)
        self.assertTrue(restored.is_object_hidden("sphere1"))
        self.assertFalse(restored.is_object_hidden("cube1"))

    def test_legacy_list_format_no_hidden(self):
        """Legacy data (plain list) should load with empty hidden set."""
        data = [
            {"scene_id": 0, "name": "A", "start": 0, "end": 50, "objects": ["x"]},
        ]
        seq = Sequencer.from_dict(data)
        self.assertEqual(seq.hidden_objects, set())

    def test_scene_by_name(self):
        seq = self._make()
        self.assertEqual(seq.scene_by_name("S1").scene_id, 1)
        self.assertIsNone(seq.scene_by_name("nonexistent"))


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

    def test_load_fade_in_out(self):
        t = load_behavior("fade_in_out")
        self.assertIn("attributes", t)
        self.assertIn("visibility", t["attributes"])
        vis = t["attributes"]["visibility"]
        self.assertIn("in", vis)
        self.assertIn("out", vis)
        self.assertEqual(vis["in"]["values"], [0.0, 1.0])

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
        Sequencer.delete_storage_node()

    def tearDown(self):
        try:
            Sequencer.delete_storage_node()
        except Exception:
            pass

    def _create_animated_cube(self, name, keys):
        """Create a cube and set keyframes at the given {frame: value} dict on translateX."""
        cube = pm.polyCube(name=name)[0]
        for frame, value in keys.items():
            pm.setKeyframe(cube, attribute="translateX", time=frame, value=value)
        return cube

    # -- helpers / per-object methods --------------------------------------

    def test_scene_nodes_returns_live_nodes(self):
        """_scene_nodes returns PyNode refs for existing objects."""
        cube = self._create_animated_cube("sn_test", {0: 0, 10: 5})
        scene = SceneBlock(0, "S", 0, 10, [str(cube)])
        nodes = Sequencer._scene_nodes(scene)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(str(nodes[0]), str(cube))

    def test_scene_nodes_skips_missing(self):
        """_scene_nodes silently skips objects that no longer exist."""
        scene = SceneBlock(0, "S", 0, 10, ["ghost_node"])
        nodes = Sequencer._scene_nodes(scene)
        self.assertEqual(len(nodes), 0)

    def test_move_object_keys_shifts(self):
        """move_object_keys offsets keys within the given range."""
        cube = self._create_animated_cube("mv", {10: 0, 20: 5})
        seq = Sequencer()
        seq.move_object_keys(str(cube), 10, 20, 30)
        keys = sorted(pm.keyframe(cube, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 30.0, places=1)
        self.assertAlmostEqual(keys[-1], 40.0, places=1)

    def test_move_object_keys_noop_for_missing(self):
        """move_object_keys silently skips non-existent objects."""
        seq = Sequencer()
        seq.move_object_keys("no_such_obj", 0, 50, 10)  # should not raise

    def test_scale_object_keys_rescales(self):
        """scale_object_keys remaps keys into a new time range."""
        cube = self._create_animated_cube("sc", {0: 0, 100: 10})
        seq = Sequencer()
        seq.scale_object_keys(str(cube), 0, 100, 0, 200)
        keys = sorted(pm.keyframe(cube, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 0.0, places=1)
        self.assertAlmostEqual(keys[-1], 200.0, places=1)

    def test_scale_object_keys_noop_for_missing(self):
        """scale_object_keys silently skips non-existent objects."""
        seq = Sequencer()
        seq.scale_object_keys("no_such_obj", 0, 50, 0, 80)  # should not raise

    # -- error handling ----------------------------------------------------

    def test_set_scene_duration_invalid_id(self):
        """set_scene_duration raises ValueError for unknown scene_id."""
        seq = Sequencer()
        with self.assertRaises(ValueError):
            seq.set_scene_duration(99, 100)

    def test_set_scene_start_invalid_id(self):
        """set_scene_start raises ValueError for unknown scene_id."""
        seq = Sequencer()
        with self.assertRaises(ValueError):
            seq.set_scene_start(99, 0)

    def test_resize_object_invalid_id(self):
        """resize_object raises ValueError for unknown scene_id."""
        seq = Sequencer()
        with self.assertRaises(ValueError):
            seq.resize_object(99, "cube1", 0, 50, 0, 80)

    def test_resize_object_scales_single_object(self):
        """resize_object should only scale the target object, not others."""
        c1 = self._create_animated_cube("obj_a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("obj_b", {10: 0, 40: 5})
        seq = Sequencer([SceneBlock(0, "S0", 0, 50, [str(c1), str(c2)])])

        # Resize only obj_a from [0,50] → [0,80]
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
        seq = Sequencer(
            [
                SceneBlock(0, "S0", 0, 50, [str(c1)]),
                SceneBlock(1, "S1", 100, 150, [str(c2)]),
            ]
        )

        # Extend obj in S0 from [0,50] → [0,80]  (delta = +30)
        seq.resize_object(0, str(c1), 0, 50, 0, 80)

        # S1 should have shifted by +30
        self.assertAlmostEqual(seq.scene_by_id(1).start, 130.0, places=1)
        self.assertAlmostEqual(seq.scene_by_id(1).end, 180.0, places=1)

    def test_detect_scenes_single_object(self):
        """One object with keys → one scene."""
        cube = self._create_animated_cube("box", {1: 0, 10: 5, 20: 10})
        seq = Sequencer.detect_scenes([cube])
        self.assertEqual(len(seq.scenes), 1)
        self.assertAlmostEqual(seq.scenes[0].start, 1.0)
        self.assertAlmostEqual(seq.scenes[0].end, 20.0)

    def test_detect_scenes_gap_creates_two(self):
        """Two objects with a large gap → two scenes."""
        c1 = self._create_animated_cube("early", {1: 0, 10: 5})
        c2 = self._create_animated_cube("late", {100: 0, 110: 5})
        seq = Sequencer.detect_scenes([c1, c2], gap_threshold=10)
        self.assertEqual(len(seq.scenes), 2)

    def test_set_scene_duration_ripple(self):
        """Changing scene 0's duration ripples scene 1's start/end."""
        c1 = self._create_animated_cube("a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("b", {100: 0, 150: 10})
        seq = Sequencer.detect_scenes([c1, c2], gap_threshold=10)

        scene0 = seq.scene_by_id(0)
        original_s1_start = seq.scene_by_id(1).start

        # Extend scene 0 by 20 frames
        seq.set_scene_duration(0, scene0.duration + 20)

        # Scene 1 should have shifted by +20
        self.assertAlmostEqual(
            seq.scene_by_id(1).start, original_s1_start + 20, places=1
        )

    def test_apply_behavior_sets_keys(self):
        """apply_behavior should create keyframes on the object."""
        cube = self._create_animated_cube("obj", {0: 0, 100: 10})
        apply_behavior(str(cube), "fade_in_out", 0, 100, attrs=["visibility"])

        # Visibility should now have keyframes
        vis_keys = pm.keyframe(cube, attribute="visibility", query=True)
        self.assertIsNotNone(vis_keys)
        self.assertGreater(len(vis_keys), 0)

    def test_save_creates_network_node(self):
        """save() should create a locked network node with JSON data."""
        cube = self._create_animated_cube("save_cube", {0: 0, 50: 10})
        seq = Sequencer([SceneBlock(0, "S0", 0, 50, [str(cube)])])
        node_name = seq.save()

        self.assertTrue(pm.objExists(node_name))
        node = pm.PyNode(node_name)
        self.assertEqual(node.nodeType(), "network")
        self.assertTrue(node.hasAttr(Sequencer._DATA_ATTR))

        raw = node.attr(Sequencer._DATA_ATTR).get()
        import json

        data = json.loads(raw)
        self.assertIn("scenes", data)
        self.assertEqual(len(data["scenes"]), 1)
        self.assertEqual(data["scenes"][0]["name"], "S0")

    def test_save_load_round_trip(self):
        """save() then load() should restore identical scene data."""
        c1 = self._create_animated_cube("rt_a", {0: 0, 50: 10})
        c2 = self._create_animated_cube("rt_b", {60: 0, 100: 5})
        seq = Sequencer(
            [
                SceneBlock(0, "Alpha", 0, 50, [str(c1)]),
                SceneBlock(1, "Beta", 60, 100, [str(c2)]),
            ]
        )
        seq.save()

        loaded = Sequencer.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.scenes), 2)
        self.assertEqual(loaded.scene_by_id(0).name, "Alpha")
        self.assertEqual(loaded.scene_by_id(1).name, "Beta")
        self.assertIn(str(c1), loaded.scene_by_id(0).objects)

    def test_load_resolves_renamed_objects(self):
        """load() should resolve object names via message connections after rename."""
        cube = self._create_animated_cube("orig_name", {0: 0, 50: 10})
        seq = Sequencer([SceneBlock(0, "S", 0, 50, [str(cube)])])
        seq.save()

        # Rename the object
        pm.rename(cube, "new_name")

        loaded = Sequencer.load()
        self.assertIn("new_name", loaded.scene_by_id(0).objects)

    def test_delete_storage_node(self):
        """delete_storage_node() should remove the node."""
        seq = Sequencer([SceneBlock(0, "S", 0, 50)])
        seq.save()
        self.assertTrue(pm.objExists(Sequencer.STORAGE_NODE))

        result = Sequencer.delete_storage_node()
        self.assertTrue(result)
        self.assertFalse(pm.objExists(Sequencer.STORAGE_NODE))

        # Deleting again returns False
        self.assertFalse(Sequencer.delete_storage_node())

    def test_load_returns_none_when_no_node(self):
        """load() should return None when no storage node exists."""
        # Ensure clean state
        Sequencer.delete_storage_node()
        self.assertIsNone(Sequencer.load())


# ---------------------------------------------------------------------------
# Scene Builder tests (pure Python — no Maya)
# ---------------------------------------------------------------------------

from mayatk.anim_utils.scene_builder._scene_builder import (
    detect_behavior,
    parse_csv,
    BuilderObject,
    BuilderStep,
    ColumnMap,
    SceneBuilder,
)


class TestDetectBehavior(unittest.TestCase):
    """Test behavior auto-detection from step-contents text."""

    def test_fade_in(self):
        self.assertEqual(detect_behavior("Arrow fades in."), "fade_in")

    def test_fade_out(self):
        self.assertEqual(detect_behavior("Checklist fades out."), "fade_out")

    def test_fade_in_out(self):
        self.assertEqual(
            detect_behavior("Arrow fades in, then fades out."), "fade_in_out"
        )

    def test_no_behavior(self):
        self.assertEqual(detect_behavior("User is teleported."), "")

    def test_empty(self):
        self.assertEqual(detect_behavior(""), "")

    def test_na(self):
        self.assertEqual(detect_behavior("N/A"), "")


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
        self.assertEqual(a01.objects[0].behavior, "fade_in")
        self.assertEqual(a01.objects[1].behavior, "fade_in")  # inherited

    def test_behavior_detected(self):
        steps = parse_csv(self._csv_path)
        self.assertEqual(steps[0].objects[0].behavior, "fade_in")
        self.assertEqual(steps[1].objects[0].behavior, "fade_out")

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
            self.assertEqual(steps[0].content, "first")
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
            self.assertIn("Second line.", steps[0].content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSceneBuilderPure(unittest.TestCase):
    """Test SceneBuilder data-only features (no Maya)."""

    def _make_steps(self):
        return [
            BuilderStep(
                "A01",
                "A",
                "SEC A",
                "Arrow fades in.",
                [
                    BuilderObject("ARROW_01", "fade_in"),
                    BuilderObject("ARROW_02", "fade_in"),
                ],
            ),
            BuilderStep(
                "A02",
                "A",
                "SEC A",
                "Checklist fades out.",
                [
                    BuilderObject("CHECK_01", "fade_out"),
                ],
            ),
        ]

    def test_preview_returns_layout(self):
        seq = Sequencer()
        builder = SceneBuilder(seq, step_duration=30, gap=5, start_frame=1)
        layout = builder.preview(self._make_steps())
        self.assertEqual(len(layout), 2)
        self.assertEqual(layout[0]["step_id"], "A01")
        self.assertAlmostEqual(layout[0]["start"], 1)
        self.assertAlmostEqual(layout[0]["end"], 31)
        self.assertAlmostEqual(layout[1]["start"], 36)
        self.assertAlmostEqual(layout[1]["end"], 66)
        self.assertEqual(len(layout[0]["objects"]), 2)

    def test_preview_does_not_mutate(self):
        seq = Sequencer()
        builder = SceneBuilder(seq, step_duration=30)
        builder.preview(self._make_steps())
        self.assertEqual(len(seq.scenes), 0)  # nothing added

    def test_build_without_behaviors(self):
        seq = Sequencer()
        builder = SceneBuilder(seq, step_duration=30, gap=0, start_frame=1)
        builder.build(self._make_steps(), apply_behaviors=False)
        self.assertEqual(len(seq.scenes), 2)
        self.assertEqual(seq.scenes[0].name, "A01")
        self.assertAlmostEqual(seq.scenes[0].start, 1)
        self.assertAlmostEqual(seq.scenes[0].end, 31)

    def test_from_csv_accepts_existing_sequencer(self):
        """from_csv should use the provided sequencer, not create a new one."""
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
            existing = Sequencer()
            builder, steps = SceneBuilder.from_csv(csv_path, sequencer=existing)
            self.assertIs(builder.sequencer, existing)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBehaviorYAMLAnchors(unittest.TestCase):
    """Verify YAML templates include explicit anchor fields."""

    def test_fade_in_out_has_anchors(self):
        t = load_behavior("fade_in_out")
        vis = t["attributes"]["visibility"]
        self.assertEqual(vis["in"]["anchor"], "start")
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


class TestSceneBuilderSlotsImport(unittest.TestCase):
    """Verify the scene_builder_slots module can be imported (no Qt required)."""

    def test_controller_class_exists(self):
        from mayatk.anim_utils.scene_builder.scene_builder_slots import (
            SceneBuilderController,
        )

        self.assertTrue(callable(SceneBuilderController))

    def test_slots_class_exists(self):
        from mayatk.anim_utils.scene_builder.scene_builder_slots import (
            SceneBuilderSlots,
        )

        self.assertTrue(callable(SceneBuilderSlots))


class TestSceneBuilderUIFile(unittest.TestCase):
    """Verify the .ui file exists alongside the slots."""

    def test_ui_file_exists(self):
        from pathlib import Path

        ui_path = (
            Path(__file__).parent.parent
            / "mayatk"
            / "anim_utils"
            / "scene_builder"
            / "scene_builder.ui"
        )
        self.assertTrue(ui_path.exists(), f"Missing: {ui_path}")


# ---------------------------------------------------------------------------
# Audio Track tests (pure Python — no Maya)
# ---------------------------------------------------------------------------


class TestAudioClipInfo(unittest.TestCase):
    """Test AudioClipInfo dataclass."""

    def test_end_frame(self):
        clip = AudioClipInfo(
            node_name="audio1",
            file_path="/tmp/test.wav",
            offset=10.0,
            duration_frames=50.0,
        )
        self.assertAlmostEqual(clip.end_frame, 60.0)

    def test_defaults(self):
        clip = AudioClipInfo(node_name="a", file_path="", offset=0, duration_frames=0)
        self.assertEqual(clip.sample_rate, 44100)
        self.assertEqual(clip.num_channels, 1)
        self.assertEqual(clip.num_frames, 0)


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


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestAudioTrackManagerMaya(unittest.TestCase):
    """Tests for AudioTrackManager requiring Maya."""

    @classmethod
    def setUpClass(cls):
        """Create a temp WAV for audio node tests."""
        import struct
        import wave
        import tempfile

        cls._tmp_dir = tempfile.mkdtemp()
        cls._wav_path = str(Path(cls._tmp_dir) / "test_clip.wav")

        n_samples = 44100  # 1 second at 44100 Hz
        samples = [int(16000 * ((i % 100) / 50.0 - 1.0)) for i in range(n_samples)]
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

    def setUp(self):
        pm.mel.file(new=True, force=True)
        self.mgr = AudioTrackManager()

    def _create_audio_node(self, name, wav_path, offset=0.0):
        """Create a Maya audio node with a file and offset."""
        from maya import cmds

        node = cmds.createNode("audio", name=name)
        cmds.setAttr(f"{node}.filename", wav_path, type="string")
        cmds.setAttr(f"{node}.offset", offset)
        return node

    def test_find_audio_nodes_empty_scene(self):
        """No audio nodes yields empty list."""
        clips = self.mgr.find_audio_nodes()
        self.assertEqual(clips, [])

    def test_find_audio_nodes_discovers(self):
        """Audio nodes are discovered with correct metadata."""
        self._create_audio_node("clip_a", self._wav_path, offset=10)
        clips = self.mgr.find_audio_nodes()
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].node_name, "clip_a")
        self.assertAlmostEqual(clips[0].offset, 10.0)
        self.assertGreater(clips[0].duration_frames, 0)

    def test_find_audio_nodes_range_filter(self):
        """Range filtering excludes out-of-range clips."""
        self._create_audio_node("early", self._wav_path, offset=0)
        self._create_audio_node("late", self._wav_path, offset=1000)
        clips = self.mgr.find_audio_nodes(start=0, end=100)
        names = [c.node_name for c in clips]
        self.assertIn("early", names)
        self.assertNotIn("late", names)

    def test_collect_audio_segments_includes_waveform(self):
        """collect_audio_segments returns segment dicts with waveform data."""
        self._create_audio_node("seg_test", self._wav_path, offset=5)
        segs = self.mgr.collect_audio_segments(include_waveform=True)
        self.assertEqual(len(segs), 1)
        self.assertTrue(segs[0]["is_audio"])
        self.assertGreater(len(segs[0]["waveform"]), 0)
        self.assertEqual(segs[0]["label"], "test_clip")

    def test_set_audio_offset(self):
        """set_audio_offset updates the Maya node."""
        from maya import cmds

        node = self._create_audio_node("move_me", self._wav_path, offset=0)
        AudioTrackManager.set_audio_offset(node, 42.0)
        self.assertAlmostEqual(cmds.getAttr(f"{node}.offset"), 42.0)

    def test_collect_without_waveform(self):
        """collect_audio_segments with include_waveform=False returns empty waveform."""
        self._create_audio_node("no_wave", self._wav_path, offset=0)
        segs = self.mgr.collect_audio_segments(include_waveform=False)
        self.assertEqual(segs[0]["waveform"], [])

    def test_waveform_cache_reuses(self):
        """Repeated calls with same WAV reuse the cached envelope."""
        self._create_audio_node("cache_test", self._wav_path, offset=0)
        segs1 = self.mgr.collect_audio_segments(include_waveform=True)
        segs2 = self.mgr.collect_audio_segments(include_waveform=True)
        self.assertIs(segs1[0]["waveform"], segs2[0]["waveform"])

    def test_segment_cache_invalidate(self):
        """collect_all uses cache; invalidate() forces re-read."""
        self._create_audio_node("c1", self._wav_path, offset=0)
        segs1 = self.mgr.collect_all_audio_segments()
        self.assertEqual(len(segs1), 1)
        # Same range returns cached list
        segs2 = self.mgr.collect_all_audio_segments()
        self.assertIs(segs1, segs2)
        # After invalidate, re-reads
        self.mgr.invalidate()
        segs3 = self.mgr.collect_all_audio_segments()
        self.assertIsNot(segs1, segs3)

    def test_audio_source_tag(self):
        """DG audio segments include audio_source='dg'."""
        self._create_audio_node("tag_test", self._wav_path, offset=0)
        segs = self.mgr.collect_audio_segments()
        self.assertEqual(segs[0]["audio_source"], "dg")


@unittest.skipUnless(HAS_MAYA, "Requires Maya (standalone or GUI)")
class TestEventAudioTrackManager(unittest.TestCase):
    """Tests for AudioEvents (keyed enum locator) discovery path."""

    @classmethod
    def setUpClass(cls):
        import struct
        import wave
        import tempfile

        cls._tmp_dir = tempfile.mkdtemp()
        cls._wav_path = str(Path(cls._tmp_dir) / "event_clip.wav")

        n_samples = 22050  # 0.5 seconds at 44100 Hz
        samples = [int(8000 * ((i % 50) / 25.0 - 1.0)) for i in range(n_samples)]
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

    def setUp(self):
        pm.mel.file(new=True, force=True)
        self.mgr = AudioTrackManager()

    def _create_event_locator(self, name, labels, file_map, keys):
        """Create a locator with audio_trigger enum + audio_file_map + keys.

        Parameters:
            name: Locator name.
            labels: List of enum label strings (first should be 'None').
            file_map: Dict mapping lowercase label -> file path.
            keys: List of (frame, enum_index) pairs.
        """
        from maya import cmds
        import json

        loc = cmds.spaceLocator(name=name)[0]
        enum_str = ":".join(labels)
        cmds.addAttr(loc, ln="audio_trigger", at="enum", en=enum_str, k=True)
        cmds.addAttr(loc, ln="audio_file_map", dt="string")
        cmds.setAttr(f"{loc}.audio_file_map", json.dumps(file_map), type="string")

        for frame, idx in keys:
            cmds.setKeyframe(f"{loc}.audio_trigger", time=frame, value=idx)

        return loc

    def test_find_event_locators(self):
        """Locators with audio_trigger are found."""
        self._create_event_locator(
            "ev_loc",
            ["None", "clip_a"],
            {"clip_a": self._wav_path},
            [(10, 1)],
        )
        locs = AudioTrackManager.find_event_audio_locators()
        self.assertEqual(locs, ["ev_loc"])

    def test_collect_event_segments(self):
        """Event segments are discovered with correct frame and duration."""
        self._create_event_locator(
            "ev_loc2",
            ["None", "clip_a", "clip_b"],
            {"clip_a": self._wav_path, "clip_b": self._wav_path},
            [(0, 0), (10, 1), (50, 2)],
        )
        segs = self.mgr.collect_event_audio_segments()
        # Key at frame 0 is 'None' -> skipped.  Keys 10 and 50 produce segments.
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["label"], "clip_a")
        self.assertAlmostEqual(segs[0]["start"], 10.0)
        self.assertGreater(segs[0]["duration"], 0)
        self.assertEqual(segs[0]["audio_source"], "event")
        self.assertAlmostEqual(segs[0]["event_key_frame"], 10.0)

    def test_event_range_filter(self):
        """Event segments outside range are excluded."""
        self._create_event_locator(
            "ev_range",
            ["None", "clip_a"],
            {"clip_a": self._wav_path},
            [(5, 1), (9999, 1)],
        )
        segs = self.mgr.collect_event_audio_segments(scene_start=0, scene_end=100)
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0]["start"], 5.0)

    def test_move_event_key(self):
        """move_event_key shifts the keyframe on the trigger attribute."""
        from maya import cmds

        loc = self._create_event_locator(
            "ev_move",
            ["None", "clip_a"],
            {"clip_a": self._wav_path},
            [(20, 1)],
        )
        AudioTrackManager.move_event_key(loc, 20.0, 35.0)
        keys = cmds.keyframe(f"{loc}.audio_trigger", q=True)
        self.assertAlmostEqual(keys[0], 35.0)

    def test_collect_all_combines_both(self):
        """collect_all_audio_segments returns DG + event segments together."""
        from maya import cmds

        # DG audio node
        dg = cmds.createNode("audio", name="dg_clip")
        cmds.setAttr(f"{dg}.filename", self._wav_path, type="string")
        cmds.setAttr(f"{dg}.offset", 0.0)
        # Event locator
        self._create_event_locator(
            "ev_both",
            ["None", "clip_a"],
            {"clip_a": self._wav_path},
            [(100, 1)],
        )
        segs = self.mgr.collect_all_audio_segments()
        sources = {s["audio_source"] for s in segs}
        self.assertIn("dg", sources)
        self.assertIn("event", sources)


if __name__ == "__main__":
    unittest.main()
