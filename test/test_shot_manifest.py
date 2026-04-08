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

All tests run WITHOUT Maya by mocking pymel/cmds.
"""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock Maya modules BEFORE any mayatk imports
# ---------------------------------------------------------------------------

_mock_pm = MagicMock()
_mock_pm.objExists.return_value = True
_mock_pm.playbackOptions.return_value = 0.0
_mock_pm.currentTime.return_value = 120.0
_mock_pm.select = MagicMock()
_mock_pm.undoInfo = MagicMock()
_mock_pm.ls.return_value = []
_mock_pm.keyframe.return_value = []

_mock_cmds = MagicMock()
_mock_cmds.objExists.return_value = True
_mock_cmds.ls.return_value = []
_mock_cmds.keyframe.return_value = []

sys.modules.setdefault("pymel", types.ModuleType("pymel"))
sys.modules.setdefault("pymel.core", _mock_pm)
sys.modules["pymel.core"] = _mock_pm
sys.modules.setdefault("maya", types.ModuleType("maya"))
sys.modules.setdefault("maya.api", types.ModuleType("maya.api"))
sys.modules.setdefault("maya.api.OpenMaya", MagicMock())
sys.modules.setdefault("maya.cmds", _mock_cmds)
sys.modules["maya.cmds"] = _mock_cmds
sys.modules.setdefault("maya.mel", MagicMock())
sys.modules.setdefault("maya.OpenMaya", MagicMock())
sys.modules.setdefault("maya.OpenMayaUI", MagicMock())

# Ensure workspace roots are on sys.path
_WORKSPACE = Path(__file__).parent.parent.parent.absolute()
for subdir in ("pythontk", "uitk", "mayatk"):
    p = str(_WORKSPACE / subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderStep,
    BuilderObject,
    ShotManifest,
    ColumnMap,
    parse_csv,
    detect_behaviors,
    detect_shot_regions,
)


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
    store = ShotStore()
    ShotStore._active = store
    return store


# ---------------------------------------------------------------------------
# Tests: detect_behaviors
# ---------------------------------------------------------------------------


class TestDetectBehaviors(unittest.TestCase):
    def test_fade_in(self):
        self.assertEqual(detect_behaviors("Object fades in from black"), ["fade_in"])

    def test_fade_out(self):
        self.assertEqual(detect_behaviors("Object fades out slowly"), ["fade_out"])

    def test_fade_in_and_out(self):
        self.assertEqual(
            detect_behaviors("Fades in then fades out"), ["fade_in", "fade_out"]
        )

    def test_no_behavior(self):
        self.assertEqual(detect_behaviors("Object sits still"), [])


# ---------------------------------------------------------------------------
# Tests: ShotManifest.update (baseline Ã¢â‚¬â€ no ranges)
# ---------------------------------------------------------------------------


class TestUpdateBaseline(unittest.TestCase):
    """Test update() without ranges Ã¢â‚¬â€ sequential cursor placement."""

    def setUp(self):
        self.store = _fresh_store()
        self.assembler = ShotManifest(self.store)
        self.steps = _make_steps("A01", "A02", "A03")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_creates_shots_sequentially(self, mock_dur):
        mock_dur.return_value = 30.0
        actions = self.assembler.update(self.steps)

        self.assertEqual(actions["A01"], "created")
        self.assertEqual(actions["A02"], "created")
        self.assertEqual(actions["A03"], "created")
        self.assertEqual(len(self.store.shots), 3)

        shots = self.store.sorted_shots()
        self.assertEqual(shots[0].start, 1)  # cursor starts at 1
        self.assertEqual(shots[0].end, 31)
        self.assertEqual(shots[1].start, 31)
        self.assertEqual(shots[1].end, 61)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_removes_shots_not_in_csv(self, mock_dur):
        mock_dur.return_value = 30.0
        self.assembler.update(self.steps)
        # Now rebuild with A01 and A03 only
        reduced = _make_steps("A01", "A03")
        actions = self.assembler.update(reduced)
        self.assertEqual(actions["A02"], "removed")
        names = {s.name for s in self.store.shots}
        self.assertNotIn("A02", names)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_skips_locked_shots(self, mock_dur):
        mock_dur.return_value = 30.0
        self.assembler.update(self.steps)
        # Lock A02
        for s in self.store.shots:
            if s.name == "A02":
                s.locked = True
        actions = self.assembler.update(self.steps)
        self.assertEqual(actions["A02"], "locked")


# ---------------------------------------------------------------------------
# Tests: ShotManifest.update with ranges (Stage 6)
# ---------------------------------------------------------------------------


class TestUpdateWithRanges(unittest.TestCase):
    """Test update() with user-provided ranges for shot placement."""

    def setUp(self):
        self.store = _fresh_store()
        self.assembler = ShotManifest(self.store)
        self.steps = _make_steps("A01", "A02", "A03")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_new_shots_use_provided_ranges(self, mock_dur):
        mock_dur.return_value = 30.0
        ranges = {
            "A01": (100.0, 200.0),
            "A02": (250.0, 350.0),
            "A03": (400.0, 500.0),
        }
        actions = self.assembler.update(self.steps, ranges=ranges)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 100.0)
        self.assertEqual(shots["A01"].end, 200.0)
        self.assertEqual(shots["A02"].start, 250.0)
        self.assertEqual(shots["A02"].end, 350.0)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_existing_shots_repositioned_by_ranges(self, mock_dur):
        """Existing shots should be repositioned when ranges differ.

        Bug context: Without range support, rebuild would leave existing
        shots at their original positions even when the user specified
        different frame ranges in the align-mode column.
        """
        mock_dur.return_value = 30.0
        # Initial build without ranges
        self.assembler.update(self.steps)
        shots_before = {s.name: (s.start, s.end) for s in self.store.shots}

        # Rebuild with ranges Ã¢â‚¬â€ should reposition
        ranges = {
            "A01": (500.0, 600.0),
            "A02": (700.0, 800.0),
            "A03": (900.0, 1000.0),
        }
        actions = self.assembler.update(self.steps, ranges=ranges)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 500.0)
        self.assertEqual(shots["A01"].end, 600.0)
        # repositioned Ã¢â€ â€™ patched
        self.assertEqual(actions["A01"], "patched")

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_partial_ranges_fallback_to_cursor(self, mock_dur):
        """Steps without explicit ranges should use cursor placement."""
        mock_dur.return_value = 30.0
        ranges = {"A01": (100.0, 200.0)}  # only A01 has a range
        actions = self.assembler.update(self.steps, ranges=ranges)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 100.0)
        self.assertEqual(shots["A01"].end, 200.0)
        # A02 and A03 should be cursor-placed after A01
        self.assertEqual(shots["A02"].start, 200.0)
        self.assertEqual(shots["A02"].end, 230.0)

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
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


# ---------------------------------------------------------------------------
# Tests: detect_shot_regions
# ---------------------------------------------------------------------------

# NOTE: detect_shot_regions() requires PyMEL + SegmentKeys, so it is tested
# end-to-end in the sequencer controller tests.  The controller-level tests
# below mock detect_shot_regions for isolation.


# ---------------------------------------------------------------------------
# Tests: Controller range logic (Stages 3-5)
# ---------------------------------------------------------------------------

# Qt setup
from qtpy import QtWidgets, QtCore

_app = QtWidgets.QApplication.instance()
if _app is None:
    _app = QtWidgets.QApplication(sys.argv)


from mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots import (
    ShotManifestController,
)
from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
    COL_STEP,
    COL_DESC,
    COL_START,
    COL_END,
    parse_range,
    short_name,
)


class _ControllerHarness:
    """Creates a ShotManifestController with a minimal fake UI."""

    def setup_controller(self):
        """Build a controller with a mocked switchboard and tree widget."""
        from uitk.widgets.treeWidget import TreeWidget
        from qtpy.QtWidgets import QAbstractItemView

        self.tree = TreeWidget()
        self.tree.setColumnCount(5)

        # Minimal slots_instance mock (controller reads .sb and .ui)
        slots_instance = MagicMock()
        slots_instance.ui.tbl_steps = self.tree
        slots_instance.ui.footer = MagicMock()
        slots_instance.ui.footer._status_label = MagicMock()
        slots_instance.ui.btn_build = MagicMock()
        slots_instance.sb.get_setting.return_value = None

        # Create the controller
        self.ctrl = ShotManifestController(slots_instance)
        self.ctrl.logger = MagicMock()

        # Convenience references
        self.sb = self.ctrl.sb
        self.ui = self.ctrl.ui

        # Load steps
        self.steps = _make_steps("A01", "A02", "A03")
        self.ctrl._steps = self.steps


class TestResolveRanges(unittest.TestCase, _ControllerHarness):
    """Test _resolve_ranges() merging user pins with auto-fill."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_user_pin_overrides_auto_fill(self, mock_dur, mock_regions):
        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        self.ctrl._user_ranges["A02"] = (200.0, None)

        resolved = self.ctrl._resolve_ranges()
        # A02 should start at 200
        a02 = next(r for r in resolved if r[0] == "A02")
        self.assertEqual(a02[1], 200.0)
        self.assertTrue(a02[3])  # is_user

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_gap_detection_used_for_auto_fill(self, mock_dur, mock_regions):
        mock_dur.return_value = 30.0
        mock_regions.return_value = [
            {"name": "S", "start": 50.0, "end": 80.0, "objects": []},
            {"name": "S", "start": 150.0, "end": 180.0, "objects": []},
            {"name": "S", "start": 250.0, "end": 280.0, "objects": []},
        ]

        resolved = self.ctrl._resolve_ranges()
        # Steps should use region-start positions
        self.assertEqual(resolved[0][1], 50.0)  # A01 at region 1
        self.assertEqual(resolved[1][1], 150.0)  # A02 at region 2
        self.assertEqual(resolved[2][1], 250.0)  # A03 at region 3
        # None of these are user-entered
        for r in resolved:
            self.assertFalse(r[3])

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_many_regions_pruned_to_step_count(self, mock_dur, mock_regions):
        """When more regions than steps, largest gaps become boundaries."""
        mock_dur.return_value = 30.0
        # 6 region starts (5 gaps) but only 3 steps.
        # Diffs: 5, 5, 90, 5, 95 Ã¢â€ â€™ top 2 are 95 (idx 4) and 90 (idx 2)
        # Selected regions: [0, 100, 200]
        mock_regions.return_value = [
            {"name": "S", "start": 0.0, "end": 30.0, "objects": []},
            {"name": "S", "start": 5.0, "end": 35.0, "objects": []},
            {"name": "S", "start": 10.0, "end": 40.0, "objects": []},
            {"name": "S", "start": 100.0, "end": 130.0, "objects": []},
            {"name": "S", "start": 105.0, "end": 135.0, "objects": []},
            {"name": "S", "start": 200.0, "end": 230.0, "objects": []},
        ]

        resolved = self.ctrl._resolve_ranges()
        self.assertEqual(resolved[0][1], 0.0)  # A01
        self.assertEqual(resolved[1][1], 100.0)  # A02
        self.assertEqual(resolved[2][1], 200.0)  # A03

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_end_derived_from_next_start(self, mock_dur, mock_regions):
        """End of step N = start of step N+1 minus gap."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = []
        store = ShotStore.active()
        store.gap = 5.0

        resolved = self.ctrl._resolve_ranges()
        # A01 end should be A02.start - gap
        self.assertAlmostEqual(resolved[0][2], resolved[1][1] - 5.0, places=1)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_from_step_idx_freezes_prefix(self, mock_dur, mock_regions):
        """from_step_idx preserves earlier steps and re-resolves later ones."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = [
            {"name": "S", "start": 10.0, "end": 40.0, "objects": []},
            {"name": "S", "start": 100.0, "end": 130.0, "objects": []},
            {"name": "S", "start": 200.0, "end": 230.0, "objects": []},
        ]

        # First full resolve Ã¢â‚¬â€ gaps assigned to all 3 steps
        resolved_full = self.ctrl._resolve_ranges()
        self.assertEqual(resolved_full[0][1], 10.0)  # A01 at gap 1
        self.assertEqual(resolved_full[1][1], 100.0)  # A02 at gap 2
        self.assertEqual(resolved_full[2][1], 200.0)  # A03 at gap 3

        # Re-resolve from step 2 (A03): A01 and A02 should be frozen
        resolved_partial = self.ctrl._resolve_ranges(from_step_idx=2)
        self.assertEqual(resolved_partial[0], resolved_full[0])  # A01 frozen
        self.assertEqual(resolved_partial[1], resolved_full[1])  # A02 frozen
        # A03 re-resolves Ã¢â‚¬â€ gap 200 is past A02's end, so it uses it
        self.assertEqual(resolved_partial[2][1], 200.0)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_set_start_cascades_subsequent(self, mock_dur, mock_regions):
        """Setting a user pin clears subsequent user ranges so they re-flow."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        # Pin all three steps
        self.ctrl._user_ranges["A01"] = (1.0, 30.0)
        self.ctrl._user_ranges["A02"] = (50.0, 80.0)
        self.ctrl._user_ranges["A03"] = (100.0, 130.0)

        # Simulate "Set Start to Current Frame" on A02: clears A03's pin
        step_idx = 1  # A02
        for s in self.ctrl._steps[step_idx + 1 :]:
            self.ctrl._user_ranges.pop(s.step_id, None)
        self.ctrl._user_ranges["A02"] = (500.0, None)

        resolved = self.ctrl._resolve_ranges()
        self.assertEqual(resolved[0][1], 1.0)  # A01 pinned
        self.assertEqual(resolved[1][1], 500.0)  # A02 at new pin
        # A03 should cascade from A02's end (500+30=530)
        self.assertEqual(resolved[2][1], 530.0)


class TestValidateCollisions(unittest.TestCase, _ControllerHarness):
    """Test _validate_range_collisions() detecting overlapping ranges."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_no_collision_when_ranges_are_ordered(self, mock_dur, mock_regions):
        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 30.0)
        self.ctrl._user_ranges["A02"] = (31.0, 60.0)
        self.ctrl._user_ranges["A03"] = (61.0, 90.0)

        # Populate tree so validation has items
        self.ctrl._populate_table()
        count = self.ctrl._validate_range_collisions()
        self.assertEqual(count, 0)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_collision_detected_when_ranges_overlap(self, mock_dur, mock_regions):
        """Overlapping ranges should be flagged as collisions.

        Bug context: Without collision detection, overlapping user ranges
        would silently create overlapping shots in the store.
        """
        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 50.0)
        self.ctrl._user_ranges["A02"] = (40.0, 70.0)  # overlaps A01
        self.ctrl._user_ranges["A03"] = (80.0, 100.0)

        self.ctrl._populate_table()
        count = self.ctrl._validate_range_collisions()
        self.assertGreater(count, 0)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_no_collision_when_ranges_are_contiguous(self, mock_dur, mock_regions):
        """Adjacent ranges where end == next_start should NOT collide.

        Bug: >= comparison treated contiguous (touching) ranges as
        overlapping, causing every auto-filled shot to show a collision
        when gap=0. Fixed to strict > comparison.
        """
        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 30.0)
        self.ctrl._user_ranges["A02"] = (30.0, 60.0)  # touches A01 exactly
        self.ctrl._user_ranges["A03"] = (60.0, 90.0)  # touches A02 exactly

        self.ctrl._populate_table()
        count = self.ctrl._validate_range_collisions()
        self.assertEqual(count, 0, "Contiguous ranges should not be collisions")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_collision_applies_background_color(self, mock_dur, mock_regions):
        """Collision cells should have both foreground and background color set."""
        from qtpy.QtCore import Qt

        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 50.0)
        self.ctrl._user_ranges["A02"] = (40.0, 70.0)  # overlaps A01
        self.ctrl._user_ranges["A03"] = (80.0, 100.0)

        self.ctrl._populate_table()
        self.ctrl._validate_range_collisions()

        # Find A01 item â€” should have collision background
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            step_data = item.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and step_data.step_id == "A01":
                bg = item.background(COL_START)
                self.assertEqual(
                    bg.color().name(),
                    "#3d2828",
                    "Collision cell should have rose background",
                )
                break


