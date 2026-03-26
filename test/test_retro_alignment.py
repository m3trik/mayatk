#!/usr/bin/env python
# coding=utf-8
"""Integration test: Run parse_csv + assess against a real Maya scene.

Tests the actual ShotManifest pipeline against:
  Scene: C130H_FCR_SPEEDRUN_copy.ma
  CSV:   Speed_Run_C-130H Rigging Verification - Sequence Doc.csv

This validates:
  1. parse_csv() produces sane BuilderSteps from the real CSV
  2. assess() identifies real naming mismatches when run against the live scene
  3. detect_shots() discovers animation clusters in the real scene
  4. Quantifies the gap between CSV names and scene object names

Run via: python mayatk/test/run_tests.py retro_alignment --extended
Or directly in a Maya standalone/port session.
"""
import unittest
import sys
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPTS_DIR = r"O:\Cloud\Code\_scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

SCENE_PATH = (
    r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
    r"\_tests\sequencer_test\C130H_FCR_SPEEDRUN_copy.ma"
)
CSV_PATH = (
    r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
    r"\_tests\seq_doc"
    r"\Speed_Run_C-130H Rigging Verification - Sequence Doc.csv"
)
RESULTS_DIR = Path(SCRIPTS_DIR) / "test" / "temp_tests"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Pureâ€‘Python imports (always available)
# ---------------------------------------------------------------------------
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    parse_csv,
    BuilderStep,
    BuilderObject,
    ShotManifest,
)
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from mayatk.anim_utils.shots._shots import ShotStore, ShotBlock

# ---------------------------------------------------------------------------
# Maya bootstrap (standalone)
# ---------------------------------------------------------------------------
HAS_MAYA = False
try:
    import pymel.core as pm

    # Quick sanity check â€” will succeed inside Maya or maya.standalone
    pm.about(version=True)
    HAS_MAYA = True
except Exception:
    # Fallback: try standalone bootstrap
    try:
        from mayatk.env_utils.maya_connection import MayaConnection

        _conn = MayaConnection.get_instance()
        if not _conn.is_connected:
            _conn.connect(mode="standalone")
        HAS_MAYA = _conn.is_connected
        if HAS_MAYA:
            import pymel.core as pm
    except Exception as exc:
        print(f"Maya bootstrap failed: {exc}")


# ======================================================================
# PHASE 1: Pure-Python CSV Parsing Tests (no Maya needed)
# ======================================================================
class TestParseCSVReal(unittest.TestCase):
    """Validate parse_csv() against the real C-130H CSV."""

    @classmethod
    def setUpClass(cls):
        if not Path(CSV_PATH).exists():
            raise unittest.SkipTest(f"CSV not found: {CSV_PATH}")
        cls.steps = parse_csv(CSV_PATH)

    def test_steps_nonempty(self):
        """CSV should parse into a non-trivial number of steps."""
        self.assertGreater(len(self.steps), 50, "Expected 50+ steps")

    def test_sections_present(self):
        """All expected sections (A, B, C) should be represented."""
        sections = {s.section for s in self.steps}
        for expected in ("A", "B", "C"):
            self.assertIn(expected, sections, f"Section {expected} missing")

    def test_step_ids_format(self):
        """Every step_id should match the pattern [A-Z]\\d+."""
        import re

        pattern = re.compile(r"^[A-Z]\d+$")
        for step in self.steps:
            self.assertRegex(
                step.step_id,
                pattern,
                f"Bad step_id: {step.step_id!r}",
            )

    def test_objects_are_asset_names_not_prose(self):
        """Object names should look like identifiers, not sentence fragments.

        The CSV has step-content text that should NOT leak into the
        asset list.  Flag any object name containing spaces or quotes.
        """
        prose_objects = []
        for step in self.steps:
            for obj in step.objects:
                if " " in obj.name or '"' in obj.name or len(obj.name) > 50:
                    prose_objects.append((step.step_id, obj.name[:60]))

        # Write findings regardless
        out = RESULTS_DIR / "csv_prose_leak.txt"
        with open(out, "w") as f:
            f.write(f"Total steps: {len(self.steps)}\n")
            f.write(f"Prose-like objects: {len(prose_objects)}\n\n")
            for sid, name in prose_objects:
                f.write(f"  {sid}: {name}\n")

        # This test documents the issue â€” it may fail
        if prose_objects:
            self.fail(
                f"{len(prose_objects)} object names look like prose, not asset "
                f"names. See {out}"
            )

    def test_no_duplicate_objects_per_step(self):
        """Each step shouldn't list the same object twice."""
        for step in self.steps:
            names = [o.name for o in step.objects]
            dupes = [n for n in names if names.count(n) > 1]
            self.assertEqual(
                len(set(dupes)),
                0,
                f"Step {step.step_id} has duplicate objects: {set(dupes)}",
            )

    def test_total_unique_assets(self):
        """Sanity check: we should find a reasonable number of unique assets."""
        all_names = {o.name for s in self.steps for o in s.objects}
        self.assertGreater(len(all_names), 20, "Too few unique asset names")
        self.assertLess(len(all_names), 300, "Far too many â€” content leaking?")

    def test_behaviors_detected(self):
        """At least some objects should have behaviors detected."""
        with_behavior = [o for s in self.steps for o in s.objects if o.behaviors]
        self.assertGreater(
            len(with_behavior),
            10,
            "Expected >10 objects with detected behaviors",
        )

    def test_write_parsed_steps_report(self):
        """Write full parsed output for manual inspection."""
        out = RESULTS_DIR / "parsed_steps.txt"
        with open(out, "w") as f:
            for step in self.steps:
                f.write(f"\n{'='*60}\n")
                f.write(f"Step: {step.step_id}  Section: {step.section}\n")
                f.write(f"Content: {step.description[:100]}\n")
                f.write(f"Objects ({len(step.objects)}):\n")
                for obj in step.objects:
                    f.write(f"  {obj.name:<40} behaviors={obj.behaviors}\n")
        # Always passes â€” just writes the report
        self.assertTrue(out.exists())


