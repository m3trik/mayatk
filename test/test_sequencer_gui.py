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
import maya.cmds as cmds

# --- pymel migration shims (auto-injected by _convert_pm_to_cmds.py) ---
from contextlib import contextmanager as _contextmanager


def _pm_open_file(*args, **kw):
    kw.setdefault("open", True)
    return cmds.file(*args, **kw)


def _pm_new_file(**kw):
    kw.setdefault("new", True)
    return cmds.file(**kw)


def _pm_rename_file(path):
    return cmds.file(rename=path)


@_contextmanager
def _pm_undo_chunk():
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


# --- end shims ---
import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

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
    from qtpy import QtWidgets

    _app = QtWidgets.QApplication.instance()
    if _app is None:
        _app = QtWidgets.QApplication(sys.argv)
    HAS_QT = True
except Exception:
    HAS_QT = False

# ---------------------------------------------------------------------------
# Conditional imports (only available when Maya is running)
# ---------------------------------------------------------------------------
if HAS_MAYA and HAS_QT:
    from uitk import SequencerWidget
    from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
        ShotSequencer,
    )
    from mayatk.anim_utils.shots._shots import ShotStore
    from mayatk.anim_utils.segment_keys import SegmentKeys


# =========================================================================
# Helpers
# =========================================================================


def _new_scene():
    cmds.file(new=True, force=True)


def _make_cube(name, keys, attr="translateX"):
    cube = cmds.polyCube(name=name)[0]
    for frame, value in keys.items():
        cmds.setKeyframe(cube, attribute=attr, time=frame, value=value)
    return cube


def _make_stepped_key(name, frame, value, attr="translateX"):
    cube = cmds.polyCube(name=name)[0]
    cmds.setKeyframe(cube, attribute=attr, time=frame, value=value)
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
        keys = sorted(cmds.keyframe(c1, q=True, attribute="translateX"))
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


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestDeleteStaleShots(unittest.TestCase):
    """The shot list's Delete Shots > Stale: names the shots whose objects and
    keys are gone, asks, and drops their records -- one undo step, and no
    key or live shot moves (a Delete Shot would close the gap behind each).
    Empty, the other narrow scope, takes every shot with nothing keyed in it."""

    def setUp(self):
        from types import SimpleNamespace

        _new_scene()
        gone = _make_cube("staleGone", {1: 0, 50: 5})
        self.live = _make_cube("staleLive", {120: 0, 160: 3})
        self.store = ShotStore()
        self.store.define_shot("Gone", 1, 50, [str(gone)])
        self.store.define_shot("Live", 120, 160, [str(self.live)])
        cmds.delete(gone)
        self.footer = []
        # The controller's own collaborators, and nothing that builds a widget.
        self.ctrl = SimpleNamespace(
            sequencer=SimpleNamespace(store=self.store),
            ui=None,
            _get_sequencer_widget=lambda: None,
            _discard_shot_state=self.store.discard_boundary_snapshot,
            _after_shot_change=lambda: None,
            _set_footer=self.footer.append,
        )

    def _remove(self, button, scope="stale"):
        """Run the *scope* delete, answering its question with *button*; return
        the question's text.  Patched with a function, never a bare MagicMock: a
        QObject subclass faults natively on a mock attribute (uitk/CLAUDE.md)."""
        from unittest import mock
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerController,
        )

        asked = []

        def answer(*args, **kwargs):
            asked.append(args[2])
            return button

        with mock.patch.object(
            QtWidgets.QMessageBox, "question", new=staticmethod(answer)
        ):
            ShotSequencerController.delete_shots(self.ctrl, scope)
        return asked[0] if asked else ""

    def test_confirmed_it_drops_only_the_stale_and_moves_no_key(self):
        asked = self._remove(QtWidgets.QMessageBox.Yes)
        self.assertIn("Gone [1–50]", asked)
        self.assertEqual([s.name for s in self.store.shots], ["Live"])
        self.assertEqual(
            cmds.keyframe(self.live, q=True, timeChange=True), [120.0, 160.0]
        )
        self.assertEqual(self.footer, ["Deleted 1 stale shot(s)"])

    def test_declined_it_drops_nothing(self):
        self._remove(QtWidgets.QMessageBox.Cancel)
        self.assertEqual([s.name for s in self.store.shots], ["Gone", "Live"])

    def test_one_undo_puts_them_back(self):
        self._remove(QtWidgets.QMessageBox.Yes)
        self.assertTrue(self.store.restore_boundary_snapshot())
        self.assertEqual(sorted(s.name for s in self.store.shots), ["Gone", "Live"])

    def test_the_empty_scope_takes_every_shot_that_holds_nothing(self):
        """A new shot never filled, past the animation (no member, no key);
        one that names a live object keyed only outside it (2026-10-10:
        "delete empty is leaving behind empty shots"); and the stale one."""
        self.store.define_shot("Unfilled", 200, 240)
        self.store.define_shot("Cleared", 170, 190, [str(self.live)])
        asked = self._remove(QtWidgets.QMessageBox.Yes, scope="empty")
        for row in ("Unfilled [200–240]", "Cleared [170–190]", "Gone [1–50]"):
            self.assertIn(row, asked)
        self.assertIn("nothing keyed in their frames", asked)
        self.assertEqual([s.name for s in self.store.shots], ["Live"])
        self.assertEqual(self.footer, ["Deleted 3 empty shot(s)"])
        self.assertEqual(
            cmds.keyframe(self.live, q=True, timeChange=True), [120.0, 160.0]
        )

    def test_a_system_sample_does_not_fill_a_shot(self):
        """The shot system's own bound samples are its bookkeeping, not the
        shot's content: a shot holding only one is empty."""
        cmds.setKeyframe(self.live, attribute="translateX", t=175, v=3.0)
        crv = cmds.keyframe(self.live, attribute="translateX", q=True, name=True)[0]
        self.store.define_shot("Sampled", 170, 190, [str(self.live)])
        led = self.store.edit_ledger
        led.record_key(crv, 175.0, -1, "start")
        self.assertIn("Sampled", [s.name for s in self.store.empty_shots()])
        led.release_key(crv, 175.0)  # now the animator's key
        self.assertNotIn("Sampled", [s.name for s in self.store.empty_shots()])

    def test_a_system_sample_does_not_fill_a_shot_that_names_nothing(self):
        """Bug: a shot naming nothing is judged by the whole scene's curves,
        and that scan counted the shot system's own bound samples -- with a
        gap on, a New Shot's start pin on every curve passing it read as
        content, and Delete Empty left the shot behind.
        Fixed: 2026-10-10"""
        cmds.setKeyframe(self.live, attribute="translateX", t=175, v=3.0)
        crv = cmds.keyframe(self.live, attribute="translateX", q=True, name=True)[0]
        self.store.define_shot("New", 170, 190)
        led = self.store.edit_ledger
        led.record_key(crv, 175.0, -1, "start")
        self.assertIn("New", [s.name for s in self.store.empty_shots()])
        led.release_key(crv, 175.0)  # now the animator's key
        self.assertNotIn("New", [s.name for s in self.store.empty_shots()])

    def test_audio_playing_through_a_shot_fills_it(self):
        """The Sequencer draws a shot's audio clips as its content: a
        narration shot whose objects hold still is not empty -- even with the
        clip's on and off keys outside the shot."""
        from mayatk.audio_utils._audio_utils import AudioUtils

        AudioUtils.write_key("narration", frame=165, value=1)
        AudioUtils.write_key("narration", frame=195, value=0)
        self.store.define_shot("Narrated", 170, 190, [str(self.live)])
        self.assertNotIn("Narrated", [s.name for s in self.store.empty_shots()])
        self.store.define_shot("Silent", 200, 240, [str(self.live)])
        self.assertIn("Silent", [s.name for s in self.store.empty_shots()])


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
        self._populate_widget()
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
        """A member with a FEW value-less keys in the shot shows them as
        stepped points; a member with MANY (a bake) draws nothing.

        2026-09-05 made membership a motion label so a baked rig -- a key on
        every frame of every shot -- stopped drawing a track everywhere.
        2026-09-07 found the other edge of that rule: the lone key Move to
        Shot carried into a shot "did not arrive" because, value-less, it drew
        nothing.  A handful of keys are the animator's marks and are drawn;
        the count is what tells a mark from a bake
        (``ShotSequencer.ISOLATED_KEY_LIMIT``).
        """
        c1 = _make_cube("sv_a", {10: 5, 50: 5})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        segs = [s for s in seq.collect_object_segments(0) if s["obj"] == str(c1)]
        self.assertEqual(
            [(s["start"], s["is_stepped"]) for s in segs],
            [(10.0, True), (50.0, True)],
            "two value-less keys are two stepped points",
        )

    def test_a_flat_bake_still_draws_nothing(self):
        c1 = _make_cube("sv_bake", {t: 1.0 for t in range(0, 101, 2)})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        found = {s["obj"] for s in seq.collect_object_segments(0)}
        self.assertNotIn(str(c1), found, "a bake is not a track")

    def _pinned_member(self, name, keys, claim=True):
        """A flat cube keyed at *keys*, a shot [10, 50] over it, and -- with
        *claim* -- the keys on its bounds claimed as the shot's own samples."""
        c1 = _make_cube(name, keys)
        crv = cmds.listConnections(f"{c1}.translateX", type="animCurve")[0]
        store = ShotStore()
        shot = store.define_shot("S0", 10, 50, [str(c1)])
        seq = ShotSequencer(store=store)
        if claim:
            seq.ledger.record_key(crv, 10.0, shot.shot_id, "start")
            seq.ledger.record_key(crv, 50.0, shot.shot_id, "end")
        segs = [
            s for s in seq.collect_object_segments(shot.shot_id) if s["obj"] == str(c1)
        ]
        return [(s["start"], s.get("marker", False)) for s in segs]

    def test_a_member_with_nothing_but_the_shots_bound_samples_draws_nothing(self):
        """The system's own bound samples are never marks.  Measured
        2026-09-07 on "Step 9.1.1-3" [2358, 2791] of the production assembly:
        seven of the ten members drawn carried exactly two keys in the shot
        -- its two bound samples, claimed, flat on every channel -- and were
        shown as members of a shot they never move in."""
        self.assertEqual(
            self._pinned_member("sv_pins", {0: 5, 10: 5, 50: 5, 100: 5}), []
        )

    def test_a_released_bound_sample_that_holds_nothing_draws_nothing_either(self):
        """The same pin without its claim: an unclaimed key ON a bound that is
        provably redundant is what a released sample becomes."""
        self.assertEqual(
            self._pinned_member(
                "sv_disowned", {0: 5, 10: 5, 50: 5, 100: 5}, claim=False
            ),
            [],
        )

    def test_the_animators_own_hold_key_inside_is_still_a_mark(self):
        self.assertEqual(
            self._pinned_member("sv_mark", {0: 5, 10: 5, 30: 5, 50: 5, 100: 5}),
            [(30.0, True)],
            "the key between the two samples is the animator's, and is drawn",
        )

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
        with _pm_undo_chunk():
            seq.move_object_in_shot(0, str(c1), 10, 50, 60)
        segs_moved = seq.collect_object_segments(0)
        self.assertGreater(len(segs_moved), 0, "No segments after move")
        cmds.undo()
        segs_undo = seq.collect_object_segments(0)
        self.assertGreater(len(segs_undo), 0, "Segments vanished after undo")
        found = {s["obj"] for s in segs_undo}
        self.assertIn(str(c1), found, "Object vanished after undo")

    def test_undo_preserves_key_positions(self):
        c1 = _make_cube("ukp_a", {10: 0, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 100, [str(c1)])
        seq = ShotSequencer(store=store)
        with _pm_undo_chunk():
            seq.move_object_in_shot(0, str(c1), 10, 50, 60)
        cmds.undo()
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

        c1 = _make_cube("rsu_a", {10: 0, 50: 10})
        store = ShotStore()
        store.define_shot("S0", 0, 60, [str(c1)])
        seq = ShotSequencer(store=store)
        curves = cmds.listConnections(str(c1), type="animCurve", s=True, d=False) or []
        times_before = sorted(
            t for c in curves for t in (cmds.keyframe(c, q=True, timeChange=True) or [])
        )
        with _pm_undo_chunk():
            seq.resize_shot(0, 0, 40)
        cmds.undo()
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

        c1 = _make_cube("vmt_a", {10: 0, 30: 5, 50: 10})
        cmds.setKeyframe(str(c1), attribute="visibility", time=10, value=1)
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

        c1 = cmds.polyCube(name="vop_a")[0]
        cmds.setKeyframe(str(c1), attribute="visibility", time=10, value=1)
        cmds.setKeyframe(str(c1), attribute="visibility", time=50, value=0)
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

        c1 = _make_cube("nsk_a", {10: 0, 50: 10})
        cmds.setKeyframe(str(c1), attribute="visibility", time=10, value=1)
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

# =========================================================================
# Controller tests -- a FakeSlots host, as the real-scene harness builds it
# =========================================================================

if HAS_MAYA and HAS_QT:
    from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
        ShotSequencerController,
    )
    from uitk.widgets.comboBox import ComboBox