class TestUserRangesPersistence(unittest.TestCase, _ControllerHarness):
    """Test that _user_ranges survive table rebuilds (Stage 3)."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()

    def test_user_ranges_survive_populate(self):
        """User-entered ranges must persist after _populate_table() rebuild."""
        self.ctrl._user_ranges["A02"] = (200.0, 300.0)

        # Rebuild the table
        self.ctrl._populate_table()

        # Check that A02's range is written into the tree
        from qtpy.QtCore import Qt

        found = False
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            step_data = item.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and step_data.step_id == "A02":
                start_text = item.text(COL_START)
                end_text = item.text(COL_END)
                self.assertIn("200", start_text)
                self.assertIn("300", end_text)
                found = True
        self.assertTrue(found, "A02 range not found in tree after rebuild")


class TestRangeReadOnlyPostBuild(unittest.TestCase, _ControllerHarness):
    """Test that Range column is read-only after build (Stage 7)."""

    def setUp(self):
        self.setup_controller()
        store = _fresh_store()
        # Simulate a built state by adding shots to the store
        store.define_shot("A01", 1, 31, ["obj_A01"])
        store.define_shot("A02", 31, 61, ["obj_A02"])
        store.define_shot("A03", 61, 91, ["obj_A03"])

    def test_is_built_returns_true(self):
        self.assertTrue(self.ctrl._is_built)

    def test_editable_flag_removed_post_build(self):
        """Parent rows should NOT have ItemIsEditable after build."""
        from qtpy.QtCore import Qt

        self.ctrl._populate_table()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            flags = item.flags()
            self.assertFalse(
                flags & Qt.ItemIsEditable,
                f"Row {i} should not be editable post-build",
            )


class TestParseRange(unittest.TestCase, _ControllerHarness):
    """Test _parse_range() parsing logic."""

    def setUp(self):
        self.setup_controller()

    def test_start_only(self):
        result = parse_range("120")
        self.assertEqual(result, (120.0, None))

    def test_start_end_with_en_dash(self):
        result = parse_range("120\u2013250")
        self.assertEqual(result, (120.0, 250.0))

    def test_start_end_with_hyphen(self):
        result = parse_range("120-250")
        self.assertEqual(result, (120.0, 250.0))

    def test_whitespace_trimmed(self):
        result = parse_range(" 100 - 200 ")
        self.assertEqual(result, (100.0, 200.0))

    def test_invalid_returns_none(self):
        result = parse_range("abc")
        self.assertIsNone(result)


class TestRangeAbsorption(unittest.TestCase, _ControllerHarness):
    """Test that a user range whose end consumes the next gap pushes it forward.

    Bug: When a user entered e.g. "100-500" on step 1 and gap boundaries were
    at [100, 200, 300], step 2 would still be placed at 200 (inside step 1's
    range) instead of being pushed past 500 + gap.
    Fixed: 2026-03-21
    """

    def setUp(self):
        self.setup_controller()
        _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
    def test_user_end_past_next_gap_pushes_downstream(self, mock_dur, mock_regions):
        """Step 2 must start at or after step 1's user-end + gap, not at gap boundary."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = [
            {"name": "S", "start": 100.0, "end": 130.0, "objects": []},
            {"name": "S", "start": 200.0, "end": 230.0, "objects": []},
            {"name": "S", "start": 300.0, "end": 330.0, "objects": []},
        ]

        store = ShotStore.active()
        store.gap = 5.0

        # User pins step 1 with an end that consumes gap boundary at 200
        self.ctrl._user_ranges["A01"] = (100.0, 500.0)
        resolved = self.ctrl._resolve_ranges()

        # A01 is user-pinned
        self.assertEqual(resolved[0][1], 100.0)
        self.assertEqual(resolved[0][2], 500.0)
        self.assertTrue(resolved[0][3])

        # A02 must be at 505 (500 + 5 gap), NOT 200
        self.assertGreaterEqual(resolved[1][1], 505.0)
        # A03 must follow A02
        self.assertGreater(resolved[2][1], resolved[1][1])


# ---------------------------------------------------------------------------
# Tests: Scene Detection Consolidation
# ---------------------------------------------------------------------------


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
        "mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration",
        return_value=30.0,
    )
    def test_remove_missing_true_deletes_absent(self, mock_dur):
        """Default behavior: shots not in steps are removed."""
        builder = ShotManifest(self.store)
        steps = _make_steps("A01", "A02")
        builder.update(steps)
        self.assertEqual(len(self.store.shots), 2)

        # Now update with only A01 â€” A02 should be removed
        steps2 = _make_steps("A01")
        actions = builder.update(steps2, remove_missing=True)
        self.assertEqual(actions.get("A02"), "removed")
        self.assertEqual(len(self.store.shots), 1)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration",
        return_value=30.0,
    )
    def test_remove_missing_false_preserves_absent(self, mock_dur):
        """Detection mode: existing shots not in steps are preserved."""
        builder = ShotManifest(self.store)
        steps = _make_steps("A01", "A02")
        builder.update(steps)
        self.assertEqual(len(self.store.shots), 2)

        # Now update with only A01 â€” A02 should be kept
        steps2 = _make_steps("A01")
        actions = builder.update(steps2, remove_missing=False)
        self.assertNotIn("A02", actions)
        self.assertEqual(len(self.store.shots), 2)


