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

import json
import os
import sys
import tempfile
import types
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
from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore
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


def _mock_undo_info(test_case):
    """Neutralize undo bracketing during a controller ``build()`` WITHOUT
    leaking the mock.

    Under mayapy, ``maya.cmds`` is the real shared module — a bare
    ``_cmds.undoInfo = MagicMock()`` (the old pattern here) permanently
    replaced the real function for every test module that runs later in the
    suite: their undo chunks silently stopped opening and ``cmds.undo()``
    reverted only single commands (test_skinning's undo test failed exactly
    this way under ``--all``). ``patch.object`` restores the original (or
    removes the attribute if it was genuinely missing) when the test ends.
    """
    import maya.cmds as _cmds

    patcher = patch.object(_cmds, "undoInfo", MagicMock(), create=True)
    patcher.start()
    test_case.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# Tests: detect_behaviors
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests: ShotManifest.update (baseline - no ranges)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests: ShotManifest.update with ranges (Stage 6)
# ---------------------------------------------------------------------------


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
from qtpy import QtCore, QtWidgets
from uitk.managers.settings_manager import SettingsManager

_app = QtWidgets.QApplication.instance()
if _app is None:
    _app = QtWidgets.QApplication(sys.argv)


from mayatk.anim_utils.shots.shot_manifest.shot_manifest_slots import (
    ShotManifestController,
)
from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
    COL_STEP,
    COL_DESC,
    COL_START,
    COL_END,
)


class _ControllerHarness:
    """Creates a ShotManifestController with a minimal fake UI."""

    def setup_controller(self):
        """Build a controller with a mocked switchboard and tree widget."""
        from uitk.widgets.treeWidget import TreeWidget

        self.tree = TreeWidget()
        self.tree.setColumnCount(5)

        # Minimal slots_instance mock (controller reads .sb and .ui)
        slots_instance = MagicMock()
        slots_instance.ui.tbl_steps = self.tree
        slots_instance.ui.footer = MagicMock()
        slots_instance.ui.footer._status_label = MagicMock()
        slots_instance.ui.btn_build = MagicMock()
        slots_instance.sb.get_setting.return_value = None
        # Wired as production wires it -- a field's ``ui`` is its panel, the
        # panel's ``sb`` the switchboard -- answering a standalone host's ""
        # suffix, so the recent-paths store keys on a real name rather than on
        # a MagicMock's repr (TestHarnessPersistsUnderRealKeys).
        slots_instance.sb._host_suffix.return_value = ""
        slots_instance.ui.sb = slots_instance.sb
        slots_instance.ui.txt_csv_path.ui = slots_instance.ui
        # An empty source field: the scene's own shots are the source.
        slots_instance.ui.txt_csv_path.text.return_value = ""

        # Create the controller
        self.ctrl = ShotManifestController(slots_instance)
        self.ctrl.logger = MagicMock()

        # Convenience references
        self.sb = self.ctrl.sb
        self.ui = self.ctrl.ui

        # Load steps
        self.steps = _make_steps("A01", "A02", "A03")
        self.ctrl._steps = self.steps
        # The store's invalidation registry is class-level: a controller left
        # in it answers every later test's scene swap.
        self.addCleanup(self.ctrl.remove_callbacks)


class TestHarnessPersistsUnderRealKeys(unittest.TestCase, _ControllerHarness):
    """Nothing a harness-built controller persists is keyed on a MagicMock.

    The recent-CSV-paths store keys on its ``settings_key`` plus the host
    suffix the field's switchboard answers (``field.ui.sb._host_suffix()``),
    and a bare MagicMock answered with another MagicMock: every controller
    built here persisted under ``RecentValues/<MagicMock name=...>``, a new key
    per test (2,995 of them in a developer's live store, 2026-10-04). The
    sandbox now keeps them off that store, but a key named after a mock is a
    store nothing will ever read again, in a sandbox or out of it.
    """

    def test_recent_csv_paths_persist_under_their_settings_key(self):
        self.setup_controller()

        settings = self.ctrl._recent_csv_option.store._settings
        self.assertEqual(settings.namespace, "shot_manifest_csv_paths")


class TestResolveRanges(unittest.TestCase, _ControllerHarness):
    """Test _resolve_ranges() merging user pins with auto-fill."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_many_regions_pruned_to_step_count(self, mock_dur, mock_regions):
        """When more regions than steps, largest gaps become boundaries."""
        mock_dur.return_value = 30.0
        # 6 region starts (5 gaps) but only 3 steps.
        # Diffs: 5, 5, 90, 5, 95 -> top 2 are 95 (idx 4) and 90 (idx 2)
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_from_step_idx_freezes_prefix(self, mock_dur, mock_regions):
        """from_step_idx preserves earlier steps and re-resolves later ones."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = [
            {"name": "S", "start": 10.0, "end": 40.0, "objects": []},
            {"name": "S", "start": 100.0, "end": 130.0, "objects": []},
            {"name": "S", "start": 200.0, "end": 230.0, "objects": []},
        ]

        # First full resolve - gaps assigned to all 3 steps
        resolved_full = self.ctrl._resolve_ranges()
        self.assertEqual(resolved_full[0][1], 10.0)  # A01 at gap 1
        self.assertEqual(resolved_full[1][1], 100.0)  # A02 at gap 2
        self.assertEqual(resolved_full[2][1], 200.0)  # A03 at gap 3

        # Re-resolve from step 2 (A03): A01 and A02 should be frozen
        resolved_partial = self.ctrl._resolve_ranges(from_step_idx=2)
        self.assertEqual(resolved_partial[0], resolved_full[0])  # A01 frozen
        self.assertEqual(resolved_partial[1], resolved_full[1])  # A02 frozen
        # A03 re-resolves - gap 200 is past A02's end, so it uses it
        self.assertEqual(resolved_partial[2][1], 200.0)

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_set_start_cascades_subsequent(self, mock_dur, mock_regions):
        """Setting a user pin clears subsequent user ranges so they re-flow."""
        mock_dur.return_value = 30.0
        mock_regions.return_value = []
        # Contiguous, whatever a new store's gap defaults to (10 since
        # 2026-10-11): the cascade is under test, not the spacing.
        ShotStore.active().gap = 0.0

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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
    def test_a_collision_is_a_row_status_not_a_tint(self, mock_dur, mock_regions):
        """Both rows of an overlap say so in their row tooltip; the range
        cells stay untinted (2026-10-12: the colour coding "overlaps and is
        confusing")."""
        from qtpy.QtCore import Qt

        mock_dur.return_value = 30.0
        mock_regions.return_value = []

        self.ctrl._user_ranges["A01"] = (1.0, 50.0)
        self.ctrl._user_ranges["A02"] = (40.0, 70.0)  # overlaps A01
        self.ctrl._user_ranges["A03"] = (80.0, 100.0)

        self.ctrl._populate_table()
        self.ctrl._validate_range_collisions()

        tips = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            step = item.data(0, Qt.UserRole)
            tips[step.step_id] = self.tree.row_tooltip(item) or ""
            self.assertEqual(item.background(COL_START).style(), Qt.NoBrush)
        self.assertIn("overlaps 'A02'", tips["A01"])
        self.assertIn("overlaps 'A01'", tips["A02"])
        self.assertEqual(tips["A03"], "")


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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.behaviors.Behaviors.compute_duration")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    def test_all_ranges_complete_false_for_csv(self, mock_regions):
        """_all_ranges_complete() returns False when CSV steps lack user ranges."""
        mock_regions.return_value = []
        # Steps from setup_controller have no user_ranges
        self.ctrl._user_ranges.clear()
        self.assertFalse(self.ctrl._all_ranges_complete())


class TestLocalEditsCells(unittest.TestCase, _ControllerHarness):
    """Step names, descriptions and object names edit through Local Edits
    (``ManifestEditor``): always in force, recorded against the sheet's IDs,
    kept on the scene's store, never written into the loaded steps; Edits >
    Compare to Original Sheet shows the sheet as delivered, read-only, Build
    off."""

    SHEET = "/some/file.csv"

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()

    def _load(self, on=True):
        """Load the sheet; *on* False views the original sheet."""
        self.ctrl._load_data(_make_steps("A01", "A02", "A03"), csv_path=self.SHEET)
        self.ctrl._editor.view_original(not on)
        for _ in range(3):
            QtWidgets.QApplication.processEvents()

    def _commit(self, item, column, text):
        """Open the cell the way a double-click does, type, commit."""
        self.assertTrue(self.ctrl._editor.begin_edit(item, column))
        item.setText(column, text)  # what the editor's commit writes
        for _ in range(3):
            QtWidgets.QApplication.processEvents()

    def test_edits_are_in_force_by_default(self):
        """2026-10-10 ("the general idea is sound"): the edits are the
        manifest's own revision -- always applied and editable -- where an
        option, off by default, used to gate both."""
        self._load()
        self.assertTrue(
            self.ctrl._editor.begin_edit(self.tree.topLevelItem(0), COL_STEP)
        )
        self.assertIsNotNone(self.ctrl._editor._pending)

    def test_the_retired_option_leaves_the_users_settings(self):
        key = f"{self.ctrl._settings.namespace}/local_edits"
        qs = self.ctrl._settings.settings
        qs.setValue(key, True)
        self.addCleanup(qs.remove, key)
        self.setup_controller()
        self.assertFalse(qs.contains(key))

    def test_viewing_the_original_sheet_edits_nothing(self):
        self._load(on=False)
        parent = self.tree.topLevelItem(0)
        for col in (COL_STEP, COL_DESC):
            self.assertTrue(self.ctrl._editor.begin_edit(parent, col))
            self.assertIsNone(self.ctrl._editor._pending, "no editor opened")
        self.assertIn("another Edits mode", self.ui.footer.setText.call_args[0][0])

    def test_an_edit_runs_the_assessment_again_and_a_rename_wants_a_build(self):
        """Bug: a Local Edit left the last Assess up and marked Build due
        whatever it said, so every hide meant pressing Assess again ("once I
        hide shots, I have to re-assess every time"); and an Assess after a
        built step's rename turned Build off -- Assess never saw a rename, so
        it was never built.
        Fixed: 2026-10-11"""
        import pythontk as ptk

        self._load()
        ptk.ShotManifest(self.store).update(self.ctrl._steps, initial_shot_length=50)
        self.ctrl._redraw()
        self.ctrl.assess(skip_key_check=True)
        # Built; their missing objects are an artist's to add, not Build's.
        self.ui.b003.setEnabled.assert_called_with(False)

        def soon(note=""):  # the timer's work, now
            self.ctrl._reassess_note = note
            self.ctrl._reassess()

        with patch.object(self.ctrl, "_reassess_soon", side_effect=soon) as reassess:
            self._commit(self.tree.topLevelItem(0), COL_STEP, "Renamed")
        reassess.assert_called_once()
        footer = self.ui.footer.setText.call_args[0][0]
        self.assertTrue(footer.startswith("Renamed 'A01' to 'Renamed'"), footer)
        self.assertIn("Assessment:", footer)
        self.assertEqual(self.ctrl._last_results[0].updates, ["name"])
        self.ui.b003.setEnabled.assert_called_with(True)

    def test_the_original_view_shows_the_sheet_turns_build_off_and_loses_nothing(self):
        self._load()
        self._commit(self.tree.topLevelItem(0), COL_STEP, "Renamed")
        self.ctrl._editor.view_original(True)
        self.assertEqual(self.tree.topLevelItem(0).text(COL_STEP), "A01")
        self.ui.b003.setEnabled.assert_called_with(False)
        with patch.object(self.ctrl, "_ensure_steps") as ensure:
            self.ctrl.build()
        ensure.assert_not_called()  # refused before anything ran
        self.ctrl._editor.view_original(False)
        self.assertEqual(self.tree.topLevelItem(0).text(COL_STEP), "Renamed")
        self.assertEqual(
            self.store.manifest_edits[self.SHEET]["steps"]["A01"], {"name": "Renamed"}
        )

    def test_a_moved_sheet_keeps_its_edits_and_the_next_edit_re_keys_them(self):
        """Bug: the edit layer was found by the sheet's exact path, so a sheet
        moved or opened through another mount silently lost its edits
        (SOL_REPLACE_ASSEMBLY keeps its 5 name fixes under a personal Dropbox
        folder's path).
        Fixed: 2026-10-11 -- the path, else the one layer of that file name
        (``ManifestEdits.layer_key``); a save re-keys it to the new path."""
        self._load()
        self._commit(self.tree.topLevelItem(0), COL_STEP, "Renamed")
        moved = "D:/moved/elsewhere/file.csv"
        self.ctrl._load_data(_make_steps("A01", "A02", "A03"), csv_path=moved)
        for _ in range(3):
            QtWidgets.QApplication.processEvents()
        self.assertEqual(self.tree.topLevelItem(0).text(COL_STEP), "Renamed")
        self._commit(self.tree.topLevelItem(1), COL_DESC, "Moved on")
        self.assertEqual(list(self.store.manifest_edits), [moved])
        self.assertEqual(
            self.store.manifest_edits[moved]["steps"]["A01"], {"name": "Renamed"}
        )

    def test_description_edit_is_a_local_edit(self):
        self._load()
        self._commit(self.tree.topLevelItem(0), COL_DESC, "Updated description")
        self.assertEqual(self.ctrl._steps[0].description, "Updated description")
        self.assertEqual(self.ctrl._sheet_steps[0].description, "Content for A01")
        record = self.store.manifest_edits[self.SHEET]["steps"]["A01"]
        self.assertEqual(record, {"description": "Updated description"})

    def test_rename_keeps_the_step_id_and_its_typed_range(self):
        """Bug (superseded): a rename rewrote ``step_id`` -- the step's shot
        binding -- so a renamed built step built a second shot.
        Fixed: 2026-10-06"""
        self._load()
        self.ctrl._user_ranges["A01"] = (10.0, 50.0)
        self._commit(self.tree.topLevelItem(0), COL_STEP, "Renamed")
        step = self.ctrl._steps[0]
        self.assertEqual((step.step_id, step.shot_name), ("A01", "Renamed"))
        self.assertEqual(self.ctrl._user_ranges["A01"], (10.0, 50.0))
        self.assertEqual(self.tree.topLevelItem(0).text(COL_STEP), "Renamed")

    def test_empty_or_taken_names_are_refused(self):
        self._load()
        for name in ("   ", "a02"):
            self._commit(self.tree.topLevelItem(0), COL_STEP, name)
            self.assertEqual(self.ctrl._steps[0].shot_name, "A01")
        self.assertNotIn(self.SHEET, self.store.manifest_edits)

    def test_a_closed_behavior_menu_is_deleted(self):
        """Bug: each click on an object's Behaviors cell parented a new menu
        to the cell's label and never deleted it -- one hidden menu kept per
        click until the table was next drawn.
        Fixed: 2026-10-10"""
        from qtpy.QtCore import QCoreApplication, QEvent
        from uitk.widgets.menu import Menu
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            COL_BEHAVIORS,
        )

        self._load()
        label = self.tree.itemWidget(self.tree.topLevelItem(0).child(0), COL_BEHAVIORS)
        for _ in range(2):
            label.clicked.emit()  # opens the menu; nothing ticked
            menus = label.findChildren(Menu)
            self.assertEqual(len(menus), 1)
            menus[0].hide()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(label.findChildren(Menu), [])


