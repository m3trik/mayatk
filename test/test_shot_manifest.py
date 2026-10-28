#!/usr/bin/python
# coding=utf-8
"""Tests for the Shot Manifest align-mode features.

Covers:
    - parse_csv and detect_behavior (existing)
    - detect_shot_regions (unified detection)
    - ShotManifest.update() with ranges (Stage 6)
    - ShotManifest.update() without ranges (baseline)
    - _resolve_ranges: user pins, auto-fill, gap integration (Stages 3-4)
    - _validate_range_collisions (Stage 5)
    - _user_ranges persistence across table rebuilds (Stage 3)
    - Editable range column read-only post-build (Stage 7)
    - Context menu actions: set-to-current-frame, clear-range (Stage 8)

All tests run WITHOUT Maya by mocking maya.cmds/cmds.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import shared Maya mocks from conftest (injected into sys.modules there)
# ---------------------------------------------------------------------------
from conftest import mock_cmds  # noqa: E402  (test dir on sys.path)

# Aliases for backward-compat with existing test code
_mock_cmds = mock_cmds

# Ensure workspace roots are on sys.path
_WORKSPACE = Path(__file__).parent.parent.parent.absolute()
for subdir in ("pythontk", "uitk", "mayatk"):
    p = str(_WORKSPACE / subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderStep,
    BuilderObject,
    ShotManifest,
    ObjectStatus,
    StepStatus,
    ManifestModel,
)
from pythontk.core_utils.engines.shots.manifest.mapping import Mapping
from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors
from mayatk.anim_utils.shots.shot_manifest.range_resolver import RangeResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_steps(*names, behaviors=None):
    """Create a list of BuilderSteps with one object each."""
    if behaviors is None:
        behaviors = ["fade_in", "fade_out"]
    steps = []
    for name in names:
        step = BuilderStep(
            step_id=name,
            section="A",
            section_title="Section A",
            description=f"Content for {name}",
        )
        step.objects.append(
            BuilderObject(name=f"obj_{name}", behaviors=list(behaviors))
        )
        steps.append(step)
    return steps


def _fresh_store():
    """Create and activate a fresh ShotStore."""
    ShotStore._persistence = None  # clear class-level persistence from prior tests
    store = ShotStore()
    ShotStore._active = store
    return store


class TestDetectBehaviors(unittest.TestCase):
    def test_fade_in(self):
        self.assertEqual(
            ManifestModel.detect_behaviors("Object fades in from black"), ["fade_in"]
        )

    def test_fade_out(self):
        self.assertEqual(
            ManifestModel.detect_behaviors("Object fades out slowly"), ["fade_out"]
        )

    def test_fade_in_and_out(self):
        self.assertEqual(
            ManifestModel.detect_behaviors("Fades in then fades out"),
            ["fade_in", "fade_out"],
        )

    def test_no_behavior(self):
        self.assertEqual(ManifestModel.detect_behaviors("Object sits still"), [])


class TestUpdateBaseline(unittest.TestCase):
    """Test update() without ranges - sequential cursor placement."""

    def setUp(self):
        self.store = _fresh_store()
        self.assembler = ShotManifest(self.store)
        self.steps = _make_steps("A01", "A02", "A03")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_creates_shots_sequentially(self, mock_dur):
        mock_dur.return_value = 30.0
        # Use initial_shot_length=30 so cursor placement matches the
        # 30-frame contract this test asserts (resolve_duration uses
        # initial_shot_length, not the mocked compute_duration).
        actions = self.assembler.update(self.steps, initial_shot_length=30)

        self.assertEqual(actions["A01"], "created")
        self.assertEqual(actions["A02"], "created")
        self.assertEqual(actions["A03"], "created")
        self.assertEqual(len(self.store.shots), 3)

        shots = self.store.sorted_shots()
        self.assertEqual(shots[0].start, 1)  # cursor starts at 1
        self.assertEqual(shots[0].end, 31)
        self.assertEqual(shots[1].start, 31)
        self.assertEqual(shots[1].end, 61)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_removes_shots_not_in_csv(self, mock_dur):
        mock_dur.return_value = 30.0
        self.assembler.update(self.steps)
        # Now rebuild with A01 and A03 only
        reduced = _make_steps("A01", "A03")
        actions = self.assembler.update(reduced)
        self.assertEqual(actions["A02"], "removed")
        names = {s.name for s in self.store.shots}
        self.assertNotIn("A02", names)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_skips_locked_shots(self, mock_dur):
        mock_dur.return_value = 30.0
        self.assembler.update(self.steps)
        # Lock A02
        for s in self.store.shots:
            if s.name == "A02":
                s.locked = True
        actions = self.assembler.update(self.steps)
        self.assertEqual(actions["A02"], "locked")


class TestUpdateWithRanges(unittest.TestCase):
    """Test update() with user-provided ranges for shot placement."""

    def setUp(self):
        self.store = _fresh_store()
        self.assembler = ShotManifest(self.store)
        self.steps = _make_steps("A01", "A02", "A03")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_new_shots_use_provided_ranges(self, mock_dur):
        mock_dur.return_value = 30.0
        ranges = {
            "A01": (100.0, 200.0),
            "A02": (250.0, 350.0),
            "A03": (400.0, 500.0),
        }
        # initial_shot_length=100 so resolve_duration uses 100, matching
        # the user-supplied range widths (extend_only floor would otherwise
        # stretch each shot to 200f and ignore the upper bound).
        self.assembler.update(self.steps, ranges=ranges, initial_shot_length=100)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 100.0)
        self.assertEqual(shots["A01"].end, 200.0)
        self.assertEqual(shots["A02"].start, 250.0)
        self.assertEqual(shots["A02"].end, 350.0)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_existing_shots_repositioned_by_ranges(self, mock_dur):
        """Existing shots should be repositioned when ranges differ.

        Bug context: Without range support, rebuild would leave existing
        shots at their original positions even when the user specified
        different frame ranges in the align-mode column.
        """
        mock_dur.return_value = 30.0
        # Initial build without ranges
        self.assembler.update(self.steps)

        # Rebuild with ranges - should reposition
        ranges = {
            "A01": (500.0, 600.0),
            "A02": (700.0, 800.0),
            "A03": (900.0, 1000.0),
        }
        actions = self.assembler.update(self.steps, ranges=ranges)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 500.0)
        self.assertEqual(shots["A01"].end, 600.0)
        # repositioned -> patched
        self.assertEqual(actions["A01"], "patched")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_partial_ranges_fallback_to_cursor(self, mock_dur):
        """Steps without explicit ranges should use cursor placement."""
        mock_dur.return_value = 30.0
        ranges = {"A01": (100.0, 200.0)}  # only A01 has a range
        # initial_shot_length=30 so cursor-placed shots have 30-frame durations.
        self.assembler.update(self.steps, ranges=ranges, initial_shot_length=30)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 100.0)
        self.assertEqual(shots["A01"].end, 200.0)
        # A02 and A03 should be cursor-placed after A01
        self.assertEqual(shots["A02"].start, 200.0)
        self.assertEqual(shots["A02"].end, 230.0)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_locked_shots_not_repositioned(self, mock_dur):
        """Locked shots must not be repositioned even with ranges."""
        mock_dur.return_value = 30.0
        self.assembler.update(self.steps)
        for s in self.store.shots:
            if s.name == "A02":
                s.locked = True
                original_start = s.start
                original_end = s.end

        ranges = {"A02": (999.0, 1099.0)}
        actions = self.assembler.update(self.steps, ranges=ranges)

        shot = next(s for s in self.store.shots if s.name == "A02")
        self.assertEqual(shot.start, original_start)
        self.assertEqual(shot.end, original_end)
        self.assertEqual(actions["A02"], "locked")


class TestFromDetection(unittest.TestCase):
    """Test BuilderStep.from_detection() factory classmethod."""

    def test_basic_conversion(self):
        """Candidates convert to BuilderSteps with correct step_ids, objects, and ranges."""
        candidates = [
            {
                "name": "Shot 1",
                "start": 10.0,
                "end": 50.0,
                "objects": ["ctrl_A", "ctrl_B"],
            },
            {"name": "Shot 2", "start": 60.0, "end": 120.0, "objects": ["mesh_C"]},
        ]
        steps, ranges = BuilderStep.from_detection(candidates)

        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].step_id, "Shot 1")
        self.assertEqual(steps[1].step_id, "Shot 2")
        self.assertEqual(len(steps[0].objects), 2)
        self.assertEqual(steps[0].objects[0].name, "ctrl_A")
        self.assertEqual(steps[0].objects[1].name, "ctrl_B")
        self.assertEqual(len(steps[1].objects), 1)
        self.assertEqual(ranges["Shot 1"], (10.0, 50.0))
        self.assertEqual(ranges["Shot 2"], (60.0, 120.0))

    def test_empty_candidates(self):
        """Empty candidates list returns empty steps and ranges."""
        steps, ranges = BuilderStep.from_detection([])
        self.assertEqual(steps, [])
        self.assertEqual(ranges, {})

    def test_content_empty_for_user_editing(self):
        """Content field is empty so the user can add their own description."""
        candidates = [
            {"name": "Shot 1", "start": 1.0, "end": 10.0, "objects": ["a", "b", "c"]},
        ]
        steps, _ = BuilderStep.from_detection(candidates)
        self.assertEqual(steps[0].description, "")

    def test_zero_objects(self):
        """Candidate with no objects produces a step with empty objects list."""
        candidates = [{"name": "S1", "start": 0.0, "end": 10.0, "objects": []}]
        steps, ranges = BuilderStep.from_detection(candidates)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].objects, [])
        self.assertEqual(steps[0].description, "")
        self.assertEqual(ranges["S1"], (0.0, 10.0))


class TestRemoveMissing(unittest.TestCase):
    """Test update() with remove_missing=False."""

    def setUp(self):
        self.store = _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30.0,
    )
    def test_remove_missing_true_deletes_absent(self, mock_dur):
        """Default behavior: shots not in steps are removed."""
        builder = ShotManifest(self.store)
        steps = _make_steps("A01", "A02")
        builder.update(steps)
        self.assertEqual(len(self.store.shots), 2)

        # Now update with only A01 - A02 should be removed
        steps2 = _make_steps("A01")
        actions = builder.update(steps2, remove_missing=True)
        self.assertEqual(actions.get("A02"), "removed")
        self.assertEqual(len(self.store.shots), 1)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30.0,
    )
    def test_remove_missing_false_preserves_absent(self, mock_dur):
        """Detection mode: existing shots not in steps are preserved."""
        builder = ShotManifest(self.store)
        steps = _make_steps("A01", "A02")
        builder.update(steps)
        self.assertEqual(len(self.store.shots), 2)

        # Now update with only A01 - A02 should be kept
        steps2 = _make_steps("A01")
        actions = builder.update(steps2, remove_missing=False)
        self.assertNotIn("A02", actions)
        self.assertEqual(len(self.store.shots), 2)


class TestFromDetectionMalformed(unittest.TestCase):
    """Test that from_detection() handles malformed candidates gracefully."""

    def test_missing_name_skipped(self):
        """Candidates missing 'name' are skipped with a warning."""
        candidates = [
            {"start": 0.0, "end": 10.0, "objects": []},
            {"name": "S1", "start": 10.0, "end": 20.0, "objects": []},
        ]
        steps, ranges = BuilderStep.from_detection(candidates)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].step_id, "S1")

    def test_missing_start_skipped(self):
        """Candidates missing 'start' are skipped."""
        candidates = [
            {"name": "S1", "end": 10.0, "objects": []},
        ]
        steps, ranges = BuilderStep.from_detection(candidates)
        self.assertEqual(len(steps), 0)

    def test_missing_end_skipped(self):
        """Candidates missing 'end' are skipped."""
        candidates = [
            {"name": "S1", "start": 0.0, "objects": []},
        ]
        steps, ranges = BuilderStep.from_detection(candidates)
        self.assertEqual(len(steps), 0)


class TestCrossScenePrefs(unittest.TestCase):
    """Verify detection_mode survives across scenes via the shared prefs store.

    Prefs live in the engine's cross-DCC JSON store
    (``user_config_root()/shots/prefs.json`` — shared with blendertk); legacy
    ``QSettings("uitk", "shots")`` values are migrated once when the JSON file
    is absent (see ``ShotStore._restore_user_prefs``).  Every test sandboxes
    ``_prefs_dir_override`` so the user's real prefs file is never touched.
    """

    def setUp(self):
        ShotStore._active = None
        ShotStore._persistence = None
        self._tmpdir = tempfile.TemporaryDirectory()
        ShotStore._prefs_dir_override = self._tmpdir.name

    def tearDown(self):
        ShotStore._active = None
        ShotStore._persistence = None
        # Remove the subclass shadow so any suite-level override (or the
        # engine default) is restored.
        if "_prefs_dir_override" in ShotStore.__dict__:
            del ShotStore._prefs_dir_override
        self._tmpdir.cleanup()

    def _prefs_file(self):
        return Path(self._tmpdir.name) / "shots_prefs.json"

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_fresh_store_restores_detection_mode_from_qsettings(self, mock_qs_cls):
        """Legacy QSettings apply (and write through) when no prefs file exists."""
        mock_qs = MagicMock()
        mock_qs.value.side_effect = lambda key, *a: {
            "ShotStore/detection_mode": "skip_zero",
        }.get(key)
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store._restore_user_prefs()

        self.assertEqual(store.detection_mode, "skip_zero")
        # Migration writes through so the legacy read never re-runs.
        self.assertTrue(self._prefs_file().exists())

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_legacy_qsettings_migrated_to_detection_mode(self, mock_qs_cls):
        """Legacy use_selected_keys + key_filter_mode QSettings are migrated."""
        mock_qs = MagicMock()
        mock_qs.value.side_effect = lambda key, *a: {
            "ShotStore/detection_mode": None,
            "ShotStore/use_selected_keys": True,
            "ShotStore/key_filter_mode": "skip_zero",
        }.get(key)
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store._restore_user_prefs()

        self.assertEqual(store.detection_mode, "skip_zero")

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_save_writes_detection_mode_to_prefs_store(self, mock_qs_cls):
        """_save_user_prefs persists to the JSON store — QSettings is read-only legacy."""
        import json as _json

        mock_qs = MagicMock()
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store.detection_mode = "zero_as_end"
        store._save_user_prefs()

        data = _json.loads(self._prefs_file().read_text(encoding="utf-8"))
        self.assertEqual(data["detection_mode"], "zero_as_end")
        mock_qs.setValue.assert_not_called()

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_prefs_file_wins_over_legacy_qsettings(self, mock_qs_cls):
        """Once the JSON store exists, legacy QSettings are never consulted."""
        store = ShotStore()
        store.detection_mode = "zero_as_end"
        store._save_user_prefs()

        mock_qs = MagicMock()
        mock_qs.value.return_value = "skip_zero"
        mock_qs_cls.return_value = mock_qs

        fresh = ShotStore()
        fresh._restore_user_prefs()

        self.assertEqual(fresh.detection_mode, "zero_as_end")
        mock_qs.value.assert_not_called()

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_qsettings_ignored_when_persistence_has_data(self, mock_qs_cls):
        """QSettings must NOT override values loaded from per-scene persistence."""
        mock_persistence = MagicMock()
        mock_persistence.load.return_value = {
            "shots": [],
            "detection_mode": "auto",
        }

        mock_qs = MagicMock()
        mock_qs.value.return_value = "skip_zero"
        mock_qs_cls.return_value = mock_qs

        ShotStore._persistence = mock_persistence
        store = ShotStore.active()

        self.assertEqual(store.detection_mode, "auto")

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_legacy_persistence_data_migrated(self, mock_qs_cls):
        """Legacy per-scene data with use_selected_keys is migrated to detection_mode."""
        mock_persistence = MagicMock()
        mock_persistence.load.return_value = {
            "shots": [],
            "use_selected_keys": True,
            "key_filter_mode": "zero_as_end",
        }

        mock_qs = MagicMock()
        mock_qs.value.return_value = None
        mock_qs_cls.return_value = mock_qs

        ShotStore._persistence = mock_persistence
        store = ShotStore.active()

        self.assertEqual(store.detection_mode, "zero_as_end")

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_fresh_store_no_qsettings_data_uses_default(self, mock_qs_cls):
        """When QSettings has no saved value, default (auto) is used."""
        mock_qs = MagicMock()
        mock_qs.value.return_value = None
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store._restore_user_prefs()

        self.assertEqual(store.detection_mode, "auto")


class TestZeroDurationFallback(unittest.TestCase):
    """Test update() with zero_duration_fallback=True.

    New shots without explicit ranges get zero duration (start == end)
    instead of using compute_duration.
    Fixed: 2026-04-03
    """

    def setUp(self):
        self.store = _fresh_store()
        self.store.gap = 5.0
        self.assembler = ShotManifest(self.store)
        # Pre-build two shots
        self.store.define_shot("A01", 1, 31, ["obj_A01"])
        self.store.define_shot("A02", 31, 61, ["obj_A02"])

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_new_shot_gets_zero_duration(self, mock_dur):
        mock_dur.return_value = 30.0
        steps = _make_steps("A01", "A02", "A03")
        # Provide ranges for existing shots, omit A03
        ranges = {"A01": (1.0, 31.0), "A02": (31.0, 61.0)}
        actions = self.assembler.update(
            steps, ranges=ranges, zero_duration_fallback=True
        )
        self.assertEqual(actions["A03"], "created")
        shot = next(s for s in self.store.shots if s.name == "A03")
        self.assertEqual(shot.start, shot.end, "New shot should have zero duration")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_zero_duration_false_uses_compute_duration(self, mock_dur):
        mock_dur.return_value = 30.0
        steps = _make_steps("A01", "A02", "A03")
        ranges = {"A01": (1.0, 31.0), "A02": (31.0, 61.0)}
        actions = self.assembler.update(
            steps, ranges=ranges, zero_duration_fallback=False
        )
        self.assertEqual(actions["A03"], "created")
        shot = next(s for s in self.store.shots if s.name == "A03")
        self.assertGreater(shot.end, shot.start, "Should use compute_duration")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_consecutive_zero_duration_shots_dont_stack(self, mock_dur):
        """Multiple new zero-duration shots should each get a unique position.

        Cursor must advance by store.gap between zero-duration shots to
        prevent them from stacking at the same frame.
        """
        mock_dur.return_value = 30.0
        steps = _make_steps("A01", "A02", "A03", "A04", "A05")
        # Only A01 and A02 have ranges; A03, A04, A05 are new
        ranges = {"A01": (1.0, 31.0), "A02": (31.0, 61.0)}
        self.assembler.update(steps, ranges=ranges, zero_duration_fallback=True)
        new_shots = sorted(
            [s for s in self.store.shots if s.name in ("A03", "A04", "A05")],
            key=lambda s: s.start,
        )
        self.assertEqual(len(new_shots), 3)
        # Each should be at a distinct position
        starts = [s.start for s in new_shots]
        self.assertEqual(len(set(starts)), 3, f"Starts should be unique: {starts}")
        # Gap between consecutive zero-duration shots should be store.gap (5.0)
        self.assertAlmostEqual(starts[1] - starts[0], 5.0)
        self.assertAlmostEqual(starts[2] - starts[1], 5.0)


class TestIncrementalBuildWithUserRange(unittest.TestCase):
    """Verify that a user-entered range on a new shot overrides zero-duration
    in incremental build mode.

    Flow: CSV has new step A03, user enters range (100, 150) in table,
    build runs incremental — A03 should get (100, 150), not zero-duration.
    Fixed: 2026-04-03
    """

    def setUp(self):
        self.store = _fresh_store()
        self.store.gap = 5.0
        self.assembler = ShotManifest(self.store)
        self.store.define_shot("A01", 1, 31, ["obj_A01"])
        self.store.define_shot("A02", 31, 61, ["obj_A02"])

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_explicit_range_overrides_zero_duration(self, mock_dur):
        """New shot with explicit range should use that range, not zero-duration."""
        mock_dur.return_value = 30.0
        steps = _make_steps("A01", "A02", "A03")
        # Existing shots keep positions, A03 gets user-entered range
        ranges = {
            "A01": (1.0, 31.0),
            "A02": (31.0, 61.0),
            "A03": (100.0, 150.0),
        }
        actions = self.assembler.update(
            steps, ranges=ranges, zero_duration_fallback=True
        )
        self.assertEqual(actions["A03"], "created")
        shot = next(s for s in self.store.shots if s.name == "A03")
        self.assertEqual(shot.start, 100.0)
        self.assertEqual(shot.end, 150.0)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_mixed_explicit_and_zero_duration(self, mock_dur):
        """Steps with ranges get those ranges; steps without get zero-duration."""
        mock_dur.return_value = 30.0
        steps = _make_steps("A01", "A02", "A03", "A04")
        # A03 has a user range, A04 does not
        ranges = {
            "A01": (1.0, 31.0),
            "A02": (31.0, 61.0),
            "A03": (100.0, 150.0),
        }
        self.assembler.update(steps, ranges=ranges, zero_duration_fallback=True)
        a03 = next(s for s in self.store.shots if s.name == "A03")
        a04 = next(s for s in self.store.shots if s.name == "A04")
        self.assertEqual(a03.start, 100.0)
        self.assertEqual(a03.end, 150.0)
        self.assertEqual(a04.start, a04.end, "A04 should have zero duration")


class TestIsDetectionRelevant(unittest.TestCase):
    """ShotStore.is_detection_relevant reflects whether shots exist."""

    def test_true_when_empty(self):
        store = _fresh_store()
        self.assertTrue(store.is_detection_relevant)

    def test_false_when_shots_exist(self):
        store = _fresh_store()
        store.define_shot("A01", 1, 31, ["obj"])
        self.assertFalse(store.is_detection_relevant)

    def test_becomes_true_after_removing_all_shots(self):
        store = _fresh_store()
        shot = store.define_shot("A01", 1, 31, ["obj"])
        self.assertFalse(store.is_detection_relevant)
        store.remove_shot(shot.shot_id)
        self.assertTrue(store.is_detection_relevant)


class TestStoreDetectRegions(unittest.TestCase):
    """ShotStore.detect_regions() dispatches by detection_mode."""

    def setUp(self):
        self.store = _fresh_store()

    @patch(
        "mayatk.anim_utils.shots._shots.Detection.detect_shot_regions",
        return_value=[{"name": "R1", "start": 1, "end": 30, "objects": []}],
    )
    def test_auto_mode_calls_detect_shot_regions(self, mock_auto):
        self.store.detection_mode = "auto"
        self.store.detection_threshold = 7.0
        result = self.store.detect_regions()
        mock_auto.assert_called_once_with(gap_threshold=7.0)
        self.assertEqual(len(result), 1)

    @patch(
        "mayatk.anim_utils.shots._shots.Detection.regions_from_selected_keys",
        return_value=[{"name": "K1", "start": 10, "end": 40, "objects": []}],
    )
    def test_skip_zero_mode_calls_selected_keys(self, mock_sel):
        self.store.detection_mode = "skip_zero"
        self.store.detection_threshold = 3.0
        result = self.store.detect_regions()
        mock_sel.assert_called_once_with(gap_threshold=3.0, key_filter="skip_zero")
        self.assertEqual(len(result), 1)

    @patch(
        "mayatk.anim_utils.shots._shots.Detection.regions_from_selected_keys",
        return_value=[],
    )
    def test_zero_as_end_mode_calls_selected_keys(self, mock_sel):
        self.store.detection_mode = "zero_as_end"
        self.store.detection_threshold = 5.0
        self.store.detect_regions()
        mock_sel.assert_called_once_with(gap_threshold=5.0, key_filter="zero_as_end")


class TestOverlapsExisting(unittest.TestCase):
    """ShotStore._overlaps_existing detects range overlaps."""

    def setUp(self):
        self.store = _fresh_store()
        self.store.define_shot("A01", 10, 30, [])

    def test_overlap_before(self):
        self.assertTrue(self.store._overlaps_existing({"start": 5, "end": 15}))

    def test_overlap_after(self):
        self.assertTrue(self.store._overlaps_existing({"start": 25, "end": 40}))

    def test_contained(self):
        self.assertTrue(self.store._overlaps_existing({"start": 15, "end": 25}))

    def test_no_overlap_before(self):
        self.assertFalse(self.store._overlaps_existing({"start": 1, "end": 10}))

    def test_no_overlap_after(self):
        self.assertFalse(self.store._overlaps_existing({"start": 30, "end": 50}))

    def test_exact_match(self):
        """Exact same range counts as overlap."""
        self.assertTrue(self.store._overlaps_existing({"start": 10, "end": 30}))


class TestDetectAndDefine(unittest.TestCase):
    """ShotStore.detect_and_define() detects and creates shots."""

    def setUp(self):
        self.store = _fresh_store()
        self.regions = [
            {"name": "R1", "start": 1, "end": 30, "objects": ["obj_a"]},
            {"name": "R2", "start": 40, "end": 70, "objects": ["obj_b"]},
        ]

    @patch("mayatk.anim_utils.shots._shots.Detection.detect_shot_regions")
    def test_creates_all_shots(self, mock_detect):
        mock_detect.return_value = self.regions
        created = self.store.detect_and_define()
        self.assertEqual(len(created), 2)
        self.assertEqual(len(self.store.shots), 2)
        self.assertEqual(created[0].name, "R1")
        self.assertEqual(created[1].name, "R2")

    @patch("mayatk.anim_utils.shots._shots.Detection.detect_shot_regions")
    def test_skips_overlapping_by_default(self, mock_detect):
        self.store.define_shot("existing", 20, 50, [])
        mock_detect.return_value = self.regions
        created = self.store.detect_and_define()
        # R1 overlaps [20,50], R2 overlaps [20,50] — both skipped
        self.assertEqual(len(created), 0)
        self.assertEqual(len(self.store.shots), 1)  # only "existing"

    @patch("mayatk.anim_utils.shots._shots.Detection.detect_shot_regions")
    def test_overwrite_creates_overlapping(self, mock_detect):
        self.store.define_shot("existing", 20, 50, [])
        mock_detect.return_value = self.regions
        created = self.store.detect_and_define(overwrite=True)
        self.assertEqual(len(created), 2)
        self.assertEqual(len(self.store.shots), 3)

    @patch("mayatk.anim_utils.shots._shots.Detection.detect_shot_regions")
    def test_fires_single_batch_complete(self, mock_detect):
        mock_detect.return_value = self.regions
        events = []
        self.store.add_listener(lambda e: events.append(type(e).__name__))
        self.store.detect_and_define()
        batch_events = [e for e in events if e == "BatchComplete"]
        self.assertEqual(len(batch_events), 1)


class TestStoreAssess(unittest.TestCase):
    """ShotStore.assess() checks object existence."""

    def setUp(self):
        self.store = _fresh_store()

    def test_no_objects_is_valid(self):
        self.store.define_shot("A01", 1, 30, [])
        result = self.store.assess()
        shot = self.store.shots[0]
        self.assertEqual(result[shot.shot_id], "valid")

    def _patch_cmds_ls(self, return_value):
        """Patch ``cmds.ls`` on the *real* maya.cmds module.

        ``import maya.cmds as cmds`` rebinds via the maya module's .cmds
        attribute (not via sys.modules), so when conftest replaces
        sys.modules['maya.cmds'] with a MagicMock, production code still
        gets the real cmds. We patch the real module directly so the
        production-side import sees the mocked ``ls``.

        Routes only the object-existence calls (``cmds.ls([objs],
        long=True)``) used by ``ShotStore.assess``; defers anything else
        to the real cmds.ls so unrelated scene queries (animCurves, etc.)
        keep working.
        """
        import maya

        real_cmds = maya.cmds
        original_ls = real_cmds.ls

        def smart_ls(*args, **kwargs):
            if args and isinstance(args[0], list) and kwargs.get("long") is True:
                return return_value
            return original_ls(*args, **kwargs)

        return patch.object(real_cmds, "ls", side_effect=smart_ls)

    def test_all_objects_exist(self):
        self.store.define_shot("A01", 1, 30, ["|obj_a", "|obj_b"])
        with self._patch_cmds_ls(["|obj_a", "|obj_b"]):
            result = self.store.assess()
        shot = self.store.shots[0]
        self.assertEqual(result[shot.shot_id], "valid")

    def test_missing_object(self):
        self.store.define_shot("A01", 1, 30, ["|obj_a", "|obj_b"])
        with self._patch_cmds_ls(["|obj_a"]):  # only 1 of 2
            result = self.store.assess()
        shot = self.store.shots[0]
        self.assertEqual(result[shot.shot_id], "missing_object")

    def tearDown(self):
        _mock_cmds.ls.return_value = []


class TestAudioExcludedFromShotObjects(unittest.TestCase):
    """Audio BuilderObjects must not leak into ShotBlock.objects."""

    def setUp(self):
        self.store = _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_creation_excludes_audio_from_shot_objects(self, mock_dur):
        """Audio object names are not in store shot.objects after creation."""
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="Sec",
            description="d",
        )
        step.objects.append(BuilderObject(name="obj_A01"))
        step.objects.append(BuilderObject(name="narration_A01", kind="audio"))
        builder = ShotManifest(self.store)
        builder.update([step])

        shot = self.store.shots[0]
        self.assertIn("obj_A01", shot.objects)
        self.assertNotIn("narration_A01", shot.objects)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_metadata_still_contains_audio(self, mock_dur):
        """Audio object is serialized in metadata.csv_objects for round-trip."""
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="Sec",
            description="d",
        )
        step.objects.append(BuilderObject(name="obj_A01"))
        step.objects.append(BuilderObject(name="clip_A01", kind="audio"))
        builder = ShotManifest(self.store)
        builder.update([step])

        shot = self.store.shots[0]
        csv_objs = shot.metadata["csv_objects"]
        kinds = {e["name"]: e["kind"] for e in csv_objs}
        self.assertEqual(kinds["obj_A01"], "scene")
        self.assertEqual(kinds["clip_A01"], "audio")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_audio_change_triggers_patched(self, mock_dur):
        """Changing audio clip name (no scene changes) yields 'patched'."""
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="Sec",
            description="d",
        )
        step.objects.append(BuilderObject(name="obj_A01"))
        step.objects.append(BuilderObject(name="clip_v1", kind="audio"))
        builder = ShotManifest(self.store)
        builder.update([step])

        # Rebuild with changed audio name
        step2 = BuilderStep(
            step_id="A01",
            section="A",
            section_title="Sec",
            description="d",
        )
        step2.objects.append(BuilderObject(name="obj_A01"))
        step2.objects.append(BuilderObject(name="clip_v2", kind="audio"))
        actions = builder.update([step2])
        self.assertEqual(actions["A01"], "patched")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_same_audio_yields_skipped(self, mock_dur):
        """Identical audio on rebuild yields 'skipped'."""
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="Sec",
            description="d",
        )
        step.objects.append(BuilderObject(name="obj_A01"))
        step.objects.append(BuilderObject(name="clip_A01", kind="audio"))
        builder = ShotManifest(self.store)
        builder.update([step])

        actions = builder.update([step])
        self.assertEqual(actions["A01"], "skipped")

    def tearDown(self):
        ShotStore._active = None


class TestAssessAudioStatus(unittest.TestCase):
    """ShotManifest.assess() handles audio BuilderObjects in the objects list."""

    def setUp(self):
        self.store = _fresh_store()

    def _make_step_with_audio(self, step_id="A01", audio_name="narration_A01"):
        step = BuilderStep(
            step_id=step_id,
            section="A",
            section_title="Section A",
            description=f"Content for {step_id}",
        )
        step.objects.append(BuilderObject(name=f"obj_{step_id}"))
        step.objects.append(BuilderObject(name=audio_name, kind="audio"))
        return step

    def _find_audio_status(self, result):
        """Return the ObjectStatus for kind='audio' from result.objects."""
        for o in result.objects:
            # ObjectStatus carries the name but not kind; match by name pattern
            # Since we know the audio object is the last, check objects in order
            pass
        # The assess loop processes step.objects in order; audio objects are
        # checked via audio_exists_fn.  We find audio by matching the name
        # we passed to _make_step_with_audio.
        return next(
            (o for o in result.objects if o.name.startswith("narration")),
            None,
        )

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_audio_exists_valid(self, mock_dur):
        """assess() marks audio object as 'valid' when audio node found."""
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
        )
        self.assertEqual(len(results), 1)
        audio_st = self._find_audio_status(results[0])
        self.assertIsNotNone(audio_st)
        self.assertEqual(audio_st.name, "narration_A01")
        self.assertTrue(audio_st.exists)
        self.assertEqual(audio_st.status, "valid")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_audio_missing_object(self, mock_dur):
        """assess() marks audio object as 'missing_object' when not found."""
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: False,
        )
        audio_st = self._find_audio_status(results[0])
        self.assertEqual(audio_st.status, "missing_object")
        self.assertFalse(audio_st.exists)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_no_audio_object_no_audio_status(self, mock_dur):
        """assess() produces no audio ObjectStatus when step has no audio objects."""
        steps = _make_steps("A01")
        builder = ShotManifest(self.store)
        builder.update(steps)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
        )
        audio_st = next(
            (o for o in results[0].objects if o.name.startswith("narration")),
            None,
        )
        self.assertIsNone(audio_st)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_missing_audio_affects_rollup(self, mock_dur):
        """StepStatus.status rollup returns 'missing_object' when audio missing."""
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: False,
        )
        self.assertEqual(results[0].status, "missing_object")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_valid_audio_does_not_degrade_rollup(self, mock_dur):
        """Valid audio doesn't turn a 'valid' overall status into something worse."""
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
        )
        self.assertEqual(results[0].status, "valid")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_locked_shot_skips_audio_check(self, mock_dur):
        """Locked shots skip detailed checking — no audio ObjectStatus."""
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)
        shot = self.store.shots[0]
        self.store.update_shot(shot.shot_id, locked=True)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
        )
        # Locked shots produce empty objects list
        self.assertEqual(results[0].status, "locked")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_default_audio_exists_fn(self, mock_dur):
        """Default audio_exists_fn delegates to cmds.ls(name, type='audio').

        Patches ``_default_audio_exists`` directly so the assertion exercises
        the default audio-exists code path (calling ``cmds.ls(name,
        type='audio')``) without needing to mock cmds globally — global
        cmds patches break unrelated scene-discovery calls.
        """
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)

        import maya

        real_cmds = maya.cmds

        captured_calls = []

        original_ls = real_cmds.ls

        def smart_ls(*args, **kwargs):
            # Intercept only the audio-existence query; defer everything
            # else to the real cmds.ls so scene discovery still works.
            if (
                len(args) == 1
                and args[0] == "narration_A01"
                and kwargs.get("type") == "audio"
            ):
                captured_calls.append((args, kwargs))
                return ["narration_A01"]
            return original_ls(*args, **kwargs)

        with (
            patch(
                "mayatk.anim_utils.shots.shot_manifest._shot_manifest.AudioUtils.has_track",
                return_value=False,
            ),
            patch.object(real_cmds, "ls", side_effect=smart_ls),
        ):
            results = builder.assess(
                steps,
                exists_fn=lambda n: True,
                # audio_exists_fn intentionally omitted — exercises default
            )
        audio_st = self._find_audio_status(results[0])
        self.assertIsNotNone(audio_st)
        self.assertTrue(audio_st.exists)
        self.assertTrue(
            captured_calls,
            "cmds.ls('narration_A01', type='audio') was not called",
        )

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_audio_never_gets_user_animated(self, mock_dur):
        """Audio objects must not hit the keyframe check path.

        Bug fixed: audio objects with built=True and no behaviors would
        fall into the keyframe_range_fn branch and get 'user_animated'.
        """
        steps = [self._make_step_with_audio()]
        builder = ShotManifest(self.store)
        builder.update(steps)

        kf_called_for = []

        def spy_kf(name):
            kf_called_for.append(name)
            return (1.0, 30.0)

        results = builder.assess(
            steps,
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
            keyframe_range_fn=spy_kf,
        )
        audio_st = self._find_audio_status(results[0])
        self.assertEqual(audio_st.status, "valid")
        self.assertNotIn("narration_A01", kf_called_for)

    def tearDown(self):
        ShotStore._active = None