class TestDetectController(unittest.TestCase, _ControllerHarness):
    """Test controller.detect() populates the table from scene detection."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()
        # Add spn_gap mock to the UI
        spn_mock = MagicMock()
        spn_mock.value.return_value = 5.0
        self.ui.spn_gap = spn_mock
        self.ui.txt_csv_path = MagicMock()
        # Patch QMessageBox so confirmation dialog auto-confirms
        patcher = patch(
            "qtpy.QtWidgets.QMessageBox",
        )
        self._mock_msgbox = patcher.start()
        self._mock_msgbox.question.return_value = self._mock_msgbox.Yes
        self.addCleanup(patcher.stop)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_detect_populates_steps_and_ranges(self, mock_regions):
        """detect() fills _steps and _user_ranges from detection results."""
        mock_regions.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl_A"]},
            {"name": "Shot 2", "start": 60.0, "end": 100.0, "objects": ["ctrl_B"]},
        ]
        self.ctrl.detect()

        self.assertEqual(len(self.ctrl._steps), 2)
        self.assertEqual(self.ctrl._steps[0].step_id, "Shot 1")
        self.assertEqual(self.ctrl._user_ranges["Shot 1"], (10.0, 50.0))
        self.assertEqual(self.ctrl._user_ranges["Shot 2"], (60.0, 100.0))

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_detect_ranges_are_editable_user_ranges(self, mock_regions):
        """Detection ranges are stored as user_ranges (non-dim, editable)."""
        mock_regions.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": []},
        ]
        self.ctrl.detect()

        # user_ranges has a complete (start, end) tuple
        rng = self.ctrl._user_ranges.get("Shot 1")
        self.assertIsNotNone(rng)
        self.assertEqual(rng, (10.0, 50.0))
        # _all_ranges_complete should be True
        self.assertTrue(self.ctrl._all_ranges_complete())

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_detect_clears_csv_path(self, mock_regions):
        """detect() clears the CSV path, entering detection mode."""
        mock_regions.return_value = [
            {"name": "S1", "start": 0.0, "end": 10.0, "objects": []},
        ]
        self.ctrl._csv_path = "/some/file.csv"
        self.ctrl.detect()

        self.assertEqual(self.ctrl._csv_path, "")
        self.assertTrue(self.ctrl._is_detection_mode)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_detect_no_animation_clears_table(self, mock_regions):
        """detect() with no regions clears the table and shows a message.

        Bug: detect() returning early without clearing left stale CSV
        data visible after unchecking the CSV checkbox.
        Fixed: 2026-03-22
        """
        mock_regions.return_value = []
        self.ctrl.detect()

        self.assertEqual(self.ctrl._steps, [])
        self.assertEqual(self.ctrl._csv_path, "")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_csv_after_detect_replaces_data(self, mock_regions):
        """Loading a CSV after detect replaces the detection data."""
        mock_regions.return_value = [
            {"name": "S1", "start": 0.0, "end": 10.0, "objects": []},
        ]
        self.ctrl.detect()
        self.assertTrue(self.ctrl._is_detection_mode)

        # Simulate CSV load
        self.ctrl._csv_path = "/some/file.csv"
        self.ctrl._steps = _make_steps("A01", "A02")
        self.assertFalse(self.ctrl._is_detection_mode)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_detect_after_csv_replaces_data(self, mock_regions):
        """Detecting after CSV load replaces CSV data."""
        self.ctrl._csv_path = "/some/file.csv"
        self.ctrl._steps = _make_steps("A01")

        mock_regions.return_value = [
            {"name": "Shot 1", "start": 5.0, "end": 15.0, "objects": ["x"]},
        ]
        self.ctrl.detect()

        self.assertEqual(len(self.ctrl._steps), 1)
        self.assertEqual(self.ctrl._steps[0].step_id, "Shot 1")
        self.assertEqual(self.ctrl._csv_path, "")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_all_ranges_complete_false_for_csv(self, mock_regions):
        """_all_ranges_complete() returns False when CSV steps lack user ranges."""
        mock_regions.return_value = []
        # Steps from setup_controller have no user_ranges
        self.ctrl._user_ranges.clear()
        self.assertFalse(self.ctrl._all_ranges_complete())


class TestDescriptionEdit(unittest.TestCase, _ControllerHarness):
    """Test that Description column is editable in both CSV and detection modes."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()
        self.ui.spn_gap = MagicMock(value=MagicMock(return_value=5.0))
        self.ui.txt_csv_path = MagicMock()
        # Patch QMessageBox so confirmation dialog auto-confirms
        patcher = patch(
            "qtpy.QtWidgets.QMessageBox",
        )
        self._mock_msgbox = patcher.start()
        self._mock_msgbox.question.return_value = self._mock_msgbox.Yes
        self.addCleanup(patcher.stop)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_description_edit_detection_mode(self, mock_regions):
        """Editing Description column in detection mode updates step.description."""
        mock_regions.return_value = [
            {"name": "S1", "start": 0.0, "end": 10.0, "objects": ["a"]},
        ]
        self.ctrl.detect()

        tree = self.ui.tbl_steps
        parent = tree.topLevelItem(0)
        tree.blockSignals(True)
        parent.setText(COL_DESC, "My description")
        tree.blockSignals(False)

        self.ctrl._on_item_changed(parent, COL_DESC)
        self.assertEqual(self.ctrl._steps[0].description, "My description")

    def test_description_edit_csv_mode(self):
        """Editing Description column in CSV mode updates step.description."""
        # Steps from setup_controller (CSV mode: _csv_path is empty but
        # steps exist â€” simulate CSV mode by setting a path)
        self.ctrl._csv_path = "/some/file.csv"

        tree = self.ui.tbl_steps
        self.ctrl._populate_table()
        parent = tree.topLevelItem(0)

        tree.blockSignals(True)
        parent.setText(COL_DESC, "Updated description")
        tree.blockSignals(False)

        self.ctrl._on_item_changed(parent, COL_DESC)
        self.assertEqual(self.ctrl._steps[0].description, "Updated description")


class TestStepNameEdit(unittest.TestCase, _ControllerHarness):
    """Test that Step name column is editable and re-keys user ranges."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()

    def test_rename_updates_step_id(self):
        """Editing Step column updates step_id on the dataclass."""
        self.ctrl._populate_table()
        tree = self.ui.tbl_steps
        parent = tree.topLevelItem(0)

        tree.blockSignals(True)
        parent.setText(COL_STEP, "NewName")
        tree.blockSignals(False)

        self.ctrl._on_item_changed(parent, COL_STEP)
        self.assertEqual(self.ctrl._steps[0].step_id, "NewName")

    def test_rename_rekeys_user_ranges(self):
        """Renaming a step re-keys its entry in _user_ranges."""
        self.ctrl._user_ranges["A01"] = (10.0, 50.0)
        self.ctrl._populate_table()
        tree = self.ui.tbl_steps
        parent = tree.topLevelItem(0)

        tree.blockSignals(True)
        parent.setText(COL_STEP, "Renamed")
        tree.blockSignals(False)

        self.ctrl._on_item_changed(parent, COL_STEP)
        self.assertNotIn("A01", self.ctrl._user_ranges)
        self.assertEqual(self.ctrl._user_ranges["Renamed"], (10.0, 50.0))

    def test_empty_name_rejected(self):
        """Empty or whitespace-only names are silently rejected."""
        self.ctrl._populate_table()
        tree = self.ui.tbl_steps
        parent = tree.topLevelItem(0)

        tree.blockSignals(True)
        parent.setText(COL_STEP, "   ")
        tree.blockSignals(False)

        self.ctrl._on_item_changed(parent, COL_STEP)
        self.assertEqual(self.ctrl._steps[0].step_id, "A01")  # unchanged


class TestBuildDetectionMode(unittest.TestCase, _ControllerHarness):
    """Test that build() in detection mode short-circuits and passes remove_missing=False."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()
        self.ui.spn_gap = MagicMock(value=MagicMock(return_value=5.0))
        self.ui.txt_csv_path = MagicMock()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    @patch("qtpy.QtWidgets.QMessageBox")
    def test_build_after_detect_uses_detection_ranges(
        self, mock_msgbox, mock_active, mock_manifest_cls, mock_regions
    ):
        """build() after detect() uses pre-filled ranges and remove_missing=False."""
        # Make QMessageBox.question return Yes so detect() proceeds
        mock_msgbox.question.return_value = mock_msgbox.Yes

        # During detect(), ShotStore.active() must return a real-ish store
        # so _detect_regions reads use_selected_keys=False correctly.
        detect_store = _fresh_store()
        mock_active.return_value = detect_store

        mock_regions.return_value = [
            {"name": "S1", "start": 10.0, "end": 50.0, "objects": ["obj_A"]},
            {"name": "S2", "start": 60.0, "end": 100.0, "objects": ["obj_B"]},
        ]
        self.ctrl.detect()

        # Confirm detection mode is active
        self.assertTrue(self.ctrl._is_detection_mode)
        self.assertTrue(self.ctrl._all_ranges_complete())

        # Mock the ShotManifest instance returned by the constructor
        mock_store = _fresh_store()
        mock_active.return_value = mock_store

        mock_builder = MagicMock()
        mock_builder.sync.return_value = (
            {"S1": "created", "S2": "created"},
            {"applied": [], "skipped": []},
            [],
        )
        mock_manifest_cls.return_value = mock_builder

        # Ensure pymel.core mock has undoInfo (may be lost when
        # test_sequencer.py runs first and clobbers sys.modules)
        import pymel.core as pm

        if not hasattr(pm, "undoInfo") or not callable(pm.undoInfo):
            pm.undoInfo = MagicMock()

        self.ctrl.build()

        # Verify sync was called with the detection ranges and remove_missing=False
        mock_builder.sync.assert_called_once()
        _, kwargs = mock_builder.sync.call_args
        self.assertFalse(kwargs["remove_missing"])
        self.assertEqual(kwargs["ranges"], {"S1": (10.0, 50.0), "S2": (60.0, 100.0)})


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