class _FakeSlots:
    def __init__(self, widget):
        from unittest.mock import MagicMock

        self.sb = MagicMock()
        self.ui = MagicMock()
        self.ui.sequencer_widget = widget
        self.ui.cmb_shot = ComboBox()


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class _ControllerCase(unittest.TestCase):
    """Two shots over two cubes, a controller driving a live widget."""

    def setUp(self):
        _new_scene()
        self.a = _make_cube("ctlA", {0: 0, 40: 5})
        self.b = _make_cube("ctlB", {60: 0, 100: 5})
        self.store = ShotStore()
        self.store.define_shot("S0", 0, 50, [str(self.a)])
        self.store.define_shot("S1", 60, 120, [str(self.b)])
        self.widget = SequencerWidget()
        self.widget.resize(900, 400)
        self.widget.show()
        self.slots = _FakeSlots(self.widget)
        self.ctrl = ShotSequencerController(self.slots)
        self.ctrl.sequencer = ShotSequencer(store=self.store)
        _process_events()

    def tearDown(self):
        try:
            self.ctrl.remove_callbacks()
        except Exception:
            pass
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def _shot(self, name):
        return self.store.shot_by_name(name)

    def _keys(self, cube):
        return sorted(cmds.keyframe(cube, q=True, at="translateX") or [])


class TestActiveShotAtPlayhead(_ControllerCase):
    """The panel opens on the shot under the playhead, not the first one."""

    def test_first_sync_selects_the_shot_under_the_playhead(self):
        cmds.currentTime(80)
        self.ctrl._sync_combobox()
        self.assertEqual(self.ctrl.active_shot_id, self._shot("S1").shot_id)
        self.ctrl._sync_to_widget()
        self.assertEqual(self.widget.range_highlight(), (60.0, 120.0))

    def test_outside_every_shot_the_first_stands_in(self):
        cmds.currentTime(55)
        self.ctrl._sync_combobox()
        self.assertEqual(self.ctrl.active_shot_id, self._shot("S0").shot_id)

    def test_a_store_selection_wins_over_the_playhead(self):
        cmds.currentTime(80)
        self.store.set_active_shot(self._shot("S0").shot_id)
        self.ctrl._sync_combobox()
        self.assertEqual(self.ctrl.active_shot_id, self._shot("S0").shot_id)


class TestAShortNamedMemberDrawsATrack(_ControllerCase):
    """A member is stored as it was given -- often a short name -- and the
    track builder's existence check compared it against ``ls -long`` paths,
    so ``ctlA`` read as missing next to ``|ctlA`` and drew no track at all
    unless it was pinned (found 2026-10-06 building this fixture)."""

    def test_a_short_named_member_gets_its_track(self):
        self.assertEqual(self._shot("S0").objects, ["ctlA"])
        # The membership reconcile re-points a short name at its long path
        # (``_resolved_name``) before the track builder runs, which left this
        # check reaching only names it had already rewritten: held off here,
        # the builder meets the short name itself.
        self.ctrl._reconcile_needed = False
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        self.assertEqual(self._shot("S0").objects, ["ctlA"], "reconciled anyway")
        self.assertIn("ctlA", [t.name for t in self.widget.tracks()])


class TestSystemSamplesDrawNoDot(_ControllerCase):
    """The shot system's claimed bound samples shape the curve but are not
    the animator's keys: an expanded track draws no dot for them, so a
    navigation or snap never stops on one either.

    Reported 2026-10-06: "auto generated border keys ... cause clutter in the
    sequencer" -- every claimed sample drew a key dot on the shot's bound.
    """

    def test_a_claimed_sample_has_no_dot(self):
        # Keys 0, 40, 45: the one at 40 is claimed, INSIDE the clip's span, so
        # only the flag can hide its dot (out of span a dot hides anyway).
        cmds.setKeyframe(self.a, attribute="translateX", time=45, value=9)
        crv = cmds.listConnections(f"{self.a}.translateX", type="animCurve")[0]
        sid = self._shot("S0").shot_id
        self.store.edit_ledger.record_key(crv, 40.0, sid, "end")
        self.store.set_active_shot(sid)
        self.ctrl._sync_to_widget(shot_id=sid)
        tid = next(t.track_id for t in self.widget.tracks() if t.name == "ctlA")
        self.widget.expand_track(tid)
        _process_events()
        dots = {
            round(k._time): k.isVisible()
            for c in self.widget.clips()
            if c.sub_row and c.track_id == tid
            for k in self.widget._clip_items[c.clip_id]._keyframe_items
        }
        self.assertTrue(dots.get(0) and dots.get(45), dots)
        self.assertIs(dots.get(40), False, "the claimed sample drew a dot")


class TestANewShotTakesThePlayhead(_ControllerCase):
    """Creating a shot moves the playhead to its start (2026-10-06)."""

    def test_the_one_click_new_shot(self):
        cmds.currentTime(10)
        self.ctrl._create_shot_one_click()
        shot = self.ctrl.sequencer.shot_by_id(self.ctrl.active_shot_id)
        self.assertNotIn(shot.name, ("S0", "S1"))
        self.assertEqual(cmds.currentTime(q=True), shot.start)

    def test_an_inserted_shot(self):
        cmds.currentTime(10)
        self.ctrl._insert_shot(self._shot("S1").shot_id, before=True)
        shot = self.ctrl.sequencer.shot_by_id(self.ctrl.active_shot_id)
        self.assertNotIn(shot.name, ("S0", "S1"))
        self.assertEqual(cmds.currentTime(q=True), shot.start)


class TestChangingShotsMovesThePlayhead(_ControllerCase):
    """A shot change puts the playhead on the new shot's first frame, behind
    one global header option that is on by default (2026-10-07: "the playhead
    is still not moving to the start of a shot when we change shots.  this
    should be a global option in the shot sequencer header").  Every way of
    changing shots takes the same path."""

    def setUp(self):
        super().setUp()
        cmds.currentTime(10)
        self.store.set_active_shot(self._shot("S0").shot_id)
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()

    def _now(self):
        return cmds.currentTime(q=True)

    def test_the_option_is_on_by_default(self):
        self.assertTrue(self.ctrl._playhead_to_shot_start)

    def test_next_shot(self):
        self.ctrl._navigate_shot(1)
        self.assertEqual(self._now(), 60.0)

    def test_the_dropdown(self):
        from types import SimpleNamespace
        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerSlots,
        )

        cmb = self.slots.ui.cmb_shot
        idx = next(
            i for i in range(cmb.count()) if cmb.itemData(i) == self._shot("S1").shot_id
        )
        host = SimpleNamespace(ui=self.slots.ui, controller=self.ctrl)
        ShotSequencerSlots.cmb_shot(host, idx)
        self.assertEqual(self.store.active_shot_id, self._shot("S1").shot_id)
        self.assertEqual(self._now(), 60.0)

    def test_a_click_on_a_shot_block(self):
        self.ctrl.on_shot_block_clicked("S1")
        self.assertEqual(self._now(), 60.0)

    def test_a_shot_picked_in_another_panel(self):
        """The Shots window, the manifest's Open in Shot Sequencer."""
        # The panel listens to the ACTIVE store; this fixture's is not one.
        self.store.add_listener(self.ctrl._on_store_event)
        self.addCleanup(self.store.remove_listener, self.ctrl._on_store_event)
        self.store.set_active_shot(self._shot("S1").shot_id)
        self.assertEqual(self._now(), 60.0)

    def test_a_slot_filled_after_a_delete_is_no_shot_change(self):
        """The Shots window selects the first shot once the active one is
        deleted: that fills an empty slot, the user changed no shot."""
        self.store.add_listener(self.ctrl._on_store_event)
        self.addCleanup(self.store.remove_listener, self.ctrl._on_store_event)
        self.store.set_active_shot(None)
        self.store.set_active_shot(self._shot("S1").shot_id)
        self.assertEqual(self._now(), 10.0)

    def test_the_shot_already_active_leaves_it(self):
        self.ctrl.on_shot_block_clicked("S0")
        self.assertEqual(self._now(), 10.0)

    def test_off_leaves_it_and_a_new_shot_too(self):
        self.ctrl._set_playhead_to_shot_start(False)
        self.ctrl._navigate_shot(1)
        self.assertEqual(self.store.active_shot_id, self._shot("S1").shot_id)
        self.assertEqual(self._now(), 10.0)
        self.ctrl._create_shot_one_click()
        self.assertEqual(self._now(), 10.0)

    def test_the_move_is_no_undo_step(self):
        """A view move: Ctrl+Z after it must reach the user's last edit."""
        cmds.undoInfo(state=True)
        cmds.setKeyframe(self.a, at="translateX", t=20, v=3.0)
        self.ctrl._navigate_shot(1)
        cmds.undo()
        self.assertNotIn(20.0, self._keys(self.a), "Ctrl+Z undid the key")


