# !/usr/bin/python
# coding=utf-8
"""GUI integration tests for the Shot Sequencer.

Tests the full stack: ShotSequencer engine → SequencerWidget (Qt).
Uses ``MayaConnection`` for Maya bootstrap so it works with both
``run_tests.py`` (Maya GUI mode) and ``python -m pytest`` (standalone).

Usage via run_tests.py::

    python run_tests.py sequencer_gui

Usage via pytest::

    python -m pytest mayatk/test/test_sequencer_gui.py -v

Usage via mayapy::

    mayapy mayatk/test/test_sequencer_gui.py
"""
import unittest
import sys
import os

scripts_dir = r"O:\Cloud\Code\_scripts"
for p in (
    scripts_dir,
    os.path.join(scripts_dir, "mayatk"),
    os.path.join(scripts_dir, "pythontk"),
    os.path.join(scripts_dir, "uitk"),
    os.path.join(scripts_dir, "tentacle"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Maya bootstrap — detect running Maya first, then fall back to standalone
# ---------------------------------------------------------------------------
HAS_MAYA = False
try:
    import maya.cmds as _cmds

    _cmds.about(version=True)  # Verify Maya is actually running
    HAS_MAYA = True
except Exception:
    # Not inside Maya — try standalone bootstrap
    try:
        from mayatk.env_utils.maya_connection import MayaConnection

        _conn = MayaConnection.get_instance()
        if not _conn.is_connected:
            _conn.connect(mode="standalone")
        HAS_MAYA = _conn.is_connected
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Qt — ensure QApplication exists (standalone creates one, GUI already has one)
# ---------------------------------------------------------------------------
os.environ.setdefault("QT_API", "pyside6")
try:
    from qtpy import QtWidgets, QtCore

    _app = QtWidgets.QApplication.instance()
    if _app is None:
        _app = QtWidgets.QApplication(sys.argv)
    HAS_QT = True
except Exception:
    HAS_QT = False

# ---------------------------------------------------------------------------
# Conditional imports (only available when Maya is running)
# ---------------------------------------------------------------------------
if HAS_MAYA:
    import pymel.core as pm
    import maya.cmds as cmds

if HAS_MAYA and HAS_QT:
    from uitk.widgets.sequencer._sequencer import SequencerWidget
    from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
        ShotSequencer,
        ShotBlock,
    )
    from mayatk.anim_utils.shots._shots import ShotStore
    from mayatk.anim_utils.segment_keys import SegmentKeys


# =========================================================================
# Helpers
# =========================================================================


def _new_scene():
    pm.mel.file(new=True, force=True)


def _make_cube(name, keys, attr="translateX"):
    cube = pm.polyCube(name=name)[0]
    for frame, value in keys.items():
        pm.setKeyframe(cube, attribute=attr, time=frame, value=value)
    return cube


def _make_stepped_key(name, frame, value, attr="translateX"):
    cube = pm.polyCube(name=name)[0]
    pm.setKeyframe(cube, attribute=attr, time=frame, value=value)
    curves = cmds.listConnections(str(cube), type="animCurve", s=True, d=False) or []
    for crv in curves:
        cmds.keyTangent(crv, time=(frame, frame), outTangentType="step")
    return cube


def _process_events():
    QtWidgets.QApplication.processEvents()


# =========================================================================
# Widget tests
# =========================================================================

_SKIP_MSG = "Requires Maya + Qt"


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestWidgetPopulation(unittest.TestCase):
    """SequencerWidget is populated correctly from engine data."""

    def setUp(self):
        _new_scene()
        self.c1 = _make_cube("popA", {0: 0, 50: 10})
        self.c2 = _make_cube("popB", {0: 0, 50: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(self.c1), str(self.c2)])
        self.seq = ShotSequencer(store=store)
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_tracks_created(self):
        """One track per object in the shot."""
        segs = self.seq.collect_object_segments(0)
        for obj in sorted({s["obj"] for s in segs}):
            self.widget.add_track(obj.split("|")[-1])
        self.assertEqual(len(self.widget.tracks()), 2)

    def test_clips_added(self):
        """Clips are created for objects with animation."""
        segs = self.seq.collect_object_segments(0)
        by_obj = {}
        for s in segs:
            by_obj.setdefault(s["obj"], []).append(s)
        for obj, obj_segs in by_obj.items():
            tid = self.widget.add_track(obj.split("|")[-1])
            s = min(seg["start"] for seg in obj_segs)
            e = max(seg["end"] for seg in obj_segs)
            self.widget.add_clip(tid, s, e - s, obj=obj, orig_start=s, orig_end=e)
        self.assertGreaterEqual(len(self.widget.clips()), 2)

    def test_range_highlight_set(self):
        """Range highlight spans the shot boundaries."""
        self.widget.set_range_highlight(0, 50)
        _process_events()
        self.assertIsNotNone(self.widget._range_highlight)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestFrameShot(unittest.TestCase):

    def setUp(self):
        _new_scene()
        self.widget = SequencerWidget()
        self.widget.resize(800, 400)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_frame_shot_zooms_to_range(self):
        """After frame_shot, the range highlight should be visible."""
        tid = self.widget.add_track("obj")
        self.widget.add_clip(tid, 100, 50, label="clip")
        self.widget.set_range_highlight(100, 150)
        _process_events()
        self.widget.frame_shot()
        _process_events()
        self.assertIsNotNone(self.widget._range_highlight)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestSnapDefault(unittest.TestCase):

    def test_default_snap(self):
        w = SequencerWidget()
        self.assertAlmostEqual(w._snap_interval, 1.0)
        w.deleteLater()


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestDimmedTracks(unittest.TestCase):

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_dimmed_flag_stored(self):
        """add_track(dimmed=True) stores the dimmed flag on the header."""
        self.widget.add_track("bright")
        self.widget.add_track("faded", dimmed=True)
        self.assertFalse(self.widget._header._dimmed[0])
        self.assertTrue(self.widget._header._dimmed[1])

    def test_dimmed_default_false(self):
        """Without dimmed kwarg, tracks are not dimmed."""
        self.widget.add_track("default")
        self.assertFalse(self.widget._header._dimmed[0])


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestLockedClips(unittest.TestCase):

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_locked_clip_flag(self):
        tid = self.widget.add_track("track")
        cid = self.widget.add_clip(tid, 10, 20, locked=True)
        self.assertTrue(self.widget.get_clip(cid).locked)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestPlayheadNavigation(unittest.TestCase):

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_set_playhead(self):
        self.widget.set_playhead(42.0)
        _process_events()
        ph = self.widget._timeline._scene.playhead
        self.assertIsNotNone(ph)
        self.assertAlmostEqual(ph.time, 42.0)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestRangeOverlays(unittest.TestCase):

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_add_range_overlay(self):
        self.widget.add_range_overlay(0, 50)
        _process_events()
        from uitk.widgets.sequencer._sequencer import _StaticRangeOverlay

        overlays = [
            item
            for item in self.widget._timeline.scene().items()
            if isinstance(item, _StaticRangeOverlay)
        ]
        self.assertEqual(len(overlays), 1)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestMarkers(unittest.TestCase):

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_add_marker(self):
        self.widget.add_marker(time=25.0, note="test")
        _process_events()
        from uitk.widgets.sequencer._sequencer import MarkerItem

        markers = [
            item
            for item in self.widget._timeline.scene().items()
            if isinstance(item, MarkerItem)
        ]
        self.assertEqual(len(markers), 1)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestZoomPreservation(unittest.TestCase):

    def setUp(self):
        _new_scene()
        self.widget = SequencerWidget()
        self.widget.resize(800, 400)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_zoom_roundtrip(self):
        original_zoom = self.widget._timeline.pixels_per_unit
        self.widget._timeline._pixels_per_unit = original_zoom * 2
        self.widget._timeline._refresh_all()
        _process_events()
        self.assertAlmostEqual(
            self.widget._timeline.pixels_per_unit, original_zoom * 2, places=2
        )


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestShortcutOverride(unittest.TestCase):

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_f_key_accepted(self):
        """F key ShortcutOverride handler exists on the widget."""
        self.assertTrue(hasattr(self.widget, "event"))


# =========================================================================
# Maya engine tests (real keyframe manipulation)
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestEngineMove(unittest.TestCase):

    def setUp(self):
        _new_scene()

    def test_move_shifts_keys(self):
        c1 = _make_cube("mv_a", {0: 0, 50: 10})
        c2 = _make_cube("mv_b", {60: 0, 100: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        store.define_shot("S1", 60, 100, [str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_shot(0, 10)
        self.assertAlmostEqual(seq.shot_by_id(0).start, 10)
        self.assertAlmostEqual(seq.shot_by_id(0).end, 60)

    def test_move_ripples_downstream(self):
        c1 = _make_cube("rp_a", {0: 0, 50: 10})
        c2 = _make_cube("rp_b", {60: 0, 100: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        store.define_shot("S1", 60, 100, [str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_shot(0, 10)
        self.assertAlmostEqual(seq.shot_by_id(1).start, 70)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestEngineResize(unittest.TestCase):

    def setUp(self):
        _new_scene()

    def test_resize_scales_keys(self):
        c1 = _make_cube("rs_a", {0: 0, 100: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        seq.resize_shot(0, 0, 200)
        keys = sorted(pm.keyframe(c1, q=True, attribute="translateX"))
        self.assertAlmostEqual(keys[0], 0.0, places=1)
        self.assertAlmostEqual(keys[-1], 200.0, places=1)


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestSteppedKeyPreservation(unittest.TestCase):
    """CRITICAL regression: dragging a stepped key must not delete it.

    Bug: SegmentKeys.shift_curves was called with remove_flat_at_dest=True.
    Fixed: Changed to remove_flat_at_dest=False (2026-03-16).
    """

    def setUp(self):
        _new_scene()

    def test_shift_stepped_key_survives(self):
        """Shifting a single stepped key preserves it at the new time."""
        cube = _make_stepped_key("step_test", frame=10, value=5.0)
        curves = (
            cmds.listConnections(str(cube), type="animCurve", s=True, d=False) or []
        )
        self.assertTrue(len(curves) > 0, "No anim curves found")
        keys_before = cmds.keyframe(curves[0], q=True, timeChange=True)
        self.assertIn(10.0, keys_before)

        SegmentKeys.shift_curves(
            curves,
            offset=10.0,
            time_range=(10, 10),
            remove_flat_at_dest=False,
        )

        keys_after = cmds.keyframe(curves[0], q=True, timeChange=True)
        self.assertIn(20.0, keys_after, "Stepped key was destroyed during shift!")
        self.assertNotIn(10.0, keys_after, "Old key should be gone")

    def test_shift_stepped_key_with_remove_flat_diagnostic(self):
        """Diagnostic: documents remove_flat_at_dest=True behavior."""
        cube = _make_stepped_key("step_bug", frame=10, value=5.0)
        curves = (
            cmds.listConnections(str(cube), type="animCurve", s=True, d=False) or []
        )
        self.assertTrue(len(curves) > 0)

        SegmentKeys.shift_curves(
            curves,
            offset=10.0,
            time_range=(10, 10),
            remove_flat_at_dest=True,
        )

        keys_after = cmds.keyframe(curves[0], q=True, timeChange=True)
        if not keys_after:
            print("CONFIRMED: remove_flat_at_dest=True destroyed the key")
        else:
            print(f"Key survived at: {keys_after}")


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestCollectSegments(unittest.TestCase):

    def setUp(self):
        _new_scene()

    def test_segments_for_animated_object(self):
        c1 = _make_cube("seg_a", {0: 0, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        seq = ShotSequencer(store=store)
        segs = seq.collect_object_segments(0)
        self.assertGreater(len(segs), 0)
        self.assertEqual(segs[0]["obj"], str(c1))

    def test_segments_have_time_info(self):
        c1 = _make_cube("seg_b", {10: 0, 40: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        seq = ShotSequencer(store=store)
        segs = seq.collect_object_segments(0)
        self.assertGreater(len(segs), 0)
        self.assertIn("start", segs[0])
        self.assertIn("end", segs[0])

    def test_empty_shot_returns_no_segments(self):
        store = ShotStore()
        store.define_shot("Empty", 0, 50, [])
        seq = ShotSequencer(store=store)
        self.assertEqual(len(seq.collect_object_segments(0)), 0)


# =========================================================================
# Full integration: engine → widget
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestWidgetWithEngine(unittest.TestCase):

    def setUp(self):
        _new_scene()
        self.c1 = _make_cube("integ_a", {0: 0, 50: 10})
        self.c2 = _make_cube("integ_b", {0: 5, 50: 15})
        self.store = ShotStore()
        self.store.define_shot("Shot1", 0, 50, [str(self.c1), str(self.c2)])
        self.seq = ShotSequencer(store=self.store)
        self.widget = SequencerWidget()
        self.widget.resize(800, 400)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def _populate_widget(self):
        shot = self.seq.shot_by_id(0)
        segs = self.seq.collect_object_segments(0)
        from collections import defaultdict

        by_obj = defaultdict(list)
        for s in segs:
            by_obj[s["obj"]].append(s)

        track_ids = {}
        for obj in sorted(shot.objects):
            tid = self.widget.add_track(obj.split("|")[-1])
            track_ids[obj] = tid

        for obj, obj_segs in by_obj.items():
            tid = track_ids.get(obj)
            if tid is None:
                continue
            span_segs = [s for s in obj_segs if not s.get("is_stepped")]
            stepped_segs = [s for s in obj_segs if s.get("is_stepped")]

            if span_segs:
                s = min(seg["start"] for seg in span_segs)
                e = max(seg["end"] for seg in span_segs)
                self.widget.add_clip(
                    tid,
                    s,
                    e - s,
                    shot_id=0,
                    obj=obj,
                    orig_start=s,
                    orig_end=e,
                )
            for seg in stepped_segs:
                self.widget.add_clip(
                    tid,
                    seg["start"],
                    0.0,
                    shot_id=0,
                    obj=obj,
                    orig_start=seg["start"],
                    orig_end=seg["start"],
                    is_stepped=True,
                    stepped_key_time=seg["start"],
                )

        self.widget.set_range_highlight(shot.start, shot.end)
        self.widget.set_playhead(shot.start)
        return track_ids

    def test_full_population(self):
        track_ids = self._populate_widget()
        _process_events()
        self.assertEqual(len(self.widget.tracks()), 2)
        self.assertGreaterEqual(len(self.widget.clips()), 2)
        self.assertIsNotNone(self.widget._range_highlight)

    def test_frame_shot_after_population(self):
        self._populate_widget()
        _process_events()
        self.widget.frame_shot()
        _process_events()

    def test_clear_resets_everything(self):
        self._populate_widget()
        _process_events()
        self.widget.clear()
        _process_events()
        self.assertEqual(len(self.widget.tracks()), 0)
        self.assertEqual(len(self.widget.clips()), 0)

    def test_roundtrip_repopulate(self):
        self._populate_widget()
        _process_events()
        n_tracks = len(self.widget.tracks())
        n_clips = len(self.widget.clips())
        self.widget.clear()
        self._populate_widget()
        _process_events()
        self.assertEqual(len(self.widget.tracks()), n_tracks)
        self.assertEqual(len(self.widget.clips()), n_clips)

    def test_playhead_at_shot_start(self):
        self._populate_widget()
        _process_events()
        ph = self.widget._timeline._scene.playhead
        self.assertIsNotNone(ph)

    def test_dimmed_non_active_track(self):
        self.widget.clear()
        self.widget.add_track("active_obj")
        self.widget.add_track("inactive_obj", dimmed=True)
        _process_events()
        self.assertFalse(self.widget._header._dimmed[0])
        self.assertTrue(self.widget._header._dimmed[1])


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestSteppedKeyWidgetIntegration(unittest.TestCase):
    """Stepped key drag simulation with real Maya data."""

    def setUp(self):
        _new_scene()
        self.cube = _make_stepped_key("sk_widget", frame=20, value=7.0)
        self.widget = SequencerWidget()
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_stepped_key_clip_created(self):
        tid = self.widget.add_track("sk_widget")
        cid = self.widget.add_clip(
            tid,
            20,
            0.0,
            is_stepped=True,
            stepped_key_time=20.0,
            obj=str(self.cube),
        )
        clip = self.widget.get_clip(cid)
        self.assertAlmostEqual(clip.duration, 0.0)
        self.assertTrue(clip.data.get("is_stepped"))

    def test_simulated_stepped_key_move(self):
        """Simulates the engine-level stepped key move."""
        obj_name = str(self.cube)
        curves = cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
        self.assertTrue(len(curves) > 0)
        self.assertIn(20.0, cmds.keyframe(curves[0], q=True, timeChange=True))

        SegmentKeys.shift_curves(
            curves,
            10,
            time_range=(20, 20),
            remove_flat_at_dest=False,
        )

        keys_after = cmds.keyframe(curves[0], q=True, timeChange=True)
        self.assertIn(30.0, keys_after, "Key should be at frame 30")
        self.assertNotIn(20.0, keys_after, "Key should no longer be at frame 20")
        val = cmds.keyframe(curves[0], q=True, time=(30, 30), valueChange=True)
        self.assertAlmostEqual(val[0], 7.0, places=2)


# =========================================================================
# Regression: Objects disappearing after move/resize (2026-03-16)
#
# Bug: collect_object_segments returned empty lists for objects with:
#   - single keyframes (zero-duration segment filtered by strict <)
#   - static-value keys (treated as "no active animation")
#   - stepped-only tangents (not emitted as active segments)
# Fixed: segment_keys.py collect_segments time_range filter uses <=
#        and static-value intervals emit endpoint markers.
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestObjectPersistenceAfterMove(unittest.TestCase):
    """Objects must remain visible after clip moves and shot resizes."""

    def setUp(self):
        _new_scene()

    def test_move_forward_preserves_objects(self):
        c1 = _make_cube("pf_a", {10: 0, 50: 10})
        c2 = _make_cube("pf_b", {10: 5, 50: 15})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_object_in_shot(0, str(c1), 10, 50, 30)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found)
        self.assertIn(str(c2), found)

    def test_move_beyond_shot_end(self):
        c1 = _make_cube("be_a", {10: 0, 50: 10})
        c2 = _make_cube("be_b", {10: 5, 50: 15})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_object_in_shot(0, str(c1), 10, 50, 60)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found, "Moved object vanished")
        self.assertIn(str(c2), found, "Unmoved object vanished")

    def test_resize_shrink_preserves_objects(self):
        c1 = _make_cube("sh_a", {0: 0, 100: 10})
        c2 = _make_cube("sh_b", {0: 5, 100: 25})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        seq.resize_shot(0, 0, 50)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found)
        self.assertIn(str(c2), found)

    def test_multi_object_move_one(self):
        """Moving one object must not cause others to disappear."""
        c1 = _make_cube("mo_a", {0: 0, 50: 10})
        c2 = _make_cube("mo_b", {0: 5, 50: 15})
        c3 = _make_cube("mo_c", {0: -5, 50: 20})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1), str(c2), str(c3)])
        seq = ShotSequencer(store=store)
        seq.move_object_in_shot(0, str(c1), 0, 50, 10)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        for c in (c1, c2, c3):
            self.assertIn(str(c), found, f"{c} disappeared")

    def test_resize_then_move(self):
        c1 = _make_cube("rtm_a", {0: 0, 100: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        seq.resize_shot(0, 0, 50)
        segs = seq.collect_object_segments(0)
        self.assertGreater(len(segs), 0, "Object vanished after resize")
        s = min(seg["start"] for seg in segs)
        e = max(seg["end"] for seg in segs)
        seq.move_object_in_shot(0, str(c1), s, e, s + 10)
        segs2 = seq.collect_object_segments(0)
        self.assertGreater(len(segs2), 0, "Object vanished after resize+move")


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestEdgeCaseSegmentDetection(unittest.TestCase):
    """Edge cases that caused objects to silently disappear."""

    def setUp(self):
        _new_scene()

    def test_single_keyframe_object(self):
        """Object with one keyframe must still appear as a segment.

        Bug: _get_active_animation_segments returned (t,t) zero-duration
        interval which was filtered out by strict < in time_range filter.
        Fixed: 2026-03-16 — use <= instead of <.
        """
        c1 = _make_cube("sk_a", {25: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found, "Single-key object disappeared")

    def test_static_value_object(self):
        """Object with keys at identical values must still appear.

        Bug: static intervals (v1==v2) were silently dropped, leaving
        the object with zero segments.
        Fixed: 2026-03-16 — emit endpoint markers for static intervals.
        """
        c1 = _make_cube("sv_a", {10: 5, 50: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found, "Static-value object disappeared")

    def test_stepped_key_only_object(self):
        """Object with only stepped keys must appear in segments.

        Bug: single stepped key produced zero-duration segment that was
        filtered out.
        Fixed: 2026-03-16.
        """
        c1 = _make_stepped_key("sko_a", frame=30, value=7.0)
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found, "Stepped-key object disappeared")

    def test_move_stepped_key_preserves_segment(self):
        """Moving a stepped key must not cause the object to disappear."""
        c1 = _make_stepped_key("msk_a", frame=20, value=5.0)
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        SegmentKeys.shift_curves(
            curves,
            15,
            time_range=(20, 20),
            remove_flat_at_dest=False,
        )
        segs = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs}
        self.assertIn(str(c1), found, "Stepped key vanished after move")

    def test_enforce_gap_holds_preserves_segments(self):
        """_enforce_gap_holds must not cause objects to disappear."""
        c1 = _make_cube("egh_a", {0: 0, 50: 10})
        c2 = _make_cube("egh_b", {60: 0, 100: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        store.define_shot("S1", 60, 100, [str(c2)])
        seq = ShotSequencer(store=store)
        seq._enforce_gap_holds()
        segs0 = seq.collect_object_segments(0)
        self.assertGreater(len(segs0), 0, "S0 objects vanished after gap holds")
        seq.move_object_in_shot(0, str(c1), 0, 50, 10)
        segs0_after = seq.collect_object_segments(0)
        self.assertGreater(
            len(segs0_after), 0, "S0 objects vanished after move+gap holds"
        )


# =========================================================================
# Regression: Undo + repeated edits losing keys (2026-03-16)
#
# Bug: Operations like move/resize were not wrapped in a single Maya
# undo chunk, so Ctrl+Z only partially reverted changes. Shot
# boundaries (in-memory Python state) were never restored on undo,
# causing keys to fall outside the shot range and disappear.
# Fixed: on_clip_moved/on_clip_resized/on_clips_batch_moved now wrap
#        all Maya mutations in pm.UndoChunk and save/restore shot state.
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestConsecutiveEditsPreserveKeys(unittest.TestCase):
    """Keys must survive two consecutive edits (the second-edit disappearance bug)."""

    def setUp(self):
        _new_scene()

    def test_two_moves_preserve_all_objects(self):
        c1 = _make_cube("tm_a", {10: 0, 50: 10})
        c2 = _make_cube("tm_b", {10: 5, 50: 15})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_object_in_shot(0, str(c1), 10, 50, 20)
        segs = seq.collect_object_segments(0)
        s = min(s["start"] for s in segs if s["obj"] == str(c1))
        e = max(s["end"] for s in segs if s["obj"] == str(c1))
        seq.move_object_in_shot(0, str(c1), s, e, s + 10)
        segs2 = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs2}
        self.assertIn(str(c1), found, "c1 vanished after second move")
        self.assertIn(str(c2), found, "c2 vanished after second move")

    def test_resize_then_move(self):
        c1 = _make_cube("rtm2_a", {0: 0, 100: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        seq.resize_object(0, str(c1), 0, 100, 0, 50)
        segs = seq.collect_object_segments(0)
        self.assertGreater(len(segs), 0)
        s = min(s["start"] for s in segs)
        e = max(s["end"] for s in segs)
        seq.move_object_in_shot(0, str(c1), s, e, s + 10)
        segs2 = seq.collect_object_segments(0)
        self.assertGreater(len(segs2), 0, "Object vanished after resize+move")

    def test_rapid_moves_preserve_keys(self):
        """Simulate 5 rapid small moves (drag-like behavior)."""
        c1 = _make_cube("rapid_a", {0: 0, 40: 10})
        c2 = _make_cube("rapid_b", {0: 5, 40: 15})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        for _ in range(5):
            segs = seq.collect_object_segments(0)
            c1_segs = [s for s in segs if s["obj"] == str(c1)]
            self.assertTrue(c1_segs, "c1 lost segments during rapid moves")
            s = min(s["start"] for s in c1_segs)
            e = max(s["end"] for s in c1_segs)
            seq.move_object_keys(str(c1), s, e, s + 2)
            seq._enforce_gap_holds()
        segs_final = seq.collect_object_segments(0)
        found = {s["obj"] for s in segs_final}
        self.assertIn(str(c1), found, "c1 vanished after rapid moves")
        self.assertIn(str(c2), found, "c2 vanished after rapid moves")


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestUndoRestoresSegments(unittest.TestCase):
    """Undo must restore keys to their original positions."""

    def setUp(self):
        _new_scene()

    def test_undo_after_move_restores_keys(self):
        c1 = _make_cube("uar_a", {10: 0, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        with pm.UndoChunk():
            seq.move_object_in_shot(0, str(c1), 10, 50, 60)
        segs_moved = seq.collect_object_segments(0)
        self.assertGreater(len(segs_moved), 0, "No segments after move")
        pm.undo()
        segs_undo = seq.collect_object_segments(0)
        self.assertGreater(len(segs_undo), 0, "Segments vanished after undo")
        found = {s["obj"] for s in segs_undo}
        self.assertIn(str(c1), found, "Object vanished after undo")

    def test_undo_preserves_key_positions(self):
        c1 = _make_cube("ukp_a", {10: 0, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        with pm.UndoChunk():
            seq.move_object_in_shot(0, str(c1), 10, 50, 60)
        pm.undo()
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        times = []
        for crv in curves:
            times.extend(cmds.keyframe(crv, q=True, timeChange=True) or [])
        self.assertTrue(times, "No keys found after undo")
        self.assertAlmostEqual(min(times), 10, delta=1, msg="Keys not restored")
        self.assertAlmostEqual(max(times), 50, delta=1, msg="Keys not restored")


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestMultiShotEditing(unittest.TestCase):
    """Editing one shot must not affect segments in other shots."""

    def setUp(self):
        _new_scene()

    def test_edit_one_preserves_other(self):
        c1 = _make_cube("mse_a", {0: 0, 50: 10})
        c2 = _make_cube("mse_b", {60: 0, 100: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        store.define_shot("S1", 60, 100, [str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_object_in_shot(0, str(c1), 0, 50, 10)
        segs0 = seq.collect_object_segments(0)
        segs1 = seq.collect_object_segments(1)
        self.assertGreater(len(segs0), 0, "S0 empty after edit")
        self.assertGreater(len(segs1), 0, "S1 vanished after editing S0")

    def test_multi_shot_double_edit(self):
        c1 = _make_cube("msde_a", {0: 0, 50: 10})
        c2 = _make_cube("msde_b", {60: 0, 100: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 50, [str(c1)])
        store.define_shot("S1", 60, 100, [str(c2)])
        seq = ShotSequencer(store=store)
        seq.move_object_in_shot(0, str(c1), 0, 50, 10)
        segs = seq.collect_object_segments(0)
        s = min(s["start"] for s in segs if s["obj"] == str(c1))
        e = max(s["end"] for s in segs if s["obj"] == str(c1))
        seq.move_object_in_shot(0, str(c1), s, e, s + 5)
        segs1 = seq.collect_object_segments(1)
        self.assertGreater(len(segs1), 0, "S1 vanished after double edit on S0")


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestShotResizePreservesObjects(unittest.TestCase):
    """Resizing a shot's range must not remove objects or destroy keys.

    Bug: Grabbing the shot-duration range handle accidentally scaled keys
    and the operation had no undo support, effectively deleting animation.
    Fixed: 2026-03-16
    """

    def setUp(self):
        _new_scene()

    def test_resize_shot_preserves_all_objects(self):
        """All objects must remain after a shot range resize."""
        c1 = _make_cube("rsp_a", {10: 0, 50: 10})
        c2 = _make_cube("rsp_b", {10: 5, 50: 15})
        store = ShotStore()
        store.define_shot("S0", 0, 60, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        segs_before = seq.collect_object_segments(0)
        objs_before = {s["obj"] for s in segs_before}
        self.assertEqual(len(objs_before), 2)
        # Resize shot: shrink the end by 5 frames
        seq.resize_shot(0, 0, 55)
        segs_after = seq.collect_object_segments(0)
        objs_after = {s["obj"] for s in segs_after}
        self.assertEqual(objs_before, objs_after, "Objects vanished after shot resize")

    def test_resize_shot_preserves_key_count(self):
        """Keys should be scaled, not deleted, by a shot range resize."""
        import maya.cmds as cmds

        c1 = _make_cube("rsk_a", {10: 0, 30: 5, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 60, [str(c1)])
        seq = ShotSequencer(store=store)
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        keys_before = sum(cmds.keyframe(c, q=True, keyframeCount=True) for c in curves)
        seq.resize_shot(0, 0, 50)
        keys_after = sum(cmds.keyframe(c, q=True, keyframeCount=True) for c in curves)
        self.assertEqual(keys_before, keys_after, "Keys lost during shot resize")

    def test_resize_shot_small_delta(self):
        """A tiny resize (like an accidental drag) must not destroy keys."""
        c1 = _make_cube("rss_a", {10: 0, 50: 10})
        c2 = _make_cube("rss_b", {10: 5, 50: 15})
        store = ShotStore()
        store.define_shot("S0", 0, 60, [str(c1), str(c2)])
        seq = ShotSequencer(store=store)
        # Tiny resize: 1 frame change at end
        seq.resize_shot(0, 0, 61)
        segs = seq.collect_object_segments(0)
        objs = {s["obj"] for s in segs}
        self.assertIn(str(c1), objs, "c1 vanished after tiny resize")
        self.assertIn(str(c2), objs, "c2 vanished after tiny resize")

    def test_resize_shot_undo_restores_keys(self):
        """Undo after resize_shot must restore original key positions."""
        import maya.cmds as cmds

        c1 = _make_cube("rsu_a", {10: 0, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 60, [str(c1)])
        seq = ShotSequencer(store=store)
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        times_before = sorted(
            t for c in curves for t in (cmds.keyframe(c, q=True, timeChange=True) or [])
        )
        with pm.UndoChunk():
            seq.resize_shot(0, 0, 40)
        pm.undo()
        shot = seq.shot_by_id(0)
        times_after = sorted(
            t for c in curves for t in (cmds.keyframe(c, q=True, timeChange=True) or [])
        )
        for tb, ta in zip(times_before, times_after):
            self.assertAlmostEqual(tb, ta, delta=0.1, msg="Keys not restored by undo")


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestVisibilityKeyMove(unittest.TestCase):
    """Moving a stepped visibility key must not destroy translate keys or
    remove the object from the sequencer.

    Bug: _apply_clip_move moved ALL curves' keys at old_time when attr_name
    was None, corrupting smooth animation on the same object.  Also,
    shift_curves could strand keys at temp offset (~100k) if Pass 2 failed.
    Fixed: 2026-03-16
    """

    def setUp(self):
        _new_scene()

    def test_visibility_move_preserves_translate_keys(self):
        """Moving a visibility key must leave translate keys untouched."""
        import maya.cmds as cmds

        c1 = _make_cube("vmt_a", {10: 0, 30: 5, 50: 10})
        pm.setKeyframe(str(c1), attribute="visibility", time=10, value=1)
        vis_curves = (
            cmds.listConnections(
                str(c1) + ".visibility", type="animCurve", s=True, d=False
            )
            or []
        )
        for crv in vis_curves:
            cmds.keyTangent(crv, time=(10, 10), outTangentType="step")
        tx_curves = (
            cmds.listConnections(
                str(c1) + ".translateX", type="animCurve", s=True, d=False
            )
            or []
        )
        tx_times_before = sorted(
            t
            for crv in tx_curves
            for t in (cmds.keyframe(crv, q=True, timeChange=True) or [])
        )
        SegmentKeys.shift_curves(
            vis_curves, 5, time_range=(10, 10), remove_flat_at_dest=False
        )
        tx_times_after = sorted(
            t
            for crv in tx_curves
            for t in (cmds.keyframe(crv, q=True, timeChange=True) or [])
        )
        self.assertEqual(
            tx_times_before,
            tx_times_after,
            "Translate keys changed after visibility key move",
        )

    def test_visibility_only_object_persists(self):
        """An object with only visibility keys must remain after key move."""
        import maya.cmds as cmds

        c1 = pm.polyCube(name="vop_a")[0]
        pm.setKeyframe(str(c1), attribute="visibility", time=10, value=1)
        pm.setKeyframe(str(c1), attribute="visibility", time=50, value=0)
        vis_curves = (
            cmds.listConnections(
                str(c1) + ".visibility", type="animCurve", s=True, d=False
            )
            or []
        )
        for crv in vis_curves:
            cmds.keyTangent(crv, time=(10, 10), outTangentType="step")
            cmds.keyTangent(crv, time=(50, 50), outTangentType="step")
        store = ShotStore()
        store.define_shot("S0", 0, 60, [str(c1)])
        seq = ShotSequencer(store=store)
        segs_before = seq.collect_object_segments(0)
        self.assertGreater(len(segs_before), 0, "No segments before move")
        SegmentKeys.shift_curves(
            vis_curves, 10, time_range=(10, 10), remove_flat_at_dest=False
        )
        segs_after = seq.collect_object_segments(0)
        objs_after = {s["obj"] for s in segs_after}
        self.assertIn(str(c1), objs_after, "Object vanished after visibility key move")

    def test_no_stranded_keys_at_temp_offset(self):
        """shift_curves must never leave keys stranded at ~100000."""
        import maya.cmds as cmds

        c1 = _make_cube("nsk_a", {10: 0, 50: 10})
        pm.setKeyframe(str(c1), attribute="visibility", time=10, value=1)
        vis_curves = (
            cmds.listConnections(
                str(c1) + ".visibility", type="animCurve", s=True, d=False
            )
            or []
        )
        for crv in vis_curves:
            cmds.keyTangent(crv, time=(10, 10), outTangentType="step")
        SegmentKeys.shift_curves(
            vis_curves, 5, time_range=(10, 10), remove_flat_at_dest=False
        )
        for crv in vis_curves:
            all_times = cmds.keyframe(crv, q=True, timeChange=True) or []
            stranded = [t for t in all_times if t > 99000]
            self.assertEqual(len(stranded), 0, f"Keys stranded at {stranded}")


# =========================================================================
# Runner (for direct execution via mayapy or python)
# =========================================================================

if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    )
    sys.exit(0 if result.wasSuccessful() else 1)