class TestAllRangesCompleteBothComponents(unittest.TestCase, _ControllerHarness):
    """Verify _all_ranges_complete checks both start and end."""

    def setUp(self):
        self.setup_controller()

    def test_none_start_returns_false(self):
        """A range with (None, 50.0) is not complete."""
        self.ctrl._user_ranges["A01"] = (None, 50.0)
        self.ctrl._user_ranges["A02"] = (10.0, 60.0)
        self.ctrl._user_ranges["A03"] = (70.0, 100.0)
        self.assertFalse(self.ctrl._all_ranges_complete())

    def test_none_end_returns_false(self):
        """A range with (10.0, None) is not complete."""
        self.ctrl._user_ranges["A01"] = (10.0, None)
        self.ctrl._user_ranges["A02"] = (50.0, 80.0)
        self.ctrl._user_ranges["A03"] = (90.0, 120.0)
        self.assertFalse(self.ctrl._all_ranges_complete())

    def test_both_present_returns_true(self):
        """All ranges with (start, end) returns True."""
        self.ctrl._user_ranges["A01"] = (0.0, 30.0)
        self.ctrl._user_ranges["A02"] = (40.0, 70.0)
        self.ctrl._user_ranges["A03"] = (80.0, 110.0)
        self.assertTrue(self.ctrl._all_ranges_complete())

    def test_empty_steps_returns_false(self):
        """No steps means ranges can't be complete."""
        self.ctrl._steps = []
        self.assertFalse(self.ctrl._all_ranges_complete())


class TestUseSelectedKeysGuard(unittest.TestCase, _ControllerHarness):
    """Verify build() aborts when use_selected_keys is on but no keys are selected.

    Bug: _detect_regions correctly showed a warning and returned [], but
    _resolve_ranges fell through to sequential placement from cursor=1.0,
    generating fallback ranges.  build() then proceeded to sync().
    Fixed: 2025-07-25
    """

    def setUp(self):
        self.setup_controller()
        store = _fresh_store()
        store.detection_mode = "all"
        store.detection_threshold = 5.0
        store.gap = 0.0
        self.ui.spn_gap = MagicMock(value=MagicMock(return_value=5.0))
        self.ui.txt_csv_path = MagicMock()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    def test_resolve_ranges_returns_empty_when_no_selected_keys(self, _mock_sel):
        """_resolve_ranges must return [] when use_selected_keys is on and no keys found."""
        self.ctrl._cached_gaps = None
        resolved = self.ctrl._resolve_ranges()
        self.assertEqual(resolved, [])

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    def test_resolve_ranges_empty_even_with_user_ranges(self, _mock_sel):
        """_resolve_ranges returns [] even when some steps have user-entered ranges.

        Bug: The guard checked `not self._user_ranges`, so pre-existing CSV
        ranges caused the guard to be bypassed and sequential fallback to generate
        ranges for the remaining steps.
        Fixed: 2026-03-23
        """
        self.ctrl._user_ranges = {"A01": (10.0, 50.0)}
        self.ctrl._cached_gaps = None
        resolved = self.ctrl._resolve_ranges()
        self.assertEqual(resolved, [])

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_build_aborts_when_no_selected_keys(
        self, mock_active, mock_manifest_cls, _mock_sel
    ):
        """build() must not call sync() when use_selected_keys yields no regions."""
        mock_active.return_value = _fresh_store()
        mock_active.return_value.detection_mode = "all"

        mock_builder = MagicMock()
        mock_manifest_cls.return_value = mock_builder

        import pymel.core as pm

        if not hasattr(pm, "undoInfo") or not callable(pm.undoInfo):
            pm.undoInfo = MagicMock()

        self.ctrl._cached_gaps = None
        self.ctrl.build()

        mock_builder.sync.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_build_aborts_even_with_complete_user_ranges(
        self, mock_active, mock_manifest_cls, _mock_sel
    ):
        """build() aborts when use_selected_keys is on, even if all ranges are complete.

        Bug: _all_ranges_complete() returned True when CSV provided full ranges,
        causing build to bypass _resolve_ranges entirely and use stale ranges.
        Fixed: 2026-03-23
        """
        # All steps have complete user ranges (simulates CSV with full ranges)
        self.ctrl._user_ranges = {
            "A01": (10.0, 50.0),
            "A02": (60.0, 100.0),
            "A03": (110.0, 150.0),
        }
        self.assertTrue(self.ctrl._all_ranges_complete())

        mock_store = _fresh_store()
        mock_store.detection_mode = "all"
        mock_active.return_value = mock_store

        mock_builder = MagicMock()
        mock_manifest_cls.return_value = mock_builder

        import pymel.core as pm

        if not hasattr(pm, "undoInfo") or not callable(pm.undoInfo):
            pm.undoInfo = MagicMock()

        self.ctrl._cached_gaps = None
        self.ctrl.build()

        mock_builder.sync.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
    )
    def test_resolve_ranges_no_sequential_fallback_with_partial_regions(self, mock_sel):
        """_resolve_ranges must not add sequential-placement entries for steps
        that have no matching detected region when use_selected_keys is on.

        Bug: When use_selected_keys=True and 2 selected-key regions were
        found for 28 CSV steps, the remaining 26 steps received sequential
        fallback ranges starting at the cursor.  This made it appear as if
        all steps had valid ranges even though only 2 key regions existed.
        Fixed: 2026-03-23
        """
        mock_sel.return_value = [
            {"name": "R1", "start": 0.0, "end": 400.0, "objects": ["obj"]},
        ]
        self.ctrl._cached_gaps = None
        resolved = self.ctrl._resolve_ranges()

        # Only 1 detected region â†’ at most 1 step should have a range.
        self.assertEqual(len(resolved), 1, "Extra steps received fallback ranges")
        self.assertEqual(resolved[0][0], "A01")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_build_only_syncs_resolved_steps_in_selected_keys_mode(
        self, mock_active, mock_manifest_cls, mock_sel
    ):
        """build() must only sync steps that received resolved ranges.

        Bug: update() created shots for all 28 CSV steps using its own
        sequential cursor fallback even though only a few key regions
        existed.
        Fixed: 2026-03-23
        """
        mock_sel.return_value = [
            {"name": "R1", "start": 0.0, "end": 400.0, "objects": ["obj"]},
        ]

        mock_store = _fresh_store()
        mock_store.detection_mode = "all"
        mock_active.return_value = mock_store

        mock_builder = MagicMock()
        mock_builder.sync.return_value = (
            {"A01": "created"},
            {"applied": [], "skipped": []},
            [],
        )
        mock_manifest_cls.return_value = mock_builder

        import pymel.core as pm

        if not hasattr(pm, "undoInfo") or not callable(pm.undoInfo):
            pm.undoInfo = MagicMock()

        self.ctrl._cached_gaps = None
        self.ctrl.build()

        # sync() should be called with only the steps that have ranges
        mock_builder.sync.assert_called_once()
        synced_steps = mock_builder.sync.call_args[0][0]
        synced_ids = [s.step_id for s in synced_steps]
        self.assertEqual(synced_ids, ["A01"])


# ---------------------------------------------------------------------------
# Tests: cross-scene QSettings persistence of use_selected_keys
# ---------------------------------------------------------------------------


class TestCrossScenePrefs(unittest.TestCase):
    """Verify detection_mode survives across scenes via QSettings.

    Bug: use_selected_keys was only persisted per-scene via MayaScenePersistence.
    Opening a new scene without opening the shots settings panel caused the
    store to default to use_selected_keys=False, bypassing the guard entirely.
    Fixed: 2025-07-25
    """

    def setUp(self):
        ShotStore._active = None
        ShotStore._persistence = None

    def tearDown(self):
        ShotStore._active = None
        ShotStore._persistence = None

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_fresh_store_restores_detection_mode_from_qsettings(self, mock_qs_cls):
        """active() must apply detection_mode from QSettings when no per-scene data."""
        mock_qs = MagicMock()
        mock_qs.value.side_effect = lambda key, *a: {
            "ShotStore/detection_mode": "skip_zero",
        }.get(key)
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store._restore_user_prefs()

        self.assertEqual(store.detection_mode, "skip_zero")

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
    def test_save_writes_detection_mode_to_qsettings(self, mock_qs_cls):
        """save() must persist detection_mode to QSettings."""
        mock_qs = MagicMock()
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store.detection_mode = "zero_as_end"
        store._save_user_prefs()

        mock_qs.setValue.assert_any_call("ShotStore/detection_mode", "zero_as_end")

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


class TestCsvLayoutPresets(unittest.TestCase, _ControllerHarness):
    """Integration tests for CSV layout preset save/load on the controller."""

    def setUp(self):
        self.setup_controller()

    def test_preset_manager_wired_on_construction(self):
        """Controller has a _csv_layout_presets PresetManager after init."""
        self.assertTrue(hasattr(self.ctrl, "_csv_layout_presets"))
        self.assertIsNotNone(self.ctrl._csv_layout_presets)

    def test_metadata_provider_serializes_column_map(self):
        """metadata_provider returns the current ColumnMap as a dict."""
        self.ctrl._column_map = ColumnMap(
            description=("Desc",), assets=("Obj",), audio=("VO",)
        )
        meta = self.ctrl._csv_layout_presets.metadata_provider()
        cm = meta["column_map"]
        self.assertEqual(cm["description"], ["Desc"])
        self.assertEqual(cm["assets"], ["Obj"])
        self.assertEqual(cm["audio"], ["VO"])

    def test_on_csv_layout_loaded_restores_column_map(self):
        """_on_csv_layout_loaded restores a ColumnMap from metadata."""
        meta = {
            "column_map": {
                "step_id": ["ID"],
                "description": ["Body"],
                "assets": ["Thing"],
                "audio": ["Narration"],
                "exclude_steps": [],
            }
        }
        self.ctrl._on_csv_layout_loaded(meta)
        cm = self.ctrl._column_map
        self.assertEqual(cm.step_id, ("ID",))
        self.assertEqual(cm.description, ("Body",))
        self.assertEqual(cm.assets, ("Thing",))
        self.assertEqual(cm.audio, ("Narration",))
        self.assertEqual(cm.exclude_steps, ())

    def test_on_csv_layout_loaded_missing_key_uses_defaults(self):
        """Empty metadata falls back to default ColumnMap."""
        self.ctrl._column_map = ColumnMap(description=("Custom",))
        self.ctrl._on_csv_layout_loaded({})
        # Should reset to defaults
        cm = self.ctrl._column_map
        self.assertEqual(cm.description, ("Step Contents", "Contents"))

    def test_on_csv_layout_applied_reparses_csv(self):
        """Applying a preset re-parses the active CSV file."""
        self.ctrl._csv_path = "/fake/path.csv"
        self.ctrl._load_csv = MagicMock()
        self.ctrl._on_csv_layout_applied()
        self.ctrl._load_csv.assert_called_once_with("/fake/path.csv")

    def test_on_csv_layout_applied_no_path_is_noop(self):
        """Applying a preset with no CSV path does nothing."""
        self.ctrl._csv_path = ""
        self.ui.txt_csv_path.text.return_value = ""
        self.ctrl._load_csv = MagicMock()
        self.ctrl._on_csv_layout_applied()
        self.ctrl._load_csv.assert_not_called()

    def test_round_trip_through_callbacks(self):
        """Saving then loading via callbacks preserves the ColumnMap."""
        original = ColumnMap(
            step_id=("ID",),
            description=("Desc",),
            assets=("Obj",),
            audio=("VO",),
            exclude_steps=("INTRO",),
        )
        self.ctrl._column_map = original
        # Simulate save: capture what metadata_provider returns
        meta = self.ctrl._csv_layout_presets.metadata_provider()
        # Simulate load: feed it back through the load callback
        self.ctrl._column_map = ColumnMap()  # Reset first
        self.ctrl._on_csv_layout_loaded(meta)
        restored = self.ctrl._column_map
        self.assertEqual(restored.step_id, ("ID",))
        self.assertEqual(restored.description, ("Desc",))
        self.assertEqual(restored.assets, ("Obj",))
        self.assertEqual(restored.audio, ("VO",))
        self.assertEqual(restored.exclude_steps, ("INTRO",))