class TestTheSheetsOptionsLiveOnTheScene(unittest.TestCase, _ControllerHarness):
    """Bug: a sheet's mapping-template options were kept per user, so a scene
    reopened on its sheet read it another way than it was built
    (SOL_REPLACE_ASSEMBLY, built with Object Case = upper, read As written:
    every built object missing, all 5 Local Edits stale).
    Fixed: 2026-10-12 (maintainer) -- per sheet, on the scene, beside its
    Local Edits; the user's own options only seed a sheet the scene never read."""

    CSV = "Step,Step Contents,Asset Names\nA01.),Red Door fades in,\n"

    def setUp(self):
        import json
        import maya.cmds as cmds
        import pythontk as ptk

        cmds.file(new=True, force=True)
        cmds.polyCube(name="RED_DOOR")
        self.setup_controller()
        self.store = _fresh_store()
        p = patch(
            "mayatk.anim_utils.shots._shots.ShotStore.active", return_value=self.store
        )
        p.start()
        self.addCleanup(p.stop)
        tmp = ptk.TempArtifacts("mtk_manifest_sheet_options", policy="scoped")
        self.addCleanup(tmp.cleanup)
        self.csv = os.path.join(tmp.dir_path(), "sheet.csv")
        with open(self.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.CSV)
        key = "mapping_options/default"
        self.addCleanup(self.ctrl._settings.setValue, key, "")
        self._user = lambda values: self.ctrl._settings.setValue(
            key, json.dumps(values)
        )
        _mock_undo_info(self)

    def _open(self):
        """A panel opening on the scene: a fresh controller, the default
        template, the scene's sheet loaded as ``_open_scene_source`` loads it."""
        self.setup_controller()
        self.ctrl._apply_mapping("default", persist=False)
        self.assertTrue(self.ctrl._load_csv(self.store.source_csv or self.csv))
        return [o.name for o in self.ctrl._steps[0].objects]

    def test_a_reopened_scene_reads_its_sheet_as_it_was_built(self):
        self._user({"object_case": "upper"})
        self.assertEqual(self._open(), ["RED_DOOR"])
        self.ctrl.build()
        self.assertEqual(
            self.store.manifest_options[self.csv]["values"]["object_case"], "upper"
        )
        self._user({})  # the user's own options since: As written
        self.assertEqual(self._open(), ["RED_DOOR"], "read as it was built")
        self.ctrl.assess(skip_key_check=True)
        found = {o.name: o.exists for r in self.ctrl._last_results for o in r.objects}
        self.assertEqual(found, {"RED_DOOR": True})

    def test_a_sheet_the_scene_never_read_takes_the_users_options(self):
        self._user({"object_case": "upper"})
        self.assertEqual(self._open(), ["RED_DOOR"])
        self.assertEqual(self.store.manifest_options, {}, "reading writes nothing")
        self._user({})
        self.assertEqual(self._open(), ["Red_Door"])

    def test_an_option_changed_for_the_sheet_is_the_scenes(self):
        self._open()
        self.ctrl._on_option_changed("object_case", "upper")
        self.assertEqual(
            self.store.manifest_options[self.csv]["values"]["object_case"], "upper"
        )
        self._user({})
        self.assertEqual(self._open(), ["RED_DOOR"])

    def _other_sheet(self, text):
        """A second sheet beside the first, browsed to as the user would."""
        path = os.path.join(os.path.dirname(self.csv), "other.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        self.ctrl.ui.txt_csv_path.text.return_value = path  # the field holds it
        return path

    def test_a_sheet_the_scene_never_read_takes_the_users_template(self):
        """Bug: a template switched in for one sheet's record stayed current,
        so the next sheet -- one the scene had never read -- was read with it,
        not with the user's own.
        Fixed: 2026-10-12"""
        import shutil
        import pythontk as ptk
        from pythontk.core_utils.engines.shots.manifest import mapping

        tmp = ptk.TempArtifacts("mtk_manifest_templates", policy="scoped")
        self.addCleanup(tmp.cleanup)
        folder = tmp.dir_path()
        default = os.path.join(os.path.dirname(mapping.__file__), "default.json")
        for name in ("mine", "theirs"):
            shutil.copy(default, os.path.join(folder, f"{name}.json"))
        self.ctrl._mapping_dir = folder
        self.ctrl._apply_mapping("mine", persist=False)
        ptk.ManifestEdits.set_sheet_options(self.store, self.csv, "theirs", {})
        self.assertTrue(self.ctrl._load_csv(self.csv))
        self.assertEqual(self.ctrl._mapping_name, "theirs", "precondition: its record")

        self.assertTrue(self.ctrl._load_csv(self._other_sheet(self.CSV)))
        self.assertEqual(self.ctrl._mapping_name, "mine")

    def test_a_sheet_that_fails_to_load_leaves_the_shown_sheets_record(self):
        """Bug: a sheet that read no step was refused, but its own options
        stayed adopted, so a Build then recorded them on the sheet still
        shown (Object Case upper -> As written).
        Fixed: 2026-10-12"""
        self._user({"object_case": "upper"})
        self._open()
        self.ctrl.build()
        self._user({})  # the user's own since: As written
        other = self._other_sheet("Step,Step Contents,Asset Names\n")
        self.assertFalse(self.ctrl._load_csv(other), "precondition: no step read")
        self.ctrl.build()
        self.assertEqual(
            self.store.manifest_options[self.csv]["values"].get("object_case"),
            "upper",
        )

    def test_an_option_set_after_a_sheet_read_no_step_reads_that_sheet(self):
        """Bug: the field said to set the sheet's Step IDs, but the option
        change read the sheet shown before again and recorded it there.
        Fixed: 2026-10-12"""
        self._user({"step_ids": "classic"})  # reads the A01.) sheet shown
        self._open()
        other = self._other_sheet(
            "Step,Step Contents,Asset Names\nIN_01,Red Door fades in,\n"
        )
        self.assertFalse(self.ctrl._load_csv(other), "precondition: no step read")
        self.ctrl._on_option_changed("step_ids", "numbered")
        self.assertEqual(self.ctrl._csv_path, other)
        self.assertEqual([s.step_id for s in self.ctrl._steps], ["IN_01"])
        self.assertNotIn(self.csv, self.store.manifest_options, "not that sheet's")


class TestRowTags(unittest.TestCase, _ControllerHarness):
    """Colour tags on the step rows, picked from the real context menu: the
    user's kept on the scene's store under the sheet (never a Local Edit),
    the template's ``color_by`` shown under them."""

    SHEET = "/some/tagged.csv"

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.tags = self.ctrl._row_tags

    def _load(self, steps=None):
        steps = steps or _make_steps("A01", "A02", "A03")
        self.ctrl._load_data(steps, csv_path=self.SHEET)
        return [self.tree.topLevelItem(i) for i in range(len(steps))]

    def _pick(self, item, swatch):
        """Right-click step row *item*, click swatch *swatch* (0: clear)."""
        from qtpy.QtCore import Qt
        from qtpy.QtTest import QTest
        from qtpy.QtWidgets import QMenu, QWidgetAction

        def chosen(menu, *_args):
            row = next(
                a.defaultWidget()
                for a in menu.actions()
                if isinstance(a, QWidgetAction)
            )
            QTest.mouseClick(row.swatches[swatch], Qt.LeftButton)
            return None

        with patch.object(self.tree, "itemAt", return_value=item):
            with patch.object(QMenu, "exec_", chosen):
                self.ctrl._show_item_menu(self.tree.visualItemRect(item).center())

    def _shown(self, items):
        return [self.tags.shown_tag(item) for item in items]

    def test_a_pick_tags_the_selected_steps_and_survives_a_reload(self):
        a01, a02, a03 = self._load()
        self.tree.clearSelection()
        a01.setSelected(True)
        a03.setSelected(True)
        self._pick(a01, 3)
        self.assertEqual(
            self.store.manifest_tags, {self.SHEET: {"A01": "tag3", "A03": "tag3"}}
        )
        self.assertNotIn(self.SHEET, self.store.manifest_edits, "not a Local Edit")
        self.assertEqual(self._shown(self._load()), ["tag3", None, "tag3"])

    def test_an_unselected_step_is_tagged_alone(self):
        a01, a02, _a03 = self._load()
        self.tree.clearSelection()
        a01.setSelected(True)
        self._pick(a02, 1)
        self.assertEqual(self.store.manifest_tags[self.SHEET], {"A02": "tag1"})

    def test_a_closed_item_menu_is_not_kept(self):
        """Bug: every right-click parented a fresh ``QMenu`` to the tree and
        never deleted it, so each menu (swatch row and all) lived as long as
        the panel.
        Fixed: 2026-10-09"""
        from qtpy.QtCore import QCoreApplication, QEvent, Qt
        from qtpy.QtWidgets import QMenu

        a01, _a02, _a03 = self._load()
        for swatch in (1, 2, 0):
            self._pick(a01, swatch)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(
            self.tree.findChildren(QMenu, "", Qt.FindDirectChildrenOnly), []
        )

    def test_object_rows_show_their_steps_colour(self):
        a01, _a02, _a03 = self._load()
        self._pick(a01, 4)
        self.assertEqual(self.tags.shown_tag(a01.child(0)), "tag4")

    def test_color_by_section_shows_under_the_users_pick(self):
        """The template's ``color_by``: each section its own colour -- the
        next section a far hue, never the neighbouring one (tag4 after tag1:
        ``Palette.contrast_order``); a pick shows over it, and clearing the
        pick shows the section's again."""
        steps = _make_steps("A01", "A02", "B01")
        steps[2].section = "B"
        self.ctrl._active_mapping = {"color_by": "section"}
        a01, a02, b01 = self._load(steps)
        self.assertEqual(self._shown((a01, a02, b01)), ["tag1", "tag1", "tag4"])
        self._pick(a02, 5)
        self.assertEqual(self.tags.shown_tag(a02), "tag5")
        self._pick(a02, 0)
        self.assertEqual(self.tags.shown_tag(a02), "tag1")
        self.assertNotIn(self.SHEET, self.store.manifest_tags)


class TestBuildDetectionMode(unittest.TestCase, _ControllerHarness):
    """Test that build() in detection mode short-circuits and passes remove_missing=False."""

    def setUp(self):
        self.setup_controller()
        _fresh_store()
        self.ui.spn_gap = MagicMock(value=MagicMock(return_value=5.0))
        self.ui.txt_csv_path = MagicMock()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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

        _mock_undo_info(self)

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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
        return_value=[],
    )
    def test_resolve_ranges_returns_empty_when_no_selected_keys(self, _mock_sel):
        """_resolve_ranges must return [] when use_selected_keys is on and no keys found."""
        self.ctrl._cached_gaps = None
        resolved = self.ctrl._resolve_ranges()
        self.assertEqual(resolved, [])

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_build_aborts_when_no_selected_keys(
        self, mock_active, mock_manifest_cls, _mock_sel
    ):
        """build() must not call sync() when use_selected_keys yields no regions."""
        mock_active.return_value = _fresh_store()
        mock_active.return_value.detection_mode = "all"

        mock_builder = MagicMock()
        mock_manifest_cls.return_value = mock_builder

        _mock_undo_info(self)

        self.ctrl._cached_gaps = None
        self.ctrl.build()

        mock_builder.sync.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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

        _mock_undo_info(self)

        self.ctrl._cached_gaps = None
        self.ctrl.build()

        mock_builder.sync.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
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

        # Only 1 detected region -> at most 1 step should have a range.
        self.assertEqual(len(resolved), 1, "Extra steps received fallback ranges")
        self.assertEqual(resolved[0][0], "A01")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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

        _mock_undo_info(self)

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