class TestStepStatusAudioRollup(unittest.TestCase):
    """Direct unit tests for StepStatus.status with audio objects in objects list."""

    def test_missing_audio_returns_missing_object(self):
        objs = [ObjectStatus(name="clip", exists=False, status="missing_object")]
        ss = StepStatus(step_id="A01", built=True, objects=objs)
        self.assertEqual(ss.status, "missing_object")

    def test_valid_audio_returns_valid(self):
        objs = [ObjectStatus(name="clip", exists=True, status="valid")]
        ss = StepStatus(step_id="A01", built=True, objects=objs)
        self.assertEqual(ss.status, "valid")

    def test_no_audio_returns_valid(self):
        ss = StepStatus(
            step_id="A01",
            built=True,
            objects=[ObjectStatus(name="obj", exists=True, status="valid")],
        )
        self.assertEqual(ss.status, "valid")

    def test_missing_object_takes_priority_over_valid_audio(self):
        """Object missing trumps valid audio."""
        objs = [
            ObjectStatus(name="obj", exists=False, status="missing_object"),
            ObjectStatus(name="clip", exists=True, status="valid"),
        ]
        ss = StepStatus(step_id="A01", built=True, objects=objs)
        self.assertEqual(ss.status, "missing_object")


class TestSetClipBehavior(unittest.TestCase):
    """The set_clip YAML and its verify/apply routing."""

    def test_list_behaviors_audio_kind(self):
        """Behaviors.list_behaviors(kind='audio') includes set_clip."""
        names = Behaviors.list_behaviors(kind="audio")
        self.assertIn("set_clip", names)

    def test_list_behaviors_scene_kind_excludes_set_clip(self):
        """Behaviors.list_behaviors(kind='scene') does not include set_clip."""
        names = Behaviors.list_behaviors(kind="scene")
        self.assertNotIn("set_clip", names)

    def test_load_set_clip_template(self):
        """set_clip.yaml is loadable and has audio_clip verify mode."""
        tmpl = Behaviors.load_behavior("set_clip")
        self.assertEqual(tmpl["kind"], ["audio"])
        self.assertEqual(tmpl["verify"]["mode"], "audio_clip")

    def test_verify_behavior_routes_to_audio_clip(self):
        """verify_behavior with audio_clip mode delegates to _verify_audio_clip."""
        from unittest.mock import patch as _p

        with _p(
            "mayatk.anim_utils.shots.shot_manifest.behaviors._behaviors._BehaviorsInternal._verify_audio_clip",
            return_value=True,
        ) as mock_v:
            result = Behaviors.verify_behavior("node", "set_clip", 1, 30)
        # _verify_audio_clip takes the full (obj, start, end) tuple.
        mock_v.assert_called_once_with("node", 1, 30)
        self.assertTrue(result)

    def test_verify_returns_false_when_clip_missing(self):
        """verify_behavior returns False when _verify_audio_clip returns False."""
        from unittest.mock import patch as _p

        with _p(
            "mayatk.anim_utils.shots.shot_manifest.behaviors._behaviors._BehaviorsInternal._verify_audio_clip",
            return_value=False,
        ):
            self.assertFalse(Behaviors.verify_behavior("node", "set_clip", 1, 30))

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_assess_audio_with_set_clip_behavior(self, mock_dur):
        """assess() checks audio behaviors and reports missing_behavior when unverified."""
        store = _fresh_store()
        step = BuilderStep(
            step_id="A01", section="A", section_title="A", description="test"
        )
        audio_obj = BuilderObject(
            name="narration_A01", kind="audio", behaviors=["set_clip"]
        )
        step.objects.append(audio_obj)
        builder = ShotManifest(store)
        builder.update([step])

        results = builder.assess(
            [step],
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
            verify_fn=lambda obj, beh, s, e: False,  # clip not verified
        )
        audio_st = next(o for o in results[0].objects if o.name == "narration_A01")
        self.assertEqual(audio_st.status, "missing_behavior")
        self.assertEqual(audio_st.broken_behaviors, ["set_clip"])

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration",
        return_value=30,
    )
    def test_assess_audio_set_clip_valid(self, mock_dur):
        """assess() marks audio 'valid' when set_clip verifies."""
        store = _fresh_store()
        step = BuilderStep(
            step_id="A01", section="A", section_title="A", description="test"
        )
        audio_obj = BuilderObject(
            name="narration_A01", kind="audio", behaviors=["set_clip"]
        )
        step.objects.append(audio_obj)
        builder = ShotManifest(store)
        builder.update([step])

        results = builder.assess(
            [step],
            exists_fn=lambda n: True,
            audio_exists_fn=lambda n: True,
            verify_fn=lambda obj, beh, s, e: True,
        )
        audio_st = next(o for o in results[0].objects if o.name == "narration_A01")
        self.assertEqual(audio_st.status, "valid")
        self.assertEqual(audio_st.behaviors, ["set_clip"])

    def test_apply_behavior_routes_source_path_to_audio_clip(self):
        """apply_behavior threads source_path to apply_audio_clip for audio_clip mode."""
        from unittest.mock import patch as _p

        with _p(
            "mayatk.anim_utils.shots.shot_manifest.behaviors._behaviors.Behaviors.apply_audio_clip",
        ) as mock_apply:
            Behaviors.apply_behavior(
                "my_node", "set_clip", 1, 30, source_path="/audio/clip.wav"
            )
        # apply_audio_clip now requires both start AND end.
        mock_apply.assert_called_once_with(
            "my_node", 1, 30, source_path="/audio/clip.wav"
        )

    def test_apply_behavior_defaults_empty_source_path(self):
        """apply_behavior passes empty source_path by default."""
        from unittest.mock import patch as _p

        with _p(
            "mayatk.anim_utils.shots.shot_manifest.behaviors._behaviors.Behaviors.apply_audio_clip",
        ) as mock_apply:
            Behaviors.apply_behavior("my_node", "set_clip", 1, 30)
        # apply_audio_clip now requires both start AND end (end is the
        # upper bound for the off-key).
        mock_apply.assert_called_once_with("my_node", 1, 30, source_path="")

    def test_apply_audio_clip_warns_when_no_node_no_source(self):
        """apply_audio_clip logs warning when node missing and no source_path."""
        from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        # has_track() probes the carrier via cmds.objExists/attributeQuery,
        # which the shared mock reports truthy — so patch it to False to
        # deterministically exercise the missing-track warning path.
        with (
            patch(
                "mayatk.audio_utils._audio_utils.AudioUtils.has_track",
                return_value=False,
            ),
            self.assertLogs(
                "mayatk.anim_utils.shots.shot_manifest.behaviors",
                level="WARNING",
            ) as cm,
        ):
            # apply_audio_clip now requires both start and end.
            Behaviors.apply_audio_clip("missing_node", 10.0, 40.0)
        self.assertTrue(any("missing_node" in m for m in cm.output))

    def tearDown(self):
        ShotStore._active = None


