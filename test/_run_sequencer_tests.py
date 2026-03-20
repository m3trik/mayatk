"""Run sequencer Maya tests via maya_connection.

Writes the test code to a temp file and tells Maya to exec it,
avoiding command-port namespace-scoping issues with inline strings.

Usage:
    python mayatk/test/_run_sequencer_tests.py
"""

import sys
import os
import time
import textwrap

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from mayatk.env_utils import maya_connection

RESULTS_FILE = os.path.join(
    scripts_dir, "mayatk", "test", "temp_tests", "scene_seq_results.txt"
)
TEST_SCRIPT = os.path.join(
    scripts_dir, "mayatk", "test", "temp_tests", "_maya_test_scene_seq.py"
)


def _write_test_script():
    """Write the test code to a standalone .py file Maya will exec."""
    results_path = RESULTS_FILE.replace("\\", "/")
    code = textwrap.dedent(
        f"""\
        # Auto-generated – run inside Maya via command port
        import sys, os, unittest, io

        for p in [r'O:/Cloud/Code/_scripts', r'O:/Cloud/Code/_scripts/mayatk/test']:
            if p not in sys.path:
                sys.path.insert(0, p)

        # Clear stale modules
        for k in list(sys.modules):
            if 'sequencer' in k or 'test_sequencer' in k:
                del sys.modules[k]

        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            SceneBlock, ShotSequencer as Sequencer,
        )
        from mayatk.anim_utils.shots.behaviors import (
            load_behavior, resolve_keys, apply_behavior,
        )
        import pymel.core as pm

        OUTPUT_FILE = r'{results_path}'


        class TestSceneBlockMaya(unittest.TestCase):
            def test_duration(self):
                b = SceneBlock(scene_id=0, name="A", start=10, end=40)
                self.assertEqual(b.duration, 30)

            def test_round_trip(self):
                seq = Sequencer([
                    SceneBlock(0, "S0", 0, 50, ["cube1"]),
                    SceneBlock(1, "S1", 60, 100, ["sphere1"]),
                ])
                data = seq.to_dict()
                restored = Sequencer.from_dict(data)
                self.assertEqual(len(restored.scenes), 2)
                self.assertEqual(restored.scene_by_id(0).name, "S0")


        class TestResolveKeysMaya(unittest.TestCase):
            def test_in_anchor(self):
                keys = resolve_keys(
                    {{"offset": 0, "duration": 10, "values": [0.0, 1.0], "anchor": "start"}},
                    start=100.0,
                    end=200.0,
                )
                self.assertAlmostEqual(keys[0]["time"], 100.0)
                self.assertAlmostEqual(keys[1]["time"], 110.0)

            def test_out_anchor(self):
                keys = resolve_keys(
                    {{"offset": 0, "duration": 20, "values": [1.0, 0.0], "anchor": "end"}},
                    start=100.0,
                    end=200.0,
                )
                self.assertAlmostEqual(keys[0]["time"], 180.0)
                self.assertAlmostEqual(keys[1]["time"], 200.0)


        class TestDetectScenes(unittest.TestCase):
            def setUp(self):
                pm.mel.file(new=True, force=True)

            def _make_cube(self, name, keys):
                cube = pm.polyCube(name=name)[0]
                for frame, value in keys.items():
                    pm.setKeyframe(cube, attribute="translateX", time=frame, value=value)
                return cube

            def test_single_object(self):
                cube = self._make_cube("box", {{1: 0, 10: 5, 20: 10}})
                seq = Sequencer.detect_scenes([cube])
                self.assertEqual(len(seq.scenes), 1)
                self.assertAlmostEqual(seq.scenes[0].start, 1.0)
                self.assertAlmostEqual(seq.scenes[0].end, 20.0)

            def test_gap_creates_two_scenes(self):
                c1 = self._make_cube("early", {{1: 0, 10: 5}})
                c2 = self._make_cube("late", {{100: 0, 110: 5}})
                seq = Sequencer.detect_scenes([c1, c2], gap_threshold=10)
                self.assertEqual(len(seq.scenes), 2)

            def test_overlapping_ranges_merge(self):
                c1 = self._make_cube("a", {{0: 0, 50: 10}})
                c2 = self._make_cube("b", {{30: 0, 80: 10}})
                seq = Sequencer.detect_scenes([c1, c2], gap_threshold=10)
                self.assertEqual(len(seq.scenes), 1)
                self.assertAlmostEqual(seq.scenes[0].start, 0.0)
                self.assertAlmostEqual(seq.scenes[0].end, 80.0)


        class TestRippleEdit(unittest.TestCase):
            def setUp(self):
                pm.mel.file(new=True, force=True)

            def _make_cube(self, name, keys):
                cube = pm.polyCube(name=name)[0]
                for frame, value in keys.items():
                    pm.setKeyframe(cube, attribute="translateX", time=frame, value=value)
                return cube

            def test_set_scene_duration_ripple(self):
                c1 = self._make_cube("a", {{0: 0, 50: 10}})
                c2 = self._make_cube("b", {{100: 0, 150: 10}})
                seq = Sequencer.detect_scenes([c1, c2], gap_threshold=10)

                scene0 = seq.scene_by_id(0)
                s1_start_before = seq.scene_by_id(1).start

                seq.set_scene_duration(0, scene0.duration + 20)

                self.assertAlmostEqual(
                    seq.scene_by_id(1).start, s1_start_before + 20, places=1
                )

            def test_set_scene_start_ripple(self):
                c1 = self._make_cube("x", {{0: 0, 30: 5}})
                c2 = self._make_cube("y", {{50: 0, 80: 5}})
                seq = Sequencer.detect_scenes([c1, c2], gap_threshold=10)

                s1_start_before = seq.scene_by_id(1).start
                seq.set_scene_start(0, 10, ripple=True)

                self.assertAlmostEqual(
                    seq.scene_by_id(1).start, s1_start_before + 10, places=1
                )


        class TestApplyBehavior(unittest.TestCase):
            def setUp(self):
                pm.mel.file(new=True, force=True)

            def test_fade_in_out_creates_keys(self):
                cube = pm.polyCube(name="fade_obj")[0]
                pm.setKeyframe(cube, attribute="translateX", time=0, value=0)
                pm.setKeyframe(cube, attribute="translateX", time=100, value=10)

                apply_behavior(str(cube), "fade_in_out", 0, 100, attrs=["visibility"])

                vis_keys = pm.keyframe(cube, attribute="visibility", query=True)
                self.assertIsNotNone(vis_keys)
                self.assertGreaterEqual(
                    len(vis_keys), 2, "Expected at least 2 visibility keys"
                )

            def test_behavior_not_found(self):
                cube = pm.polyCube(name="tmp")[0]
                with self.assertRaises(FileNotFoundError):
                    apply_behavior(str(cube), "nonexistent_xyz", 0, 100)


        class TestLoadBehavior(unittest.TestCase):
            def test_fade_in_out(self):
                t = load_behavior("fade_in_out")
                self.assertIn("attributes", t)
                self.assertIn("visibility", t["attributes"])
                vis = t["attributes"]["visibility"]
                self.assertEqual(vis["in"]["values"], [0.0, 1.0])
                self.assertEqual(vis["out"]["values"], [1.0, 0.0])


        class TestPersistence(unittest.TestCase):
            def setUp(self):
                pm.mel.file(new=True, force=True)
                Sequencer.delete_storage_node()

            def tearDown(self):
                Sequencer.delete_storage_node()

            def _make_cube(self, name, keys):
                cube = pm.polyCube(name=name)[0]
                for frame, value in keys.items():
                    pm.setKeyframe(cube, attribute="translateX", time=frame, value=value)
                return cube

            def test_save_creates_node(self):
                cube = self._make_cube("sc", {{0: 0, 50: 10}})
                seq = Sequencer([SceneBlock(0, "S0", 0, 50, [str(cube)])])
                node_name = seq.save()
                self.assertTrue(pm.objExists(node_name))
                self.assertEqual(pm.PyNode(node_name).nodeType(), "network")

            def test_save_load_round_trip(self):
                c1 = self._make_cube("rt_a", {{0: 0, 50: 10}})
                c2 = self._make_cube("rt_b", {{60: 0, 100: 5}})
                seq = Sequencer([
                    SceneBlock(0, "Alpha", 0, 50, [str(c1)]),
                    SceneBlock(1, "Beta", 60, 100, [str(c2)]),
                ])
                seq.save()
                loaded = Sequencer.load()
                self.assertIsNotNone(loaded)
                self.assertEqual(len(loaded.scenes), 2)
                self.assertEqual(loaded.scene_by_id(0).name, "Alpha")
                self.assertIn(str(c1), loaded.scene_by_id(0).objects)

            def test_load_resolves_renames(self):
                cube = self._make_cube("orig", {{0: 0, 50: 10}})
                seq = Sequencer([SceneBlock(0, "S", 0, 50, [str(cube)])])
                seq.save()
                pm.rename(cube, "renamed")
                loaded = Sequencer.load()
                self.assertIn("renamed", loaded.scene_by_id(0).objects)

            def test_delete_storage_node(self):
                seq = Sequencer([SceneBlock(0, "S", 0, 50)])
                seq.save()
                self.assertTrue(Sequencer.delete_storage_node())
                self.assertFalse(pm.objExists(Sequencer.STORAGE_NODE))
                self.assertFalse(Sequencer.delete_storage_node())

            def test_load_returns_none_when_empty(self):
                self.assertIsNone(Sequencer.load())


        # --- Run and write results ---
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        for cls in [TestSceneBlockMaya, TestResolveKeysMaya, TestDetectScenes,
                    TestRippleEdit, TestApplyBehavior, TestLoadBehavior,
                    TestPersistence]:
            suite.addTests(loader.loadTestsFromTestCase(cls))

        buf = io.StringIO()
        runner = unittest.TextTestRunner(stream=buf, verbosity=2)
        result = runner.run(suite)

        output = buf.getvalue()
        print(output)

        summary = (
            "\\nSUMMARY: Tests={{}} Fail={{}} Err={{}} Skip={{}}".format(
                result.testsRun, len(result.failures),
                len(result.errors), len(result.skipped),
            )
        )
        print(summary)

        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(output)
            f.write(summary)
            for t, tb in result.failures:
                f.write("\\nFAIL: {{}}\\n{{}}\\n".format(t, tb))
            for t, tb in result.errors:
                f.write("\\nERROR: {{}}\\n{{}}\\n".format(t, tb))
    """
    )
    os.makedirs(os.path.dirname(TEST_SCRIPT), exist_ok=True)
    with open(TEST_SCRIPT, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] Wrote test script: {TEST_SCRIPT}")