class TestAnEditDrawsOnce(_ControllerCase):
    """``ShotsEdited`` -- every ``scene_edit``'s announcement, for the panels
    that did not make the edit -- is not a reason for the sequencer to draw
    again: it redraws after its own edits, so reacting drew each twice."""

    def test_the_announcement_draws_nothing(self):
        from unittest.mock import patch
        from mayatk.anim_utils.shots._shots import ShotsEdited

        with patch.object(self.ctrl, "_sync_to_widget") as draw:
            self.ctrl._on_store_event(ShotsEdited(label="trim"))
        draw.assert_not_called()


class TestCopyToShotFromThePanel(_ControllerCase):
    """The panel's Copy to Shot (2026-10-07): the originals stay, the copy
    lands in the destination, the footer says Copied, and one Ctrl+Z takes
    the copy back."""

    def test_a_copied_clip_lands_and_one_undo_takes_it_back(self):
        cmds.undoInfo(state=True, infinity=True)
        footers = []
        self.ctrl._set_footer = lambda text, **_kw: footers.append(text)
        s0, s1 = self._shot("S0"), self._shot("S1")
        seqs = [
            sq
            for sq in self.ctrl.sequencer.collect_shot_sequences(
                s0.shot_id, include_audio=False
            )
            if sq["obj"].split("|")[-1] == "ctlA"
        ]
        self.assertTrue(seqs)
        self.ctrl._send_to_shot(seqs, s1.shot_id, copy=True)
        keys = self._keys(self.a)
        self.assertTrue({0.0, 40.0} <= set(keys), f"the originals stay: {keys}")
        s1 = self._shot("S1")
        self.assertTrue(
            [t for t in keys if s1.start <= t <= s1.end], f"nothing landed: {keys}"
        )
        self.assertEqual(footers[-1], "Copied 1 clip to S1")
        cmds.undo()
        self.assertEqual(self._keys(self.a), [0.0, 40.0], "one undo takes it back")

    def test_audio_alone_is_not_copied(self):
        footers = []
        self.ctrl._set_footer = lambda text, **_kw: footers.append(text)
        self.ctrl._send_to_shot(
            [{"kind": "audio", "obj": "trk", "start": 0.0, "end": 10.0}],
            self._shot("S1").shot_id,
            copy=True,
        )
        self.assertEqual(footers, ["Copy to Shot: audio clips are not copied."])

    def test_a_refused_landing_is_reported_not_raised(self):
        """A landing that would make two shots share a sample whose poses
        disagree is refused by the engine; the menu says so in the footer
        (the shot lane's edits do) instead of handing over a traceback."""
        from unittest.mock import patch
        from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict

        footers = []
        self.ctrl._set_footer = lambda text, **_kw: footers.append(text)
        seqs = self.ctrl.sequencer.collect_shot_sequences(
            self._shot("S0").shot_id, include_audio=False
        )
        with patch.object(
            self.ctrl.sequencer,
            "copy_sequences_to_shot",
            side_effect=ShotBoundaryConflict([("ctlA_translateX", 60.0, [5.0, 0.0])]),
        ):
            self.ctrl._send_to_shot(seqs, self._shot("S1").shot_id, copy=True)
        self.assertTrue(footers[-1].startswith("Copy to Shot: "), footers)
        self.assertIn("ctlA_translateX", footers[-1])


class TestANewShotUndoes(_ControllerCase):
    """A new shot is one undo step, from Maya's own Ctrl+Z as from the
    panel's, and every view follows it back.  Reported 2026-10-07: "new shot
    creation is not undoable, or if it is, the sequencer doesn't refresh
    properly" -- both were true: the first shot of a scene changed nothing
    Maya records, so Maya's undo went to the operation before it, and the
    panel's undo put the store back but left the dropdown and the highlight
    on the shot it had just removed."""

    def setUp(self):
        super().setUp()
        cmds.undoInfo(state=True, infinity=True)
        self.store.set_active_shot(self._shot("S0").shot_id)
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()

    def _listed(self):
        cmb = self.ctrl.ui.cmb_shot
        return [cmb.itemData(i) for i in range(cmb.count())]

    def _assert_back_on_s0(self):
        s0 = self._shot("S0")
        self.assertEqual([s.name for s in self.store.sorted_shots()], ["S0", "S1"])
        self.assertEqual(self._listed(), [s0.shot_id, self._shot("S1").shot_id])
        self.assertEqual(self.store.active_shot_id, s0.shot_id, "the shot it was on")
        self.assertEqual(self.ctrl.active_shot_id, s0.shot_id)
        self.assertEqual(self.widget.range_highlight(), (s0.start, s0.end))

    def test_mayas_undo_takes_back_the_first_shot_of_a_scene(self):
        for shot in list(self.store.shots):
            self.store.remove_shot(shot.shot_id)
        self.ctrl._sync_combobox()
        cmds.setKeyframe(self.a, at="translateY", t=5, v=1)  # the op before it
        self.ctrl._create_shot_one_click()
        self.assertEqual(len(self.store.shots), 1)
        cmds.undo()
        _process_events()
        self.assertEqual(self.store.shots, [], "Maya's undo reached the new shot")
        self.assertEqual(self._listed(), [])
        self.assertEqual(
            cmds.keyframe(self.a, q=True, at="translateY"), [5.0], "not the op before"
        )
        cmds.redo()
        _process_events()
        self.assertEqual(len(self.store.shots), 1)
        self.assertEqual(self.store.active_shot_id, self.store.shots[0].shot_id)

    def test_the_panels_undo_puts_the_dropdown_and_view_back(self):
        self.ctrl._create_shot_one_click()
        new_id = self.store.active_shot_id
        self.assertIn(new_id, self._listed())
        self.ctrl.on_undo()
        _process_events()
        self._assert_back_on_s0()
        self.ctrl.on_redo()
        _process_events()
        self.assertIn(new_id, self._listed())
        self.assertEqual(self.ctrl.active_shot_id, new_id, "redo: the new one again")

    def test_mayas_undo_puts_the_dropdown_and_view_back(self):
        self.ctrl._create_shot_one_click()
        cmds.undo()
        _process_events()
        self._assert_back_on_s0()

    def test_an_inserted_shot_undoes(self):
        self.ctrl._insert_shot(self._shot("S1").shot_id, before=True)
        self.assertEqual(len(self.store.shots), 3)
        cmds.undo()
        _process_events()
        self._assert_back_on_s0()
        self.assertEqual(self._keys(self.b), [60.0, 100.0], "the ripple went back")

    def test_a_detected_shot_undoes(self):
        """It was defined outside any scene_edit: no restore point, so no
        undo at all."""
        from types import SimpleNamespace
        from unittest import mock

        from mayatk.anim_utils.shots.shot_sequencer import shot_sequencer_slots

        cand = {"name": "Shot_1", "start": 130.0, "end": 170.0, "objects": []}
        self.ctrl.sequencer.detect_next_shot = lambda **_kw: cand
        panel = SimpleNamespace(controller=self.ctrl, ui=None)
        with mock.patch.object(
            shot_sequencer_slots.ShotEditDialog,
            "show",
            return_value=("Detected", 130.0, 170.0, ""),
        ):
            shot_sequencer_slots.ShotSequencerSlots._detect_next_shot(panel)
        detected = self.store.shot_by_name("Detected")
        self.assertIsNotNone(detected)
        self.assertEqual(self.ctrl.active_shot_id, detected.shot_id)
        self.assertEqual(cmds.currentTime(q=True), 130.0, "the playhead landed")
        cmds.undo()
        _process_events()
        self._assert_back_on_s0()


class TestTheEditedShotIsTheOneShown(_ControllerCase):
    """An edit made on a shot other than the active one -- the shot lane's
    menu reaches every band -- draws THAT shot, so it becomes the active one:
    the timeline showed it while the dropdown and Maya's playback range stayed
    on the other (found in review, 2026-10-07)."""

    def test_padding_another_shot_makes_it_active(self):
        s0, s1 = self._shot("S0"), self._shot("S1")
        self.store.set_active_shot(s0.shot_id)
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        self.ctrl._add_shot_space(s1.shot_id, 10, "trailing")
        s1 = self._shot("S1")
        self.assertEqual(self.store.active_shot_id, s1.shot_id)
        self.assertEqual(self.ctrl.active_shot_id, s1.shot_id, "the dropdown")
        self.assertEqual(self.widget.range_highlight(), (s1.start, s1.end))


class TestEditShotOpensThePinnedShotsWindow(_ControllerCase):
    """Edit Shot opens the Shots window PINNED (2026-10-06): opened from the
    sequencer to edit a shot, it has to stay up while the user works in it,
    and its pin button then reads as the one that puts it away."""

    class _Window:
        def __init__(self, buttons=("pin",)):
            from types import SimpleNamespace

            self.pinned = False
            self.header = SimpleNamespace(buttons={b: object() for b in buttons})

        def set_pinned(self, value):
            self.pinned = bool(value)

    def test_edit_shot_pins_the_window(self):
        window = self._Window()
        self.slots.sb.handlers.marking_menu.show.return_value = window
        self.ctrl._edit_shot_dialog(self._shot("S1"))
        self.slots.sb.handlers.marking_menu.show.assert_called_with("shots")
        self.assertTrue(window.pinned)
        self.assertEqual(self.store.active_shot_id, self._shot("S1").shot_id)

    def test_a_window_with_no_pin_button_is_left_unpinned(self):
        """A pinned window refuses hide(); with no pin button to undo it, it
        would be stranded on screen.  Such a window never auto-hides anyway."""
        window = self._Window(buttons=("hide",))
        self.slots.sb.handlers.marking_menu.show.return_value = window
        self.ctrl._edit_shot_dialog(self._shot("S1"))
        self.assertFalse(window.pinned)


