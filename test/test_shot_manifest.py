# !/usr/bin/python
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
            content=f"Content for {name}",
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
# Tests: ShotManifest.update (baseline â€” no ranges)
# ---------------------------------------------------------------------------


class TestUpdateBaseline(unittest.TestCase):
    """Test update() without ranges â€” sequential cursor placement."""

    def setUp(self):
        self.store = _fresh_store()
        self.assembler = ShotManifest(self.store)
        self.steps = _make_steps("A01", "A02", "A03")

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_removes_shots_not_in_csv(self, mock_dur):
        mock_dur.return_value = 30.0
        self.assembler.update(self.steps)
        # Now rebuild with A01 and A03 only
        reduced = _make_steps("A01", "A03")
        actions = self.assembler.update(reduced)
        self.assertEqual(actions["A02"], "removed")
        names = {s.name for s in self.store.shots}
        self.assertNotIn("A02", names)

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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

        # Rebuild with ranges â€” should reposition
        ranges = {
            "A01": (500.0, 600.0),
            "A02": (700.0, 800.0),
            "A03": (900.0, 1000.0),
        }
        actions = self.assembler.update(self.steps, ranges=ranges)

        shots = {s.name: s for s in self.store.shots}
        self.assertEqual(shots["A01"].start, 500.0)
        self.assertEqual(shots["A01"].end, 600.0)
        # repositioned â†’ patched
        self.assertEqual(actions["A01"], "patched")

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_many_regions_pruned_to_step_count(self, mock_dur, mock_regions):
        """When more regions than steps, largest gaps become boundaries."""
        mock_dur.return_value = 30.0
        # 6 region starts (5 gaps) but only 3 steps.
        # Diffs: 5, 5, 90, 5, 95 â†’ top 2 are 95 (idx 4) and 90 (idx 2)
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_from_step_idx_freezes_prefix(self, mock_dur, mock_regions):
        """from_step_idx preserves earlier steps and re-resolves later ones."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = [
            {"name": "S", "start": 10.0, "end": 40.0, "objects": []},
            {"name": "S", "start": 100.0, "end": 130.0, "objects": []},
            {"name": "S", "start": 200.0, "end": 230.0, "objects": []},
        ]

        # First full resolve â€” gaps assigned to all 3 steps
        resolved_full = self.ctrl._resolve_ranges()
        self.assertEqual(resolved_full[0][1], 10.0)  # A01 at gap 1
        self.assertEqual(resolved_full[1][1], 100.0)  # A02 at gap 2
        self.assertEqual(resolved_full[2][1], 200.0)  # A03 at gap 3

        # Re-resolve from step 2 (A03): A01 and A02 should be frozen
        resolved_partial = self.ctrl._resolve_ranges(from_step_idx=2)
        self.assertEqual(resolved_partial[0], resolved_full[0])  # A01 frozen
        self.assertEqual(resolved_partial[1], resolved_full[1])  # A02 frozen
        # A03 re-resolves â€” gap 200 is past A02's end, so it uses it
        self.assertEqual(resolved_partial[2][1], 200.0)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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

        # Find A01 item — should have collision background
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
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
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
        self.assertEqual(steps[0].content, "")

    def test_zero_objects(self):
        """Candidate with no objects produces a step with empty objects list."""
        candidates = [{"name": "S1", "start": 0.0, "end": 10.0, "objects": []}]
        steps, ranges = BuilderStep.from_detection(candidates)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].objects, [])
        self.assertEqual(steps[0].content, "")
        self.assertEqual(ranges["S1"], (0.0, 10.0))


class TestRemoveMissing(unittest.TestCase):
    """Test update() with remove_missing=False."""

    def setUp(self):
        self.store = _fresh_store()

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration", return_value=30.0)
    def test_remove_missing_true_deletes_absent(self, mock_dur):
        """Default behavior: shots not in steps are removed."""
        builder = ShotManifest(self.store)
        steps = _make_steps("A01", "A02")
        builder.update(steps)
        self.assertEqual(len(self.store.shots), 2)

        # Now update with only A01 — A02 should be removed
        steps2 = _make_steps("A01")
        actions = builder.update(steps2, remove_missing=True)
        self.assertEqual(actions.get("A02"), "removed")
        self.assertEqual(len(self.store.shots), 1)

    @patch("mayatk.anim_utils.shots.behaviors.compute_duration", return_value=30.0)
    def test_remove_missing_false_preserves_absent(self, mock_dur):
        """Detection mode: existing shots not in steps are preserved."""
        builder = ShotManifest(self.store)
        steps = _make_steps("A01", "A02")
        builder.update(steps)
        self.assertEqual(len(self.store.shots), 2)

        # Now update with only A01 — A02 should be kept
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
        """Editing Description column in detection mode updates step.content."""
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
        self.assertEqual(self.ctrl._steps[0].content, "My description")

    def test_description_edit_csv_mode(self):
        """Editing Description column in CSV mode updates step.content."""
        # Steps from setup_controller (CSV mode: _csv_path is empty but
        # steps exist — simulate CSV mode by setting a path)
        self.ctrl._csv_path = "/some/file.csv"

        tree = self.ui.tbl_steps
        self.ctrl._populate_table()
        parent = tree.topLevelItem(0)

        tree.blockSignals(True)
        parent.setText(COL_DESC, "Updated description")
        tree.blockSignals(False)

        self.ctrl._on_item_changed(parent, COL_DESC)
        self.assertEqual(self.ctrl._steps[0].content, "Updated description")


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
        store.use_selected_keys = True
        store.key_filter_mode = "all"
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
        mock_active.return_value.use_selected_keys = True
        mock_active.return_value.key_filter_mode = "all"

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
        mock_store.use_selected_keys = True
        mock_store.key_filter_mode = "all"
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

        # Only 1 detected region → at most 1 step should have a range.
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
        mock_store.use_selected_keys = True
        mock_store.key_filter_mode = "all"
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
    """Verify use_selected_keys survives across scenes via QSettings.

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
    def test_fresh_store_restores_use_selected_keys_from_qsettings(self, mock_qs_cls):
        """active() must apply use_selected_keys from QSettings when no per-scene data."""
        mock_qs = MagicMock()
        mock_qs.value.side_effect = lambda key, *a: {
            "ShotStore/use_selected_keys": True,
            "ShotStore/key_filter_mode": "skip_zero",
        }.get(key)
        mock_qs_cls.return_value = mock_qs

        # No persistence backend → fresh default store
        store = ShotStore()
        store._restore_user_prefs()

        self.assertTrue(store.use_selected_keys)
        self.assertEqual(store.key_filter_mode, "skip_zero")

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_save_writes_prefs_to_qsettings(self, mock_qs_cls):
        """save() must persist use_selected_keys to QSettings."""
        mock_qs = MagicMock()
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store.use_selected_keys = True
        store.key_filter_mode = "zero_as_end"
        store._save_user_prefs()

        mock_qs.setValue.assert_any_call("ShotStore/use_selected_keys", True)
        mock_qs.setValue.assert_any_call("ShotStore/key_filter_mode", "zero_as_end")

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_qsettings_ignored_when_persistence_has_data(self, mock_qs_cls):
        """QSettings must NOT override values loaded from per-scene persistence."""
        # Simulate a scene with persisted data (use_selected_keys=False)
        mock_persistence = MagicMock()
        mock_persistence.load.return_value = {
            "shots": [],
            "use_selected_keys": False,
            "key_filter_mode": "all",
        }

        # QSettings says True — but per-scene persistence should win
        mock_qs = MagicMock()
        mock_qs.value.return_value = True
        mock_qs_cls.return_value = mock_qs

        ShotStore._persistence = mock_persistence
        store = ShotStore.active()

        self.assertFalse(store.use_selected_keys)

    @patch("mayatk.anim_utils.shots._shots.QSettings")
    def test_fresh_store_no_qsettings_data_uses_default(self, mock_qs_cls):
        """When QSettings has no saved value, default (False) is used."""
        mock_qs = MagicMock()
        mock_qs.value.return_value = None
        mock_qs_cls.return_value = mock_qs

        store = ShotStore()
        store._restore_user_prefs()

        self.assertFalse(store.use_selected_keys)
        self.assertEqual(store.key_filter_mode, "all")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