class TestMappingCombo(unittest.TestCase, _ControllerHarness):
    """Integration tests for JSON mapping combo on the controller."""

    def setUp(self):
        self.setup_controller()

    def test_mapping_combo_exists(self):
        """Controller has a _cmb_mapping combo after init."""
        self.assertTrue(hasattr(self.ctrl, "_cmb_mapping"))
        self.assertIsNotNone(self.ctrl._cmb_mapping)

    def test_active_mapping_defaults_to_none(self):
        """No mapping is active by default."""
        self.assertIsNone(self.ctrl._active_mapping)

    def test_on_mapping_changed_sets_active(self):
        """Selecting a mapping name loads the JSON dict."""
        import os
        import tempfile
        import shutil
        import json

        tmp = tempfile.mkdtemp()
        try:
            data = {"columns": {"step_id": ["ID"]}}
            path = os.path.join(tmp, "test_map.json")
            with open(path, "w") as f:
                json.dump(data, f)
            self.ctrl._mapping_dir = tmp
            self.ctrl._load_csv = MagicMock()
            self.ctrl._on_mapping_changed("test_map")
            self.assertIsNotNone(self.ctrl._active_mapping)
            self.assertEqual(self.ctrl._active_mapping["columns"]["step_id"], ["ID"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_on_mapping_changed_none_clears(self):
        """Selecting '(none)' clears the active mapping."""
        self.ctrl._active_mapping = {"columns": {}}
        self.ctrl._load_csv = MagicMock()
        self.ctrl._on_mapping_changed("(none)")
        self.assertIsNone(self.ctrl._active_mapping)

    def test_on_mapping_changed_reparses_csv(self):
        """Changing mapping re-parses the current CSV."""
        self.ctrl._csv_path = "/fake/path.csv"
        self.ctrl._load_csv = MagicMock()
        self.ctrl._active_mapping = None
        self.ctrl._on_mapping_changed("(none)")
        self.ctrl._load_csv.assert_called_once_with("/fake/path.csv")

    def test_on_mapping_changed_bad_name_clears(self):
        """Bad mapping name logs error and clears active mapping."""
        import tempfile
        import shutil

        tmp = tempfile.mkdtemp()
        try:
            self.ctrl._mapping_dir = tmp
            self.ctrl._load_csv = MagicMock()
            self.ctrl._on_mapping_changed("nonexistent")
            self.assertIsNone(self.ctrl._active_mapping)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_option_box_toolbar_constructs_on_real_combo(self):
        """The option-box toolbar builds on a real ComboBox and matches the row.

        The other mapping tests use a mocked combo, so this is the offscreen
        check that the real construction is correct (what a live Maya session
        would otherwise be the first to exercise): exactly two square icon
        buttons (Refresh + Open folder, no in-place editing controls), and the
        combo plus both buttons pinned to the header row height.
        """
        from qtpy import QtWidgets
        from uitk.widgets.comboBox import ComboBox

        host = QtWidgets.QWidget()
        QtWidgets.QVBoxLayout(host)
        cmb = ComboBox()
        host.layout().addWidget(cmb)
        cmb.addItem("(none)", None)

        ctrl = object.__new__(ShotManifestController)
        ctrl._mapping_dir = None
        ctrl._cmb_mapping = cmb

        target = 20
        ctrl._wire_mapping_option_box(cmb, target_h=target)  # must not raise

        # Combo pinned to the row height.
        self.assertEqual(cmb.maximumHeight(), target)

        # Toolbar = exactly two square icon buttons, both sized to the row.
        layout = cmb.option_box.container.layout()
        btns = [layout.itemAt(i).widget() for i in range(1, layout.count())]
        self.assertEqual(len(btns), 2)
        for b in btns:
            self.assertEqual(b.maximumHeight(), target)

        # Order: Refresh then Open folder (both equal-priority ActionOptions,
        # so the option box's stable sort keeps Refresh on the left, as asked).
        handlers = [
            getattr(o, "_action_handler", None)
            for o in cmb.option_box._option_box.get_options()
        ]
        self.assertEqual(
            handlers, [ctrl._refresh_mapping_list, ctrl._open_mappings_folder]
        )

    def test_wire_option_box_is_noop_on_non_widget(self):
        """Toolbar wiring is a clean no-op when the combo isn't a real widget
        (the mocked controller-test UI)."""
        ctrl = object.__new__(ShotManifestController)
        ctrl._wire_mapping_option_box(MagicMock())  # must not raise

    def test_seed_mappings_folder_seeds_empty_then_noops(self):
        """First open of an empty mappings folder drops an example + format
        reference (the externally-managed workflow needs a model to copy);
        a populated folder is left untouched on later opens."""
        import tempfile
        import shutil
        from pathlib import Path
        from pythontk import TemplateSet
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        tmp = Path(tempfile.mkdtemp())
        try:
            ts = TemplateSet(
                "seed_test", Mapping.templates().spec, "mayatk", user_dir=tmp
            )
            udir = ts.user_dir
            udir.mkdir(parents=True, exist_ok=True)

            ShotManifestController._seed_mappings_folder(ts)
            names = sorted(p.name for p in udir.iterdir())
            self.assertIn("example.json", names)
            self.assertIn("MAPPING_FORMAT.md", names)

            # Non-empty folder → no-op: a user edit survives a second open.
            (udir / "example.json").write_text("EDIT", encoding="utf-8")
            ShotManifestController._seed_mappings_folder(ts)
            self.assertEqual(
                (udir / "example.json").read_text(encoding="utf-8"), "EDIT"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_seed_survives_active_sentinel(self):
        """Regression: selecting a mapping writes the PresetStore ``.active``
        bookkeeping dotfile into the user folder; that must NOT count as
        'non-empty', or the first 'Open folder' after a selection would skip
        seeding (no example/reference)."""
        import tempfile
        import shutil
        from pathlib import Path
        from pythontk import TemplateSet
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        tmp = Path(tempfile.mkdtemp())
        try:
            ts = TemplateSet(
                "seed_test", Mapping.templates().spec, "mayatk", user_dir=tmp
            )
            udir = ts.user_dir
            udir.mkdir(parents=True, exist_ok=True)
            ts.active = "speedrun"  # writes the `.active` dotfile, as a selection does
            self.assertTrue((udir / ".active").is_file())

            ShotManifestController._seed_mappings_folder(ts)
            names = sorted(p.name for p in udir.iterdir())
            self.assertIn("example.json", names)  # seed not defeated by `.active`
            self.assertIn("MAPPING_FORMAT.md", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_source.ManifestModel.parse_csv"
    )
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

    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    def test_incremental_passes_zero_duration_flag(self, mock_cls):
        """sync() should receive zero_duration_fallback=True in incremental mode."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_builder.plan_insertions.return_value = []
        mock_cls.return_value = mock_builder
        _mock_undo_info(self)

        self.ctrl.build()

        mock_builder.sync.assert_called_once()
        call_kwargs = mock_builder.sync.call_args
        self.assertTrue(
            call_kwargs.kwargs.get("zero_duration_fallback")
            or (len(call_kwargs.args) > 4 and call_kwargs.args[4]),
            "sync should be called with zero_duration_fallback=True",
        )

    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    def test_a_typed_start_alone_builds_at_that_frame(self, mock_cls):
        """Bug: Set Start to Current Frame types ``(frame, None)``, and the
        incremental Build handed that ``None`` end straight to the engine,
        whose zero-duration placement adds to it (a TypeError: Build failed).
        A typed start alone is a place: zero-length there.
        Fixed: 2026-10-10"""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_builder.plan_insertions.return_value = []
        mock_cls.return_value = mock_builder
        _mock_undo_info(self)
        self.ctrl._steps = _make_steps("A01", "A02")
        self.ctrl._user_ranges["A02"] = (300.0, None)

        self.ctrl.build()

        ranges = mock_builder.sync.call_args.kwargs["ranges"]
        self.assertEqual(ranges["A01"], (10, 40), "a built step keeps its shot")
        self.assertEqual(ranges["A02"], (300.0, 300.0))


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
                "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions",
            ) as mock_auto,
            patch(
                "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
                return_value=[],
            ) as mock_sel,
        ):
            self.ctrl._cached_gaps = None
            self.ctrl._resolve_ranges()
            mock_sel.assert_called_once()
            mock_auto.assert_not_called()

    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    def test_build_uses_selected_keys_in_csv_mode(self, mock_cls):
        """build() in CSV mode with non-auto detection_mode should use
        selected-keys detection for range verification."""
        mock_builder = MagicMock()
        mock_builder.sync.return_value = ({}, {}, [])
        mock_builder.plan_insertions.return_value = []
        mock_cls.return_value = mock_builder
        _mock_undo_info(self)

        with patch(
            "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
            return_value=[],
        ) as mock_sel:
            self.ctrl.build()
            mock_sel.assert_called_once()


class TestSyncDetectionWidgets(unittest.TestCase, _ControllerHarness):
    """_sync_detection_widgets delegates to ShotsController.refresh_state.

    Fixed: 2026-04-03
    Updated: 2026-04-16 — Now delegates to refresh_state via slot_instances.
    """

    def setUp(self):
        self.setup_controller()
        # Create mock detection widgets on a fake parent shots UI
        self.mock_shots_ui = MagicMock()
        self.mock_shots_ui.cmb_detection_mode = MagicMock()
        self.mock_shots_ui.spn_detection = MagicMock()
        self.sb.loaded_ui.shots = self.mock_shots_ui

    def test_delegates_to_refresh_state(self):
        """When slot_instances has a shots controller, delegates to it."""
        _fresh_store()
        mock_ctrl = MagicMock()
        mock_slots = MagicMock()
        mock_slots.controller = mock_ctrl
        self.sb.slot_instances = {"shots": mock_slots}

        self.ctrl._sync_detection_widgets()
        mock_ctrl.refresh_state.assert_called_once()

    def test_fallback_widgets_disabled_when_built(self):
        """Fallback: direct widget manipulation when controller unavailable."""
        store = _fresh_store()
        store.define_shot("A01", 1, 31, ["obj_A01"])
        self.assertTrue(self.ctrl._is_built)
        # No slots controller available
        self.sb.slot_instances = {}

        self.ctrl._sync_detection_widgets()

        self.mock_shots_ui.cmb_detection_mode.setEnabled.assert_called_with(False)
        self.mock_shots_ui.spn_detection.setEnabled.assert_called_with(False)

    def test_fallback_widgets_enabled_when_not_built(self):
        """Fallback: widgets enabled when no shots exist."""
        _fresh_store()
        self.assertFalse(self.ctrl._is_built)
        self.sb.slot_instances = {}

        self.ctrl._sync_detection_widgets()

        self.mock_shots_ui.cmb_detection_mode.setEnabled.assert_called_with(True)
        self.mock_shots_ui.spn_detection.setEnabled.assert_called_with(True)

    def test_tolerates_missing_shots_ui(self):
        """Should not raise if shots UI is not loaded."""
        self.sb.slot_instances = {}
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


class TestIncrementalPlacement(unittest.TestCase, _ControllerHarness):
    """On a built timeline Build inserts a new step's shot in sheet order --
    after its previous step's shot -- and the later shots move out of its
    way, after the user confirms how many move.

    Bug: New CSV step A09 (between A08 and A10) was placed at frame
    21041 (end of timeline) instead of at A08's end (2520).
    Fixed: 2026-04-04
    Bug: the fix placed it zero-length at A08's end, ON A10, and a step with
    a stale resolved range wherever that range was: Build with Hide Missing
    Shots off left overlapping, zero-length, out-of-order shots (SOL: 37 new
    shots, dozens of overlaps, 9 zero-length).
    Fixed: 2026-10-12
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.gap = 0.0
        self.store.define_shot("A01", 1, 100, ["obj_A01"])
        self.store.define_shot("A02", 100, 200, ["obj_A02"])
        self.store.define_shot("A03", 200, 300, ["obj_A03"])
        self.ctrl._csv_path = "/fake.csv"
        self.ctrl._source = "csv"
        _mock_undo_info(self)

    def _layout(self):
        return [(s.name, s.start, s.end) for s in self.store.sorted_shots()]

    def _build(self, *ids, answer="Insert"):
        self.ctrl._steps = _make_steps(*ids, behaviors=[])
        self.sb.message_box.return_value = answer
        self.ctrl.build()

    def test_a_new_step_goes_after_its_previous_step_and_the_rest_ripple(self):
        length = self.ctrl._initial_shot_length
        self._build("A01", "A02", "B01", "A03")
        self.assertEqual(
            self._layout(),
            [
                ("A01", 1, 100),
                ("A02", 100, 200),
                ("B01", 200, 200 + length),
                ("A03", 200 + length, 300 + length),
            ],
        )
        self.sb.message_box.assert_called_once()
        self.assertIn("1 existing shot(s)", self.sb.message_box.call_args.args[0])

    def test_consecutive_new_steps_keep_their_sheet_order(self):
        length = self.ctrl._initial_shot_length
        self._build("A01", "B01", "B02", "A02", "A03")
        names = [name for name, _start, _end in self._layout()]
        self.assertEqual(names, ["A01", "B01", "B02", "A02", "A03"])
        rows = self._layout()
        for (_a, _s, end), (_b, start, _e) in zip(rows, rows[1:]):
            self.assertLessEqual(end, start, "no overlap")
        self.assertEqual(rows[1][1:], (100, 100 + length))

    def test_a_new_first_step_takes_the_head(self):
        length = self.ctrl._initial_shot_length
        self._build("B01", "A01", "A02", "A03")
        self.assertEqual(
            self._layout()[:2],
            [("B01", 1, 1 + length), ("A01", 1 + length, 100 + length)],
        )

    def test_cancel_moves_nothing(self):
        before = self._layout()
        self._build("A01", "B01", "A02", "A03", answer="Cancel")
        self.assertEqual(self._layout(), before)

    def test_appending_after_the_last_shot_asks_nothing(self):
        self._build("A01", "A02", "A03", "B01")
        self.sb.message_box.assert_not_called()
        self.assertEqual(self._layout()[-1][0], "B01")
        self.assertEqual(self._layout()[-1][1], 300)

    def test_the_table_previews_where_build_inserts(self):
        """An unbuilt step's Start / End is where Build will put it, not the
        animation regions' guess, and a projection is no collision."""
        length = self.ctrl._initial_shot_length
        self.ctrl._steps = _make_steps("A01", "A02", "B01", "A03", behaviors=[])
        resolved = {r[0]: r for r in self.ctrl._resolve_ranges()}
        self.assertEqual(resolved["B01"], ("B01", 200, 200 + length, False))
        self.assertEqual(self.ctrl._projected, {"B01": "after shot 'A02'"})
        self.ctrl._populate_table()  # an Assess, an option: a redraw
        self.ctrl._redraw()
        row = self.ctrl._step_items(["B01"])[0]
        self.assertEqual(
            (row.text(COL_START), row.text(COL_END)), ("200", f"{200 + length:.0f}")
        )
        self.assertIn("after shot 'A02'", row.toolTip(COL_START))

    def test_a_step_whose_shot_was_deleted_is_inserted_not_put_back(self):
        """A load seeds each built step's range from its shot: once the shot
        is gone (and the timeline closed up behind it) that range is no
        place the user chose, and putting the step back there would lay it
        over the next shot."""
        self.ctrl._user_ranges["A02"] = self.ctrl._seeded_ranges["A02"] = (100, 200)
        self.ctrl._user_ranges["A04"] = (900, 950)  # typed
        self.assertEqual(self.ctrl._typed_ranges(), {"A04": (900, 950)})

    def _hide_missing(self, on=True):
        self.addCleanup(self.ctrl._settings.setValue, "hide_missing", False)
        self.ctrl._settings.setValue("hide_missing", on)

    def _preview(self, *ids):
        """The table showing *ids*' steps, Start / End resolved."""
        self.ctrl._steps = _make_steps(*ids, behaviors=[])
        self.ctrl._populate_table()
        self.ctrl._refresh_ranges()

    def test_hidden_steps_mark_no_overlap(self):
        """Bug: with the steps without a shot hidden, their ranges -- never
        previewed -- were still checked, marking built rows as overlapping.
        Fixed: 2026-10-12"""
        self._hide_missing()
        self._preview("A01", "A02", "B01", "B02", "A03")
        self.assertEqual(self.ctrl._range_issues, {})

    def test_after_a_build_the_table_shows_what_was_built(self):
        """Bug: a redraw after the Build re-applied the insert preview: the
        new shot's Start still said "Build inserts this step's shot after
        ...", and every built row's length tooltip was blanked.
        Fixed: 2026-10-12"""
        length = self.ctrl._initial_shot_length
        self._preview("A01", "A02", "B01", "A03")
        self.assertIn(
            "Build inserts", self.ctrl._step_items(["B01"])[0].toolTip(COL_START)
        )
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        b01 = self.ctrl._step_items(["B01"])[0]
        a01 = self.ctrl._step_items(["A01"])[0]
        self.assertEqual(b01.toolTip(COL_START), f"{length:.0f}f")
        self.assertEqual(a01.toolTip(COL_START), "99f")
        self.assertEqual(self.ctrl._projected, {})

    def test_a_shot_resized_elsewhere_moves_the_preview(self):
        """Bug: a shot resized in the Shot Sequencer left the unbuilt rows
        previewing the frames from before.
        Fixed: 2026-10-12"""
        self.ctrl._first_shown = True
        self._preview("A01", "A02", "B01", "A03")
        self.assertEqual(self.ctrl._step_items(["B01"])[0].text(COL_START), "200")
        a02 = self.store.shot_by_name("A02")
        self.store.update_shot(a02.shot_id, end=250)
        self.ctrl._follow_store()
        self.assertEqual(self.ctrl._step_items(["B01"])[0].text(COL_START), "250")

    def test_showing_the_steps_without_a_shot_previews_them(self):
        """Bug: Show > Steps Without a Shot (no Assess showing) re-applied the
        ranges resolved while they were hidden: no insert preview.
        Fixed: 2026-10-12"""
        self._hide_missing()
        self._preview("A01", "A02", "B01", "A03")
        self.ctrl._set_hide_missing(False)
        row = self.ctrl._step_items(["B01"])[0]
        self.assertEqual(row.text(COL_START), "200")
        self.assertIn("after shot 'A02'", row.toolTip(COL_START))

    def test_assess_marks_each_row_once_by_its_own_results(self):
        """Bug: Assess marked the rows from the assessment before it, then
        again from its own -- two or three markings per row per redraw.
        Fixed: 2026-10-12"""
        from pythontk import StepStatus

        self._preview("A01", "A02", "B01", "A03")
        self.ctrl._last_results = [StepStatus(step_id="A01", built=False)]  # stale
        new = [StepStatus(step_id=s.step_id, built=True) for s in self.ctrl._steps]
        engine = self.ctrl._manifest(self.store)
        engine.assess = lambda *a, **k: new
        marks = []
        mark = self.ctrl._mark_step_row

        def spy(item, step_status=None):
            marks.append((item.text(COL_STEP), step_status))
            mark(item, step_status)

        with (
            patch.object(self.ctrl, "_manifest", return_value=engine),
            patch.object(self.ctrl, "_mark_step_row", new=spy),
        ):
            self.ctrl.assess(skip_key_check=True)
        self.assertEqual(
            sorted(name for name, _st in marks), ["A01", "A02", "A03", "B01"]
        )
        for name, status in marks:
            self.assertIs(status, next(r for r in new if r.step_id == name), name)

    def test_a_step_sync_built_after_a_refused_insert_counts_as_built(self):
        """A refused insert leaves the step to the build, which may still
        make its shot: the footer counts what was made."""
        engine = self.ctrl._manifest(self.store)
        engine.insert_planned = lambda plans, typed=None: {
            p.step.step_id: "refused" for p in plans
        }
        with patch.object(self.ctrl, "_manifest", return_value=engine):
            self._build("A01", "A02", "B01", "A03")
        self.assertIsNotNone(self.store.shot_by_name("B01"))
        footer = self.ui.footer.setText.call_args[0][0]
        self.assertIn("1 created", footer)
        self.assertNotIn("not built", footer)

    def test_a_trailing_key_past_the_last_shot_moves_the_preview(self):
        """The preview plans afresh on every redraw: what it reads -- keys
        trailing the last shot, the clips' lengths -- raises no store event.

        Bug: the plan was kept against the store's records, so a key set past
        the last shot left B01 previewed over it.
        Fixed: 2026-10-13"""
        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer as MayaShotSequencer,
        )

        self._preview("A01", "A02", "A03", "B01")
        self.assertEqual(self.ctrl._step_items(["B01"])[0].text(COL_START), "300")
        with patch.object(
            MayaShotSequencer,
            "_trailing_content_extent",
            lambda seq, shot: shot.end + (30 if shot.name == "A03" else 0),
        ):
            self.ctrl._redraw()
        self.assertEqual(self.ctrl._step_items(["B01"])[0].text(COL_START), "330")

    def test_an_audio_file_replaced_on_disk_is_measured_again(self):
        """A clip's length is read once per file as it is on disk: the same
        file is not read again, a replaced one is."""
        import struct
        import wave

        import pythontk as ptk
        from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
            _ShotManifestInternal,
        )

        tmp = ptk.TempArtifacts("mtk_manifest_clip", policy="scoped")
        self.addCleanup(tmp.cleanup)
        path = tmp.path(".wav")

        def write(seconds):
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(4800)
                w.writeframes(struct.pack("<h", 0) * int(4800 * seconds))

        obj = BuilderObject("vo", kind="audio", source_path=path)
        write(1.0)
        self.assertEqual(_ShotManifestInternal._measure_audio_obj(obj, 24.0), 24.0)
        write(2.0)
        self.assertEqual(_ShotManifestInternal._measure_audio_obj(obj, 24.0), 48.0)


class TestTypedRangesRideTheInserts(unittest.TestCase, _ControllerHarness):
    """A range typed for an unbuilt step is in the timeline's current frames:
    Build moves it with every shot an earlier insert moves (maintainer
    2026-10-12), and the table shows and checks it there.

    Bug: Build placed the typed A04 (220, 270) as typed, over A03, which
    A02's insert had moved to [170, 270].
    Fixed: 2026-10-12
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.gap = 10.0
        self.store.initial_shot_length = 50
        self.store.define_shot("A01", 0, 100, ["obj_A01"])
        self.store.define_shot("A03", 110, 210, ["obj_A03"])
        self.ctrl._csv_path, self.ctrl._source = "/fake.csv", "csv"
        _mock_undo_info(self)

    def _layout(self):
        return [(s.name, s.start, s.end) for s in self.store.sorted_shots()]

    def _load(self, *ids, typed):
        self.ctrl._steps = _make_steps(*ids, behaviors=[])
        self.ctrl._user_ranges.update(typed)
        self.ctrl._populate_table()

    def test_build_moves_it_with_the_shots_an_insert_moves(self):
        self._load("A01", "A02", "A03", "A04", typed={"A04": (220.0, 270.0)})
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        self.assertEqual(
            self._layout(),
            [("A01", 0, 100), ("A02", 110, 160), ("A03", 170, 270), ("A04", 280, 330)],
        )

    def test_the_table_shows_it_where_build_puts_it(self):
        self._load("A01", "A02", "A03", "A04", typed={"A04": (220.0, 270.0)})
        resolved = {r[0]: r for r in self.ctrl._refresh_ranges()}
        self.assertEqual(resolved["A04"], ("A04", 280, 330, True))
        row = self.ctrl._step_items(["A04"])[0]
        self.assertEqual((row.text(COL_START), row.text(COL_END)), ("280", "330"))
        self.assertIn("220", row.toolTip(COL_START))
        self.assertEqual(self.ctrl._range_issues, {})
        self.assertEqual(self.ctrl._user_ranges["A04"], (220.0, 270.0), "kept as typed")

    def test_an_edit_reads_the_cell_it_changed_and_keeps_the_other(self):
        """The other cell shows the moved frame; it is not read back as a
        typed one (which would move it twice)."""
        self._load("A01", "A02", "A03", "A04", typed={"A04": (220.0, 270.0)})
        self.ctrl._refresh_ranges()
        row = self.ctrl._step_items(["A04"])[0]
        row.setText(COL_END, "300")  # committed; its start cell still reads 280
        self.assertEqual(self.ctrl._user_ranges["A04"], (220.0, 300.0))

    def test_a_typed_range_over_a_projected_insert_is_an_overlap(self):
        """B01 appends after A03 at [220, 270] and moves B02's typed range
        (205, 260) 60 frames, to (265, 320): still over B01 -- the projection
        is checked, not passed by."""
        self._load("A01", "A03", "B01", "B02", typed={"B02": (205.0, 260.0)})
        self.ctrl._refresh_ranges()
        self.assertEqual(set(self.ctrl._range_issues), {"B01", "B02"})

    def test_build_drops_the_typed_range_of_a_step_it_gave_a_shot(self):
        """The shot carries the placement once built: a typed range left over
        would place the step again, where it was, once the shot is gone.

        Bug: typed ranges were never re-based after a Build.
        Fixed: 2026-10-13"""
        self._load("A01", "A02", "A03", "A04", typed={"A04": (220.0, 270.0)})
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        self.assertNotIn("A04", self.ctrl._user_ranges)

    def test_build_moves_the_typed_range_of_a_step_it_could_not_build(self):
        """A step the store refuses keeps its typed range -- moved with the
        shots the inserts moved, or the next preview places it 60 frames early.
        """
        self.store.define_shot("a05", 900, 950)  # A05 ignoring case: refused
        self._load("A01", "A02", "A03", "A05", typed={"A05": (220.0, 270.0)})
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        self.assertIsNone(self.store.shot_by_name("A05"))
        self.assertEqual(self.ctrl._user_ranges["A05"], (280.0, 330.0))

    def test_undoing_the_build_puts_the_typed_ranges_back(self):
        from pythontk import NativeStepFollowed

        self._load("A01", "A02", "A03", "A04", typed={"A04": (220.0, 270.0)})
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        self.assertNotIn("A04", self.ctrl._user_ranges)
        for name in ("A02", "A04"):  # the undo takes the shots it made
            self.store.remove_shot(self.store.shot_by_name(name).shot_id)
        self.ctrl._on_native_step_followed(
            NativeStepFollowed(redo=False, applied=1, store=self.store)
        )
        self.assertEqual(self.ctrl._user_ranges["A04"], (220.0, 270.0))

    def test_a_built_steps_shot_removed_later_is_placed_like_an_unbuilt_one(self):
        self._load("A01", "A02", "A03", "A04", typed={"A04": (220.0, 270.0)})
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        self.store.remove_shot(self.store.shot_by_name("A04").shot_id)
        self.ctrl._refresh_ranges()
        self.assertIn("A04", self.ctrl._projected, "inserted, not put back")
        self.assertNotIn("A04", self.ctrl._typed_shift)


class TestTypedRangesOnTheTimelineFrames(unittest.TestCase, _ControllerHarness):
    """Q [1, 100], R [200, 300], S [400, 500], gap 10; N inserted after Q
    moves the rest 60 frames; P typed at (510, 550) shows at (570, 610)."""

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.gap = 10.0
        self.store.initial_shot_length = 50
        for name, start, end in (("Q", 1, 100), ("R", 200, 300), ("S", 400, 500)):
            self.store.define_shot(name, start, end, [f"obj_{name}"])
        self.ctrl._csv_path, self.ctrl._source = "/fake.csv", "csv"
        _mock_undo_info(self)
        self.ctrl._steps = _make_steps("Q", "N", "R", "S", "P", "X", behaviors=[])
        self.ctrl._user_ranges["P"] = (510.0, 550.0)
        self.ctrl._populate_table()
        self.resolved = {r[0]: r for r in self.ctrl._refresh_ranges()}

    def _layout(self):
        return [(s.name, s.start, s.end) for s in self.store.sorted_shots()]

    def test_a_new_step_after_a_typed_one_follows_it(self):
        """Bug: X appended after the last SHOT, previewed at (570, 620) over
        P's (570, 610), and Build made both.
        Fixed: 2026-10-13"""
        self.assertEqual(self.resolved["P"], ("P", 570, 610, True))
        self.assertEqual(self.resolved["X"], ("X", 620, 670, False))
        self.assertEqual(self.ctrl._range_issues, {})
        self.sb.message_box.return_value = "Insert"
        self.ctrl.build()
        self.assertEqual(
            self._layout(),
            [
                ("Q", 1, 100),
                ("N", 110, 160),
                ("R", 260, 360),
                ("S", 460, 560),
                ("P", 570, 610),
                ("X", 620, 670),
            ],
        )

    def test_a_typed_start_is_checked_where_build_puts_it(self):
        """X typed at 560 lands at 620, after P's 610: accepted.

        Bug: the start, in the timeline's current frames, was checked against
        P's end where Build puts it (610): silently reverted, so the user
        typed 610 -- which then moved too, to 670.
        Fixed: 2026-10-13"""
        row = self.ctrl._step_items(["X"])[0]
        row.setText(COL_START, "560")
        self.assertEqual(self.ctrl._user_ranges["X"][0], 560.0)


class TestAnEditOfAMovedTypedRange(unittest.TestCase, _ControllerHarness):
    """A01 [0, 50], A02 [60, 90], gap 10; N (40 frames) inserted after A01
    moves the rest 50 frames: B typed at (100, 150) shows at (150, 200)."""

    def test_an_end_edit_reads_the_start_as_typed(self):
        """Bug: an End edit read the Start cell's moved frame (150), so End
        140 -- (100, 140) as typed -- was refused.
        Fixed: 2026-10-13"""
        self.setup_controller()
        self.store = _fresh_store()
        self.store.gap = 10.0
        self.store.initial_shot_length = 40
        self.store.define_shot("A01", 0, 50, ["obj_A01"])
        self.store.define_shot("A02", 60, 90, ["obj_A02"])
        self.ctrl._csv_path, self.ctrl._source = "/fake.csv", "csv"
        self.ctrl._steps = _make_steps("A01", "N", "A02", "B", behaviors=[])
        self.ctrl._user_ranges["B"] = (100.0, 150.0)
        self.ctrl._populate_table()
        resolved = {r[0]: r for r in self.ctrl._refresh_ranges()}
        self.assertEqual(resolved["B"], ("B", 150, 200, True))
        row = self.ctrl._step_items(["B"])[0]
        row.setText(COL_END, "140")
        self.assertEqual(self.ctrl._user_ranges["B"], (100.0, 140.0))


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
        from uitk.managers.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import PASTEL_STATUS

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


class TestRowStatus(unittest.TestCase, _ControllerHarness):
    """Each row carries one status signal: its icon in the Step column, in
    its worst issue's colour, and a row tooltip listing the issues, a
    coloured glyph before each -- nothing tinted (2026-10-12: "the error
    color coding overlaps and is confusing ... format and color code (can
    just be newline text with a prefixed color coded icon)")."""

    def setUp(self):
        self.store = _fresh_store()
        self.setup_controller()
        self.store.define_shot("A01", 1, 40, ["obj_A01", "stray"])
        self.ctrl._steps = _make_steps("A01", "A02")
        self.ctrl._populate_table()

    def _assess(self):
        from pythontk import ObjectStatus, StepStatus

        missing = ObjectStatus(
            name="obj_A01", exists=False, status="missing_object", behaviors=[]
        )
        self.ctrl._last_results = [
            StepStatus(step_id="A01", built=True, objects=[missing]),
            StepStatus(step_id="A02", built=False),
        ]
        self.ctrl._apply_assessment(self.ctrl._last_results)

    def _row(self, step_id):
        return self.ctrl._step_items([step_id])[0]

    def _tinted(self, item):
        return [
            c
            for c in range(self.tree.columnCount())
            if item.background(c).style() != QtCore.Qt.NoBrush
            or item.foreground(c).style() != QtCore.Qt.NoBrush
        ]

    def test_a_row_says_its_issues_in_its_tooltip_never_in_a_tint(self):
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            ManifestData,
        )

        self._assess()
        a01, a02 = self._row("A01"), self._row("A02")
        tip = self.tree.row_tooltip(a01)
        self.assertIn(ManifestData.glyph_html("missing_object"), tip)
        self.assertIn("obj_A01", tip)
        self.assertIn(ManifestData.LEVEL_GLYPHS["info"], self.tree.row_tooltip(a02))
        for row in (a01, a02, a01.child(0), a01.child(1)):
            self.assertEqual(self._tinted(row), [], row.text(COL_DESC))
        missing = a01.child(0)
        self.assertIn("Not found", self.tree.row_tooltip(missing))
        self.assertFalse(missing.icon(COL_STEP).isNull(), "its status icon")

    def test_a_shots_member_the_step_does_not_list_is_marked_not_tinted(self):
        member = self._row("A01").child(1)
        self.assertEqual(member.text(COL_DESC), "stray")
        self.assertEqual(self._tinted(member), [])
        self.assertIn("not listed", self.tree.row_tooltip(member))

    def test_a_cells_own_tooltip_follows_the_rows(self):
        self._assess()
        a01 = self._row("A01")
        a01.setToolTip(COL_DESC, "Sheet: Open the lid")
        index = self.tree.indexFromItem(a01, COL_DESC)
        tip = self.tree.item_tooltip(index, a01.toolTip(COL_DESC))
        self.assertTrue(tip.endswith("Sheet: Open the lid"), tip)
        self.assertIn("obj_A01", tip)

    def test_overlapping_ranges_are_a_row_status(self):
        self.ctrl._user_ranges.update({"A01": (1.0, 40.0), "A02": (30.0, 60.0)})
        self.ctrl._validate_range_collisions(
            [("A01", 1.0, 40.0, True), ("A02", 30.0, 60.0, True)]
        )
        a02 = self._row("A02")
        self.assertIn("overlaps 'A01'", self.tree.row_tooltip(a02))
        self.assertEqual(self._tinted(a02), [])


class TestPanelPalette(unittest.TestCase):
    """Bug: the panel's palette WAS the engine's (``PASTEL_STATUS =
    SHOT_PALETTE``), so the Colors... editor recoloured the Shot Sequencer too.
    Fixed: 2026-10-12"""

    def test_recolouring_the_panel_leaves_the_engines_palette(self):
        from pythontk import SHOT_PALETTE
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            PASTEL_STATUS,
            ManifestData,
        )

        orig = PASTEL_STATUS["missing_behavior"]
        self.addCleanup(PASTEL_STATUS.__setitem__, "missing_behavior", orig)
        PASTEL_STATUS["missing_behavior"] = ("#AABBCC", None)
        self.assertNotEqual(str(SHOT_PALETTE["missing_behavior"][0]), "#AABBCC")
        self.assertIn("#AABBCC", ManifestData.glyph_html("missing_behavior"))