class TestShotLaneMenu(_ControllerCase):
    """The shot menu: a compact root whose verbs fan out into their forms."""

    @staticmethod
    def _rows(lst):
        return [w.text() for w in lst._row_widgets() if hasattr(w, "text")]

    def test_root_is_compact_and_rows_fan_out(self):
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = self.ctrl._build_shot_lane_context_menu(30.0)
        try:
            root = self._rows(menu.list)
            self.assertEqual(root[0], 'Edit "S0"\u2026')
            for label in (
                "New Shot",
                "Split Here (30)",
                "Merge",
                "Move To",
                "Add Frames",
                "Trim Empty Space",
            ):
                self.assertIn(label, root)
            self.assertNotIn("Trim Leading Space", root, "lives in the flyout")
            self.assertNotIn("Refresh", root, "the header refreshes")
            # 2026-10-10: "the shot context menu should also have a delete
            # shot button" -- last, the one row that takes keys with it.
            self.assertEqual(root[-1], 'Delete "S0"\u2026')
            by_text = {
                w.text(): w for w in menu.list._row_widgets() if hasattr(w, "text")
            }
            self.assertEqual(
                self._rows(by_text["Trim Empty Space"].sublist),
                ["Trim Leading Space", "Trim Trailing Space"],
            )
            self.assertTrue(by_text["Trim Empty Space"].property("contextAction"))
            self.assertEqual(
                self._rows(by_text["New Shot"].sublist),
                ["Insert Shot Before", "Insert Shot After"],
            )
            split = self._rows(by_text["Split Here (30)"].sublist)
            self.assertEqual(len(split), 1)
            self.assertTrue(split[0].startswith("Split at Current Time"), split[0])
        finally:
            menu.dispose()

    def test_shot_menu_is_only_about_the_shot(self):
        """Key edits belong to a key selection and display toggles to the
        timeline; both used to hang off this menu."""
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = self.ctrl._build_shot_lane_context_menu(30.0)
        try:
            root = self._rows(menu.list)
            for gone in (
                'Select "S0"',
                "Keys",
                "Tangents",
                "Extend to Keys",
                "Timeline",
            ):
                self.assertNotIn(gone, root)
        finally:
            menu.dispose()

    def test_delete_row_deletes_the_shot_under_the_click(self):
        from unittest import mock

        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = self.ctrl._build_shot_lane_context_menu(90.0)  # over S1
        try:
            row = next(
                w
                for w in menu.list._row_widgets()
                if hasattr(w, "text") and w.text() == 'Delete "S1"…'
            )
            with mock.patch.object(
                QtWidgets.QMessageBox,
                "question",
                return_value=QtWidgets.QMessageBox.Yes,
            ):
                row.click()
            _process_events()
        finally:
            menu.dispose()
        self.assertEqual([s.name for s in self.store.shots], ["S0"])

    def test_outside_every_shot_only_creation_remains(self):
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = self.ctrl._build_shot_lane_context_menu(55.0)
        try:
            self.assertEqual(self._rows(menu.list), ["New Shot"])
        finally:
            menu.dispose()

    def test_timeline_has_its_own_menu(self):
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = self.ctrl._build_timeline_context_menu(30.0)
        try:
            rows = self._rows(menu.list)
            self.assertEqual(rows[0], "Add Marker at 30\u2026")
            self.assertIn("Show Gap Overlays", rows)
            self.assertNotIn("Refresh", rows, "the header button is the one Refresh")
        finally:
            menu.dispose()

    def test_move_to_lists_the_shots_in_running_order(self):
        """The flyout IS the running order, numbered as the user reads it.

        Where a pick lands, and which rows are inert, need more than two
        shots to mean anything -- see ``TestMoveToFlyout``.
        """
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = self.ctrl._build_shot_lane_context_menu(30.0)
        try:
            by_text = {
                w.text(): w for w in menu.list._row_widgets() if hasattr(w, "text")
            }
            ordered = self.ctrl.sequencer.sorted_shots()
            self.assertEqual(
                self._rows(by_text["Move To"].sublist),
                [f"{i}. {s.name}" for i, s in enumerate(ordered, start=1)],
            )
        finally:
            menu.dispose()

    def test_move_to_position_reorders_and_pushes_the_rest_along(self):
        seq = self.ctrl.sequencer
        names_before = [s.name for s in seq.sorted_shots()]
        if len(names_before) < 2:
            self.skipTest("needs two shots to reorder")
        first = seq.sorted_shots()[0]
        self.ctrl.move_shot_to_position(first.shot_id, len(names_before))
        names_after = [s.name for s in seq.sorted_shots()]
        self.assertEqual(names_after[-1], names_before[0], "moved to the last slot")
        self.assertEqual(
            sorted(names_after), sorted(names_before), "no shot lost or duplicated"
        )


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
class TestMoveToFlyout(unittest.TestCase):
    """Picking a shot lands the moved one in FRONT of it, both directions.

    Four shots on purpose: with two, "before the picked shot" and "at the
    picked shot's index" agree, so the off-by-one that only bites a
    downstream move is invisible.
    """

    NAMES = ("A", "B", "C", "D")

    def setUp(self):
        _new_scene()
        self.store = ShotStore()
        self.cubes = {}
        for i, name in enumerate(self.NAMES):
            start, end = i * 30, i * 30 + 20
            cube = _make_cube(f"mv{name}", {start: 0, end: 5})
            self.cubes[name] = cube
            self.store.define_shot(name, start, end, [str(cube)])
        self.widget = SequencerWidget()
        self.widget.resize(900, 400)
        self.widget.show()
        self.ctrl = ShotSequencerController(_FakeSlots(self.widget))
        self.ctrl.sequencer = ShotSequencer(store=self.store)
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        _process_events()

    def tearDown(self):
        try:
            self.ctrl.remove_callbacks()
        except Exception:
            pass
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def _order(self):
        return [s.name for s in self.ctrl.sequencer.sorted_shots()]

    def _pick(self, over_name, target_name):
        """Open the lane menu over *over_name* and click *target_name*'s row."""
        shot = self.store.shot_by_name(over_name)
        menu = self.ctrl._build_shot_lane_context_menu((shot.start + shot.end) / 2.0)
        try:
            rows = {w.text(): w for w in menu.list._row_widgets() if hasattr(w, "text")}
            sub = [
                w for w in rows["Move To"].sublist._row_widgets() if hasattr(w, "text")
            ]
            row = next(w for w in sub if w.text().endswith(target_name))
            self.assertTrue(row.isEnabled(), f"{row.text()} should be pickable")
            row.click()
            _process_events()
        finally:
            menu.dispose()

    def test_moving_downstream_lands_in_front_of_the_picked_shot(self):
        self.assertEqual(self._order(), ["A", "B", "C", "D"])
        self._pick("A", "C")
        self.assertEqual(self._order(), ["B", "A", "C", "D"])

    def test_moving_upstream_lands_in_front_of_the_picked_shot(self):
        self._pick("D", "B")
        self.assertEqual(self._order(), ["A", "D", "B", "C"])

    def test_moving_to_the_far_end_in_each_direction(self):
        self._pick("A", "D")
        self.assertEqual(self._order(), ["B", "C", "A", "D"])
        self._pick("A", "B")
        self.assertEqual(self._order(), ["A", "B", "C", "D"], "and back again")

    def test_the_rows_that_would_do_nothing_are_inert(self):
        """Its own row, and the neighbour it already sits in front of."""
        shot = self.store.shot_by_name("B")
        menu = self.ctrl._build_shot_lane_context_menu((shot.start + shot.end) / 2.0)
        try:
            rows = {w.text(): w for w in menu.list._row_widgets() if hasattr(w, "text")}
            state = {
                w.text()[-1]: w.isEnabled()
                for w in rows["Move To"].sublist._row_widgets()
                if hasattr(w, "text")
            }
            self.assertEqual(
                state, {"A": True, "B": False, "C": False, "D": True}, state
            )
        finally:
            menu.dispose()

    def test_undo_puts_the_dropdown_back_in_order(self):
        """The dropdown lists the shots; an undone reorder must reach it."""
        cmb = self.ctrl.ui.cmb_shot

        def labels():
            return [cmb.itemText(i).split()[0] for i in range(cmb.count())]

        before = labels()
        self.assertEqual(before, ["A", "B", "C", "D"], before)
        self._pick("A", "C")
        self.assertEqual(labels(), ["B", "A", "C", "D"])
        cmds.undo()
        _process_events()
        self.assertEqual(self._order(), ["A", "B", "C", "D"], "the store came back")
        self.assertEqual(labels(), before, "and so did the dropdown")

    def test_ruler_zone_routes_to_the_timeline_menu(self):
        """A right-click on the ruler is the timeline's, even over a shot."""
        seen = {}

        def _timeline(t):
            seen["t"] = t  # returns None: nothing for the caller to exec_

        self.ctrl._build_timeline_context_menu = _timeline
        self.ctrl._show_shot_lane_context_menu = lambda *a: seen.setdefault("shot", a)
        self.ctrl.on_zone_context_menu("ruler", 30.0, None)
        self.assertEqual(seen.get("t"), 30.0)
        self.assertNotIn("shot", seen)