# ======================================================================
# PHASE 2: Maya Scene Tests (requires Maya + scene file)
# ======================================================================
@unittest.skipUnless(HAS_MAYA, "Maya not available")
class TestRealSceneExists(unittest.TestCase):
    """Validate the test scene can be opened."""

    @classmethod
    def setUpClass(cls):
        if not Path(SCENE_PATH).exists():
            raise unittest.SkipTest(f"Scene not found: {SCENE_PATH}")
        # Open the scene (don't create a new one â€” we need the real data)
        pm.openFile(SCENE_PATH, force=True)

    def test_scene_loaded(self):
        """Scene should have a non-trivial number of transforms."""
        transforms = pm.ls(type="transform")
        self.assertGreater(len(transforms), 100)

    def test_anim_curves_exist(self):
        """Scene should contain animation curves."""
        curves = pm.ls(type="animCurve")
        self.assertGreater(len(curves), 50)


@unittest.skipUnless(HAS_MAYA, "Maya not available")
class TestAssessAgainstRealScene(unittest.TestCase):
    """Run assess() with real scene data and measure the mismatch rate."""

    @classmethod
    def setUpClass(cls):
        if not Path(SCENE_PATH).exists() or not Path(CSV_PATH).exists():
            raise unittest.SkipTest("Scene or CSV not found")
        pm.openFile(SCENE_PATH, force=True)
        cls.steps = parse_csv(CSV_PATH)
        cls.store = ShotStore()  # Empty store â€” no shots built yet

    def test_assess_unbuilt(self):
        """With no shots built, every step should be marked 'not built'."""
        builder = ShotManifest(self.store)
        results = builder.assess(self.steps)
        for r in results:
            self.assertFalse(r.built, f"Step {r.step_id} unexpectedly built")

    def test_exists_fn_raw_csv_names(self):
        """Check which CSV asset names resolve via pm.objExists directly."""
        all_objects = {o.name for s in self.steps for o in s.objects}

        found = set()
        missing = set()
        for name in all_objects:
            if pm.objExists(name):
                found.add(name)
            else:
                missing.add(name)

        out = RESULTS_DIR / "exists_check.txt"
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"Total unique objects from CSV: {len(all_objects)}\n")
            f.write(f"Found in scene (exact match): {len(found)}\n")
            f.write(f"Missing from scene: {len(missing)}\n\n")

            f.write("--- FOUND (exact match) ---\n")
            for n in sorted(found):
                f.write(f"  {n}\n")

            f.write("\n--- MISSING ---\n")
            for n in sorted(missing):
                f.write(f"  {n}\n")

        # Report metrics â€” this test always passes but writes data
        rate = len(found) / max(len(all_objects), 1) * 100
        print(f"\nExact match rate: {rate:.1f}% ({len(found)}/{len(all_objects)})")
        print(f"Results written to {out}")