# ---------------------------------------------------------------------------
# Tests: Incremental CSV sync (zero-duration fallback)
# ---------------------------------------------------------------------------


class TestStepIsBuilt(unittest.TestCase, _ControllerHarness):
    """Test per-step _step_is_built() vs global _is_built.

    Bug: _is_built returned True for ALL steps when any step was built,
    blocking range editing for newly-added CSV steps.
    Fixed: 2026-04-03
    """

    def setUp(self):
        self.setup_controller()
        store = _fresh_store()
        # Only A01 is built
        store.define_shot("A01", 1, 31, ["obj_A01"])

    def test_is_built_true_when_any_step_built(self):
        self.assertTrue(self.ctrl._is_built)

    def test_step_is_built_true_for_built_step(self):
        self.assertTrue(self.ctrl._step_is_built("A01"))

    def test_step_is_built_false_for_unbuilt_step(self):
        self.assertFalse(self.ctrl._step_is_built("A02"))
        self.assertFalse(self.ctrl._step_is_built("A03"))


class TestLoadCsvSeedsStoreRanges(unittest.TestCase, _ControllerHarness):
    """Test that _load_csv seeds _user_ranges from existing store positions.

    When a CSV is re-loaded after some shots are already built, the table
    should immediately show correct Start/End for built steps.
    Fixed: 2026-04-03
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.define_shot("A01", 10, 40, ["obj_A01"])
        self.store.define_shot("A02", 50, 80, ["obj_A02"])

    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.parse_csv")
    def test_store_ranges_seeded_into_user_ranges(self, mock_parse):
        mock_parse.return_value = _make_steps("A01", "A02", "A03")
        import os

        with patch.object(os.path, "isfile", return_value=True):
            self.ctrl._load_csv("/fake/path.csv")

        # Built steps should have their store ranges
        self.assertEqual(self.ctrl._user_ranges["A01"], (10, 40))
        self.assertEqual(self.ctrl._user_ranges["A02"], (50, 80))
        # Unbuilt step should NOT have a range
        self.assertNotIn("A03", self.ctrl._user_ranges)


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

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
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


class TestIncrementalBuild(unittest.TestCase, _ControllerHarness):
    """Test the incremental build branch in build().

    When _is_built is True and not in selected-keys mode, build() should
    import store positions for existing shots and pass
    zero_duration_fallback=True to sync().
    Fixed: 2026-04-03
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.define_shot("A01", 10, 40, ["obj_A01"])

    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    def test_incremental_passes_zero_duration_flag(self, mock_cls):
        """sync() should receive zero_duration_fallback=True in incremental mode."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_cls.return_value = mock_builder

        import pymel.core as pm

        pm.undoInfo = MagicMock()

        self.ctrl.build()

        mock_builder.sync.assert_called_once()
        call_kwargs = mock_builder.sync.call_args
        self.assertTrue(
            call_kwargs.kwargs.get("zero_duration_fallback")
            or (len(call_kwargs.args) > 4 and call_kwargs.args[4]),
            "sync should be called with zero_duration_fallback=True",
        )


class TestCsvModeRespectsDetectionMode(unittest.TestCase, _ControllerHarness):
    """CSV mode must respect store.detection_mode for use_sel gating.

    The CSV defines step names, objects, and behaviors.  The store's
    detection_mode independently controls how timing boundaries are
    discovered (auto = full scene, skip_zero/all = selected keys).

    Bug: _csv_path was used to gate both step-source AND detection-mode,
    forcing auto-detect whenever CSV was loaded.  This broke selected-
    keys range detection for CSV users.
    Fixed: 2026-04-07
    """

    def setUp(self):
        self.setup_controller()
        store = _fresh_store()
        store.detection_mode = "all"
        store.detection_threshold = 5.0
        store.gap = 0.0
        # Simulate CSV mode — _csv_path is set
        self.ctrl._csv_path = "/fake/shots.csv"

    def test_resolve_ranges_respects_detection_mode_in_csv_mode(self):
        """_resolve_ranges should use selected-keys when detection_mode is non-auto,
        even in CSV mode."""
        with (
            patch(
                "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions",
            ) as mock_auto,
            patch(
                "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
                return_value=[],
            ) as mock_sel,
        ):
            self.ctrl._cached_gaps = None
            self.ctrl._resolve_ranges()
            mock_sel.assert_called_once()
            mock_auto.assert_not_called()

    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    def test_build_uses_selected_keys_in_csv_mode(self, mock_cls):
        """build() in CSV mode with non-auto detection_mode should use
        selected-keys detection for range verification."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_cls.return_value = mock_builder

        import pymel.core as pm

        pm.undoInfo = MagicMock()

        with patch(
            "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
            return_value=[],
        ) as mock_sel:
            self.ctrl.build()
            mock_sel.assert_called_once()


class TestSyncDetectionWidgets(unittest.TestCase, _ControllerHarness):
    """_sync_detection_widgets disables detection controls after build.

    Fixed: 2026-04-03
    """

    def setUp(self):
        self.setup_controller()
        # Create mock detection widgets on a fake parent shots UI
        self.mock_shots_ui = MagicMock()
        self.mock_shots_ui.cmb_detection_mode = MagicMock()
        self.mock_shots_ui.spn_detection = MagicMock()
        self.sb.loaded_ui.shots = self.mock_shots_ui

    def test_widgets_disabled_when_built(self):
        store = _fresh_store()
        store.define_shot("A01", 1, 31, ["obj_A01"])
        self.assertTrue(self.ctrl._is_built)

        self.ctrl._sync_detection_widgets()

        self.mock_shots_ui.cmb_detection_mode.setEnabled.assert_called_with(False)
        self.mock_shots_ui.spn_detection.setEnabled.assert_called_with(False)

    def test_widgets_enabled_when_not_built(self):
        _fresh_store()
        self.assertFalse(self.ctrl._is_built)

        self.ctrl._sync_detection_widgets()

        self.mock_shots_ui.cmb_detection_mode.setEnabled.assert_called_with(True)
        self.mock_shots_ui.spn_detection.setEnabled.assert_called_with(True)

    def test_tolerates_missing_shots_ui(self):
        """Should not raise if shots UI is not loaded."""
        del self.sb.loaded_ui.shots
        _fresh_store()
        # Should not raise
        self.ctrl._sync_detection_widgets()


class TestRangeEditValidation(unittest.TestCase, _ControllerHarness):
    """Validate _on_item_changed() rejects invalid range edits and accepts valid ones.

    These cover the validation rules in the range column edit handler:
    non-numeric text, negative start, end <= start, overlap with previous
    step's resolved end, and the happy path of a valid range flowing into
    _user_ranges.
    Fixed: 2026-04-03
    """

    def setUp(self):
        self.setup_controller()
        _fresh_store()
        self.ctrl._populate_table()
        # Seed resolved ranges so _revert_range_cell and overlap checks work.
        self.ctrl._last_resolved = [
            ("A01", 1.0, 31.0, False),
            ("A02", 31.0, 61.0, False),
            ("A03", 61.0, 91.0, False),
        ]

    def _find_item(self, step_id):
        from qtpy.QtCore import Qt

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole)
            if isinstance(data, BuilderStep) and data.step_id == step_id:
                return item
        self.fail(f"Step {step_id} not found in tree")

    def test_valid_range_stored(self):
        """A valid start/end pair should be accepted into _user_ranges."""
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "40")
        item.setText(COL_END, "70")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertEqual(self.ctrl._user_ranges["A02"], (40.0, 70.0))

    def test_non_numeric_start_rejected(self):
        """Non-numeric start value should revert the cell."""
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "abc")
        item.setText(COL_END, "70")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_non_numeric_end_rejected(self):
        """Non-numeric end value should revert the cell."""
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "40")
        item.setText(COL_END, "xyz")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_negative_start_rejected(self):
        """Negative start frame should be rejected."""
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "-10")
        item.setText(COL_END, "70")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_end_less_than_start_rejected(self):
        """End <= start should be rejected."""
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "50")
        item.setText(COL_END, "30")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_end_equal_start_rejected(self):
        """End == start should be rejected (zero-duration via manual edit)."""
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "50")
        item.setText(COL_END, "50")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_start_before_previous_end_rejected(self):
        """Start that precedes the previous step's resolved end is rejected."""
        item = self._find_item("A02")
        # A01 resolved end is 31.0, so start=25 overlaps
        self.tree.blockSignals(True)
        item.setText(COL_START, "25")
        item.setText(COL_END, "70")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)

    def test_clear_both_removes_user_range(self):
        """Clearing both start and end should remove the user range."""
        self.ctrl._user_ranges["A02"] = (40.0, 70.0)
        item = self._find_item("A02")
        self.tree.blockSignals(True)
        item.setText(COL_START, "")
        item.setText(COL_END, "")
        self.tree.blockSignals(False)
        self.ctrl._on_item_changed(item, COL_START)
        self.assertNotIn("A02", self.ctrl._user_ranges)


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

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.compute_duration")
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
        actions = self.assembler.update(
            steps, ranges=ranges, zero_duration_fallback=True
        )
        a03 = next(s for s in self.store.shots if s.name == "A03")
        a04 = next(s for s in self.store.shots if s.name == "A04")
        self.assertEqual(a03.start, 100.0)
        self.assertEqual(a03.end, 150.0)
        self.assertEqual(a04.start, a04.end, "A04 should have zero duration")