class TestExtendToKeysOption(_ControllerCase):
    """One global option, capped by a reach; -1 means any distance."""

    def test_extend_grows_over_the_gap_key_within_reach(self):
        cmds.setKeyframe(self.a, at="translateX", t=55, v=6.0)
        self.ctrl._sync_combobox()
        self.ctrl._set_extend_reach(10)
        self.ctrl._extend_shot_to_keys(self._shot("S0").shot_id)
        self.assertEqual(self._shot("S0").end, 55.0)
        self.assertEqual(self._keys(self.a), [0.0, 40.0, 55.0], "the key stays")
        self.assertEqual(self._shot("S1").start, 65.0, "S1 rippled +5")

    def test_out_of_reach_leaves_the_shot(self):
        cmds.setKeyframe(self.a, at="translateX", t=58, v=6.0)
        self.ctrl._sync_combobox()
        self.ctrl._set_extend_reach(5)
        self.ctrl._extend_shot_to_keys(self._shot("S0").shot_id)
        self.assertEqual(self._shot("S0").end, 50.0)

    def test_minus_one_uncaps_the_reach(self):
        cmds.setKeyframe(self.a, at="translateX", t=58, v=6.0)
        self.ctrl._sync_combobox()
        self.ctrl._set_extend_reach(-1)
        self.assertIsNone(self.ctrl._extend_reach_arg, "-1 is the engine's None")
        self.ctrl._extend_shot_to_keys(self._shot("S0").shot_id)
        self.assertEqual(self._shot("S0").end, 58.0)

    def test_option_off_means_no_automatic_extend(self):
        cmds.setKeyframe(self.a, at="translateX", t=55, v=6.0)
        self.ctrl._sync_combobox()
        self.ctrl._set_extend_reach(10)
        self.ctrl._extend_to_keys = False
        self.assertFalse(self.ctrl._auto_extend_to_new_keys(self._shot("S0").shot_id))
        self.assertEqual(self._shot("S0").end, 50.0)

    def test_option_on_extends_on_a_keying_burst(self):
        cmds.setKeyframe(self.a, at="translateX", t=55, v=6.0)
        self.ctrl._sync_combobox()
        self.ctrl._set_extend_to_keys(True)
        self.ctrl._set_extend_reach(10)
        self.assertTrue(self.ctrl._auto_extend_to_new_keys(self._shot("S0").shot_id))
        self.assertEqual(self._shot("S0").end, 55.0)


class TestStashIsOneEntry(_ControllerCase):
    """Store Keys puts ONE thing away, however wide the gesture was.

    It used to call ``KeyStash.stash`` once per (object, attribute) job, so
    storing a three-channel selection left three clips to find, and to
    retrieve one at a time.
    """

    def setUp(self):
        super().setUp()
        from mayatk.anim_utils.key_stash._key_stash import KeyStash

        self.stash = KeyStash.active()
        for clip in list(self.stash.clips):
            self.stash.drop(clip.clip_id)
        for attr in ("translateY", "translateZ"):
            cmds.setKeyframe(self.a, at=attr, t=0, v=0.0)
            cmds.setKeyframe(self.a, at=attr, t=40, v=3.0)
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()

    def _targets(self, *attrs):
        sid = self._shot("S0").shot_id
        return [(str(self.a), a, [0.0, 40.0], sid) for a in attrs]

    def test_three_channels_stash_as_one_clip(self):
        before = len(self.stash.clips)
        self.ctrl._stash_key_targets(
            self._targets("translateX", "translateY", "translateZ")
        )
        self.assertEqual(
            len(self.stash.clips) - before, 1, "one gesture, one stash entry"
        )
        clip = self.stash.clips[-1]
        self.assertEqual(len(clip.curves), 3, "all three channels are IN that entry")
        self.assertEqual(clip.source_shot_id, self._shot("S0").shot_id)

    def test_two_objects_stash_as_one_clip(self):
        before = len(self.stash.clips)
        s0, s1 = self._shot("S0").shot_id, self._shot("S1").shot_id
        self.ctrl._stash_key_targets(
            [
                (str(self.a), "translateX", [0.0, 40.0], s0),
                (str(self.b), "translateX", [60.0, 100.0], s1),
            ]
        )
        self.assertEqual(len(self.stash.clips) - before, 1)
        clip = self.stash.clips[-1]
        self.assertEqual(len(clip.objects), 2)
        self.assertIsNone(
            clip.source_shot_id, "spanning two shots, it belongs to neither"
        )

    def test_only_the_named_channels_and_spans_are_taken(self):
        """A merged stash must not become a bounding box over the union.

        Asking for A.translateX and A.translateZ must leave translateY where
        it is -- the scope list is per target, not a range plus a channel
        set crossed together.
        """
        self.ctrl._stash_key_targets(self._targets("translateX", "translateZ"))
        self.assertEqual(
            sorted(cmds.keyframe(self.a, q=True, at="translateY") or []),
            [0.0, 40.0],
            "translateY was never asked for",
        )
        for gone in ("translateX", "translateZ"):
            self.assertFalse(
                cmds.keyframe(self.a, q=True, at=gone) or [],
                f"{gone} should have been parked",
            )

    def test_nothing_to_store_leaves_no_clip(self):
        before = len(self.stash.clips)
        self.ctrl._stash_key_targets(
            [(str(self.a), "translateX", [900.0, 950.0], self._shot("S0").shot_id)]
        )
        self.assertEqual(len(self.stash.clips), before, "no keys out there")