@unittest.skipUnless(HAS_MAYA, "Maya not available")
class TestDetectShotsRealScene(unittest.TestCase):
    """Run detect_shots() against the real scene animation."""

    @classmethod
    def setUpClass(cls):
        if not Path(SCENE_PATH).exists():
            raise unittest.SkipTest(f"Scene not found: {SCENE_PATH}")
        pm.openFile(SCENE_PATH, force=True)

    def test_detect_shots_default_threshold(self):
        """detect_shots() should find animation clusters."""
        seq = ShotSequencer(store=ShotStore())
        candidates = seq.detect_shots(gap_threshold=10.0)

        out = RESULTS_DIR / "detected_shots.txt"
        with open(out, "w") as f:
            f.write(f"Gap threshold: 10.0\n")
            f.write(f"Detected shots: {len(candidates)}\n\n")
            for c in candidates:
                f.write(
                    f"  {c['name']}: frames {c['start']:.0f}-{c['end']:.0f}  "
                    f"objects: {len(c['objects'])} {c['objects'][:5]}\n"
                )

        self.assertGreater(len(candidates), 0, "No shots detected")
        print(f"\nDetected {len(candidates)} shots. Results: {out}")

    def test_find_keyed_transforms(self):
        """Verify _find_keyed_transforms returns data for known animated objects."""
        seq = ShotSequencer(store=ShotStore())
        # We know from the offline analysis these objects have animation:
        known_animated = [
            "S00A11_ARROW_LOC",
            "S00B31_RUDDER_LOC",
            "REGGIE_LOC",
        ]
        timeline_start = pm.playbackOptions(q=True, min=True)
        timeline_end = pm.playbackOptions(q=True, max=True)

        results = seq._find_keyed_transforms(timeline_start, timeline_end)

        out = RESULTS_DIR / "keyed_transforms.txt"
        with open(out, "w") as f:
            f.write(f"Timeline: {timeline_start}-{timeline_end}\n")
            f.write(f"Total keyed transforms: {len(results)}\n\n")
            for obj_name, start, end in sorted(results, key=lambda x: x[1]):
                f.write(f"  {obj_name:<45} {start:>8.0f} - {end:>8.0f}\n")

        result_names = {r[0] for r in results}
        for name in known_animated:
            if pm.objExists(name):
                self.assertIn(
                    name,
                    result_names,
                    f"Known animated object {name} not found in results",
                )

        print(f"\nFound {len(results)} keyed transforms. Results: {out}")


@unittest.skipUnless(HAS_MAYA, "Maya not available")
class TestBuildAndAssessFullPipeline(unittest.TestCase):
    """Full pipeline: parse CSV â†’ build shots â†’ assess â†’ report."""

    @classmethod
    def setUpClass(cls):
        if not Path(SCENE_PATH).exists() or not Path(CSV_PATH).exists():
            raise unittest.SkipTest("Scene or CSV not found")
        pm.openFile(SCENE_PATH, force=True)
        cls.steps = parse_csv(CSV_PATH)

    def test_build_then_assess(self):
        """Build shots from CSV, then assess against scene. Full pipeline."""
        store = ShotStore()
        builder = ShotManifest(store)

        # Build all shots
        actions = builder.update(self.steps)
        created = sum(1 for v in actions.values() if v == "created")
        self.assertGreater(created, 0, "No shots created")

        # Now assess
        results = builder.assess(self.steps)

        # Tally
        statuses = {}
        for r in results:
            for o in r.objects:
                statuses.setdefault(o.status, []).append(
                    (r.step_id, o.name, o.behaviors)
                )

        out = RESULTS_DIR / "full_pipeline.txt"
        with open(out, "w") as f:
            f.write(f"Steps parsed: {len(self.steps)}\n")
            f.write(f"Shots created: {created}\n")
            f.write(f"Assessment results:\n\n")

            for status, items in sorted(statuses.items()):
                f.write(f"\n--- {status.upper()} ({len(items)}) ---\n")
                for step_id, name, behavior in items:
                    f.write(f"  {step_id}: {name:<40} behavior={behavior}\n")

            # Additional objects (in shots but not in CSV)
            f.write(f"\n--- ADDITIONAL OBJECTS ---\n")
            for r in results:
                if r.additional_objects:
                    f.write(f"  {r.step_id}: {r.additional_objects}\n")

        total_objs = sum(len(r.objects) for r in results)
        n_valid = len(statuses.get("valid", []))
        n_missing = len(statuses.get("missing_object", []))
        n_miss_beh = len(statuses.get("missing_behavior", []))
        print(f"\nFull pipeline: {total_objs} objects assessed")
        print(
            f"  valid: {n_valid}, missing_object: {n_missing}, missing_behavior: {n_miss_beh}"
        )
        print(f"Results: {out}")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