class TestAudioResolverPath(unittest.TestCase):
    """Audio resolvers create BuilderObject(kind='audio') in step.objects."""

    def setUp(self):
        import tempfile

        # Per-test tempdir avoids Windows file-handle collisions between
        # successive setUp/tearDown cycles when run as part of --all.
        self._TEMP = Path(tempfile.mkdtemp(prefix="audio_resolver_"))
        for name in ("A01_narration.wav", "A02_intro.mp3"):
            (self._TEMP / name).write_bytes(b"RIFF")
        self._tmpdir = str(self._TEMP)

    def tearDown(self):
        import shutil

        shutil.rmtree(self._TEMP, ignore_errors=True)

    def _audio_obj(self, step):
        """Return the first audio BuilderObject from step.objects, or None."""
        return next((o for o in step.objects if o.kind == "audio"), None)

    def test_prefix_sets_audio_object(self):
        """_audio_prefix appends audio BuilderObject with source_path."""
        resolver = Mapping._audio_prefix(self._tmpdir)
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="S",
            description="d",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertIsNotNone(ao)
        self.assertEqual(ao.name, "A01_narration")
        self.assertEqual(ao.source_path, str(Path(self._tmpdir, "A01_narration.wav")))

    def test_prefix_no_match_leaves_no_audio_object(self):
        """_audio_prefix doesn't add audio object when no file matches."""
        resolver = Mapping._audio_prefix(self._tmpdir)
        step = BuilderStep(
            step_id="Z99",
            section="Z",
            section_title="S",
            description="d",
        )
        resolver(step)
        self.assertIsNone(self._audio_obj(step))

    def test_regex_sets_audio_object(self):
        """_audio_regex appends audio BuilderObject with source_path."""
        resolver = Mapping._audio_regex(
            self._tmpdir,
            r"{step_id}_.*",
        )
        step = BuilderStep(
            step_id="A02",
            section="A",
            section_title="S",
            description="d",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertIsNotNone(ao)
        self.assertEqual(ao.name, "A02_intro")
        self.assertEqual(ao.source_path, str(Path(self._tmpdir, "A02_intro.mp3")))

    def test_map_sets_audio_object_no_source_path(self):
        """_audio_map appends audio BuilderObject with no source_path."""
        resolver = Mapping._audio_map({"A01": "narration_A01"})
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="S",
            description="d",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertIsNotNone(ao)
        self.assertEqual(ao.name, "narration_A01")
        self.assertEqual(ao.source_path, "")

    def test_derive_generates_clip_from_audio_text(self):
        """_audio_derive builds clip name from step_id + first N words."""
        resolver = Mapping._audio_derive(words=3)
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="S",
            description="d",
            audio="Welcome to the aircraft flight control surface",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertIsNotNone(ao)
        self.assertEqual(ao.name, "A01_WelcomeToThe")
        self.assertEqual(ao.source_path, "")

    def test_derive_skips_na_audio(self):
        """_audio_derive does nothing when audio is N/A."""
        resolver = Mapping._audio_derive(words=3)
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="S",
            description="d",
            audio="N/A",
        )
        resolver(step)
        self.assertIsNone(self._audio_obj(step))

    def test_derive_skips_empty_audio(self):
        """_audio_derive does nothing when audio is empty."""
        resolver = Mapping._audio_derive(words=3)
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="S",
            description="d",
            audio="",
        )
        resolver(step)
        self.assertIsNone(self._audio_obj(step))

    def test_derive_strips_punctuation(self):
        """_audio_derive strips non-alphanumeric chars from words."""
        resolver = Mapping._audio_derive(words=3)
        step = BuilderStep(
            step_id="A08",
            section="A",
            section_title="S",
            description="d",
            audio="There's currently warning tags on the control wheels.",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertEqual(ao.name, "A08_TheresCurrentlyWarning")

    def test_derive_fewer_words_than_requested(self):
        """_audio_derive works when audio has fewer words than requested."""
        resolver = Mapping._audio_derive(words=3)
        step = BuilderStep(
            step_id="A14",
            section="A",
            section_title="S",
            description="d",
            audio="Push in.",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertEqual(ao.name, "A14_PushIn")

    def test_derive_sets_source_path_when_file_exists(self):
        """_audio_derive sets source_path when matching file found in dir."""
        resolver = Mapping._audio_derive(words=3, directory=self._tmpdir)
        expected = Path(self._tmpdir, "A01_WelcomeToThe.wav")
        expected.write_bytes(b"RIFF")
        step = BuilderStep(
            step_id="A01",
            section="A",
            section_title="S",
            description="d",
            audio="Welcome to the aircraft verification",
        )
        resolver(step)
        ao = self._audio_obj(step)
        self.assertEqual(ao.name, "A01_WelcomeToThe")
        self.assertEqual(ao.source_path, str(expected))


class TestDefaultBehaviors(unittest.TestCase):
    """Tests for ``_build_default_behaviors`` pipeline processor."""

    @staticmethod
    def _step(*objects):
        s = BuilderStep(step_id="A01", section="A", section_title="S", description="d")
        s.objects.extend(objects)
        return s

    def test_adds_behavior_to_matching_kind(self):
        """Behavior is added to objects whose kind matches the config."""
        proc = Mapping._build_default_behaviors({"audio": ["set_clip"]})
        obj = BuilderObject(name="clip", kind="audio")
        step = self._step(obj)
        proc(step)
        self.assertEqual(obj.behaviors, ["set_clip"])

    def test_skips_non_matching_kind(self):
        """Scene objects are not affected by audio-only config."""
        proc = Mapping._build_default_behaviors({"audio": ["set_clip"]})
        obj = BuilderObject(name="asset", kind="scene")
        step = self._step(obj)
        proc(step)
        self.assertEqual(obj.behaviors, [])

    def test_does_not_duplicate_existing(self):
        """Already-present behaviors are not duplicated."""
        proc = Mapping._build_default_behaviors({"audio": ["set_clip"]})
        obj = BuilderObject(name="clip", kind="audio", behaviors=["set_clip"])
        step = self._step(obj)
        proc(step)
        self.assertEqual(obj.behaviors, ["set_clip"])

    def test_appends_to_existing_behaviors(self):
        """New behaviors are appended alongside existing ones."""
        proc = Mapping._build_default_behaviors({"audio": ["set_clip"]})
        obj = BuilderObject(name="clip", kind="audio", behaviors=["fade_in"])
        step = self._step(obj)
        proc(step)
        self.assertEqual(obj.behaviors, ["fade_in", "set_clip"])

    def test_multiple_kinds(self):
        """Config can target multiple kinds simultaneously."""
        proc = Mapping._build_default_behaviors(
            {
                "audio": ["set_clip"],
                "scene": ["fade_in"],
            }
        )
        audio = BuilderObject(name="clip", kind="audio")
        scene = BuilderObject(name="asset", kind="scene")
        step = self._step(audio, scene)
        proc(step)
        self.assertEqual(audio.behaviors, ["set_clip"])
        self.assertEqual(scene.behaviors, ["fade_in"])

    def test_empty_config_is_noop(self):
        """Empty config dict leaves objects unchanged."""
        proc = Mapping._build_default_behaviors({})
        obj = BuilderObject(name="asset", kind="scene")
        step = self._step(obj)
        proc(step)
        self.assertEqual(obj.behaviors, [])


class TestDefaultDurationResolveRanges(unittest.TestCase):
    """resolve_ranges with default_duration produces uniform ranges when
    no animation regions exist in the scene.

    Bug: Without animation, auto-detect fell through to sequential
    placement using compute_duration (behavior-derived ~30f), producing
    tiny unusable ranges.  Now passes default_duration=200 for uniform
    placement starting at frame 0.
    Fixed: 2026-04-16
    """

    def setUp(self):
        self.steps = _make_steps("A01", "A02", "A03")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_uniform_200_frame_ranges(self, mock_dur):
        """Three steps with default_duration=200 yield [0,200], [200,400], [400,600]."""
        mock_dur.return_value = 30.0

        resolved = RangeResolver.resolve_ranges(
            steps=self.steps,
            user_ranges={},
            gap_starts=[],
            gap_end_map={},
            gap=0,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=200,
        )

        self.assertEqual(len(resolved), 3)
        self.assertEqual(resolved[0], ("A01", 0.0, 200.0, False))
        self.assertEqual(resolved[1], ("A02", 200.0, 400.0, False))
        self.assertEqual(resolved[2], ("A03", 400.0, 600.0, False))
        mock_dur.assert_not_called()

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_uniform_ranges_with_gap(self, mock_dur):
        """Gap is respected between default-duration steps."""
        mock_dur.return_value = 30.0

        resolved = RangeResolver.resolve_ranges(
            steps=self.steps,
            user_ranges={},
            gap_starts=[],
            gap_end_map={},
            gap=10,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=200,
        )

        self.assertEqual(resolved[0], ("A01", 0.0, 200.0, False))
        # 200 + 10 gap = 210
        self.assertEqual(resolved[1], ("A02", 210.0, 410.0, False))
        # 410 + 10 gap = 420
        self.assertEqual(resolved[2], ("A03", 420.0, 620.0, False))

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_user_range_overrides_default(self, mock_dur):
        """User-pinned ranges still take priority over default_duration."""
        mock_dur.return_value = 30.0

        resolved = RangeResolver.resolve_ranges(
            steps=self.steps,
            user_ranges={"A02": (500.0, 700.0)},
            gap_starts=[],
            gap_end_map={},
            gap=0,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=200,
        )

        self.assertEqual(resolved[0][:2], ("A01", 0.0))
        self.assertEqual(resolved[1], ("A02", 500.0, 700.0, True))
        # A03 follows after A02's end
        self.assertEqual(resolved[2][1], 700.0)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_zero_default_duration_uses_compute(self, mock_dur):
        """default_duration=0 preserves legacy behavior (compute_duration)."""
        mock_dur.return_value = 30.0

        resolved = RangeResolver.resolve_ranges(
            steps=self.steps,
            user_ranges={},
            gap_starts=[],
            gap_end_map={},
            gap=0,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=0,
        )

        # Starts at cursor=1.0 (old behavior), uses compute_duration=30
        self.assertEqual(resolved[0][1], 1.0)
        self.assertEqual(resolved[1][1], 31.0)  # 1 + 30
        self.assertEqual(resolved[2][1], 61.0)  # 31 + 30

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_default_not_used_when_regions_exist(self, mock_dur):
        """When gap_starts are present, default_duration is ignored."""
        mock_dur.return_value = 30.0

        resolved = RangeResolver.resolve_ranges(
            steps=self.steps,
            user_ranges={},
            gap_starts=[10.0, 100.0, 200.0],
            gap_end_map={},
            gap=0,
            use_selected_keys=False,
            last_resolved=[],
            default_duration=200,
        )

        # Should use gap_starts, not default_duration
        self.assertEqual(resolved[0][1], 10.0)
        self.assertEqual(resolved[1][1], 100.0)
        self.assertEqual(resolved[2][1], 200.0)

    def test_starts_at_zero_not_one(self):
        """Default ranges start at frame 0, not frame 1."""
        with patch(
            "mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration"
        ) as mock_dur:
            mock_dur.return_value = 30.0

            resolved = RangeResolver.resolve_ranges(
                steps=_make_steps("A01"),
                user_ranges={},
                gap_starts=[],
                gap_end_map={},
                gap=0,
                use_selected_keys=False,
                last_resolved=[],
                default_duration=200,
            )

            self.assertEqual(resolved[0][1], 0.0)
            self.assertEqual(resolved[0][2], 200.0)


class TestHasAnimation(unittest.TestCase):
    """ShotStore.has_animation() returns False outside Maya."""

    def test_returns_false_without_maya(self):
        self.assertFalse(ShotStore.has_animation())


if __name__ == "__main__":
    unittest.main()