class TestEditsMode(unittest.TestCase, _ControllerHarness):
    """Header menu > Edits: Keep All Edits, Rebuild Hand-Edited Shots or
    Compare to Original Sheet, Reset beside it -- one control for what View
    Original Sheet, Reset Local Edits and Rebuild Edited Shots each did
    (2026-10-12: "view original sheet should be renamed ... its not only a
    view. and it feels redundant with reset local edits. perhaps rebuild
    edited shots can also be collapsed into this option")."""

    SHEET = "/some/file.csv"

    def setUp(self):
        self.store = _fresh_store()
        SettingsManager(namespace="ShotManifest").clear("rebuild_edited")
        self.setup_controller()
        self.addCleanup(self.ctrl._settings.clear, "rebuild_edited")
        from uitk.widgets.menu import Menu

        self.menu = Menu()
        self.addCleanup(self.menu.deleteLater)
        self.ctrl._editor.add_header_options(self.menu)
        self.cmb = self.ctrl._editor._cmb_edits
        self.btn = self.ctrl._editor._btn_reset
        self.ctrl._load_data(_make_steps("A01", "A02"), csv_path=self.SHEET)

    def _pick(self, mode):
        self.cmb.setCurrentIndex(self.cmb.findData(mode))
        for _ in range(3):
            QtWidgets.QApplication.processEvents()

    def test_its_modes_and_the_mode_in_force(self):
        self.assertEqual(
            [self.cmb.itemText(i) for i in range(self.cmb.count())],
            [
                "Keep All Edits",
                "Rebuild Hand-Edited Shots",
                "Compare to Original Sheet",
            ],
        )
        self.assertEqual(self.cmb.currentData(), "keep")
        self.assertEqual(self.btn.text(), "Reset")
        self.assertFalse(self.btn.isEnabled(), "nothing to reset")

    def test_rebuild_is_kept_per_user(self):
        self._pick("rebuild")
        self.assertTrue(self.ctrl._rebuild_edited)
        self.assertTrue(self.ctrl._manifest(self.store).rebuild_edited)
        self._pick("keep")
        self.assertFalse(self.ctrl._rebuild_edited)

    def test_compare_shows_the_sheet_refuses_build_and_returns_to_the_mode(self):
        self._pick("rebuild")
        self.ctrl._editor.edits.set_step("A01", "name", "Renamed", sheet="A01")
        self.ctrl._editor.refresh()
        self.assertEqual(self.btn.text(), "Reset (1)")
        self.assertTrue(self.btn.isEnabled())
        self._pick("compare")
        self.assertTrue(self.ctrl._editor.viewing_original)
        self.assertFalse(self.btn.isEnabled(), "the edits are not in force")
        self.assertEqual(self.tree.topLevelItem(0).text(COL_STEP), "A01")
        with patch.object(self.ctrl, "_ensure_steps") as ensure:
            self.ctrl.build()
        ensure.assert_not_called()
        self._pick("rebuild")
        self.assertFalse(self.ctrl._editor.viewing_original)
        self.assertTrue(self.ctrl._rebuild_edited, "Compare kept the mode")
        self.assertEqual(self.tree.topLevelItem(0).text(COL_STEP), "Renamed")

    def test_the_combo_follows_a_view_set_elsewhere(self):
        self.ctrl._editor.view_original(True)
        self.assertEqual(self.cmb.currentData(), "compare")
        self.ctrl._editor.view_original(False)
        self.assertEqual(self.cmb.currentData(), "keep")