class TestIncrementalPlacement(unittest.TestCase, _ControllerHarness):
    """New steps in incremental build should be placed between their
    CSV-order neighbors instead of at the end of the timeline.

    Bug: New CSV step A09 (between A08 and A10) was placed at frame
    21041 (end of timeline) instead of at A08's end (2520).
    Fixed: 2026-04-04
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        # Three existing shots in the store
        self.store.define_shot("A01", 1, 100, ["obj_A01"])
        self.store.define_shot("A02", 100, 200, ["obj_A02"])
        self.store.define_shot("A03", 200, 300, ["obj_A03"])

    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    def test_new_shot_placed_between_neighbors(self, mock_cls):
        """A new step B01 inserted between A02 and A03 should get
        (200, 200) — A02's end — not (300+, 300+)."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_cls.return_value = mock_builder

        import pymel.core as pm

        pm.undoInfo = MagicMock()

        # CSV order: A01, A02, B01(new), A03
        self.ctrl._steps = _make_steps("A01", "A02", "B01", "A03")
        self.ctrl._csv_path = "/fake.csv"
        self.ctrl.build()

        call_kwargs = mock_builder.sync.call_args
        ranges = call_kwargs.kwargs.get("ranges") or call_kwargs[1].get("ranges")
        self.assertIn("B01", ranges)
        self.assertEqual(
            ranges["B01"], (200, 200), "New step should be at predecessor A02's end"
        )

    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    def test_consecutive_new_shots_stack(self, mock_cls):
        """Two adjacent new steps should both stack at the same position."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_cls.return_value = mock_builder

        import pymel.core as pm

        pm.undoInfo = MagicMock()

        # CSV order: A01, B01(new), B02(new), A02, A03
        self.ctrl._steps = _make_steps("A01", "B01", "B02", "A02", "A03")
        self.ctrl._csv_path = "/fake.csv"
        self.ctrl.build()

        call_kwargs = mock_builder.sync.call_args
        ranges = call_kwargs.kwargs.get("ranges") or call_kwargs[1].get("ranges")
        # Both should be at A01's end (100)
        self.assertEqual(ranges["B01"], (100, 100))
        self.assertEqual(ranges["B02"], (100, 100))

    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    def test_new_shot_at_start_of_csv(self, mock_cls):
        """A new step at the very start of the CSV should use the
        first existing neighbor's start position."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_cls.return_value = mock_builder

        import pymel.core as pm

        pm.undoInfo = MagicMock()

        # CSV order: B01(new), A01, A02, A03
        self.ctrl._steps = _make_steps("B01", "A01", "A02", "A03")
        self.ctrl._csv_path = "/fake.csv"
        self.ctrl.build()

        call_kwargs = mock_builder.sync.call_args
        ranges = call_kwargs.kwargs.get("ranges") or call_kwargs[1].get("ranges")
        # B01 should be at A01's start (1)
        self.assertEqual(ranges["B01"], (1, 1))


# ---------------------------------------------------------------------------
# Tests: short_name helper
# ---------------------------------------------------------------------------


class TestShortName(unittest.TestCase):
    """Verify the short_name() DAG-path leaf extractor."""

    def test_full_dag_path(self):
        self.assertEqual(short_name("|group1|subgrp|mesh"), "mesh")

    def test_already_short(self):
        self.assertEqual(short_name("mesh"), "mesh")

    def test_single_pipe(self):
        self.assertEqual(short_name("|root"), "root")

    def test_empty_string(self):
        self.assertEqual(short_name(""), "")

    def test_none_safe(self):
        """short_name('') returns '' — callers should guard against None."""
        self.assertEqual(short_name(""), "")


# ---------------------------------------------------------------------------
# Tests: tree state save/restore
# ---------------------------------------------------------------------------


class TestTreeStateSaveRestore(unittest.TestCase, _ControllerHarness):
    """Verify expand/scroll state survives table rebuilds."""

    def setUp(self):
        self.setup_controller()

    def test_expansion_preserved_across_populate(self):
        """Expanded rows stay expanded after _populate_table."""
        self.ctrl._populate_table()
        tree = self.tree
        # Expand first step, leave others collapsed
        tree.topLevelItem(0).setExpanded(True)
        self.assertTrue(tree.topLevelItem(0).isExpanded())
        self.assertFalse(tree.topLevelItem(1).isExpanded())

        state = self.ctrl._save_tree_state()
        self.ctrl._populate_table()
        self.ctrl._restore_tree_state(state)

        self.assertTrue(tree.topLevelItem(0).isExpanded())
        self.assertFalse(tree.topLevelItem(1).isExpanded())

    def test_save_returns_step_ids(self):
        """Saved state uses step-ID strings, not indices."""
        self.ctrl._populate_table()
        self.tree.topLevelItem(1).setExpanded(True)
        expanded, _ = self.ctrl._save_tree_state()
        self.assertIn("A02", expanded)
        self.assertNotIn("A01", expanded)


# ---------------------------------------------------------------------------
# Tests: long names toggle
# ---------------------------------------------------------------------------


class TestLongNamesToggle(unittest.TestCase, _ControllerHarness):
    """Verify the _use_short_names property and long-names setting."""

    def setUp(self):
        self.setup_controller()
        # Clear any persisted value so tests start from a known state
        self.ctrl._settings.clear("long_names")

    def tearDown(self):
        self.ctrl._settings.clear("long_names")

    def test_default_uses_short_names(self):
        """Short names enabled by default (long_names setting absent)."""
        self.assertTrue(self.ctrl._use_short_names)

    def test_long_names_setting_disables_short(self):
        """Setting long_names=True disables short name display."""
        self.ctrl._settings.setValue("long_names", True)
        self.assertFalse(self.ctrl._use_short_names)

    def test_long_names_false_keeps_short(self):
        """Explicitly setting long_names=False keeps short names on."""
        self.ctrl._settings.setValue("long_names", False)
        self.assertTrue(self.ctrl._use_short_names)

    def test_child_rows_show_short_names_by_default(self):
        """Child row text uses leaf name when short names is on."""
        # Add an object with a long DAG path
        self.ctrl._steps[0].objects[0].name = "|group1|subgrp|obj_A01"
        self.ctrl._populate_table()
        parent = self.tree.topLevelItem(0)
        child = parent.child(0)
        self.assertEqual(child.text(COL_DESC), "obj_A01")
        # Tooltip has full path
        self.assertEqual(child.toolTip(COL_DESC), "|group1|subgrp|obj_A01")

    def test_child_rows_show_long_names_when_toggled(self):
        """Child row text uses full path when long names enabled."""
        self.ctrl._settings.setValue("long_names", True)
        self.ctrl._steps[0].objects[0].name = "|group1|subgrp|obj_A01"
        self.ctrl._populate_table()
        parent = self.tree.topLevelItem(0)
        child = parent.child(0)
        self.assertEqual(child.text(COL_DESC), "|group1|subgrp|obj_A01")


# ---------------------------------------------------------------------------
# Tests: color override restore
# ---------------------------------------------------------------------------


class TestColorOverrideRestore(unittest.TestCase, _ControllerHarness):
    """Verify _restore_color_overrides applies persisted colours."""

    def setUp(self):
        self.setup_controller()

    def test_restore_mutates_palette(self):
        """Persisted fg override is applied to PASTEL_STATUS."""
        from uitk.widgets.mixins.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import PASTEL_STATUS

        settings = SettingsManager(namespace=self.ctrl._COLOR_SETTINGS_NS)
        orig_fg = str(PASTEL_STATUS["missing_object"][0])
        try:
            settings.setValue("missing_object/fg", "#FF0000")
            self.ctrl._restore_color_overrides()
            self.assertEqual(str(PASTEL_STATUS["missing_object"][0]), "#FF0000")
        finally:
            # Restore original to avoid polluting other tests
            settings.clear("missing_object/fg")
            PASTEL_STATUS["missing_object"] = (
                orig_fg,
                str(PASTEL_STATUS["missing_object"][1]),
            )

    def test_restore_updates_behavior_status_colors(self):
        """Persisted missing_behavior override propagates to BEHAVIOR_STATUS_COLORS."""
        from uitk.widgets.mixins.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
            PASTEL_STATUS,
            BEHAVIOR_STATUS_COLORS,
        )

        settings = SettingsManager(namespace=self.ctrl._COLOR_SETTINGS_NS)
        orig_fg = str(PASTEL_STATUS["missing_behavior"][0])
        try:
            settings.setValue("missing_behavior/fg", "#AABBCC")
            self.ctrl._restore_color_overrides()
            self.assertEqual(str(BEHAVIOR_STATUS_COLORS["missing"]), "#AABBCC")
        finally:
            settings.clear("missing_behavior/fg")
            PASTEL_STATUS["missing_behavior"] = (
                orig_fg,
                str(PASTEL_STATUS["missing_behavior"][1]),
            )
            BEHAVIOR_STATUS_COLORS["missing"] = orig_fg

    def test_restore_updates_error_color(self):
        """Persisted missing_object override propagates to BEHAVIOR_STATUS_COLORS['error']."""
        from uitk.widgets.mixins.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
            PASTEL_STATUS,
            BEHAVIOR_STATUS_COLORS,
        )

        settings = SettingsManager(namespace=self.ctrl._COLOR_SETTINGS_NS)
        orig_fg = str(PASTEL_STATUS["missing_object"][0])
        try:
            settings.setValue("missing_object/fg", "#DD0011")
            self.ctrl._restore_color_overrides()
            self.assertEqual(str(BEHAVIOR_STATUS_COLORS["error"]), "#DD0011")
        finally:
            settings.clear("missing_object/fg")
            PASTEL_STATUS["missing_object"] = (
                orig_fg,
                str(PASTEL_STATUS["missing_object"][1]),
            )
            BEHAVIOR_STATUS_COLORS["error"] = orig_fg


class TestLastResultsPreservation(unittest.TestCase, _ControllerHarness):
    """Verify _last_results isn't wiped by non-structural store events."""

    def setUp(self):
        self.setup_controller()

    def test_shot_updated_preserves_results(self):
        """ShotUpdated should not clear _last_results."""
        from types import SimpleNamespace
        from mayatk.anim_utils.shots._shots import ShotUpdated, ShotBlock

        sentinel = [SimpleNamespace(built=True)]
        self.ctrl._last_results = sentinel
        self.ctrl._steps = [object()]  # non-empty to pass guard
        self.ctrl._on_store_event(ShotUpdated(shot=ShotBlock(0, "A", 0, 10)))
        self.assertIs(self.ctrl._last_results, sentinel)

    def test_active_shot_changed_preserves_results(self):
        """ActiveShotChanged should not clear _last_results."""
        from types import SimpleNamespace
        from mayatk.anim_utils.shots._shots import ActiveShotChanged

        sentinel = [SimpleNamespace(built=True)]
        self.ctrl._last_results = sentinel
        self.ctrl._steps = [object()]
        self.ctrl._on_store_event(ActiveShotChanged(shot_id=0))
        self.assertIs(self.ctrl._last_results, sentinel)

    def test_shot_removed_clears_results(self):
        """ShotRemoved SHOULD clear _last_results."""
        from mayatk.anim_utils.shots._shots import ShotRemoved

        self.ctrl._last_results = [object()]
        self.ctrl._steps = [object()]
        self.ctrl._on_store_event(ShotRemoved(shot_id=0))
        self.assertEqual(self.ctrl._last_results, [])

    def test_batch_complete_clears_results(self):
        """BatchComplete SHOULD clear _last_results."""
        from mayatk.anim_utils.shots._shots import BatchComplete

        self.ctrl._last_results = [object()]
        self.ctrl._steps = [object()]
        self.ctrl._on_store_event(BatchComplete())
        self.assertEqual(self.ctrl._last_results, [])