class TestKeySelectionEdits(_ControllerCase):
    """The Animation panel's key edits, scoped to a KEY selection.

    They used to be offered per shot, where "remove the intermediate keys"
    reached every member's every attribute over the whole span.
    """

    def setUp(self):
        super().setUp()
        # The panel's clipboard lives on the class (it survives a re-init).
        type(self.ctrl)._key_clipboard = {}

    def _targets(self, times=None):
        """The ``(obj, attr, times, shot_id)`` rows the key menu works from."""
        return [
            (
                str(self.a),
                "translateX",
                list(times or self._keys(self.a)),
                self._shot("S0").shot_id,
            )
        ]

    def test_the_edit_rows_are_spelled_as_blendertk_spells_them(self):
        """Pinned as a LITERAL: blendertk pins the same list, so a row added
        to one fork and not the other fails on the side that drifted."""
        self.assertEqual(
            [label for label, _m in self.ctrl._KEY_EDITS],
            [
                "Simplify",
                "Remove Intermediate Keys",
                "Snap Fractional Keys",
                "Invert Keys",
                "Align Keys",
            ],
        )

    def test_menu_offers_the_stash_and_edit_rows(self):
        from qtpy import QtWidgets

        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        menu = QtWidgets.QMenu()
        try:
            self.ctrl._add_key_edit_actions(menu, self._targets(), " (2)")
            labels = [a.text() for a in menu.actions() if a.text()]
            for want in ("Store Keys (2)", "Edit"):
                self.assertIn(want, labels)
            edit = next(a.menu() for a in menu.actions() if a.text() == "Edit")
            self.assertEqual(
                [a.text() for a in edit.actions()],
                [label for label, _m in self.ctrl._KEY_EDITS],
            )
        finally:
            menu.deleteLater()

    def test_no_menu_row_duplicates_a_bound_key(self):
        """Copy / Paste / Delete are Ctrl+C / Ctrl+V / Delete, not rows -- and
        no row reaches Maya's own clipboard (2026-10-10: removed, as blendertk
        has none; a Graph Editor view in the panel is the plan)."""
        from qtpy import QtWidgets

        menu = QtWidgets.QMenu()
        try:
            self.ctrl._add_key_edit_actions(menu, self._targets(), "")
            labels = " ".join(a.text() for a in menu.actions())
            for gone in ("Copy", "Paste", "Delete"):
                self.assertNotIn(gone, labels)
        finally:
            menu.deleteLater()

    def test_the_shortcut_resolves_its_own_targets(self):
        """A menu is handed its groups; a key press has to ask the widget."""
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        self.assertEqual(self.ctrl._selected_key_targets(), [], "nothing selected")
        widget = self.ctrl._get_sequencer_widget()
        groups = [{"clip_id": 123, "times": [0.0]}]
        widget.selected_keys = lambda: groups
        seen = []
        self.ctrl._key_targets = lambda w, g: seen.append((w, g)) or ["T"]
        self.assertEqual(self.ctrl._selected_key_targets(), ["T"])
        self.assertEqual(
            seen, [(widget, groups)], "the widget's selection, the menu's resolver"
        )

    def test_thin_keeps_only_the_outer_keys_of_the_selection(self):
        cmds.setKeyframe(self.a, at="translateX", t=20, v=2.0)
        self.assertEqual(self._keys(self.a), [0.0, 20.0, 40.0])
        self.ctrl._sync_combobox()
        self.ctrl._thin_selected_keys(self._targets([0.0, 20.0, 40.0]))
        self.assertEqual(self._keys(self.a), [0.0, 40.0])

    def test_snap_pulls_a_fractional_key_onto_a_whole_frame(self):
        cmds.setKeyframe(self.a, at="translateX", t=20.4, v=2.0)
        self.ctrl._sync_combobox()
        self.ctrl._snap_selected_keys(self._targets([0.0, 20.4, 40.0]))
        self.assertEqual(self._keys(self.a), [0.0, 20.0, 40.0])

    # -- the shot system's claims follow the key edits (mirror of blendertk) --

    def _claim(self, frame, behavior="fade_in"):
        """Claim cube A's translateX key at *frame* for a behavior of S0."""
        crv = cmds.keyframe(self.a, at="translateX", q=True, name=True)[0]
        self.ctrl.sequencer.ledger.record_authored(
            crv, frame, self._shot("S0").shot_id, behavior, str(self.a)
        )
        return crv

    def test_align_releases_the_claims_of_a_key_it_lands_on(self):
        """A key Align lands on is replaced and takes its claims with it: left
        on the frame, a behavior's claim passed to the arriving key, which the
        next Build deleted (BACKLOG 2026-10-04)."""
        cmds.setKeyframe(self.a, at="translateX", t=20, v=2.0)  # A: 0, 20, 40
        other = _make_cube("ctlC", {0: 1.0, 30: 2.0})
        crv = self._claim(0.0)
        sid = self._shot("S0").shot_id
        self.ctrl._sync_combobox()

        # Every selection starts on the earliest start (0): A's block lands
        # its 20 on A's own unselected, claimed 0.
        self.ctrl._align_selected_keys(
            [
                (str(other), "translateX", [0.0], sid),
                (str(self.a), "translateX", [20.0, 40.0], sid),
            ]
        )

        self.assertEqual(self._keys(self.a), [0.0, 20.0])
        led = self.ctrl.sequencer.ledger
        self.assertFalse(led.owns_any(crv, 0.0), led.to_dict())

    def test_invert_releases_the_claims_of_a_key_it_lands_on(self):
        for t, v in ((10, 1.0), (30, 3.0)):
            cmds.setKeyframe(self.a, at="translateX", t=t, v=v)  # A: 0, 10, 30, 40
        crv = self._claim(30.0)
        self.ctrl._sync_combobox()

        # Over [0, 40] the selected 10 lands on the unselected, claimed 30.
        self.ctrl._invert_selected_keys(self._targets([0.0, 10.0, 40.0]))

        self.assertEqual(self._keys(self.a), [0.0, 30.0, 40.0])
        led = self.ctrl.sequencer.ledger
        self.assertFalse(led.owns_any(crv, 30.0), led.to_dict())

    def test_invert_carries_the_claims_of_a_key_it_moves(self):
        """A claimed key the mirror MOVES keeps its claims: left at 10 they
        passed to the key arriving from 30, and the moved key read as the
        animator's."""
        for t, v in ((10, 1.0), (30, 3.0)):
            cmds.setKeyframe(self.a, at="translateX", t=t, v=v)  # A: 0, 10, 30, 40
        crv = self._claim(10.0)
        self.ctrl._sync_combobox()

        self.ctrl._invert_selected_keys(self._targets([0.0, 10.0, 30.0, 40.0]))

        led = self.ctrl.sequencer.ledger
        self.assertEqual(led.authored(), [(crv, 30.0)], led.to_dict())

    def test_paste_releases_the_claims_of_a_key_it_replaces(self):
        """Ctrl+V over a claimed key replaces it: its claims go with it."""
        cmds.setKeyframe(self.a, at="translateX", t=20, v=2.0)  # A: 0, 20, 40
        crv = self._claim(20.0)
        self.ctrl._sync_combobox()
        self.ctrl._copy_selected_keys(self._targets([40.0]))  # the value 5
        cmds.currentTime(20)

        self.ctrl._paste_selected_keys(self._targets([40.0]))

        self.assertEqual(
            cmds.keyframe(
                self.a, at="translateX", q=True, time=(20, 20), valueChange=True
            ),
            [5.0],
        )
        led = self.ctrl.sequencer.ledger
        self.assertFalse(led.owns_any(crv, 20.0), led.to_dict())

    def test_thin_releases_the_claims_of_the_keys_it_removes(self):
        """A deleted key takes its claims with it: left on the frame, a
        behavior's claim passed to the next key to land there."""
        cmds.setKeyframe(self.a, at="translateX", t=20, v=2.0)  # A: 0, 20, 40
        crv = self._claim(20.0)
        self.ctrl._sync_combobox()

        self.ctrl._thin_selected_keys(self._targets([0.0, 20.0, 40.0]))

        self.assertEqual(self._keys(self.a), [0.0, 40.0])
        led = self.ctrl.sequencer.ledger
        self.assertFalse(led.owns_any(crv, 20.0), led.to_dict())

    def test_simplify_releases_the_claims_of_the_keys_it_removes(self):
        """Both passes: a ramp key the reducer drops (the SHAPE pass) and a
        hold key the FLAT pass drops each give up their claims."""
        for f in range(0, 41, 4):  # a straight ramp: every middle key is neutral
            cmds.setKeyframe(self.a, at="translateX", t=f, v=float(f))
        cmds.keyTangent(self.a, at=["translateX"], itt="linear", ott="linear")
        crv = self._claim(8.0)
        self.ctrl._sync_combobox()

        self.ctrl._simplify_selected_keys(self._targets(self._keys(self.a)))

        self.assertEqual(self._keys(self.a), [0.0, 40.0])
        led = self.ctrl.sequencer.ledger
        self.assertFalse(led.owns_any(crv, 8.0), led.to_dict())

    def test_simplify_releases_the_claim_of_a_redundant_hold_key(self):
        for f, v in ((0, 1.0), (10, 1.0), (20, 1.0), (30, 1.0), (40, 0.0)):
            cmds.setKeyframe(self.a, at="translateX", t=f, v=v)
        cmds.keyTangent(self.a, at=["translateX"], ott="step")
        crv = self._claim(20.0)
        self.ctrl._sync_combobox()

        self.ctrl._simplify_selected_keys(self._targets(self._keys(self.a)))

        self.assertEqual(self._keys(self.a), [0.0, 40.0])
        led = self.ctrl.sequencer.ledger
        self.assertFalse(led.owns_any(crv, 20.0), led.to_dict())

    def _b_row(self, times):
        return [(str(self.b), "translateX", times, self._shot("S1").shot_id)]

    def _ott(self, cube, frame):
        crv = cmds.keyframe(cube, at="translateX", q=True, name=True)[0]
        return cmds.keyTangent(crv, q=True, time=(frame, frame), ott=True)[0]

    def _value(self, cube, frame):
        return cmds.keyframe(cube, at="translateX", q=True, t=(frame, frame), vc=True)

    def test_the_panels_copy_pastes_onto_another_object(self):
        """Ctrl+C / Ctrl+V are the panel's own clipboard (2026-10-10)."""
        self.ctrl._sync_combobox()
        self.ctrl._copy_selected_keys(self._targets([40.0]))  # A: the value 5
        cmds.currentTime(80)
        self.ctrl._paste_selected_keys(self._b_row([60.0]))
        self.assertEqual(self._keys(self.b), [60.0, 80.0, 100.0])
        self.assertEqual(self._value(self.b, 80), [5.0])

    def test_a_copied_gap_hold_pastes_as_the_key_was_before_the_hold(self):
        """2026-10-10: copying keys in the Graph Editor carried the shot
        system's gap hold -- the step it writes on the last key before a gap
        -- into the paste, as if the animator had keyed it."""
        self.ctrl._sync_combobox()
        self.ctrl.sequencer.reconcile_system_edits()  # holds A@40, before S1
        crv = cmds.keyframe(self.a, at="translateX", q=True, name=True)[0]
        claim = self.ctrl.sequencer.ledger.claimed_step(crv, 40.0)
        self.assertIsNotNone(claim, "the fixture's seam is not held")
        self.assertEqual(self._ott(self.a, 40), "step")

        self.ctrl._copy_selected_keys(self._targets([0.0, 40.0]))
        cmds.currentTime(70)
        self.ctrl._paste_selected_keys(self._b_row([60.0]))

        self.assertEqual(self._keys(self.b), [60.0, 70.0, 100.0, 110.0])
        self.assertEqual(self._ott(self.b, 110), claim[1])
        self.assertEqual(self._ott(self.a, 40), "step", "the source's hold stays")

    def test_a_step_pasted_over_a_held_seam_is_copied_as_a_step(self):
        """Bug: Ctrl+C read every step on a claimed seam as the system's
        hold, so the animator's own stepped key pasted over it (the claim
        stays on the frame) was copied with the old pre-hold tangent.
        Fixed: 2026-10-10"""
        self.ctrl._sync_combobox()
        self.ctrl.sequencer.reconcile_system_edits()  # holds A@40, before S1
        crv = cmds.keyframe(self.a, at="translateX", q=True, name=True)[0]
        self.assertIsNotNone(self.ctrl.sequencer.ledger.claimed_step(crv, 40.0))
        # What a Graph Editor paste leaves: another value, a fixed in side.
        cmds.setKeyframe(self.a, at="translateX", t=40, v=7.0)
        cmds.keyTangent(crv, time=(40, 40), itt="fixed", ott="step")

        self.ctrl._copy_selected_keys(self._targets([40.0]))
        cmds.currentTime(70)
        self.ctrl._paste_selected_keys(self._b_row([60.0]))

        self.assertEqual(self._value(self.b, 70), [7.0])
        self.assertEqual(self._ott(self.b, 70), "step")

    def test_an_animators_step_is_copied_as_a_step(self):
        cmds.keyTangent(self.a, at="translateX", time=(0, 0), ott="step")
        self.ctrl._sync_combobox()
        self.ctrl._copy_selected_keys(self._targets([0.0]))
        cmds.currentTime(80)
        self.ctrl._paste_selected_keys(self._b_row([60.0]))
        self.assertEqual(self._ott(self.b, 80), "step")

    def test_the_spacing_between_rows_survives_and_nothing_selected_pastes_back(
        self,
    ):
        """The earliest copied key lands on the playhead and the rest keep
        their distance from it, across rows -- with nothing selected, back on
        the rows they came from."""
        self.ctrl._sync_combobox()
        self.ctrl._copy_selected_keys(self._targets([40.0]) + self._b_row([100.0]))
        cmds.currentTime(10)
        self.ctrl._paste_selected_keys([])
        self.assertEqual(self._keys(self.a), [0.0, 10.0, 40.0])
        self.assertEqual(self._keys(self.b), [60.0, 70.0, 100.0])
        self.assertEqual(self._value(self.b, 70), [5.0])

    def test_a_broken_tangent_pastes_broken(self):
        crv = cmds.keyframe(self.a, at="translateX", q=True, name=True)[0]
        cmds.setKeyframe(self.a, at="translateX", t=20, v=2.0)
        cmds.keyTangent(crv, time=(20, 20), lock=False)
        cmds.keyTangent(crv, time=(20, 20), inAngle=30.0, outAngle=-15.0)
        self.ctrl._sync_combobox()
        self.ctrl._copy_selected_keys(self._targets([20.0]))
        cmds.currentTime(80)
        self.ctrl._paste_selected_keys(self._b_row([60.0]))
        b_crv = cmds.keyframe(self.b, at="translateX", q=True, name=True)[0]
        self.assertEqual(
            cmds.keyTangent(b_crv, q=True, time=(80, 80), lock=True), [False]
        )
        self.assertAlmostEqual(
            cmds.keyTangent(b_crv, q=True, time=(80, 80), inAngle=True)[0], 30.0, 3
        )
        self.assertAlmostEqual(
            cmds.keyTangent(b_crv, q=True, time=(80, 80), outAngle=True)[0], -15.0, 3
        )

    def test_a_paste_onto_rows_gone_since_the_copy_says_nothing_pasted(self):
        """Bug: the paste counted (and selected) every copied row whatever
        landed -- the clipboard outlives the scene, so a row whose object was
        deleted since keyed nothing yet read "Pasted 1 key onto 1 row".
        Fixed: 2026-10-10"""
        self.ctrl._sync_combobox()
        self.ctrl._copy_selected_keys(self._targets([40.0]))
        cmds.delete(self.a)
        cmds.currentTime(80)
        self.ctrl._paste_selected_keys([])  # back onto the copied rows
        self.assertIn("Nothing pasted", self.ctrl.ui.footer.setText.call_args[0][0])

    def test_nothing_copied_pastes_nothing(self):
        self.ctrl._sync_combobox()
        cmds.selectKey(clear=True)
        cmds.selectKey(self.a, attribute="translateX", time=(0, 40))
        cmds.copyKey(animation="keys")  # Maya's clipboard is not the panel's
        cmds.currentTime(80)
        self.ctrl._paste_selected_keys(self._b_row([60.0]))
        self.assertEqual(self._keys(self.b), [60.0, 100.0])

    def test_the_scoped_edit_reports_whether_it_ran(self):
        ran, _ = self.ctrl._key_selection_edit([], "noop", lambda o, s: None)
        self.assertFalse(ran, "no targets, nothing to run")

    def _ramp(self, attr, first=0, last=40, step=4):
        """A straight ramp on *attr*: every middle key is shape-neutral."""
        for f in range(first, last + 1, step):
            cmds.setKeyframe(self.a, at=attr, t=f, v=float(f))
        cmds.keyTangent(self.a, at=[attr], itt="linear", ott="linear")

    def _attr_keys(self, attr):
        return sorted(cmds.keyframe(self.a, q=True, at=attr) or [])

    def test_simplify_drops_the_keys_that_carry_no_shape(self):
        self._ramp("translateX")
        self.assertEqual(len(self._attr_keys("translateX")), 11)
        self.ctrl._sync_combobox()

        self.ctrl._simplify_selected_keys(self._targets(self._attr_keys("translateX")))

        self.assertEqual(self._attr_keys("translateX"), [0.0, 40.0])

    def test_simplify_leaves_the_attributes_beside_the_selection_alone(self):
        self._ramp("translateX")
        self._ramp("translateZ")
        self.ctrl._sync_combobox()

        self.ctrl._simplify_selected_keys(self._targets(self._attr_keys("translateX")))

        self.assertEqual(self._attr_keys("translateX"), [0.0, 40.0])
        self.assertEqual(len(self._attr_keys("translateZ")), 11, "sibling untouched")

    def test_thin_narrows_to_the_selected_attribute(self):
        """The sub-row IS the scope: the object's other channels keep theirs."""
        self._ramp("translateX")
        self._ramp("translateZ")
        self.ctrl._sync_combobox()

        self.ctrl._thin_selected_keys(self._targets(self._attr_keys("translateX")))

        self.assertEqual(self._attr_keys("translateX"), [0.0, 40.0])
        self.assertEqual(len(self._attr_keys("translateZ")), 11, "sibling untouched")

    def test_simplify_drops_a_redundant_HOLD_key(self):
        """The pass that matters on real footage: a stepped hold's interiors.

        Reported from a production assembly, where Simplify appeared to do
        nothing -- the reducer alone will not touch a stepped curve, and a
        hold is normally spelled with step out-tangents.
        """
        for f, v in ((0, 1.0), (10, 1.0), (20, 1.0), (30, 1.0), (40, 0.0)):
            cmds.setKeyframe(self.a, at="translateX", t=f, v=v)
        cmds.keyTangent(self.a, at=["translateX"], ott="step")
        self.assertEqual(self._attr_keys("translateX"), [0.0, 10.0, 20.0, 30.0, 40.0])
        self.ctrl._sync_combobox()

        self.ctrl._simplify_selected_keys(self._targets(self._attr_keys("translateX")))

        # The hold collapses to the pair that still states it: the step out of
        # frame 0 carries the value all the way to 40, where it drops.
        self.assertEqual(self._attr_keys("translateX"), [0.0, 40.0])

    def test_simplify_does_not_move_the_curve(self):
        """Keys go, values do not: the hold reads the same at every frame."""
        for f, v in ((0, 1.0), (10, 1.0), (20, 1.0), (30, 1.0), (40, 0.0)):
            cmds.setKeyframe(self.a, at="translateX", t=f, v=v)
        cmds.keyTangent(self.a, at=["translateX"], ott="step")
        frames = list(range(0, 41, 2))
        before = [
            cmds.keyframe(self.a, at="translateX", q=True, eval=True, time=(f, f))[0]
            for f in frames
        ]
        self.ctrl._sync_combobox()

        self.ctrl._simplify_selected_keys(self._targets(self._attr_keys("translateX")))

        after = [
            cmds.keyframe(self.a, at="translateX", q=True, eval=True, time=(f, f))[0]
            for f in frames
        ]
        for f, b, a in zip(frames, before, after):
            self.assertAlmostEqual(b, a, places=6, msg=f"frame {f} moved")

    def test_the_attribute_scope_reads_off_the_targets(self):
        targets = [
            (str(self.a), "translateX", [0.0], 1),
            (str(self.a), "translateZ", [0.0], 1),
            (str(self.a), "translateX", [40.0], 1),
        ]
        self.assertEqual(
            self.ctrl._target_attributes(targets), ["translateX", "translateZ"]
        )


