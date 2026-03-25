#!/usr/bin/env python
# coding=utf-8
"""Performance regression tests for the shot sequencer.

Simulates the scale of the C130H scene (74 shots, ~100 objects,
~389 segments) using fully-mocked Maya to ensure that the controller
and widget can rebuild in acceptable time.

These tests do NOT require a running Maya instance.

IMPORTANT: This file reuses the mock objects from test_sequencer_controller
to avoid cross-file sys.modules pollution when pytest collects both files.
"""
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Import shared mocks from conftest (injected into sys.modules there)
from test.conftest import mock_pm, mock_cmds, mock_undo_chunk

_mock_pm = mock_pm
_mock_cmds = mock_cmds
_undo_chunk = mock_undo_chunk

from qtpy import QtWidgets

_app = QtWidgets.QApplication.instance()
if _app is None:
    _app = QtWidgets.QApplication(sys.argv)

from uitk.widgets.sequencer._sequencer import SequencerWidget
from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
    ShotSequencerController,
)


# ---------------------------------------------------------------------------
# C130H-scale data generators
# ---------------------------------------------------------------------------

# The real C130H scene: ~698 transforms, 145 anim curves, 74 shot regions,
# ~389 total segments across ~100 animated objects.


def _generate_c130h_shots(n_shots=74, n_objects_per_shot=5, total_objects=100):
    """Generate shot definitions and segment data at C130H scale."""
    # Generate unique object names
    all_objects = [f"xform_{i:03d}" for i in range(total_objects)]

    shot_defs = []
    current_frame = 1.0
    for i in range(n_shots):
        duration = 40.0 + (i % 7) * 10  # 40-100 frame shots
        gap = 5.0 + (i % 3) * 2  # 5-9 frame gaps
        start = current_frame
        end = start + duration
        # Each shot references a rotating subset of objects
        offset = (i * 3) % total_objects
        objs = [all_objects[(offset + j) % total_objects] for j in range(n_objects_per_shot)]
        shot_defs.append((f"Shot_{i:02d}", start, end, objs))
        current_frame = end + gap

    return shot_defs, all_objects


def _generate_segments_for_shot(shot_def, n_segments_per_obj=2):
    """Generate segment dicts for a shot definition."""
    name, start, end, objects = shot_def
    segments = []
    for obj in objects:
        seg_duration = (end - start) / (n_segments_per_obj + 1)
        for s in range(n_segments_per_obj):
            seg_start = start + s * seg_duration
            seg_end = seg_start + seg_duration * 0.8
            segments.append({
                "obj": obj,
                "start": seg_start,
                "end": seg_end,
                "duration": seg_end - seg_start,
                "is_stepped": s % 3 == 2,
                "curves": [f"{obj}_tx", f"{obj}_ty"],
                "attr": None,
            })
    return segments


from test.test_sequencer_controller import FakeSlotsInstance  # noqa: E402


# ---------------------------------------------------------------------------
# Performance tests
# ---------------------------------------------------------------------------