# ---------------------------------------------------------------------------
# Tests: format_behavior_html
# ---------------------------------------------------------------------------


class TestFormatBehaviorHtml(unittest.TestCase):
    """Verify behaviour label HTML respects broken / status_color flags."""

    def setUp(self):
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
            format_behavior_html,
            BEHAVIOR_STATUS_COLORS,
        )

        self.format = format_behavior_html
        self.colors = BEHAVIOR_STATUS_COLORS

    def test_plain_when_no_broken(self):
        """Behaviours with no issues produce plain text (no span tags)."""
        html = self.format(["fade_in", "fade_out"])
        self.assertNotIn("<span", html)
        self.assertIn("Fade In", html)
        self.assertIn("Fade Out", html)

    def test_broken_gets_missing_color(self):
        """Only the broken behaviour gets a colour span."""
        html = self.format(["fade_in", "fade_out"], broken=["fade_out"])
        self.assertNotIn("Fade In</span>", html)  # fade_in is plain
        self.assertIn(self.colors["missing"], html)  # fade_out is coloured
        self.assertIn("Fade Out</span>", html)

    def test_status_color_overrides_all(self):
        """status_color colours every behaviour, ignoring broken."""
        html = self.format(
            ["fade_in", "fade_out"],
            broken=["fade_out"],
            status_color="#FF0000",
        )
        self.assertEqual(html.count("color:#FF0000"), 2)
        self.assertIn("Fade In</span>", html)
        self.assertIn("Fade Out</span>", html)

    def test_missing_object_error_color(self):
        """error colour (from BEHAVIOR_STATUS_COLORS) renders all red."""
        error_color = self.colors["error"]
        html = self.format(["fade_in"], status_color=error_color)
        self.assertIn(f"color:{error_color}", html)
        self.assertIn("Fade In</span>", html)

    def test_empty_behaviors(self):
        """Empty list returns empty string."""
        self.assertEqual(self.format([]), "")
        self.assertEqual(self.format([], status_color="#FF0000"), "")


# ---------------------------------------------------------------------------
# Tests: _detect_regions honours detection_mode with empty steps
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: ShotStore detection convenience API
# ---------------------------------------------------------------------------


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
        "mayatk.anim_utils.shots._shots.detect_shot_regions",
        return_value=[{"name": "R1", "start": 1, "end": 30, "objects": []}],
    )
    def test_auto_mode_calls_detect_shot_regions(self, mock_auto):
        self.store.detection_mode = "auto"
        self.store.detection_threshold = 7.0
        result = self.store.detect_regions()
        mock_auto.assert_called_once_with(gap_threshold=7.0)
        self.assertEqual(len(result), 1)

    @patch(
        "mayatk.anim_utils.shots._shots.regions_from_selected_keys",
        return_value=[{"name": "K1", "start": 10, "end": 40, "objects": []}],
    )
    def test_skip_zero_mode_calls_selected_keys(self, mock_sel):
        self.store.detection_mode = "skip_zero"
        self.store.detection_threshold = 3.0
        result = self.store.detect_regions()
        mock_sel.assert_called_once_with(gap_threshold=3.0, key_filter="skip_zero")
        self.assertEqual(len(result), 1)

    @patch(
        "mayatk.anim_utils.shots._shots.regions_from_selected_keys",
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

    @patch("mayatk.anim_utils.shots._shots.detect_shot_regions")
    def test_creates_all_shots(self, mock_detect):
        mock_detect.return_value = self.regions
        created = self.store.detect_and_define()
        self.assertEqual(len(created), 2)
        self.assertEqual(len(self.store.shots), 2)
        self.assertEqual(created[0].name, "R1")
        self.assertEqual(created[1].name, "R2")

    @patch("mayatk.anim_utils.shots._shots.detect_shot_regions")
    def test_skips_overlapping_by_default(self, mock_detect):
        self.store.define_shot("existing", 20, 50, [])
        mock_detect.return_value = self.regions
        created = self.store.detect_and_define()
        # R1 overlaps [20,50], R2 overlaps [20,50] — both skipped
        self.assertEqual(len(created), 0)
        self.assertEqual(len(self.store.shots), 1)  # only "existing"

    @patch("mayatk.anim_utils.shots._shots.detect_shot_regions")
    def test_overwrite_creates_overlapping(self, mock_detect):
        self.store.define_shot("existing", 20, 50, [])
        mock_detect.return_value = self.regions
        created = self.store.detect_and_define(overwrite=True)
        self.assertEqual(len(created), 2)
        self.assertEqual(len(self.store.shots), 3)

    @patch("mayatk.anim_utils.shots._shots.detect_shot_regions")
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

    def test_all_objects_exist(self):
        self.store.define_shot("A01", 1, 30, ["|obj_a", "|obj_b"])
        _mock_cmds.ls.return_value = ["|obj_a", "|obj_b"]
        result = self.store.assess()
        shot = self.store.shots[0]
        self.assertEqual(result[shot.shot_id], "valid")

    def test_missing_object(self):
        self.store.define_shot("A01", 1, 30, ["|obj_a", "|obj_b"])
        _mock_cmds.ls.return_value = ["|obj_a"]  # only 1 of 2
        result = self.store.assess()
        shot = self.store.shots[0]
        self.assertEqual(result[shot.shot_id], "missing_object")

    def tearDown(self):
        _mock_cmds.ls.return_value = []


# ---------------------------------------------------------------------------
# Tests: shots_slots store event handling
# ---------------------------------------------------------------------------


class TestShotsControllerStoreEvents(unittest.TestCase):
    """ShotsController._on_store_event handles ShotDefined and ShotRemoved.

    Bug: ShotRemoved only called _populate_shot_combobox + _sync_footer,
    never re-syncing detection widgets. Deleting the last shot left them
    disabled. ShotDefined was unhandled entirely.
    Fixed: 2026-04-07
    """

    def setUp(self):
        from mayatk.anim_utils.shots.shots_slots import ShotsController

        _fresh_store()
        # Build a bare instance without __init__ (avoids UI wiring)
        self.ctrl = object.__new__(ShotsController)
        self.ctrl._sync_from_store = MagicMock()
        self.ctrl._sync_shot_editor = MagicMock()
        self.ctrl._sync_footer = MagicMock()

    def test_shot_defined_triggers_full_sync(self):
        from mayatk.anim_utils.shots._shots import ShotDefined

        shot = ShotBlock(shot_id=1, name="A01", start=1, end=30)
        self.ctrl._on_store_event(ShotDefined(shot=shot))
        self.ctrl._sync_from_store.assert_called_once()

    def test_shot_removed_triggers_full_sync(self):
        from mayatk.anim_utils.shots._shots import ShotRemoved

        self.ctrl._on_store_event(ShotRemoved(shot_id=1))
        self.ctrl._sync_from_store.assert_called_once()

    def test_batch_complete_triggers_full_sync(self):
        from mayatk.anim_utils.shots._shots import BatchComplete

        self.ctrl._on_store_event(BatchComplete())
        self.ctrl._sync_from_store.assert_called_once()

    def test_shot_updated_does_not_trigger_full_sync(self):
        from mayatk.anim_utils.shots._shots import ShotUpdated

        shot = ShotBlock(shot_id=1, name="A01", start=1, end=30)
        self.ctrl._on_store_event(ShotUpdated(shot=shot))
        self.ctrl._sync_from_store.assert_not_called()
        self.ctrl._sync_shot_editor.assert_called_once()


class TestDetectRegionsHonoursMode(unittest.TestCase, _ControllerHarness):
    """_detect_regions must use the store's detection_mode even when
    _steps is empty (first detection).

    Bug: _detect_regions guarded the selected-keys path with
    ``_is_detection_mode`` which requires non-empty _steps.  On the
    very first detection (empty steps) the mode was forced to "auto",
    silently ignoring skip_zero / all / zero_as_end.
    Fixed: 2026-04-07
    """

    def setUp(self):
        self.setup_controller()
        self.ctrl._steps = []  # simulate first detection (no steps yet)
        self.ctrl._csv_path = ""
        self.store = _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_skip_zero_calls_selected_keys_with_empty_steps(self, mock_auto, mock_sel):
        """With skip_zero mode and empty steps, _detect_regions must call
        regions_from_selected_keys, NOT detect_shot_regions."""
        self.store.detection_mode = "skip_zero"
        mock_sel.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]
        mock_auto.return_value = []

        regions = self.ctrl._detect_regions(5.0)

        mock_sel.assert_called_once_with(gap_threshold=5.0, key_filter="skip_zero")
        mock_auto.assert_not_called()
        self.assertEqual(len(regions), 1)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_auto_mode_still_calls_detect_shot_regions(self, mock_auto, mock_sel):
        """With auto mode, _detect_regions must call detect_shot_regions."""
        self.store.detection_mode = "auto"
        mock_auto.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]

        regions = self.ctrl._detect_regions(5.0)

        mock_auto.assert_called_once()
        mock_sel.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_csv_mode_respects_store_detection_mode(self, mock_auto, mock_sel):
        """When a CSV is loaded and detection_mode is skip_zero,
        _detect_regions must use selected-keys detection, not auto."""
        self.store.detection_mode = "skip_zero"
        self.ctrl._csv_path = "/some/file.csv"
        mock_sel.return_value = []

        self.ctrl._detect_regions(5.0)

        mock_sel.assert_called_once()
        mock_auto.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_settings_changed_triggers_detect_after_first_show(
        self, mock_auto, mock_sel
    ):
        """SettingsChanged event must trigger detect() after first-show,
        even when _steps is empty, so that changing to skip_zero mode
        after a failed initial detect works."""
        from mayatk.anim_utils.shots._shots import SettingsChanged

        self.store.detection_mode = "skip_zero"
        mock_sel.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]

        # Mark as shown (simulates _on_first_show having run)
        self.ctrl._first_shown = True

        # Simulate SettingsChanged event
        self.ctrl._on_store_event(SettingsChanged())

        # Should have triggered detect → regions_from_selected_keys
        mock_sel.assert_called_once()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_settings_changed_skipped_before_first_show(self, mock_auto, mock_sel):
        """SettingsChanged before _on_first_show must NOT trigger detect(),
        avoiding spurious message boxes when the widget isn't visible yet.

        Bug: Removing the _steps guard from the SettingsChanged handler
        without a visibility check let detection fire during construction.
        Fixed: 2026-04-07
        """
        from mayatk.anim_utils.shots._shots import SettingsChanged

        self.store.detection_mode = "skip_zero"
        # _first_shown defaults to False on fresh controller
        self.assertFalse(self.ctrl._first_shown)

        self.ctrl._on_store_event(SettingsChanged())

        mock_sel.assert_not_called()
        mock_auto.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    def test_settings_changed_refreshes_ranges_in_csv_mode(self, mock_auto, mock_sel):
        """SettingsChanged in CSV mode must refresh ranges (not replace
        CSV steps with detected steps).

        Bug: The SettingsChanged handler skipped CSV mode entirely,
        so changing detection_mode had no effect for CSV users.
        Fixed: 2026-04-07
        """
        from mayatk.anim_utils.shots._shots import SettingsChanged

        self.store.detection_mode = "skip_zero"
        mock_sel.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]

        # Simulate CSV mode with loaded steps
        self.ctrl._csv_path = "/some/file.csv"
        self.ctrl._steps = _make_steps("A01", "A02", "A03")
        self.ctrl._first_shown = True

        # Fire SettingsChanged
        self.ctrl._on_store_event(SettingsChanged())

        # Steps should still be from CSV (not replaced)
        self.assertEqual(len(self.ctrl._steps), 3)
        self.assertEqual(self.ctrl._steps[0].step_id, "A01")
        # detect() should NOT have been called (would replace CSV steps)
        # Instead _refresh_ranges ran, which calls _detect_regions
        # through _resolve_ranges — so selected-keys detection should
        # have fired (store mode is skip_zero).
        mock_sel.assert_called()
        mock_auto.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: assess() selected-keys guard + scene discovery skip