class TestAttributeSelectionMirrorsTheChannelBox(_ControllerCase):
    """A sub-row IS a channel: selecting one highlights it in the Channel Box.

    Selecting an OBJECT row means the whole object, so it clears that
    highlight rather than listing the object's channels -- otherwise a scope
    the user set on one click keeps narrowing the edits they aim at the
    whole track afterwards.
    """

    def setUp(self):
        super().setUp()
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()
        self.mirrored = []
        self.ctrl._mirror_channel_box_attrs = lambda a: self.mirrored.append(list(a))

    def _clip(self, **data):
        tid = self.widget.add_track("probe")
        return self.widget.add_clip(tid, 0, 40, obj=str(self.a), **data)

    def test_a_sub_row_click_highlights_that_attribute(self):
        cid = self._clip(attr_name="translateX")
        self.ctrl.on_selection_changed([cid])
        self.assertEqual(self.mirrored, [["translateX"]])

    def test_an_object_row_click_clears_the_highlight(self):
        cid = self._clip(attributes=["translateX", "translateY"])
        self.ctrl.on_selection_changed([cid])
        self.assertEqual(self.mirrored, [[]], "the whole object, not its channels")

    def test_a_mixed_selection_is_object_scoped(self):
        sub = self._clip(attr_name="translateX")
        whole = self._clip(attributes=["translateX"])
        self.ctrl.on_selection_changed([sub, whole])
        self.assertEqual(self.mirrored, [[]])

    def test_a_header_label_click_clears_the_highlight(self):
        self.ctrl.on_track_selected([str(self.a)])
        self.assertEqual(self.mirrored, [[]])

    def test_a_key_selection_carries_its_attributes(self):
        cid = self._clip(attr_name="translateX")
        self.ctrl._mirror_key_selection([{"clip_id": cid, "times": [0.0, 40.0]}])
        self.assertEqual(self.mirrored, [["translateX"]])

    def test_an_emptied_key_selection_leaves_the_scope_alone(self):
        """It says nothing about scope; the clip selection outlives it."""
        self.ctrl._mirror_key_selection([])
        self.assertEqual(self.mirrored, [], "no call at all")

    def test_a_sub_row_label_click_selects_that_channel(self):
        """The header twin of clicking the sub-row's clip."""
        self.ctrl.on_sub_track_selected(
            [(str(self.a), "translateX"), (str(self.a), "translateZ")]
        )
        self.assertEqual(self.mirrored, [["translateX", "translateZ"]])
        self.assertIn(
            str(self.a), [n.split("|")[-1] for n in cmds.ls(sl=True, long=True) or []]
        )

    def test_a_sub_row_label_click_also_survives_a_list_payload(self):
        """Qt hands a Signal(list) back with its tuples turned into lists."""
        self.ctrl.on_sub_track_selected([[str(self.a), "translateY"]])
        self.assertEqual(self.mirrored, [["translateY"]])

    def test_the_header_wires_the_sub_row_signal(self):
        """The panel's own wiring connects the header's sub-row signal: read off
        the live connection, then acted on -- a label click the widget emits
        reaches the controller the slots built. Without its wiring row the
        click does nothing at all."""
        from unittest.mock import MagicMock

        from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
            ShotSequencerSlots,
        )

        switchboard = MagicMock()
        switchboard.loaded_ui.shot_sequencer = self.slots.ui
        panel = ShotSequencerSlots(switchboard)
        self.addCleanup(panel.controller.remove_callbacks)
        seen = []
        panel.controller._mirror_channel_box_attrs = lambda a: seen.append(list(a))
        self.assertIn(
            ("sub_track_selected", panel.controller.on_sub_track_selected),
            getattr(self.widget, "_slots_connections", []),
        )
        self.widget.sub_track_selected.emit([[str(self.a), "translateZ"]])
        _process_events()
        self.assertEqual(seen, [["translateZ"]])

    def test_a_clip_with_no_object_touches_nothing(self):
        tid = self.widget.add_track("probe")
        cid = self.widget.add_clip(tid, 0, 40, label="not an object")
        self.ctrl.on_selection_changed([cid])
        self.assertEqual(self.mirrored, [])


@base_test.skipIfBatch("the Graph Editor is a GUI window")
class _GraphEditorCase(_ControllerCase):
    """A controller over a live widget, with every Graph Editor closed first."""

    def setUp(self):
        # Decided before the fixture exists: a skip raised in setUp skips
        # tearDown, which would leave the widget and its callbacks alive.
        self._close_graph_editors()
        if self._shown():
            self.skipTest("this layout docks a Graph Editor in a view pane")
        super().setUp()
        self.addCleanup(self._close_graph_editors)

    @staticmethod
    def _close_graph_editors():
        for panel in cmds.getPanel(scriptType="graphEditor") or []:
            window = panel + "Window"  # tearOffRestorePanel's naming
            if cmds.workspaceControl(window, exists=True):
                cmds.workspaceControl(window, edit=True, close=True)
        _process_events()

    @staticmethod
    def _shown() -> list:
        """The Graph Editor panels open in a view pane or a window.

        A collapsed dock counts -- it is open, only folded.  The window's own
        flag is read because ``getPanel -visiblePanels`` lists neither that
        nor a freshly opened window's panel before Maya's next idle.
        """
        visible = set(cmds.getPanel(visiblePanels=True) or [])

        def windowed(panel):
            window = panel + "Window"
            if not cmds.workspaceControl(window, exists=True):
                return False
            return cmds.workspaceControl(window, query=True, visible=True)

        return [
            p
            for p in cmds.getPanel(scriptType="graphEditor") or []
            if p in visible or windowed(p)
        ]

    def _sub_row_clip(self):
        tid = self.widget.add_track("probe")
        return self.widget.add_clip(tid, 0, 40, obj=str(self.a), attr_name="translateX")