class TestSequencerPerf(unittest.TestCase):
    """Performance regression tests at C130H scene scale.

    Each test asserts that the operation completes under a generous
    time budget.  These budgets represent *unacceptable* upper bounds —
    a fresh run on any modern machine should be well under them.
    """

    # Generous budgets (seconds) — real performance should be much faster
    SYNC_BUDGET = 2.0       # Full _sync_to_widget
    DECOR_BUDGET = 0.5      # _rebuild_decoration only
    CONTENT_BUDGET = 1.5    # _rebuild_content only
    SUBROWS_BUDGET = 0.3    # sub_row_provider for one track

    @classmethod
    def setUpClass(cls):
        cls.shot_defs, cls.all_objects = _generate_c130h_shots()
        cls.segments_by_shot_id = {}

    def _make_controller(self, shot_defs=None, initial_idx=0):
        """Build a fully-wired controller from shot definitions."""
        if shot_defs is None:
            shot_defs = self.shot_defs

        _mock_pm.reset_mock()
        _mock_cmds.reset_mock()
        _mock_pm.objExists.return_value = True
        _mock_pm.playbackOptions.return_value = 0.0
        _mock_pm.currentTime.return_value = 1.0
        _mock_pm.scriptJob.return_value = 999
        _mock_pm.scriptJob.side_effect = lambda **kw: 999 if "event" in kw else True
        _mock_pm.UndoChunk.return_value = _undo_chunk
        _mock_cmds.currentTime.return_value = 1.0
        _mock_cmds.playbackOptions.return_value = 0.0
        _mock_cmds.objExists.return_value = True
        _mock_cmds.ls.return_value = []
        _mock_cmds.listConnections.return_value = []
        _mock_cmds.listRelatives.return_value = []
        _mock_cmds.keyframe.return_value = []
        _mock_cmds.keyTangent.return_value = []

        store = ShotStore()
        for name, start, end, objs in shot_defs:
            store.define_shot(name=name, start=start, end=end, objects=objs)
        ShotStore._active = None

        sequencer = ShotSequencer(store=store)
        widget = SequencerWidget()
        slots = FakeSlotsInstance(widget)

        with patch.object(ShotSequencerController, "_register_maya_undo_callbacks"):
            with patch.object(ShotSequencerController, "_register_time_change_job"):
                with patch.object(ShotSequencerController, "_bind_store_listener"):
                    ctrl = ShotSequencerController(slots)
        ctrl.sequencer = sequencer
        ctrl._sync_combobox()

        cmb = slots.ui.cmb_shot
        if initial_idx < cmb.count():
            cmb.setCurrentIndex(initial_idx)

        # Pre-compute segments for all shots
        seg_cache = {}
        for name, start, end, objs in shot_defs:
            shot_obj = next(
                (s for s in sequencer.sorted_shots() if s.name == name), None
            )
            if shot_obj:
                seg_cache[shot_obj.shot_id] = _generate_segments_for_shot(
                    (name, start, end, objs)
                )

        sequencer.collect_object_segments = lambda sid, **kw: list(
            seg_cache.get(sid, [])
        )
        ctrl._try_load_maya_icons = staticmethod(lambda: None)

        return ctrl, widget, sequencer, seg_cache

    def test_full_sync_all_mode(self):
        """Full _sync_to_widget in 'all' mode must complete within budget.

        'All' mode renders every shot — worst-case for track/clip count.
        C130H: 74 shots, ~5 objects each, ~10 segments/shot = ~740 clips.
        """
        ctrl, widget, seq, _ = self._make_controller()
        ctrl._shot_display_mode = "all"

        t0 = time.perf_counter()
        ctrl._sync_to_widget()
        dt = time.perf_counter() - t0

        self.assertLess(
            dt, self.SYNC_BUDGET,
            f"Full sync (all mode) took {dt:.3f}s, budget={self.SYNC_BUDGET}s",
        )
        # Verify clips were actually created
        self.assertGreater(len(widget.clips()), 0, "Must produce clips")
        widget.close()
        widget.deleteLater()

    def test_full_sync_current_mode(self):
        """Full _sync_to_widget in 'current' mode (single shot visible)."""
        ctrl, widget, seq, _ = self._make_controller()
        ctrl._shot_display_mode = "current"

        t0 = time.perf_counter()
        ctrl._sync_to_widget()
        dt = time.perf_counter() - t0

        self.assertLess(
            dt, self.SYNC_BUDGET,
            f"Full sync (current mode) took {dt:.3f}s, budget={self.SYNC_BUDGET}s",
        )
        widget.close()
        widget.deleteLater()

    def test_full_sync_adjacent_mode(self):
        """Full _sync_to_widget in 'adjacent' mode (3 shots visible)."""
        ctrl, widget, seq, _ = self._make_controller(initial_idx=37)
        ctrl._shot_display_mode = "adjacent"

        t0 = time.perf_counter()
        ctrl._sync_to_widget()
        dt = time.perf_counter() - t0

        self.assertLess(
            dt, self.SYNC_BUDGET,
            f"Full sync (adjacent mode) took {dt:.3f}s, budget={self.SYNC_BUDGET}s",
        )
        widget.close()
        widget.deleteLater()

    def test_decoration_only(self):
        """_sync_decoration (overlays only) should be faster than full sync."""
        ctrl, widget, seq, _ = self._make_controller()
        ctrl._shot_display_mode = "all"
        ctrl._sync_to_widget()  # Warm up

        t0 = time.perf_counter()
        ctrl._sync_decoration()
        dt = time.perf_counter() - t0

        self.assertLess(
            dt, self.DECOR_BUDGET,
            f"Decoration rebuild took {dt:.3f}s, budget={self.DECOR_BUDGET}s",
        )
        widget.close()
        widget.deleteLater()

    def test_gap_overlay_count_at_scale(self):
        """All 73 gaps between 74 shots should generate overlays."""
        ctrl, widget, seq, _ = self._make_controller()
        ctrl._shot_display_mode = "current"
        ctrl._sync_to_widget()

        # With 74 shots and gaps between each consecutive pair, we expect 73
        n_overlays = len(widget._gap_overlays)
        self.assertEqual(
            n_overlays, len(self.shot_defs) - 1,
            f"Expected {len(self.shot_defs)-1} gap overlays, got {n_overlays}",
        )
        widget.close()
        widget.deleteLater()

    def test_repeated_sync_stability(self):
        """Multiple rapid syncs should not degrade over time.

        Verifies no resource leak: 10 full rebuilds should each stay
        within the time budget.
        """
        ctrl, widget, seq, _ = self._make_controller()
        ctrl._shot_display_mode = "all"

        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            ctrl._sync_to_widget()
            times.append(time.perf_counter() - t0)

        avg = sum(times) / len(times)
        worst = max(times)
        self.assertLess(
            worst, self.SYNC_BUDGET * 1.5,
            f"Worst of 10 syncs: {worst:.3f}s (avg={avg:.3f}s)",
        )
        # Check no significant degradation (last 3 vs first 3)
        early = sum(times[:3]) / 3
        late = sum(times[-3:]) / 3
        ratio = late / max(early, 0.001)
        self.assertLess(
            ratio, 2.0,
            f"Performance degraded: early avg={early:.3f}s, late avg={late:.3f}s",
        )
        widget.close()
        widget.deleteLater()

    def test_track_count_at_scale(self):
        """Correct number of tracks created for C130H-size scene."""
        ctrl, widget, seq, seg_cache = self._make_controller()
        ctrl._shot_display_mode = "all"
        ctrl._sync_to_widget()

        # Count unique objects across all visible segments
        all_seg_objs = set()
        for segs in seg_cache.values():
            all_seg_objs.update(s["obj"] for s in segs)
        # Also objects from shot definitions
        for s in seq.sorted_shots():
            all_seg_objs.update(s.objects)

        n_tracks = len(list(widget.tracks()))
        self.assertGreater(
            n_tracks, 0,
            "Must create tracks for animated objects",
        )
        self.assertLessEqual(
            n_tracks, len(all_seg_objs) + 10,
            "Track count shouldn't wildly exceed unique objects",
        )
        widget.close()
        widget.deleteLater()

    def test_sub_row_provider_at_scale(self):
        """Sub-row provider for attribute expansion should be fast.

        Mocks the cmds calls and verifies the provider returns quickly
        for a single track even with many curves.
        """
        ctrl, widget, seq, seg_cache = self._make_controller()
        ctrl._shot_display_mode = "current"
        ctrl._sync_to_widget()

        # Find a track to expand
        tracks = list(widget.tracks())
        if not tracks:
            self.skipTest("No tracks created")

        track = tracks[0]
        obj_name = track.name

        # Mock attribute curves for this object
        fake_attrs = {
            f"{obj_name}.translateX": [f"{obj_name}_tx"],
            f"{obj_name}.translateY": [f"{obj_name}_ty"],
            f"{obj_name}.translateZ": [f"{obj_name}_tz"],
            f"{obj_name}.rotateX": [f"{obj_name}_rx"],
            f"{obj_name}.rotateY": [f"{obj_name}_ry"],
            f"{obj_name}.rotateZ": [f"{obj_name}_rz"],
            f"{obj_name}.scaleX": [f"{obj_name}_sx"],
            f"{obj_name}.scaleY": [f"{obj_name}_sy"],
            f"{obj_name}.scaleZ": [f"{obj_name}_sz"],
        }

        def _list_conns(node=None, **kw):
            if kw.get("type") == "animCurve" and kw.get("s"):
                return list(fake_attrs.get(node, []))
            if kw.get("plugs") and kw.get("d"):
                for attr, curves in fake_attrs.items():
                    if node in curves:
                        return [attr]
                return []
            return []

        _mock_cmds.listConnections.side_effect = _list_conns
        _mock_cmds.objExists.return_value = True
        _mock_cmds.keyframe.return_value = [1.0, 10.0, 20.0, 30.0]
        _mock_cmds.keyTangent.return_value = ["spline", "spline", "step", "spline"]

        t0 = time.perf_counter()
        sub_rows = ctrl._provide_sub_rows(track.track_id, track.name)
        dt = time.perf_counter() - t0

        self.assertLess(
            dt, self.SUBROWS_BUDGET,
            f"Sub-row provider took {dt:.3f}s for {obj_name}, budget={self.SUBROWS_BUDGET}s",
        )
        widget.close()
        widget.deleteLater()

    def test_shot_switching_speed(self):
        """Switching between shots should rebuild quickly.

        Simulates user clicking through shots in the combo box.
        """
        ctrl, widget, seq, _ = self._make_controller()
        ctrl._shot_display_mode = "current"
        ctrl._sync_to_widget()

        # Switch through 10 shots
        cmb = ctrl.ui.cmb_shot
        times = []
        for i in range(0, min(10, cmb.count())):
            cmb.setCurrentIndex(i)
            t0 = time.perf_counter()
            ctrl._sync_to_widget()
            times.append(time.perf_counter() - t0)

        avg = sum(times) / len(times)
        worst = max(times)
        self.assertLess(
            worst, self.SYNC_BUDGET,
            f"Worst shot switch: {worst:.3f}s (avg={avg:.3f}s)",
        )
        widget.close()
        widget.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