# ---------------------------------------------------------------------------


class TestAssessSelectedKeysGuard(unittest.TestCase, _ControllerHarness):
    """assess() must re-verify selected-keys exist and skip full-scene
    discovery when using a selected-keys detection mode.

    Bug: assess() had no selected-keys guard — it always ran full scene
    assessment including _discover_scene_objects(), adding all animated
    scene objects even in skip_zero mode.  No warning was shown when
    no keys were selected.
    Fixed: 2026-04-07
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.detection_mode = "skip_zero"
        self.store.detection_threshold = 5.0
        self.store.gap = 0.0
        self.ui.spn_gap = MagicMock(value=MagicMock(return_value=5.0))
        self.ui.txt_csv_path = MagicMock()
        # Steps from detection (no CSV)
        self.ctrl._csv_path = ""

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_assess_aborts_no_keys_selected(
        self, mock_active, mock_manifest_cls, _mock_sel
    ):
        """assess() must abort when skip_zero mode is active and no keys
        are selected, instead of running full scene assessment."""
        mock_active.return_value = self.store
        mock_builder = MagicMock()
        mock_manifest_cls.return_value = mock_builder

        self.ctrl._first_shown = True
        self.ctrl.assess()

        mock_builder.assess.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_assess_proceeds_when_keys_selected(
        self, mock_active, mock_manifest_cls, mock_sel
    ):
        """assess() proceeds normally when selected keys are found."""
        mock_sel.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]
        self.store.define_shot("A01", 10, 50, ["ctrl"])
        mock_active.return_value = self.store
        mock_builder = MagicMock()
        mock_builder.assess.return_value = []
        mock_manifest_cls.return_value = mock_builder

        self.ctrl._first_shown = True
        self.ctrl.assess()

        mock_builder.assess.assert_called_once()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_assess_auto_mode_no_guard(self, mock_active, mock_manifest_cls, mock_auto):
        """assess() in auto mode runs without selected-keys guard."""
        self.store.detection_mode = "auto"
        self.store.define_shot("A01", 10, 50, ["ctrl"])
        mock_active.return_value = self.store
        mock_builder = MagicMock()
        mock_builder.assess.return_value = []
        mock_manifest_cls.return_value = mock_builder

        self.ctrl._first_shown = True
        self.ctrl.assess()

        mock_builder.assess.assert_called_once()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_assess_passes_skip_discovery_in_selected_keys_mode(
        self, mock_active, mock_manifest_cls, mock_sel
    ):
        """assess() must pass skip_scene_discovery=True when in selected-keys mode."""
        mock_sel.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]
        self.store.define_shot("A01", 10, 50, ["ctrl"])
        mock_active.return_value = self.store
        mock_builder = MagicMock()
        mock_builder.assess.return_value = []
        mock_manifest_cls.return_value = mock_builder

        self.ctrl._first_shown = True
        self.ctrl.assess()

        # Verify skip_scene_discovery was passed as True
        call_kwargs = mock_builder.assess.call_args
        self.assertTrue(
            (
                call_kwargs[1].get("skip_scene_discovery", False)
                if call_kwargs[1]
                else False
            ),
            "assess() must pass skip_scene_discovery=True in selected-keys mode",
        )

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_assess_no_skip_discovery_in_auto_mode(
        self, mock_active, mock_manifest_cls, mock_auto
    ):
        """assess() in auto mode should NOT skip scene discovery."""
        self.store.detection_mode = "auto"
        self.store.define_shot("A01", 10, 50, ["ctrl"])
        mock_active.return_value = self.store
        mock_builder = MagicMock()
        mock_builder.assess.return_value = []
        mock_manifest_cls.return_value = mock_builder

        self.ctrl._first_shown = True
        self.ctrl.assess()

        call_kwargs = mock_builder.assess.call_args
        skip = (
            call_kwargs[1].get("skip_scene_discovery", False)
            if call_kwargs[1]
            else False
        )
        self.assertFalse(skip)


# ---------------------------------------------------------------------------
# Tests: message_box only shown from detect(), not assess()/build()
# ---------------------------------------------------------------------------


class TestMessageBoxOnUserActions(unittest.TestCase, _ControllerHarness):
    """User-initiated actions (detect, assess, build) must show a message
    box popup when selected-keys mode is active and no keys are selected.

    Bug: Only detect() showed the message box; assess() and build() used
    footer-only feedback which was too subtle for user-initiated actions.
    Fixed: 2026-04-07 — added message_box to assess() and build() as well.
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.detection_mode = "skip_zero"
        self.store.detection_threshold = 5.0
        self.ctrl._csv_path = ""

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_detect_shows_message_box_when_no_keys(self, mock_active, mock_sel):
        """detect() in selected-keys mode with no keys must show a message box."""
        mock_active.return_value = self.store
        self.ctrl._first_shown = True
        self.ctrl.detect()
        self.ctrl.sb.message_box.assert_called_once()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_assess_shows_message_box_when_no_keys(
        self, mock_active, mock_manifest_cls, mock_sel
    ):
        """assess() in selected-keys mode with no keys must show a message box."""
        mock_active.return_value = self.store
        self.ctrl._first_shown = True
        self.ctrl.assess()
        self.ctrl.sb.message_box.assert_called_once()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_build_shows_message_box_when_no_keys(self, mock_active, mock_sel):
        """build() in selected-keys mode with no keys must show a message box."""
        mock_active.return_value = self.store
        self.ctrl._store = self.store
        self.ctrl._first_shown = True
        # Pre-load steps so _ensure_steps passes and build reaches its guard
        self.ctrl._steps = _make_steps("A01", "A02")
        self.ctrl.build()
        self.ctrl.sb.message_box.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: scene-change scriptJob (SceneOpened / NewSceneOpened)
# ---------------------------------------------------------------------------


class TestSceneChangeCallback(unittest.TestCase, _ControllerHarness):
    """_on_scene_changed must rebind the store listener and re-populate
    the table when the user opens or creates a new Maya scene.
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.ctrl._store = self.store
        self.ctrl._first_shown = True
        # Default to detection mode (CSV unchecked)
        self.ctrl.ui.chk_csv.isChecked.return_value = False

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_scene_change_re_detects_in_detection_mode(self, mock_active, mock_detect):
        """Opening a new scene (no CSV) must trigger detect() to refresh."""
        new_store = _fresh_store()
        mock_active.return_value = new_store
        mock_detect.return_value = [
            {"name": "Shot 1", "start": 1.0, "end": 50.0, "objects": ["ctrl"]},
        ]
        self.ctrl._csv_path = ""

        self.ctrl._on_scene_changed()

        mock_detect.assert_called_once()
        # Old cached store should be cleared
        self.assertIsNone(self.ctrl._store)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_scene_change_reloads_csv_when_csv_checked(self, mock_active, mock_detect):
        """Opening a new scene with CSV checked must reload the CSV."""
        new_store = _fresh_store()
        mock_active.return_value = new_store
        self.ctrl.ui.chk_csv.isChecked.return_value = True
        self.ctrl.ui.txt_csv_path.text.return_value = "/some/manifest.csv"

        with patch.object(self.ctrl, "_load_csv") as mock_load:
            self.ctrl._on_scene_changed()
            mock_load.assert_called_once_with("/some/manifest.csv")

        # detect should NOT have run (CSV takes priority)
        mock_detect.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_scene_change_skipped_before_first_show(self, mock_active, mock_detect):
        """Scene change before _on_first_show must not trigger detect.

        The widget isn't visible yet — avoid premature detection and
        message boxes during construction.
        """
        self.ctrl._first_shown = False

        self.ctrl._on_scene_changed()

        mock_detect.assert_not_called()

    def test_scene_change_rebinds_store_listener(self):
        """_on_scene_changed must unbind the old store and bind the new one."""
        old_store = self.store
        old_store.add_listener(self.ctrl._on_store_event)
        self.ctrl._bound_store = old_store
        self.ctrl._store_listener_bound = True

        new_store = _fresh_store()
        with (
            patch(
                "mayatk.anim_utils.shots._shots.ShotStore.active",
                return_value=new_store,
            ),
            patch(
                "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions",
                return_value=[],
            ),
        ):
            self.ctrl._on_scene_changed()

        # Should be bound to the new store
        self.assertIs(self.ctrl._bound_store, new_store)
        self.assertTrue(self.ctrl._store_listener_bound)

    def test_remove_callbacks_clears_scene_jobs(self):
        """remove_callbacks must reset scene job IDs."""
        self.ctrl._scene_opened_job = 999
        self.ctrl._new_scene_job = 998

        self.ctrl.remove_callbacks()

        # Without Maya, _remove_scene_jobs won't call cmds.scriptJob
        # but the IDs should be None-able for GC safety.
        self.assertFalse(self.ctrl._store_listener_bound)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