class TestSelectionLeavesTheGraphEditorAlone(_GraphEditorCase):
    """A panel click mirrors the selection into Maya; it opens no editor.

    An open Graph Editor follows the scene selection by itself, so the panel
    never needs to open one.  It used to (``GraphEditor``, which is
    ``tearOffRestorePanel``) on every clip or header click: a closed Graph
    Editor popped open, and a collapsed one expanded.
    """

    def test_a_clip_click_leaves_a_closed_graph_editor_closed(self):
        self.ctrl.on_selection_changed([self._sub_row_clip()])
        _process_events()
        self.assertEqual(self._shown(), [])
        selected = [n.split("|")[-1] for n in cmds.ls(sl=True, long=True) or []]
        self.assertIn(str(self.a), selected, "the selection itself still lands")

    def test_a_header_click_leaves_a_closed_graph_editor_closed(self):
        self.ctrl.on_track_selected([str(self.a)])
        self.ctrl.on_sub_track_selected([(str(self.a), "translateX")])
        _process_events()
        self.assertEqual(self._shown(), [])

    def test_an_open_graph_editor_still_lists_the_clicked_object(self):
        """Why nothing needs opening: an open one follows the selection."""
        import maya.mel as mel

        mel.eval("GraphEditor")  # the user's own open
        _process_events()
        panels = self._shown()
        self.assertTrue(panels, "the Graph Editor did not open")
        outline = panels[0] + "OutlineEd"
        if not mel.eval(f'isAutoLoad "{outline}"'):
            self.skipTest("the Graph Editor's Auto Load is off")
        listed = cmds.outlinerEditor(outline, query=True, mainListConnection=True)
        self.ctrl.on_track_selected([str(self.b)])
        _process_events()
        objects = cmds.selectionConnection(listed, query=True, object=True) or []
        self.assertEqual([o.split("|")[-1] for o in objects], [str(self.b)])


class TestAKeySelectionReachesTheGraphEditor(_GraphEditorCase):
    """Keys selected in the panel are selected in the Graph Editor, and a
    closed or collapsed Graph Editor stays that way (2026-10-07: "can we
    select keys in the graph editor when they are selected in the sequencer
    without showing the graph editor if its tab is currently minimized?").

    The Graph Editor lists the SELECTED objects' curves, so a key whose object
    is not selected is selected where nobody can see it -- and selecting an
    object CLEARS Maya's key selection (measured), so the objects go on the
    selection first and the keys after.
    """

    def _selected_keys(self) -> dict:
        return {
            crv: cmds.keyframe(crv, query=True, selected=True, timeChange=True)
            for crv in cmds.keyframe(query=True, selected=True, name=True) or []
        }

    def _a_tx(self) -> str:
        return cmds.listConnections(f"{self.a}.translateX", type="animCurve")[0]

    def _picked(self) -> set:
        return {n.split("|")[-1] for n in cmds.ls(sl=True, long=True) or []}

    def _select_keys(self, cid, times):
        """The panel's key mirror, its deferred Channel Box half run inline:
        Maya's idle queue never runs while the harness drives it through the
        command port, so ``flushIdleQueue`` would wait forever."""
        from unittest import mock
        from mayatk.anim_utils.shots.shot_sequencer import scene_selection

        with mock.patch.object(
            scene_selection.cmds, "evalDeferred", new=lambda fn, **_kw: fn()
        ):
            self.ctrl.on_key_selection_changed([{"clip_id": cid, "times": times}])
        _process_events()

    def test_the_keys_object_is_selected_and_its_keys_stay_selected(self):
        cmds.select(str(self.b), replace=True)  # not the key's object
        self._select_keys(self._sub_row_clip(), [40.0])
        self.assertEqual(self._selected_keys(), {self._a_tx(): [40.0]})
        self.assertIn(str(self.a), self._picked(), "the Graph Editor lists it")

    def test_the_footer_names_the_selected_keys(self):
        """The footer reads the selection: how many keys, their frames, on
        which channel of which object (2026-10-10: "footer should show key
        specific info when keys are selected like frame range, etc")."""
        from unittest import mock

        with mock.patch.object(self.ctrl, "_set_footer") as footer:
            self._select_keys(self._sub_row_clip(), [0.0, 40.0])
        text = footer.call_args[0][0]
        self.assertTrue(text.startswith("2 keys · frames 0–40 (40f"), text)
        self.assertIn("translateX on", text)

    def test_nothing_selected_in_maya_is_no_obstacle(self):
        """With no object and no key selected, the clear before the mirror
        raised ("Error retrieving default arguments") and no key was selected
        at all -- nor was an emptied panel selection mirrored."""
        cmds.select(clear=True)
        self.ctrl.on_key_selection_changed([])  # an emptied selection
        cmds.select(clear=True)
        self._select_keys(self._sub_row_clip(), [40.0])
        self.assertEqual(self._selected_keys(), {self._a_tx(): [40.0]})

    def test_a_wider_object_selection_is_kept(self):
        cmds.select([str(self.a), str(self.b)], replace=True)
        self._select_keys(self._sub_row_clip(), [40.0])
        self.assertEqual(self._picked(), {str(self.a), str(self.b)})
        self.assertEqual(self._selected_keys(), {self._a_tx(): [40.0]})

    def test_a_closed_graph_editor_stays_closed(self):
        self._select_keys(self._sub_row_clip(), [0.0])
        self.assertEqual(self._shown(), [])
        self.assertEqual(self._selected_keys(), {self._a_tx(): [0.0]})

    def test_a_collapsed_graph_editor_stays_collapsed(self):
        import maya.mel as mel

        mel.eval("GraphEditor")  # the user's own open
        _process_events()
        panels = self._shown()
        if not panels:
            self.skipTest("the Graph Editor did not open")
        window = panels[0] + "Window"
        if not cmds.workspaceControl(window, exists=True):
            self.skipTest("this layout holds the Graph Editor in a view pane")
        # The harness's Maya reads the user's prefs: put back the floating
        # state found, so no layout it might save carries this dock.
        if cmds.workspaceControl(window, query=True, floating=True):
            self.addCleanup(cmds.workspaceControl, window, edit=True, floating=True)
        cmds.workspaceControl(window, edit=True, dockToMainWindow=("right", False))
        cmds.workspaceControl(window, edit=True, collapse=True)
        _process_events()
        if not cmds.workspaceControl(window, query=True, collapse=True):
            self.skipTest("this Maya does not collapse the docked Graph Editor")
        self._select_keys(self._sub_row_clip(), [40.0])
        self.assertTrue(cmds.workspaceControl(window, query=True, collapse=True))
        self.assertEqual(self._selected_keys(), {self._a_tx(): [40.0]})
        listed = cmds.outlinerEditor(
            panels[0] + "OutlineEd", query=True, mainListConnection=True
        )
        objects = cmds.selectionConnection(listed, query=True, object=True) or []
        self.assertIn(str(self.a), [o.split("|")[-1] for o in objects])


class TestBoundCapDrags(_ControllerCase):
    """The caps before the first shot and after the last are those shots'
    own bounds: a plain drag moves the bound and nothing else."""

    def setUp(self):
        super().setUp()
        self.ctrl._sync_combobox()
        self.ctrl._sync_to_widget()

    def test_the_last_shots_tail_cap_moves_the_bound_only(self):
        self.ctrl.on_gap_left_resized(120.0, 130.0)
        s1 = self._shot("S1")
        self.assertEqual((s1.start, s1.end), (60.0, 130.0))
        self.assertEqual(self._keys(self.b), [60.0, 100.0], "keys stay")

    def test_the_first_shots_head_cap_moves_the_bound_only(self):
        self.ctrl.on_gap_resized(0.0, -10.0)
        s0 = self._shot("S0")
        self.assertEqual((s0.start, s0.end), (-10.0, 50.0))
        self.assertEqual(self._keys(self.a), [0.0, 40.0])

    def test_a_real_gap_edge_still_slides(self):
        self.ctrl.on_gap_left_resized(50.0, 55.0)
        s0 = self._shot("S0")
        self.assertEqual((s0.start, s0.end), (5.0, 55.0))
        self.assertEqual(self._keys(self.a), [5.0, 45.0])

    def test_both_caps_are_placed(self):
        caps = [
            (o._head, o._tail, o._start)
            for o in self.widget._gap_overlays
            if o._head or o._tail
        ]
        self.assertIn((True, False, 0.0), caps)
        self.assertIn((False, True, 120.0), caps)


class TestShotComboboxCells(_ControllerCase):
    """The shot dropdown's rows carry cells that edit the shot in place."""

    def setUp(self):
        super().setUp()
        self.cmb = self.slots.ui.cmb_shot
        self.ctrl._configure_shot_combobox(self.cmb)
        self.ctrl._sync_combobox()

    def test_rows_carry_cells(self):
        self.assertEqual(
            self.cmb.item_cells(1),
            {"name": "S1", "start": 60.0, "end": 120.0, "description": ""},
        )
        self.assertEqual(self.cmb.itemText(1), "S1  [60-120]")
        self.assertTrue(self.cmb.rename_on_double_click)

    def test_editing_the_name_renames(self):
        self.cmb.on_cells_edited.emit(0, {"name": "Opening"})
        self.assertEqual(self._shot("Opening").start, 0.0)
        self.assertEqual(self.cmb.itemText(0), "Opening  [0-50]")

    def test_editing_the_end_moves_that_bound_only(self):
        self.cmb.on_cells_edited.emit(0, {"end": 45})
        self.assertEqual(self._shot("S0").end, 45.0)
        self.assertEqual(self._keys(self.a), [0.0, 40.0], "keys stay")
        self.assertEqual(self._shot("S1").start, 55.0, "downstream rippled -5")

    def test_editing_the_start_moves_the_shot(self):
        self.cmb.on_cells_edited.emit(1, {"start": 70})
        s1 = self._shot("S1")
        self.assertEqual((s1.start, s1.end), (70.0, 130.0))
        self.assertEqual(self._keys(self.b), [70.0, 110.0], "keys ride")

    def test_a_blank_name_is_ignored(self):
        self.cmb.on_cells_edited.emit(0, {"name": "   "})
        self.assertIsNotNone(self._shot("S0"))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    )
    sys.exit(0 if result.wasSuccessful() else 1)