class TestViewCombos(unittest.TestCase, _ControllerHarness):
    """The row above the table: Select, Expand, Collapse, Hide and Show, a
    combobox each covering every action of its kind -- the header menu's
    Expand All Missing / Expand All Extra and Hide Missing Shots folded in
    (2026-10-12: "add a select, expand, collapse, hide, show comboboxes and
    remove the existing individual buttons for these actions. they should
    cover all perceived actions including expand and collapse all")."""

    def setUp(self):
        from uitk.widgets.comboBox import ComboBox

        self.store = _fresh_store()
        self.setup_controller()
        self.addCleanup(self.ctrl._settings.setValue, "hide_missing", False)
        self.ctrl._settings.setValue("hide_missing", False)
        self.store.define_shot("A01", 1, 40, ["obj_A01", "stray"])
        self.ctrl._steps = _make_steps("A01", "A02", "A03")
        self.ctrl._source, self.ctrl._csv_path = "csv", "X:/sheet.csv"
        self.ctrl._populate_table()
        self.combos = {}
        for name, _title, _lister in self.ctrl.VIEW_COMBOS:
            cmb = ComboBox()
            self.addCleanup(cmb.deleteLater)
            setattr(self.ui, name, cmb)
            self.combos[name] = cmb
        self.ctrl._setup_view_combos()

    def _labels(self, name):
        cmb = self.combos[name]
        return [cmb.itemText(i) for i in range(cmb.count())]

    def _pick(self, name, label):
        """Open the combo (its rows listed afresh) and pick *label*."""
        cmb = self.combos[name]
        cmb.before_popup_shown.emit()
        index = next(i for i in range(cmb.count()) if cmb.itemText(i).startswith(label))
        self.assertTrue(cmb.model().item(index).isEnabled(), label)
        cmb.setCurrentIndex(index)
        self.assertEqual(cmb.currentIndex(), -1, "its title again")

    def _expanded(self):
        return [
            self.tree.topLevelItem(i).text(COL_STEP)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).isExpanded()
        ]

    def test_each_shows_its_title_and_lists_its_actions(self):
        for name, title, _lister in self.ctrl.VIEW_COMBOS:
            cmb = self.combos[name]
            self.assertEqual(cmb.currentIndex(), -1)
            self.assertEqual(cmb.header_text, title)
        self.assertEqual(
            self._labels("cmb_expand"),
            [
                "All",
                "Rows with Issues",
                "Rows with Objects Not in the Manifest",
                "Selected Rows",
            ],
        )
        self.assertEqual(
            self._labels("cmb_collapse"),
            ["All", "Rows Without Issues", "Selected Rows"],
        )

    def test_expand_and_collapse_all(self):
        self._pick("cmb_expand", "All")
        self.assertEqual(self._expanded(), ["A01", "A02", "A03"])
        self._pick("cmb_collapse", "All")
        self.assertEqual(self._expanded(), [])

    def test_expand_the_rows_with_objects_not_in_the_manifest(self):
        self._pick("cmb_expand", "Rows with Objects Not in the Manifest")
        self.assertEqual(self._expanded(), ["A01"])

    def test_an_action_with_nothing_to_act_on_is_disabled(self):
        cmb = self.combos["cmb_expand"]
        cmb.before_popup_shown.emit()
        issues = self._labels("cmb_expand").index("Rows with Issues")
        self.assertFalse(cmb.model().item(issues).isEnabled(), "not assessed")
        self.assertIn("Assess first", cmb.itemData(issues, QtCore.Qt.ToolTipRole))

    def test_select_rows_then_their_objects_in_the_scene(self):
        self._pick("cmb_select", "Unbuilt Steps")
        selected = sorted(i.text(COL_STEP) for i in self.tree.selectedItems())
        self.assertEqual(selected, ["A02", "A03"])
        with patch.object(self.ctrl, "_show_in_outliner") as reveal:
            self._pick("cmb_select", "Scene: Objects of Selected Rows")
        reveal.assert_called_once_with(["obj_A02", "obj_A03"])

    def test_hide_and_show_the_steps_without_a_shot(self):
        self.assertFalse(self.ctrl._hide_missing)
        self._pick("cmb_hide", "Steps Without a Shot")
        self.assertTrue(self.ctrl._hide_missing)
        self.assertEqual([s.step_id for s in self.ctrl._shown_steps()], ["A01"])
        self._pick("cmb_show", "Steps Without a Shot")
        self.assertFalse(self.ctrl._hide_missing)

    def test_hide_the_selected_steps_as_a_local_edit(self):
        self.ctrl._editor.load()
        self.ctrl._step_items(["A03"])[0].setSelected(True)
        with patch.object(self.ctrl._editor, "_commit") as commit:
            self._pick("cmb_hide", "Selected Steps")
        commit.assert_called_once()
        self.assertEqual(self.ctrl._editor.edits.hidden_steps(), ["A03"])
        self._pick_show_hidden()

    def _pick_show_hidden(self):
        with patch.object(self.ctrl._editor, "_commit"):
            self._pick("cmb_show", "Hidden Steps")
        self.assertEqual(self.ctrl._editor.edits.hidden_steps(), [])

    def test_a_wheel_over_a_closed_combo_runs_nothing(self):
        """Bug: a wheel turned over the closed Hide combo stepped to its first
        enabled row and ran it: Steps Without a Shot hidden, unseen.
        Fixed: 2026-10-12"""
        from qtpy import QtGui

        cmb = self.combos["cmb_hide"]
        for delta in (-120, 120):
            event = QtGui.QWheelEvent(
                QtCore.QPointF(5, 5),
                QtCore.QPointF(5, 5),
                QtCore.QPoint(0, 0),
                QtCore.QPoint(0, delta),
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
                QtCore.Qt.NoScrollPhase,
                False,
            )
            QtWidgets.QApplication.sendEvent(cmb, event)
        self.assertFalse(self.ctrl._hide_missing)
        self.assertEqual(cmb.currentIndex(), -1)

    def test_a_pick_acts_on_the_rows_drawn_since_the_list_opened(self):
        """Bug: an action held the rows listed when its combo opened; a
        redraw while it was open (a store edit, the deferred Assess) deleted
        them, and the pick raised "Internal C++ object already deleted".
        Fixed: 2026-10-12"""
        cmb = self.combos["cmb_select"]
        cmb.before_popup_shown.emit()
        self.ctrl._populate_table()  # a redraw while it is open
        index = next(
            i for i in range(cmb.count()) if cmb.itemText(i).startswith("All Steps")
        )
        cmb.setCurrentIndex(index)
        selected = sorted(i.text(COL_STEP) for i in self.tree.selectedItems())
        self.assertEqual(selected, ["A01", "A02", "A03"])

    def test_show_hidden_objects_leaves_a_locked_steps_hidden(self):
        """A locked step takes no Local Edit until it is unlocked; Show >
        Hidden Objects showed its objects all the same.
        Fixed: 2026-10-12"""
        editor = self.ctrl._editor
        editor.load()
        editor.edits.set_object("A01", "obj_A01", "hidden", True)
        editor.edits.set_object("A02", "obj_A02", "hidden", True)
        self.store.update_shot(self.store.shot_by_name("A01").shot_id, locked=True)
        cmb = self.combos["cmb_show"]
        cmb.before_popup_shown.emit()
        self.assertIn("Hidden Objects (1)", self._labels("cmb_show"))
        with patch.object(editor, "_commit"):
            self._pick("cmb_show", "Hidden Objects")
        self.assertEqual(editor.edits.hidden_objects(), [("A01", "obj_A01")])


class TestTheTableFollowsEveryShotEdit(unittest.TestCase, _ControllerHarness):
    """Every shot edit, made in any panel, reaches the table (2026-10-07:
    "the manifest doesn't refresh properly on many actions").  The engine's
    bound edits write the shots directly -- no ShotUpdated -- so a sheet's
    built rows used to keep their old Start / End, and only after a Build in
    this session did any event refresh them."""

    def setUp(self):
        self.store = _fresh_store()
        self.setup_controller()
        self.store.define_shot("A01", 10, 50, metadata={"step": "A01"})
        self.ctrl._source, self.ctrl._csv_path = "csv", "X:/sheet.csv"
        self.ctrl._first_shown = True
        self.ctrl._populate_table()

    def _cells(self, step_id):
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            COL_END,
            COL_START,
        )

        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            if row.text(0) == step_id:
                return row.text(COL_START), row.text(COL_END)
        return None

    def test_a_built_row_shows_its_shot_from_the_start(self):
        self.assertEqual(self._cells("A01"), ("10", "50"))

    def test_a_bound_edit_made_elsewhere_reaches_the_built_row(self):
        shot = self.store.shot_by_name("A01")
        with self.store.scene_edit("trim"):
            shot.end = 40.0  # as the engine's trim writes it: no ShotUpdated
            self.store.mark_dirty()
        self.assertEqual(self._cells("A01"), ("10", "40"))

    def test_a_shot_deleted_or_added_elsewhere_reshapes_a_sheet(self):
        """Bug: with a sheet loaded, only Start / End followed the store -- a
        step whose shot was deleted elsewhere still read as built (its range
        locked), and a shot added elsewhere never showed as "not in doc".
        Fixed: 2026-10-07"""
        from qtpy.QtCore import Qt

        a01 = self.store.shot_by_name("A01")
        self.store.remove_shot(a01.shot_id)
        self.assertEqual(self._cells("A01"), ("", ""), "no shot, no range")
        row = next(
            self.tree.topLevelItem(i)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).text(0) == "A01"
        )
        self.assertTrue(row.flags() & Qt.ItemIsEditable, "an unbuilt row takes a range")
        extra = self.store.define_shot("Extra", 300, 340)
        self.assertEqual(self._cells("Extra"), ("300", "340"), "listed, not in doc")
        with self.store.scene_edit("trim"):
            extra.end = 330.0
        self.assertEqual(self._cells("Extra")[1], "330", "its range follows too")
        self.assertEqual(self._cells("A01"), ("", ""))  # the table was redrawn

    def test_an_unbuilt_row_keeps_its_own_range(self):
        self.ctrl._user_ranges["A02"] = (60.0, 90.0)
        self.ctrl._populate_table()
        with self.store.scene_edit("trim"):
            self.store.shot_by_name("A01").end = 40.0
        self.assertEqual(self._cells("A02"), ("60", "90"))

    def test_a_showing_assessment_is_run_again_once_edits_settle(self):
        from pythontk import StepStatus
        from mayatk.anim_utils.shots._shots import (
            ActiveShotChanged,
            BatchComplete,
            ShotRemoved,
            ShotsEdited,
            ShotUpdated,
        )

        shot = self.store.shot_by_name("A01")
        for event in (
            ShotUpdated(shot=shot),
            ShotRemoved(shot_id=99),
            BatchComplete(),
            ShotsEdited(label="trim"),
        ):
            with self.subTest(event=event.name):
                sentinel = [StepStatus(step_id="A01", built=True)]
                self.ctrl._last_results = sentinel
                with patch.object(self.ctrl, "_reassess_soon") as soon:
                    self.ctrl._on_store_event(event)
                soon.assert_called_once_with()
                self.assertIs(self.ctrl._last_results, sentinel, "kept until re-run")
        with patch.object(self.ctrl, "_reassess_soon") as soon:
            self.ctrl._on_store_event(ActiveShotChanged(shot_id=shot.shot_id))
        soon.assert_not_called()

    def test_with_no_assessment_showing_none_is_run(self):
        from mayatk.anim_utils.shots._shots import ShotsEdited

        self.ctrl._last_results = []
        with patch.object(self.ctrl, "_reassess_soon") as soon:
            self.ctrl._on_store_event(ShotsEdited(label="trim"))
        soon.assert_not_called()

    def test_a_hidden_panel_re_assesses_when_shown(self):
        self.ctrl.ui.isVisible = MagicMock(return_value=False)
        with patch.object(self.ctrl, "assess") as assess:
            self.ctrl._reassess()
            assess.assert_not_called()
            self.ctrl.ui.isVisible.return_value = True
            self.ctrl._on_show()
            assess.assert_called_once_with(skip_key_check=True, note="")
            self.ctrl._on_show()
            assess.assert_called_once_with(skip_key_check=True, note="")


class TestBuildOptions(unittest.TestCase, _ControllerHarness):
    """The header menu's Build options reach the engine: the Edits mode's
    Keep / Rebuild Hand-Edited Shots (Keep by default) and a Key / Marker /
    Off choice per behavior type (Off by default), both kept per user."""

    _KEYS = ("rebuild_edited", "keyed_behaviors", "marked_behaviors")

    def setUp(self):
        self.store = _fresh_store()
        for key in self._KEYS + ("disabled_behaviors",):
            SettingsManager(namespace="ShotManifest").clear(key)
        self.setup_controller()
        for key in self._KEYS + ("disabled_behaviors",):
            self.addCleanup(self.ctrl._settings.clear, key)

    def test_edited_shots_are_kept_by_default(self):
        self.assertFalse(self.ctrl._rebuild_edited)
        self.assertFalse(self.ctrl._manifest(self.store).rebuild_edited)

    def test_behaviors_are_off_until_chosen(self):
        """("behaviors should default to off")"""
        every = frozenset(Behaviors.list_behaviors())
        self.assertEqual({self.ctrl._behavior_mode(n) for n in every}, {"off"})
        self.assertEqual(self.ctrl._manifest(self.store).disabled_behaviors, every)

    def test_the_options_reach_the_engine(self):
        every = frozenset(Behaviors.list_behaviors())
        self.ctrl._set_rebuild_edited(True)
        self.ctrl._set_behavior_mode("highlight", "key")
        self.ctrl._set_behavior_mode("fade_in", "marker")
        mani = self.ctrl._manifest(self.store)
        self.assertTrue(mani.rebuild_edited)
        self.assertEqual(mani.disabled_behaviors, every - {"highlight", "fade_in"})
        self.assertEqual(mani.marked_behaviors, frozenset({"fade_in"}))
        self.ctrl._set_behavior_mode("highlight", "off")
        self.ctrl._set_behavior_mode("fade_in", "key")
        mani = self.ctrl._manifest(self.store)
        self.assertEqual(mani.disabled_behaviors, every - {"fade_in"})
        self.assertEqual(mani.marked_behaviors, frozenset())

    def test_a_users_old_choices_carry_over(self):
        """Before Off became the default, the settings named the types turned
        Off and every other type was keyed: those stay keyed.

        Bug: the carry-over deleted the old list from the store Maya and
        Blender share, so an older blendertk read no list and keyed every
        behavior the user had Off.  It stays for an older release to read.
        Fixed: 2026-10-12
        """
        settings = self.ctrl._settings
        settings.setValue("disabled_behaviors", ["highlight"])
        settings.setValue("marked_behaviors", ["set_clip"])
        self.ctrl._behavior_sets.clear()
        self.ctrl._migrate_behavior_modes()
        modes = {n: self.ctrl._behavior_mode(n) for n in Behaviors.list_behaviors()}
        self.assertEqual(modes["highlight"], "off")
        self.assertEqual(modes["set_clip"], "marker")
        self.assertEqual(modes["fade_in"], "key")
        self.assertEqual(settings.value("disabled_behaviors", None), ["highlight"])

    def test_the_carry_over_runs_once(self):
        """The old list stays, so the carry-over must not run again over the
        choices the user makes since."""
        settings = self.ctrl._settings
        settings.setValue("disabled_behaviors", ["highlight"])
        self.ctrl._behavior_sets.clear()
        self.ctrl._migrate_behavior_modes()
        self.assertEqual(self.ctrl._behavior_mode("fade_in"), "key")
        for name in Behaviors.list_behaviors():  # the user turns every one Off
            self.ctrl._set_behavior_mode(name, "off")
        self.ctrl._behavior_sets.clear()
        self.ctrl._migrate_behavior_modes()  # the next open
        modes = {self.ctrl._behavior_mode(n) for n in Behaviors.list_behaviors()}
        self.assertEqual(modes, {"off"})

    def test_each_behavior_picks_key_marker_or_off_in_the_header_menu(self):
        """Acting on the menu's choice: it opens on the kept mode, and a pick
        is kept per user (2026-10-11: "a third option (maybe using a
        combobox) of adding a marker in the sequencer instead of keying")."""
        from uitk.widgets.menu import Menu

        self.ctrl._set_behavior_mode("highlight", "marker")
        menu = Menu()
        self.addCleanup(menu.deleteLater)
        self.ctrl._add_build_options(menu)
        modes = self.ctrl._behavior_modes
        self.assertEqual(sorted(modes), sorted(Behaviors.list_behaviors()))
        cmb = modes["highlight"]
        self.assertEqual(
            [cmb.itemText(i) for i in range(cmb.count())], ["Key", "Marker", "Off"]
        )
        self.assertEqual(cmb.currentData(), "marker")
        self.assertEqual(modes["fade_in"].currentData(), "off")
        cmb.setCurrentIndex(cmb.findData("off"))
        self.ctrl._behavior_sets.clear()  # read back from the settings
        self.assertEqual(self.ctrl._behavior_mode("highlight"), "off")
        self.assertEqual(self.ctrl._marked_behaviors(), frozenset())

    def test_a_typed_range_never_moves_a_built_shot(self):
        """A built row cannot be typed in, so a range for it was typed before
        it was built; a rebuild applied it over the shot's own bounds."""
        steps = _make_steps("A01", behaviors=[])
        self.ctrl._steps = steps
        self.store.define_shot("A01", 10, 70, metadata={"step": "A01"})
        self.ctrl._user_ranges = {"A01": (10.0, 50.0)}
        seen = {}
        engine = self.ctrl._manifest(self.store)

        def sync(build_steps, ranges=None, **_kw):
            seen.update(ranges or {})
            return {}, {}, []

        engine.sync = sync
        with (
            patch.object(self.ctrl, "_manifest", return_value=engine),
            patch.object(self.ctrl, "_apply_post_build"),
        ):
            _mock_undo_info(self)
            self.ctrl.build()
        self.assertEqual(seen.get("A01"), (10, 70))