def main():
    _write_test_script()

    conn = maya_connection.MayaConnection.get_instance()
    print("[INFO] Launching NEW Maya instance for testing...")
    if not conn.connect(mode="auto", port=7002, force_new_instance=True):
        print("[ERROR] Failed to connect to Maya")
        sys.exit(1)
    print(f"[OK] Connected in {conn.mode} mode")

    # Tell Maya to run the file via runpy (proper module namespace)
    script_path = TEST_SCRIPT.replace("\\", "/")
    exec_cmd = f"import runpy; runpy.run_path(r'{script_path}', run_name='__main__')"
    print("[INFO] Sending exec command to Maya...")
    try:
        conn.execute(exec_cmd)
    except Exception as e:
        print(f"[ERROR] Execution failed: {e}")
        sys.exit(1)

    print("[OK] Tests dispatched. Waiting for results...")
    print(f"[INFO] Results file: {RESULTS_FILE}")

    # Poll for results file
    for _ in range(30):
        time.sleep(2)
        if os.path.exists(RESULTS_FILE):
            # Wait a beat for file to finish writing
            time.sleep(1)
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            if "SUMMARY:" in content:
                print("\n" + "=" * 70)
                print(content)
                print("=" * 70)
                return
    print("[WARN] Results file not written yet. Check Maya Script Editor.")


if __name__ == "__main__":
    main()
