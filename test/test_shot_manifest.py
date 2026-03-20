# !/usr/bin/python
# coding=utf-8
"""Tests for the Shot Manifest align-mode features.

Covers:
    - parse_csv and detect_behavior (existing)
    - detect_animation_gaps (Stage 4)
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
    detect_behavior,
    detect_animation_gaps,
    _motion_frames_for_curve,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_steps(*names, behavior="fade_in_out"):
    """Create a list of BuilderSteps with one object each."""
    steps = []
    for name in names:
        step = BuilderStep(
            step_id=name,
            section="A",
            section_title="Section A",
            content=f"Content for {name}",
        )
        step.objects.append(BuilderObject(name=f"obj_{name}", behavior=behavior))
        steps.append(step)
    return steps


def _fresh_store():
    """Create and activate a fresh ShotStore."""
    store = ShotStore()
    ShotStore._active = store
    return store


# ---------------------------------------------------------------------------
# Tests: detect_behavior
# ---------------------------------------------------------------------------


class TestDetectBehavior(unittest.TestCase):
    def test_fade_in(self):
        self.assertEqual(detect_behavior("Object fades in from black"), "fade_in")

    def test_fade_out(self):
        self.assertEqual(detect_behavior("Object fades out slowly"), "fade_out")

    def test_fade_in_out(self):
        self.assertEqual(detect_behavior("Fades in then fades out"), "fade_in_out")

    def test_no_behavior(self):
        self.assertEqual(detect_behavior("Object sits still"), "")


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
# Tests: detect_animation_gaps
# ---------------------------------------------------------------------------


class TestDetectAnimationGaps(unittest.TestCase):
    """Test gap detection from animation curves."""

    def _cmds_mock(self):
        """Return whichever mock is currently installed as maya.cmds."""
        return sys.modules["maya.cmds"]

    def test_no_anim_curves_returns_empty(self):
        self._cmds_mock().ls.return_value = []
        result = detect_animation_gaps(min_gap=2.0)
        self.assertEqual(result, [])

    def test_finds_gaps_between_keyframes(self):
        """Gaps larger than min_gap should produce animation region starts."""
        m = self._cmds_mock()
        m.ls.return_value = ["fake_curve"]
        # Keys at 1, 10, 50, 60: three gaps (â‰¥5f each) â†’ 4 animation regions
        m.keyframe.return_value = [1.0, 10.0, 50.0, 60.0]
        result = detect_animation_gaps(min_gap=5.0)
        self.assertEqual(result, [1.0, 10.0, 50.0, 60.0])

    def test_no_gap_when_keys_are_dense(self):
        m = self._cmds_mock()
        m.ls.return_value = ["fake_curve"]
        m.keyframe.return_value = [1.0, 2.0, 3.0, 4.0]
        result = detect_animation_gaps(min_gap=2.0)
        self.assertEqual(result, [])

    def test_ignore_flat_keys_reveals_hidden_gaps(self):
        """Baked flat keys that hide gaps should be filtered out."""
        m = self._cmds_mock()
        m.ls.return_value = ["baked_curve"]
        # Baked curve: keys every frame 1-20, but frames 5-15 are flat (value=1.0)
        # Real motion at 1-4 (values differ) and 16-20 (values differ)
        times = list(range(1, 21))
        values = [0.0, 0.3, 0.7, 1.0] + [1.0] * 12 + [1.0, 0.7, 0.3, 0.0]

        def mock_keyframe(crv, q=True, **kwargs):
            if kwargs.get("tc"):
                return list(times)
            if kwargs.get("vc"):
                return list(values)
            return list(times)

        m.keyframe.side_effect = mock_keyframe
        try:
            # Without filtering: dense keys â†’ no gap
            result = detect_animation_gaps(min_gap=2.0, ignore_flat_keys=False)
            self.assertEqual(result, [])
            # With motion-based filtering: only value-change frames kept â†’ gap revealed
            result = detect_animation_gaps(min_gap=2.0, ignore_flat_keys=True)
            self.assertTrue(len(result) > 0, "Should find gaps after motion filtering")
        finally:
            m.keyframe.side_effect = None
            m.keyframe.return_value = []


class TestMotionFramesForCurve(unittest.TestCase):
    """Test _motion_frames_for_curve motion-based frame detection."""

    def test_all_motion(self):
        """Every consecutive pair has a value change â†’ all frames returned."""
        times = [1.0, 2.0, 3.0, 4.0]
        values = [0.0, 1.0, 0.0, 1.0]
        result = _motion_frames_for_curve(times, values)
        self.assertEqual(result, times)

    def test_flat_interior_excluded(self):
        # values 0,1,1,1,0 â†’ motion at 1â†’2 and 4â†’5; frames 3 is inside flat region
        times = [1.0, 2.0, 3.0, 4.0, 5.0]
        values = [0.0, 1.0, 1.0, 1.0, 0.0]
        result = _motion_frames_for_curve(times, values)
        self.assertEqual(result, [1.0, 2.0, 4.0, 5.0])

    def test_all_flat_returns_empty(self):
        """Fully static curve â†’ no motion frames."""
        times = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        values = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        result = _motion_frames_for_curve(times, values)
        self.assertEqual(result, [])

    def test_fewer_than_two_keys_returns_input(self):
        times = [1.0]
        values = [0.0]
        result = _motion_frames_for_curve(times, values)
        self.assertEqual(result, times)

    def test_large_baked_segment(self):
        """100 flat keys between two motion regions â†’ only motion frames."""
        motion_a = [(float(i), float(i) * 0.5) for i in range(1, 6)]
        flat = [(float(i), 2.5) for i in range(6, 106)]  # 100 flat keys
        motion_b = [(float(i), (i - 105) * 0.3) for i in range(106, 111)]
        all_data = motion_a + flat + motion_b
        times = [t for t, _ in all_data]
        values = [v for _, v in all_data]
        result = _motion_frames_for_curve(times, values)
        # No flat-region frames should appear (except boundaries shared with motion)
        motion_times = {1.0, 2.0, 3.0, 4.0, 5.0, 106.0, 107.0, 108.0, 109.0, 110.0}
        # boundary: 5â†’6 is motion (2.5â†’2.5? no, 5*0.5=2.5 and flat=2.5, same value)
        # Actually 5.0*0.5=2.5 and flat region value=2.5, so 5â†’6 is NOT motion.
        # motion_a last key value=5*0.5=2.5, flat value=2.5 â†’ no transition
        # flat last key value=2.5, motion_b first=(106, 0.3) â†’ that IS motion
        # So motion_a: 1â†’2 (0â†’1), 2â†’3 (1â†’1.5), 3â†’4 (1.5â†’2), 4â†’5 (2â†’2.5)
        # motion_b: 105â†’106 (2.5â†’0.3), 106â†’107 (0.3â†’0.6), 107â†’108, 108â†’109, 109â†’110
        # frame 105 is flat[99] = time 105.0, value 2.5; frame 106 = (106, 0.3)
        for t in [1.0, 2.0, 3.0, 4.0, 5.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0]:
            self.assertIn(t, result, f"Motion frame at {t} should be present")
        # Interior flat frames (e.g., 50.0) should NOT be present
        self.assertNotIn(50.0, result)


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
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_user_pin_overrides_auto_fill(self, mock_dur, mock_gaps):
        mock_dur.return_value = 30.0
        mock_gaps.return_value = []

        self.ctrl._user_ranges["A02"] = (200.0, None)

        resolved = self.ctrl._resolve_ranges()
        # A02 should start at 200
        a02 = next(r for r in resolved if r[0] == "A02")
        self.assertEqual(a02[1], 200.0)
        self.assertTrue(a02[3])  # is_user

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_gap_detection_used_for_auto_fill(self, mock_dur, mock_gaps):
        mock_dur.return_value = 30.0
        mock_gaps.return_value = [50.0, 150.0, 250.0]

        resolved = self.ctrl._resolve_ranges()
        # Steps should use region-start positions
        self.assertEqual(resolved[0][1], 50.0)  # A01 at region 1
        self.assertEqual(resolved[1][1], 150.0)  # A02 at region 2
        self.assertEqual(resolved[2][1], 250.0)  # A03 at region 3
        # None of these are user-entered
        for r in resolved:
            self.assertFalse(r[3])

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_many_regions_pruned_to_step_count(self, mock_dur, mock_gaps):
        """When more regions than steps, largest gaps become boundaries."""
        mock_dur.return_value = 30.0
        # 6 region starts (5 gaps) but only 3 steps.
        # Diffs: 5, 5, 90, 5, 95 â†’ top 2 are 95 (idx 4) and 90 (idx 2)
        # Selected regions: [0, 100, 200]
        mock_gaps.return_value = [0.0, 5.0, 10.0, 100.0, 105.0, 200.0]

        resolved = self.ctrl._resolve_ranges()
        self.assertEqual(resolved[0][1], 0.0)  # A01
        self.assertEqual(resolved[1][1], 100.0)  # A02
        self.assertEqual(resolved[2][1], 200.0)  # A03

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_end_derived_from_next_start(self, mock_dur, mock_gaps):
        """End of step N = start of step N+1 minus gap."""
        mock_dur.return_value = 30.0
        mock_gaps.return_value = []
        store = ShotStore.active()
        store.gap = 5.0

        resolved = self.ctrl._resolve_ranges()
        # A01 end should be A02.start - gap
        self.assertAlmostEqual(resolved[0][2], resolved[1][1] - 5.0, places=1)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_from_step_idx_freezes_prefix(self, mock_dur, mock_gaps):
        """from_step_idx preserves earlier steps and re-resolves later ones."""
        mock_dur.return_value = 30.0
        mock_gaps.return_value = [10.0, 100.0, 200.0]

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
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_set_start_cascades_subsequent(self, mock_dur, mock_gaps):
        """Setting a user pin clears subsequent user ranges so they re-flow."""
        mock_dur.return_value = 30.0
        mock_gaps.return_value = []

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
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_no_collision_when_ranges_are_ordered(self, mock_dur, mock_gaps):
        mock_dur.return_value = 30.0
        mock_gaps.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 30.0)
        self.ctrl._user_ranges["A02"] = (31.0, 60.0)
        self.ctrl._user_ranges["A03"] = (61.0, 90.0)

        # Populate tree so validation has items
        self.ctrl._populate_table()
        count = self.ctrl._validate_range_collisions()
        self.assertEqual(count, 0)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots.detect_animation_gaps"
    )
    @patch("mayatk.anim_utils.shots.behaviors.compute_duration")
    def test_collision_detected_when_ranges_overlap(self, mock_dur, mock_gaps):
        """Overlapping ranges should be flagged as collisions.

        Bug context: Without collision detection, overlapping user ranges
        would silently create overlapping shots in the store.
        """
        mock_dur.return_value = 30.0
        mock_gaps.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 50.0)
        self.ctrl._user_ranges["A02"] = (40.0, 70.0)  # overlaps A01
        self.ctrl._user_ranges["A03"] = (80.0, 100.0)

        self.ctrl._populate_table()
        count = self.ctrl._validate_range_collisions()
        self.assertGreater(count, 0)


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
                text = item.text(self.ctrl._COL_RANGE)
                self.assertIn("200", text)
                self.assertIn("300", text)
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


class TestParseAndStoreRange(unittest.TestCase, _ControllerHarness):
    """Test _parse_and_store_range() parsing logic."""

    def setUp(self):
        self.setup_controller()

    def test_start_only(self):
        self.ctrl._parse_and_store_range("A01", "120")
        self.assertEqual(self.ctrl._user_ranges["A01"], (120.0, None))

    def test_start_end_with_en_dash(self):
        self.ctrl._parse_and_store_range("A01", "120\u2013250")
        self.assertEqual(self.ctrl._user_ranges["A01"], (120.0, 250.0))

    def test_start_end_with_hyphen(self):
        self.ctrl._parse_and_store_range("A01", "120-250")
        self.assertEqual(self.ctrl._user_ranges["A01"], (120.0, 250.0))

    def test_whitespace_trimmed(self):
        self.ctrl._parse_and_store_range("A01", " 100 - 200 ")
        self.assertEqual(self.ctrl._user_ranges["A01"], (100.0, 200.0))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