# ---------------------------------------------------------------------------
# Tests: format_behavior_html
# ---------------------------------------------------------------------------


class TestFormatBehaviorHtml(unittest.TestCase):
    """Verify behaviour label HTML: an issue leads its behavior with its
    glyph in its colour (the name itself never coloured); ``status_color``
    colours every behaviour."""

    def setUp(self):
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            ManifestData,
        )

        self.format = ManifestData.format_behavior_html
        self.glyph = ManifestData.glyph_html

    def test_plain_when_no_broken(self):
        """Behaviours with no issues produce plain text (no span tags)."""
        html = self.format(["fade_in", "fade_out"])
        self.assertNotIn("<span", html)
        self.assertIn("Fade In", html)
        self.assertIn("Fade Out", html)

    def test_broken_leads_with_its_glyph(self):
        """Only the broken behaviour is marked: its glyph, in the
        ``missing_behavior`` colour, before its plain name."""
        html = self.format(["fade_in", "fade_out"], broken=["fade_out"])
        self.assertTrue(html.startswith("Fade In"), html)  # fade_in is plain
        self.assertIn(f"{self.glyph('missing_behavior')} Fade Out", html)
        self.assertNotIn("Fade Out</span>", html)

    def test_stale_leads_with_its_glyph(self):
        html = self.format(["fade_in"], stale=["fade_in"])
        self.assertEqual(html, f"{self.glyph('stale_behavior')} Fade In")

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
        self.ctrl._relabel_shot_row = MagicMock()  # reads the panel's ui

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
        self.ctrl._relabel_shot_row.assert_called_once_with(shot)  # its row only

    def test_a_sequencer_edit_triggers_full_sync(self):
        """A trim or drag in the Shot Sequencer writes the bounds directly;
        its ``scene_edit`` announcement is all this panel hears of it."""
        from mayatk.anim_utils.shots._shots import ShotsEdited

        self.ctrl._on_store_event(ShotsEdited(label="trim"))
        self.ctrl._sync_from_store.assert_called_once()


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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    def test_auto_mode_still_calls_detect_shot_regions(self, mock_auto, mock_sel):
        """With auto mode, _detect_regions must call detect_shot_regions."""
        self.store.detection_mode = "auto"
        mock_auto.return_value = [
            {"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]},
        ]

        self.ctrl._detect_regions(5.0)

        mock_auto.assert_called_once()
        mock_sel.assert_not_called()

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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


class TestRecipeChangeKeepsTheTable(unittest.TestCase, _ControllerHarness):
    """A store setting that is no detection input leaves the table alone.

    Bug: ``ShotStore.update_effect_recipe`` fires the same bare
    ``SettingsChanged`` a detection setting does, so every Render Effects
    spinbox tick re-ran ``detect()`` -- the steps regenerated, a typed range
    gone -- while the last Assess, judged under the old recipe, still drove
    the Build button.
    Fixed: 2026-10-04
    """

    _DETECT = (
        "mayatk.anim_utils.shots.shot_manifest.manifest_host"
        ".Detection.detect_shot_regions"
    )
    _REGIONS = [{"name": "Shot 1", "start": 10.0, "end": 50.0, "objects": ["ctrl"]}]

    def setUp(self):
        # The store first: the controller listens to the one active when built.
        self.store = _fresh_store()
        self.setup_controller()
        self.addCleanup(self.ctrl.remove_callbacks)
        self.ctrl._first_shown = True
        with patch(self._DETECT, return_value=self._REGIONS):
            self.ctrl.detect()
        self.ctrl._user_ranges["Shot 1"] = (12.0, 60.0)  # typed by the user

    def test_a_recipe_change_keeps_the_steps_and_typed_ranges(self):
        steps = list(self.ctrl._steps)
        with patch(self._DETECT, return_value=self._REGIONS) as detect:
            self.store.update_effect_recipe(fade_frames=20)
        detect.assert_not_called()
        self.assertEqual(self.ctrl._steps, steps)
        self.assertEqual(self.ctrl._user_ranges["Shot 1"], (12.0, 60.0))

    def test_a_recipe_change_stales_the_last_assess(self):
        self.ctrl._last_results = [StepStatus(step_id="Shot 1", built=False)]
        with (
            patch(self._DETECT, return_value=self._REGIONS),
            patch.object(self.ctrl, "_reassess_soon") as soon,
        ):
            self.store.update_effect_recipe(fade_frames=20)
        self.assertEqual(self.ctrl._last_results, [])
        self.ui.b003.setEnabled.assert_called_with(False)
        soon.assert_called_once_with()  # ...and runs again by itself

    def test_a_detection_setting_still_redetects_once(self):
        with patch(self._DETECT, return_value=self._REGIONS) as detect:
            self.store.detection_threshold = 9.0
            self.store.notify_settings_changed()
            self.store.notify_settings_changed()  # nothing changed since
        detect.assert_called_once()


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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
        return_value=[],
    )
    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.regions_from_selected_keys",
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

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
    )
    @patch("mayatk.anim_utils.shots._shots.ShotStore.active")
    def test_scene_change_reloads_the_scenes_manifest(self, mock_active, mock_detect):
        """Opening a scene built from a CSV must reload that CSV."""
        new_store = _fresh_store()
        new_store.source_csv = "/some/manifest.csv"
        mock_active.return_value = new_store
        self.ctrl.ui.txt_csv_path.text.return_value = "/some/manifest.csv"

        with patch.object(self.ctrl, "_load_csv") as mock_load:
            self.ctrl._on_scene_changed()
            mock_load.assert_called_once_with("/some/manifest.csv")

        # detect should NOT have run (CSV takes priority)
        mock_detect.assert_not_called()

    def test_a_reassessment_owed_to_the_old_scene_is_dropped(self):
        """Bug: a re-assessment pending (or owed to a hidden panel) when the
        scene changed ran on the next scene, which opens unassessed.
        Fixed: 2026-10-12"""
        self.ctrl._first_shown = False  # no repopulate: only the drop is under test
        self.ctrl._reassess_soon(note="Hid 2 step(s).")
        self.ctrl._reassess_on_show = True

        self.ctrl._on_scene_changed()

        self.assertFalse(self.ctrl._reassess_timer.isActive(), "timer still pending")
        self.assertFalse(self.ctrl._reassess_on_show)
        self.assertEqual(self.ctrl._reassess_note, "")

    @patch(
        "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions"
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
                "mayatk.anim_utils.shots.shot_manifest.manifest_host.Detection.detect_shot_regions",
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
# Tests: the scene's own shots as the source (no build sheet)
# ---------------------------------------------------------------------------


class TestSceneShotsSource(unittest.TestCase, _ControllerHarness):
    """An empty source field shows the store's shots -- descriptions and all --
    instead of re-detecting animation that throws them away.

    Bug: with no CSV the manifest re-detected animation regions and built
    blank steps from them, so a scene whose shots carry descriptions showed an
    empty Description column.
    Fixed: 2026-10-01
    """

    _DETECT = (
        "mayatk.anim_utils.shots.shot_manifest.manifest_host"
        ".Detection.detect_shot_regions"
    )

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.define_shot(
            "intro", 1, 40, objects=["|rig|arm"], description="Arm reaches out"
        )
        self.store.define_shot(
            "lever", 50, 90, objects=["|rig|hand"], description="Hand pulls lever"
        )
        self._active = patch(
            "mayatk.anim_utils.shots._shots.ShotStore.active",
            return_value=self.store,
        )
        self._active.start()
        self.addCleanup(self._active.stop)
        self.ctrl._steps = []

    def test_empty_field_shows_the_scenes_shots_with_descriptions(self):
        with patch(self._DETECT) as detect:
            self.ctrl._on_first_show()
        detect.assert_not_called()
        self.assertEqual(self.ctrl._source, "scene")
        self.assertEqual(
            [(s.step_id, s.description) for s in self.ctrl._steps],
            [("intro", "Arm reaches out"), ("lever", "Hand pulls lever")],
        )
        top = self.tree.topLevelItem(0)
        self.assertEqual(top.text(COL_DESC), "Arm reaches out")
        self.assertEqual(self.ctrl._user_ranges["lever"], (50, 90))

    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    def test_a_scene_source_build_never_removes_or_records_a_sheet(self, mock_cls):
        """Built from the scene's shots (here: the sheet in the field failed),
        a build neither removes shots nor stamps the unused sheet as the
        scene's source -- or the scene would re-open on a sheet never used."""
        self.ctrl.ui.txt_csv_path.text.return_value = "X:/no/such/sheet.csv"
        with patch(self._DETECT):
            self.ctrl._populate_from_source()
        self.assertEqual(self.ctrl._source, "scene")
        builder = MagicMock()
        builder.sync.return_value = ({}, {"applied": [], "skipped": []}, [])
        builder.plan_insertions.return_value = []
        mock_cls.return_value = builder
        _mock_undo_info(self)

        self.ctrl.build()

        builder.sync.assert_called_once()
        self.assertFalse(builder.sync.call_args.kwargs["remove_missing"])
        self.assertEqual(self.store.source_csv, "")

    def test_a_store_without_shots_falls_back_to_detection(self):
        empty = _fresh_store()
        with (
            patch(
                "mayatk.anim_utils.shots._shots.ShotStore.active", return_value=empty
            ),
            patch(self._DETECT, return_value=[]) as detect,
        ):
            self.ctrl._on_first_show()
        detect.assert_called_once()

    def test_a_failed_manifest_still_shows_the_scenes_shots(self):
        self.ctrl.ui.txt_csv_path.text.return_value = "X:/no/such/sheet.csv"
        with patch(self._DETECT) as detect:
            self.ctrl._populate_from_source()
        detect.assert_not_called()
        self.assertEqual([s.step_id for s in self.ctrl._steps], ["intro", "lever"])
        self.ctrl.ui.txt_csv_path.set_action_color.assert_called_with("invalid")
        self.assertIn(
            "Manifest not loaded", self.ctrl.ui.footer.setText.call_args.args[0]
        )

    def test_a_failed_manifest_on_an_empty_scene_clears_the_table(self):
        self.ctrl._steps = _make_steps("OLD1")
        self.ctrl.ui.txt_csv_path.text.return_value = "X:/no/such/sheet.csv"
        with (
            patch(
                "mayatk.anim_utils.shots._shots.ShotStore.active",
                return_value=_fresh_store(),
            ),
            patch(self._DETECT) as detect,
        ):
            self.ctrl._populate_from_source()
        detect.assert_not_called()
        self.assertEqual(self.ctrl._steps, [])

    def test_clearing_the_field_returns_to_the_scenes_shots(self):
        self.ctrl._csv_path = "X:/sheet.csv"
        self.ctrl._source = "csv"
        self.ctrl.ui.txt_csv_path.text.return_value = ""
        self.ctrl._on_csv_path_edited()
        self.assertEqual(self.ctrl._source, "scene")
        self.assertEqual(self.ctrl._csv_path, "")

    def test_a_store_edit_refreshes_the_scene_source(self):
        from mayatk.anim_utils.shots._shots import ShotUpdated

        self.ctrl._first_shown = True
        self.ctrl._load_scene_shots()
        shot = self.store.sorted_shots()[0]
        shot.description = "Arm reaches for the lever"
        self.ctrl._on_store_event(ShotUpdated(shot=shot))
        self.assertEqual(self.ctrl._steps[0].description, "Arm reaches for the lever")

    def test_detection_settings_leave_the_scene_source_alone(self):
        from mayatk.anim_utils.shots._shots import SettingsChanged

        self.ctrl._first_shown = True
        self.ctrl._load_scene_shots()
        with patch(self._DETECT) as detect:
            self.ctrl._on_store_event(SettingsChanged())
        detect.assert_not_called()
        self.assertEqual(self.ctrl._source, "scene")

    def test_shots_deleted_elsewhere_empty_the_table_without_asking(self):
        """Bug: the scene's shots all deleted in another panel (Shots' Delete
        All), the store event reloaded them, found none and fell through to
        the interactive ``detect()`` -- whose selected-keys mode pops a modal
        "No keys selected" for an action taken elsewhere.
        Fixed: 2026-10-04
        """
        from mayatk.anim_utils.shots._shots import ShotRemoved

        self.store.detection_mode = "skip_zero"  # a selected-keys mode
        self.ctrl._first_shown = True
        self.ctrl._load_scene_shots()
        for shot in list(self.store.shots):
            self.store.remove_shot(shot.shot_id)
        selected = (
            "mayatk.anim_utils.shots.shot_manifest.manifest_host"
            ".Detection.regions_from_selected_keys"
        )
        with (
            patch(selected, return_value=[]) as by_keys,
            patch(self._DETECT, return_value=[]) as detect,
        ):
            self.ctrl._on_store_event(ShotRemoved(shot_id=1))
        self.ctrl.sb.message_box.assert_not_called()
        by_keys.assert_not_called()
        detect.assert_not_called()
        self.assertEqual(self.ctrl._steps, [])
        self.assertEqual(self.ctrl._source, "scene")  # still following the store
        footer = self.ctrl.ui.footer.setText.call_args.args[0]
        self.assertIn("no shots", footer.lower())

    def test_the_field_opens_on_the_scenes_recorded_manifest(self):
        self.store.source_csv = "X:/sheets/build_sheet.csv"
        with patch.object(self.ctrl, "_load_csv", return_value=True) as load:
            self.ctrl.ui.txt_csv_path.text.return_value = "X:/sheets/build_sheet.csv"
            self.ctrl._on_first_show()
        self.ctrl.ui.txt_csv_path.setText.assert_called_with(
            "X:/sheets/build_sheet.csv"
        )
        load.assert_called_once_with("X:/sheets/build_sheet.csv")


class _MemoryClipboard:
    """``QApplication.clipboard()`` for a test: the copy stays in this process.

    In a GUI Maya the real one is the Windows clipboard, shared with every
    program on the machine: one holding it made every read come back empty,
    the test's own "before" text included (2026-10-12, 7 Export tests red
    with nothing wrong).  Python owns the QMimeData here, so nothing is left
    in Qt's global for libc exit to tear down either (exit 139 after a green
    summary under the offscreen platform, the reason the real one was
    cleared in teardown).
    """

    def __init__(self):
        self._mime = QtCore.QMimeData()

    def setText(self, text):
        mime = QtCore.QMimeData()
        mime.setText(text)
        self._mime = mime

    def setMimeData(self, mime):
        self._mime = mime

    def mimeData(self):
        return self._mime

    def text(self):
        return self._mime.text()

    def clear(self):
        self._mime = QtCore.QMimeData()


def _use_memory_clipboard(case):
    """Give *case* a :class:`_MemoryClipboard` for every
    ``QApplication.clipboard()`` call -- the panel's and the test's."""
    board = _MemoryClipboard()
    p = patch.object(QtWidgets.QApplication, "clipboard", return_value=board)
    p.start()
    case.addCleanup(p.stop)
    return board


# ---------------------------------------------------------------------------
# Tests: Auto-fill Missing Assets
# ---------------------------------------------------------------------------


class TestAutoFillAssetsController(unittest.TestCase, _ControllerHarness):
    """A sheet's asset-less steps take the scene's objects -- and Export
    hands them on with their steps (Copy Asset Names, which pasted them back
    over the sheet's asset column, gave way to Export on 2026-10-10)."""

    CSV = (
        "Step,Step Contents,Asset Names\n"
        "A01.),Door opens,\n"
        ",Handle turns,\n"
        "A02.),Lid lifts,lid_geo\n"
    )
    _DETECT = (
        "mayatk.anim_utils.shots.shot_manifest.manifest_host"
        ".Detection.detect_shot_regions"
    )

    def setUp(self):
        import pythontk as ptk

        self.setup_controller()
        self.store = _fresh_store()
        self.store.define_shot("A01", 1, 40, objects=["|grp|door_geo", "|grp|handle"])
        for p in (
            patch(
                "mayatk.anim_utils.shots._shots.ShotStore.active",
                return_value=self.store,
            ),
            patch(self._DETECT, return_value=[]),
            # The shot's members exist and are assets: this module's own claim,
            # not an accident of which maya.cmds a conftest left in place (in a
            # real Maya, the scene holds none of them).
            *(
                patch(
                    "mayatk.anim_utils.shots.shot_manifest._shot_manifest"
                    f".ShotManifest.{check}",
                    return_value=True,
                )
                for check in ("_object_exists", "_is_asset_candidate")
            ),
        ):
            p.start()
            self.addCleanup(p.stop)
        tmp = ptk.TempArtifacts("mtk_manifest_autofill", policy="scoped")
        self.addCleanup(tmp.cleanup)
        self.csv = os.path.join(tmp.dir_path(), "sheet.csv")
        with open(self.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.CSV)
        self.ctrl._steps = []
        self.addCleanup(self.ctrl._settings.setValue, "export_options", "")
        _use_memory_clipboard(self)

    def _load(self, enabled):
        # The template's option, applied: what its Auto-fill row sets.
        self.ctrl._active_mapping = {"fill_missing_assets": enabled}
        self.assertTrue(self.ctrl._load_csv(self.csv))
        return {s.step_id: s for s in self.ctrl._steps}

    def test_an_asset_less_step_takes_its_shots_members(self):
        steps = self._load(True)
        self.assertEqual(
            [(o.name, o.origin) for o in steps["A01"].objects],
            [("door_geo", "shot"), ("handle", "shot")],
        )
        self.assertEqual(
            [(o.name, o.origin) for o in steps["A02"].objects],
            [("lid_geo", "column")],
        )
        child = self.tree.topLevelItem(0).child(0)
        self.assertIn("Auto-filled from the scene", child.toolTip(COL_DESC))

    def test_off_by_default_the_sheet_is_read_as_written(self):
        steps = self._load(False)
        self.assertEqual(steps["A01"].objects, [])

    def test_the_filled_names_export_with_their_steps(self):
        self._load(True)
        self.ctrl._set_export_option("objects", "cell")
        self.ctrl._set_export_option("columns", ["step", "objects"])
        self.ctrl._export_copy()  # the Export preview's Copy
        mime = QtWidgets.QApplication.clipboard().mimeData()
        self.assertEqual(
            mime.text(), 'Step\tObjects\nA01\t"door_geo\nhandle"\nA02\tlid_geo'
        )
        self.assertIn("<td>door_geo<br>handle</td>", mime.html())


# ---------------------------------------------------------------------------
# Tests: Export (the button beside Assess, and its live preview)
# ---------------------------------------------------------------------------


class TestExportButton(unittest.TestCase, _ControllerHarness):
    """Export hands the manifest on as the table has it -- its rows in its
    order, Local Edits applied, the scene's shot names and the status -- as
    CSV, JSON or Markdown, copied or saved to a file, the way its preview's
    options say; it never writes the loaded sheet.

    2026-10-10 (user: "the copy asset names action ... doesn't seem very
    useful ... Let's just have an export button with option menu that gives
    options for format, etc"); 2026-10-11 the option menu became a live
    preview (user: "remove the options altogether from the export button and
    just launch a real-time popup preview of the output where the user can
    adjust the output params with real time feedback before exporting").
    """

    def setUp(self):
        import pythontk as ptk

        self.setup_controller()
        self.store = _fresh_store()
        p = patch(
            "mayatk.anim_utils.shots._shots.ShotStore.active", return_value=self.store
        )
        p.start()
        self.addCleanup(p.stop)
        for key in ("export_options", "export_dir", "hide_missing"):
            self.addCleanup(
                self.ctrl._settings.setValue, key, self.ctrl._settings.value(key)
            )
        self.ctrl._settings.setValue("export_options", "")
        self.ctrl._store = self.store
        self.ctrl._active_mapping = None
        self.store.define_shot(
            "A01_1", 0, 111, objects=["|g|KEYS", "|g|LOCK"], metadata={"step": "A01"}
        )
        self.store.define_shot("A01_2", 121, 231, objects=["|g|LOCK_B"])
        self.store.define_shot("A02", 241, 321, objects=["|g|obj_A02"])
        tmp = ptk.TempArtifacts("mtk_manifest_export", policy="scoped")
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.dir_path()
        self.sheet = os.path.join(self.dir, "sheet.csv")
        with open(self.sheet, "w", encoding="utf-8", newline="") as fh:
            fh.write("Step,Step Contents,Asset Names\n")
        _use_memory_clipboard(self)

    def _load(self):
        self.ctrl._load_data(_make_steps("A01", "A02", "A03"), csv_path=self.sheet)
        for _ in range(3):
            QtWidgets.QApplication.processEvents()

    def _row(self, text):
        tree = self.tree
        return next(
            tree.topLevelItem(i)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).text(COL_STEP) == text
        )

    def _footer(self):
        return self.ui.footer.setText.call_args.args[0]

    def _copy(self, **options):
        """Copy the export with *options* (the preview's Copy); the copied
        text."""
        for key, value in options.items():
            self.ctrl._set_export_option(key, value)
        self.ctrl._export_copy()
        return QtWidgets.QApplication.clipboard().text()

    def test_the_rows_go_as_the_table_has_them(self):
        """The shot split off in the Sequencer sits after A01, as in the
        table; A01's shot keeps the scene's name; A03 has none yet."""
        self._load()
        text = self._copy(columns=["step", "name", "shot", "status"])
        self.assertEqual(
            text.splitlines(),
            [
                "Step\tName\tShot\tStatus",
                "A01\tA01\tA01_1\tbuilt",
                "\t\tA01_2\tnot in doc",
                "A02\tA02\tA02\tbuilt",
                "A03\tA03\t\tmissing shot",
            ],
        )
        self.assertEqual(
            self._footer(), "Copied 4 rows as CSV -- paste it into a spreadsheet."
        )

    def test_a_local_edit_and_hidden_missing_shots_are_what_goes(self):
        self._load()
        row = self._row("A02")
        self.assertTrue(self.ctrl._editor.begin_edit(row, COL_STEP))
        row.setText(COL_STEP, "Lid_Up")
        for _ in range(3):
            QtWidgets.QApplication.processEvents()
        self.ctrl._settings.setValue("hide_missing", True)
        self.ctrl._redraw()
        text = self._copy(columns=["step", "name", "shot"])
        self.assertEqual(
            text.splitlines()[1:],
            ["A01\tA01\tA01_1", "\t\tA01_2", "A02\tLid_Up\tA02"],
        )

    def test_the_selected_rows_an_object_row_selecting_its_step(self):
        self._load()
        self._row("A02").child(0).setSelected(True)
        text = self._copy(rows="selected", columns=["step", "objects"])
        self.assertEqual(text.splitlines(), ["Step\tObjects", "A02\tobj_A02"])

    def test_nothing_selected_says_so_and_copies_nothing(self):
        self._load()
        QtWidgets.QApplication.clipboard().setText("before")
        self.assertEqual(self._copy(rows="selected"), "before")
        self.assertIn("select the rows to export", self._footer())

    def test_json_and_markdown_go_as_their_text(self):
        self._load()
        out = json.loads(self._copy(format="json", columns=["step", "start", "end"]))
        self.assertEqual(out[0], {"step": "A01", "start": 0.0, "end": 111.0})
        self.assertIsNone(out[1]["step"])
        md = self._copy(format="markdown", columns=["step", "status"])
        self.assertEqual(
            md.splitlines()[:3],
            ["| Step | Status |", "| --- | --- |", "| A01 | built |"],
        )

    def test_a_file_in_the_chosen_format_beside_the_sheet(self):
        self._load()
        self.ctrl._set_export_option("format", "json")
        self.sb.save_file_dialog.return_value = os.path.join(self.dir, "out")
        self.ctrl._export_save()
        kwargs = self.sb.save_file_dialog.call_args.kwargs
        self.assertEqual(
            kwargs["start_dir"], os.path.join(self.dir, "sheet_manifest.json")
        )
        with open(os.path.join(self.dir, "out.json"), encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)), 4)
        self.assertEqual(self._footer(), "Exported 4 rows to out.json.")
        self.assertEqual(self.ctrl._settings.value("export_dir"), self.dir)

    def test_the_loaded_sheet_is_never_written(self):
        self._load()
        self.sb.save_file_dialog.return_value = self.sheet
        self.ctrl._export_save()
        with open(self.sheet, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Step,Step Contents,Asset Names\n")
        self.assertIn("never writes it", self._footer())

    def test_an_option_that_is_no_longer_a_choice_falls_back(self):
        self.ctrl._settings.setValue(
            "export_options",
            {"format": "xlsx", "rows": "selected", "columns": ["end", "x", "step"]},
        )
        options = self.ctrl._export_options()
        self.assertEqual(options["format"], "csv")
        self.assertEqual(options["rows"], "selected")
        self.assertEqual(options["columns"], ["step", "end"])

    def _open_preview(self):
        """Click Export, as a user does: a real preview window opens."""
        from uitk import Switchboard

        real = Switchboard()
        self.sb.preview_dialog = lambda *a, **k: real.preview_dialog(
            *a, **dict(k, parent=None)
        )
        self.ctrl.export()
        preview = self.ctrl._export_preview
        self.addCleanup(preview.close)
        return preview

    def test_export_opens_a_preview_that_renders_as_options_are_set(self):
        """The preview acted on, as a user does: pick JSON, untick a column --
        the text follows each at once, and each is kept."""
        self._load()
        preview = self._open_preview()
        self.assertTrue(preview.isVisible())
        self.assertTrue(preview.text_edit.toPlainText().startswith("Step,Name,Shot"))
        self.assertEqual(preview.footer.text(), "4 rows as CSV.")
        fmt = preview.preview_widgets["format"]
        fmt.setCurrentIndex(fmt.findData("json"))
        self.assertEqual(json.loads(preview.text_edit.toPlainText())[0]["step"], "A01")
        self.assertEqual(self.ctrl._export_options()["format"], "json")
        self.assertFalse(preview.preview_widgets["objects"].isEnabled())
        columns = preview.preview_widgets["columns"]
        columns.item(0).setCheckState(QtCore.Qt.Unchecked)  # Step
        self.assertNotIn('"step"', preview.text_edit.toPlainText())
        self.assertNotIn("step", self.ctrl._export_options()["columns"])

    def test_the_preview_follows_the_tables_selection(self):
        self._load()
        self.ctrl._set_export_option("rows", "selected")
        self.ctrl._set_export_option("columns", ["step"])
        preview = self._open_preview()
        self.assertIn("select the rows", preview.text_edit.toPlainText())
        self._row("A02").setSelected(True)
        self.assertEqual(preview.text_edit.toPlainText().splitlines(), ["Step", "A02"])

    def test_the_preview_follows_a_shot_moved_in_the_sequencer(self):
        """Bug: an open preview kept the frames it opened with when a shot
        moved (``_refresh_timing``) or a range was typed (``_refresh_ranges``).
        Fixed: 2026-10-12"""
        self._load()
        self.ctrl._set_export_option("columns", ["step", "start"])
        preview = self._open_preview()
        self.assertIn("A02,241", preview.text_edit.toPlainText())
        shot = next(s for s in self.store.shots if s.name == "A02")
        self.store.update_shot(shot.shot_id, start=251, end=331)
        self.ctrl._refresh_timing()
        self.assertIn("A02,251", preview.text_edit.toPlainText())

    def test_the_previews_copy_copies_and_leaves_it_open(self):
        self._load()
        self.ctrl._set_export_option("columns", ["step"])
        preview = self._open_preview()
        copy = next(b for b in preview.button_box.buttons() if b.text() == "Copy")
        copy.click()
        self.assertEqual(
            QtWidgets.QApplication.clipboard().text().splitlines()[:2], ["Step", "A01"]
        )
        self.assertTrue(preview.isVisible())
        self.assertIn("Copied 4 rows as CSV", preview.footer.text())

    def test_a_second_click_brings_up_the_open_preview(self):
        self._load()
        preview = self._open_preview()
        self.ctrl.export()
        self.assertIs(self.ctrl._export_preview, preview)

    def test_the_button_has_no_option_menu(self):
        from uitk.widgets.pushButton import PushButton

        host = QtWidgets.QWidget()
        self.addCleanup(host.deleteLater)
        btn = PushButton(host, setText="Export")
        self.ui.b004 = btn
        self.ctrl._setup_export_button()
        self.assertIsNone(getattr(btn, "_option_box_manager", None))
        self.assertIn("Preview the export", btn.toolTip())


# ---------------------------------------------------------------------------
# Tests: the panel over the reconciliation (pairing, no bulk removal, undo,
# placement)
# ---------------------------------------------------------------------------


class TestReconcilePanel(unittest.TestCase, _ControllerHarness):
    """The panel reads pairing from the engine, never removes a shot in bulk,
    builds as one undoable edit, and asks before guessing placement."""

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        p = patch(
            "mayatk.anim_utils.shots._shots.ShotStore.active", return_value=self.store
        )
        p.start()
        self.addCleanup(p.stop)
        self.ctrl._store = self.store
        self.ctrl._active_mapping = None
        self.ctrl._source = "csv"
        self.ctrl._csv_path = "X:/sheet.csv"

    def test_order_matching_pairs_a_differently_named_shot(self):
        self.store.define_shot("Step_2_1", 1, 40)
        self.ctrl._steps = _make_steps("IN_01")
        self.assertFalse(self.ctrl._step_is_built("IN_01"))
        self.ctrl._active_mapping = {"match": "name_then_order"}
        self.assertTrue(self.ctrl._step_is_built("IN_01"))

    def test_a_shot_no_step_pairs_with_is_a_row_and_build_keeps_it(self):
        self.store.define_shot("A01", 1, 40)
        self.store.define_shot("OLD", 50, 90)
        self.ctrl._steps = _make_steps("A01")
        self.ctrl._populate_table()
        names = [
            self.tree.topLevelItem(i).text(0)
            for i in range(self.tree.topLevelItemCount())
        ]
        self.assertEqual(names, ["A01", "OLD"])
        self.assertEqual([s.name for s in self.ctrl._orphan_shots()], ["OLD"])

    def test_removing_an_orphan_is_explicit_and_recorded_for_undo(self):
        orphan = self.store.define_shot("OLD", 50, 90)
        self.ctrl._steps = _make_steps("A01")
        before = len(self.store._boundary_undo)
        self.ctrl._remove_orphan(orphan)
        self.assertIsNone(self.store.shot_by_name("OLD"))
        self.assertEqual(len(self.store._boundary_undo), before + 1)

    def test_removing_an_orphan_keeps_the_tables_expansion(self):
        """Bug: Remove Shot redrew the table with a bare ``_populate_table``
        -- every expanded row collapsed and a showing assessment's colours
        went -- where every other store edit made from a row's menu redraws
        through ``_redraw``.
        Fixed: 2026-10-10"""
        self.store.define_shot("A01", 1, 40)
        orphan = self.store.define_shot("OLD", 50, 90)
        self.ctrl._steps = _make_steps("A01")
        self.ctrl._populate_table()
        self.ctrl._step_items(["A01"])[0].setExpanded(True)
        self.ctrl._remove_orphan(orphan)
        self.assertIsNone(self.store.shot_by_name("OLD"))
        self.assertTrue(self.ctrl._step_items(["A01"])[0].isExpanded())

    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    def test_build_never_removes_and_is_one_undoable_edit(self, mock_cls):
        self.store.define_shot("OLD", 50, 90)
        self.ctrl._steps = _make_steps("A01")
        self.ctrl._user_ranges = {"A01": (1.0, 40.0)}
        builder = MagicMock()

        def _sync(*_args, **_kwargs):
            # What a build writes.  One that changes nothing records no
            # restore point (``ShotStore.scene_edit``, 2026-10-11).
            self.store.define_shot("A01", 1, 40)
            return {}, {"applied": [], "skipped": []}, []

        builder.sync.side_effect = _sync
        mock_cls.return_value = builder
        _mock_undo_info(self)
        before = len(self.store._boundary_undo)
        self.ctrl.build()
        self.assertFalse(builder.sync.call_args.kwargs["remove_missing"])
        self.assertEqual(len(self.store._boundary_undo), before + 1)

    def test_placement_asks_only_when_steps_and_regions_disagree(self):
        self.ctrl._steps = _make_steps("A01", "A02", "A03")
        self.ctrl._user_ranges = {}
        self.ctrl._cached_gaps = [1.0, 50.0, 100.0]
        self.assertTrue(self.ctrl._placement_on_regions())
        self.ctrl.sb.message_box.assert_not_called()

        self.ctrl._cached_gaps = [1.0, 50.0]
        for answer, expected in (("Yes", True), ("No", False), ("Cancel", None)):
            self.ctrl.sb.message_box.return_value = answer
            self.assertIs(self.ctrl._placement_on_regions(), expected)

    def test_placing_sequentially_ignores_the_regions(self):
        self.ctrl._steps = _make_steps("A01", "A02")
        self.ctrl._user_ranges = {}
        self.ctrl._cached_gaps = [500.0]
        self.ctrl._cached_gap_ends = {}
        on_regions = self.ctrl._resolve_ranges()
        sequential = self.ctrl._resolve_ranges(regions=False)
        self.assertEqual(on_regions[0][1], 500.0)
        self.assertNotEqual(sequential[0][1], 500.0)


class TestShotsTheDocDoesNotList(unittest.TestCase, _ControllerHarness):
    """A sheet whose scene was edited in the Shot Sequencer (the production
    case, SOL_REPLACE_ASSEMBLY 2026-10-10: eight shots split off there, 37 of
    57 steps never animated): every shot shows its members, sits where the
    timeline has it, and its row offers what a shot needs -- open, reveal,
    add to the manifest, pair with a step, remove; steps lock by row or by
    section; steps with no shot can be hidden, and Build follows the table."""

    SHEET = "X:/split.csv"

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        p = patch(
            "mayatk.anim_utils.shots._shots.ShotStore.active", return_value=self.store
        )
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.ctrl._settings.setValue, "hide_missing", False)
        self.ctrl._store = self.store
        self.ctrl._active_mapping = None
        self.store.define_shot(
            "A01_1", 0, 111, objects=["|g|KEYS", "|g|LOCK"], metadata={"step": "A01"}
        )
        self.store.define_shot("A01_2", 121, 231, objects=["|g|LOCK_B"])
        self.store.define_shot("A02", 241, 321, objects=["|g|obj_A02"])

    def _load(self, original=False):
        """Load the sheet; *original* views it as delivered."""
        steps = _make_steps("A01", "A02", "A03")
        steps[2].section = "B"
        self.ctrl._load_data(steps, csv_path=self.SHEET)
        self.ctrl._editor.view_original(original)

    def _rows(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _row(self, text):
        return next(r for r in self._rows() if r.text(COL_STEP) == text)

    @staticmethod
    def _children(row):
        return [row.child(j).text(COL_DESC) for j in range(row.childCount())]

    def _menu(self, item, pick=None):
        """Right-click *item*; trigger the entry whose text starts with
        *pick* (submenus searched).  Returns ``{text: enabled}``."""
        from qtpy.QtWidgets import QMenu

        seen = {}

        def run(menu, *_args):
            found = []

            def walk(m):
                for a in m.actions():
                    if a.text():
                        seen[a.text()] = a.isEnabled()
                    if a.menu():
                        walk(a.menu())
                    elif pick and a.text().startswith(pick) and not found:
                        found.append(a)

            walk(menu)
            if found:
                found[0].trigger()
            return None

        with patch.object(self.tree, "itemAt", return_value=item):
            with patch.object(QMenu, "exec_", run):
                self.ctrl._show_item_menu(self.tree.visualItemRect(item).center())
        for _ in range(3):
            QtWidgets.QApplication.processEvents()
        return seen

    def test_a_split_shot_sits_beside_the_shot_it_was_split_from(self):
        """Bug: a shot no step pairs with was listed after the last step, its
        members not shown ("added shots are appended to the end of the
        manifest instead of shown in shot order"; "all shots ... should
        display their members").
        Fixed: 2026-10-10"""
        self._load()
        self.assertEqual(
            [r.text(COL_STEP) for r in self._rows()], ["A01", "A01_2", "A02", "A03"]
        )
        self.assertEqual(self._children(self._row("A01_2")), ["LOCK_B"])
        # A step's shot shows its members the step does not list, too.
        self.assertEqual(self._children(self._row("A01")), ["obj_A01", "KEYS", "LOCK"])
        self.assertEqual(self._children(self._row("A02")), ["obj_A02"])

    def test_a_split_shot_shows_the_section_of_the_step_it_was_split_from(self):
        """Bug: a split-off shot had no relation to its step, so its row (and
        its export) had no section.  The pairing links it
        (``ShotPairing.links``: here by its ``A01_2`` name) and its row shows
        the step's section; it stays a shot the doc does not list.
        Fixed: 2026-10-11"""
        import pythontk as ptk
        from qtpy.QtCore import Qt
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import COL_SECTION

        self._load()
        self.assertEqual(self._row("A01_2").text(COL_SECTION), "A: Section A")
        self.assertNotIsInstance(
            self._row("A01_2").data(0, Qt.UserRole), ptk.BuilderStep
        )
        self.assertEqual(self._row("A02").text(COL_SECTION), "A: Section A")

    def test_a_shot_not_in_the_doc_has_the_menu_a_shot_needs(self):
        self._load()
        seen = self._menu(self._row("A01_2"))
        for text, enabled in (
            ("Open 'A01_2' in Shot Sequencer", True),
            ("Open 'A01_2' in Shots", True),
            ("Show 'LOCK_B' in Outliner", True),
            ("Add 'A01_2' to the Manifest", True),
            ("Pair 'A01_2' with Step", True),  # A03 has no shot
            ("Remove Shot 'A01_2'", True),
        ):
            self.assertEqual(seen.get(text), enabled, text)
        member = self._row("A01_2").child(0)
        self.assertIn("Copy 'LOCK_B' to Clipboard", self._menu(member))
        self.ctrl._editor.view_original(True)  # read-only: nothing joins
        seen = self._menu(self._row("A01_2"))
        self.assertFalse(seen["Add 'A01_2' to the Manifest"])

    def test_adding_a_shot_makes_it_a_step_where_it_sits(self):
        self._load()
        self._menu(self._row("A01_2"), pick="Add 'A01_2'")
        self.assertEqual(
            self.store.manifest_edits[self.SHEET]["added"],
            [{"step_id": "A01_2", "after": "A01"}],
        )
        self.assertEqual(self.store.shot_by_name("A01_2").metadata["step"], "A01_2")
        from qtpy.QtCore import Qt

        row = self._row("A01_2")
        self.assertIsInstance(row.data(0, Qt.UserRole), BuilderStep)
        self.assertEqual(
            [r.text(COL_STEP) for r in self._rows()], ["A01", "A01_2", "A02", "A03"]
        )
        self.assertEqual(self._children(row), ["LOCK_B"])
        self.assertTrue(self.ctrl._step_is_built("A01_2"))
        self.assertEqual(self.ctrl._orphan_shots(), [])

    def test_pairing_a_shot_with_a_step_binds_it(self):
        self._load()
        before = len(self.store._boundary_undo)
        self._menu(self._row("A01_2"), pick="A03")
        self.assertEqual(len(self.store._boundary_undo), before + 1, "one undo")
        self.assertEqual(self.store.shot_by_name("A01_2").metadata["step"], "A03")
        self.assertEqual(self.ctrl._pairing().shots["A03"].name, "A01_2")
        self.assertEqual(
            [r.text(COL_STEP) for r in self._rows()], ["A01", "A02", "A03"]
        )
        self.assertEqual(self._children(self._row("A03")), ["obj_A03", "LOCK_B"])

    def test_a_step_with_no_shot_pairs_with_a_free_shot(self):
        self._load()
        self._menu(self._row("A03"), pick="A01_2")
        self.assertEqual(self.ctrl._pairing().shots["A03"].name, "A01_2")

    def test_a_section_locks_and_its_rows_take_no_edits_until_unlocked(self):
        self._load()
        self._menu(self._row("A01"), pick="Lock Section 'A'")
        self.assertTrue(self.store.shot_by_name("A01_1").locked)
        self.assertTrue(self.store.shot_by_name("A02").locked)
        self.assertFalse(self.store.shot_by_name("A01_2").locked, "not a step's")
        self.assertTrue(self.ctrl._editor.begin_edit(self._row("A01"), COL_STEP))
        self.assertIsNone(self.ctrl._editor._pending, "no editor opened")
        self.assertIn("locked", self.ui.footer.setText.call_args[0][0])
        self.assertNotIn("Hide 'obj_A01'", self._menu(self._row("A01").child(0)))
        self._menu(self._row("A01"), pick="Unlock 'A01'")
        self.assertFalse(self.store.shot_by_name("A01_1").locked)
        self.assertTrue(self.ctrl._editor.begin_edit(self._row("A01"), COL_STEP))
        self.assertIsNotNone(self.ctrl._editor._pending)

    def test_a_shots_members_take_no_local_edits(self):
        self._load()
        keys = self._row("A01").child(1)
        self.assertEqual(keys.text(COL_DESC), "KEYS")
        self.assertFalse(self.ctrl._editor.begin_edit(keys, COL_DESC))
        self.assertNotIn("Hide 'KEYS'", self._menu(keys))

    def test_hiding_waits_for_a_step_to_have_a_shot(self):
        """Kept on per user, Hide Missing Shots would empty the table of a
        fresh sheet -- every step is still to build; it applies once one has a
        shot."""
        for shot in list(self.store.shots):
            self.store.remove_shot(shot.shot_id)
        self.ctrl._settings.setValue("hide_missing", True)
        self._load()
        self.assertEqual(
            [r.text(COL_STEP) for r in self._rows()], ["A01", "A02", "A03"]
        )
        self.assertEqual(len(self.ctrl._shown_steps()), 3)

    @patch("mayatk.anim_utils.shots.shot_manifest.manifest_host.ShotManifest")
    def test_hidden_missing_shots_are_neither_shown_nor_built(self, mock_cls):
        """("we should be able to hide missing shots in the manifest")"""
        self._load()
        self.ctrl._set_hide_missing(True)
        self.assertEqual(
            [r.text(COL_STEP) for r in self._rows()], ["A01", "A01_2", "A02"]
        )
        builder = MagicMock()
        builder.sync.return_value = ({}, {"applied": [], "skipped": []}, [])
        builder.plan_insertions.return_value = []
        mock_cls.return_value = builder
        _mock_undo_info(self)
        self.ctrl.build()
        built = [s.step_id for s in builder.sync.call_args.args[0]]
        self.assertEqual(built, ["A01", "A02"], "A03 has no shot: hidden, not built")


class TestReapplyReportsWhatItDid(unittest.TestCase, _ControllerHarness):
    """An object row's Apply -- and Render Effects' focused Key, which runs
    it -- says when nothing was re-applied.

    Bug: ``_reapply_behavior`` ignored ``reapply_object``'s ``False`` (the
    object missing or ambiguous) and re-assessed as if it had worked, so the
    user got no message; the focused Key's ``apply`` then reported
    "Re-applied ..." whatever happened, leaving Render Effects' error branch
    dead.
    Fixed: 2026-10-04
    """

    def setUp(self):
        self.setup_controller()
        self.store = _fresh_store()
        self.store.define_shot("A01", 1, 40)
        self.ctrl._store = self.store
        self.ctrl._steps = _make_steps("A01")
        self.obj = BuilderObject(name="ghost", behaviors=["fade_in"])
        self.builder = MagicMock()
        for p in (
            patch.object(self.ctrl, "_manifest", return_value=self.builder),
            patch.object(self.ctrl, "assess"),
        ):
            p.start()
            self.addCleanup(p.stop)
        _mock_undo_info(self)

    def _footer(self) -> str:
        return self.ctrl.ui.footer.setText.call_args.args[0]

    def test_nothing_reapplied_is_said_and_not_reassessed(self):
        self.builder.reapply_object.return_value = False
        with patch.object(
            self.store, "resolve_member", return_value=("ghost", "missing")
        ):
            self.assertIs(self.ctrl._reapply_behavior("A01", self.obj), False)
        self.ctrl.assess.assert_not_called()
        self.assertIn("'ghost' is not in the scene", self._footer())

    def test_an_ambiguous_object_is_named_as_such(self):
        self.builder.reapply_object.return_value = False
        with patch.object(
            self.store, "resolve_member", return_value=("ghost", "ambiguous")
        ):
            self.assertIs(self.ctrl._reapply_behavior("A01", self.obj), False)
        self.assertIn("several scene objects are named 'ghost'", self._footer())

    def test_a_reapply_reassesses_and_says_it_did(self):
        self.builder.reapply_object.return_value = True
        self.assertIs(self.ctrl._reapply_behavior("A01", self.obj), True)
        self.ctrl.assess.assert_called_once_with(skip_key_check=True)

    def test_an_unbuilt_step_says_build_first(self):
        self.assertIs(self.ctrl._reapply_behavior("A02", self.obj), False)
        self.assertIn("build first", self._footer())
        self.builder.reapply_object.assert_not_called()

    def test_the_focused_key_raises_when_nothing_was_reapplied(self):
        focus = self.ctrl.sb.get_slots_instance.return_value.focus
        self.ctrl._open_effect("A01", self.obj, "opacity")
        apply = focus.call_args.kwargs["apply"]

        self.builder.reapply_object.return_value = False
        with self.assertRaises(RuntimeError) as ctx:
            apply()
        self.assertIn("ghost", str(ctx.exception))

        self.builder.reapply_object.return_value = True
        self.assertEqual(apply(), "Re-applied ghost's behaviors in A01.")


# ---------------------------------------------------------------------------
# Tests: audio objects excluded from shot.objects
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests: assess() audio_status
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests: default_behaviors pipeline stage
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests: default_duration / resolve_ranges with no animation
# ---------------------------------------------------------------------------


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


class TestRefreshState(unittest.TestCase):
    """ShotsController.refresh_state() centralizes widget enable/disable."""

    def setUp(self):
        from mayatk.anim_utils.shots.shots_slots import ShotsController

        _fresh_store()
        self.ctrl = object.__new__(ShotsController)
        # Mock UI widgets
        self.ctrl.ui = MagicMock()
        self.ctrl.ui.cmb_detection_mode = MagicMock()
        self.ctrl.ui.spn_detection = MagicMock()
        self.ctrl.ui.btn_apply_gap = MagicMock()

    def test_no_shots_detection_relevant(self):
        """With no shots and animation, detection widgets are enabled."""
        store = _fresh_store()
        store.detection_mode = "auto"
        with patch.object(ShotStore, "has_animation", return_value=True):
            self.ctrl.refresh_state()
        self.ctrl.ui.cmb_detection_mode.setEnabled.assert_called_with(True)
        self.ctrl.ui.spn_detection.setEnabled.assert_called_with(True)
        self.ctrl.ui.btn_apply_gap.setEnabled.assert_called_with(False)

    def test_with_shots_detection_disabled(self):
        """With existing shots, detection widgets are disabled."""
        store = _fresh_store()
        store.define_shot("S01", 0, 100, ["obj"])
        with patch.object(ShotStore, "has_animation", return_value=True):
            self.ctrl.refresh_state()
        self.ctrl.ui.cmb_detection_mode.setEnabled.assert_called_with(False)
        self.ctrl.ui.spn_detection.setEnabled.assert_called_with(False)
        self.ctrl.ui.btn_apply_gap.setEnabled.assert_called_with(True)

    def test_auto_no_animation_disables_threshold(self):
        """In auto mode with no animation, min gap spinner is disabled."""
        store = _fresh_store()
        store.detection_mode = "auto"
        with patch.object(ShotStore, "has_animation", return_value=False):
            self.ctrl.refresh_state()
        self.ctrl.ui.cmb_detection_mode.setEnabled.assert_called_with(True)
        self.ctrl.ui.spn_detection.setEnabled.assert_called_with(False)

    def test_zero_as_end_disables_threshold(self):
        """zero_as_end mode always disables the min gap spinner."""
        store = _fresh_store()
        store.detection_mode = "zero_as_end"
        with patch.object(ShotStore, "has_animation", return_value=True):
            self.ctrl.refresh_state()
        self.ctrl.ui.spn_detection.setEnabled.assert_called_with(False)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
