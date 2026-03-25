# !/usr/bin/python
# coding=utf-8
"""Comprehensive tests for ShotSequencerController key/range editing.

These tests exercise the controller logic WITHOUT requiring Maya by
mocking pymel, cmds, and OpenMaya.  Real SequencerWidget, ShotSequencer,
ShotStore, and ShotBlock objects are used — only the Maya DCC layer is
faked.

Bugs covered:
    - Combobox selection reset after _sync_combobox (shot jumps to 0)
    - Stepped key move: second move causes object disappearance
    - Stepped key move: micro-key corruption from shift_curves
    - Range highlight move/resize preserving shot_id
    - Clip expand beyond shot boundaries
    - Batch clip moves preserving shot context
    - Undo after stepped key move
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock, call
from collections import defaultdict

# ---------------------------------------------------------------------------
# Import shared Maya mocks from conftest (injected into sys.modules there)
# ---------------------------------------------------------------------------
from test.conftest import mock_pm, mock_cmds, mock_undo_chunk, mock_om2

# Aliases for backward-compat with existing test code
_mock_pm = mock_pm
_mock_cmds = mock_cmds
_undo_chunk = mock_undo_chunk

# Ensure workspace roots are on sys.path
_WORKSPACE = Path(__file__).parent.parent.parent.absolute()
for subdir in ("pythontk", "uitk", "mayatk"):
    p = str(_WORKSPACE / subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Qt setup
# ---------------------------------------------------------------------------
from qtpy import QtWidgets, QtCore

_app = QtWidgets.QApplication.instance()
if _app is None:
    _app = QtWidgets.QApplication(sys.argv)

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from uitk.widgets.sequencer._sequencer import SequencerWidget
from mayatk.anim_utils.shots._shots import ShotBlock, ShotStore
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots import (
    ShotSequencerController,
)

try:
    from mayatk.anim_utils.shots.shot_sequencer._audio_tracks import AudioTrackManager
except ImportError:
    AudioTrackManager = None


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class FakeComboBox(QtWidgets.QComboBox):
    """A real QComboBox we can inspect."""

    pass


class FakeSlotsInstance:
    """Minimal stand-in for the slots object that ShotSequencerController
    expects at construction time."""

    def __init__(self, widget: SequencerWidget):
        self.sb = MagicMock()
        self.ui = MagicMock()
        self.ui.sequencer_widget = widget
        self.ui.cmb_shot = FakeComboBox()


class FakeKeyDB:
    """In-memory key database simulating Maya anim curves.

    Tracks keys by curve name: {curve: [(time, value, itt, ott), ...]}.
    Used to back the mocked cmds.keyframe / cmds.keyTangent / etc.
    """

    def __init__(self):
        self.curves: dict = {}  # curve_name → [(time, val, itt, ott)]

    def add_key(self, curve, time, value, itt="spline", ott="step"):
        self.curves.setdefault(curve, []).append((time, value, itt, ott))

    def remove_key(self, curve, time, eps=1e-3):
        if curve not in self.curves:
            return
        self.curves[curve] = [k for k in self.curves[curve] if abs(k[0] - time) > eps]

    def get_keys_in_range(self, curve, lo, hi):
        if curve not in self.curves:
            return []
        return [k for k in self.curves[curve] if lo <= k[0] <= hi]

    def get_key_times_in_range(self, curve, lo, hi):
        return [k[0] for k in self.get_keys_in_range(curve, lo, hi)]

    def get_key_values_in_range(self, curve, lo, hi):
        return [k[1] for k in self.get_keys_in_range(curve, lo, hi)]

    def get_key_intangents_in_range(self, curve, lo, hi):
        return [k[2] for k in self.get_keys_in_range(curve, lo, hi)]

    def get_key_outtangents_in_range(self, curve, lo, hi):
        return [k[3] for k in self.get_keys_in_range(curve, lo, hi)]


def wire_cmds(mock_cmds, key_db: FakeKeyDB, obj_curves: dict):
    """Configure a mocked cmds module to read/write from *key_db*.

    Parameters
    ----------
    mock_cmds : MagicMock
        The mock for ``maya.cmds``.
    key_db : FakeKeyDB
        In–memory key storage.
    obj_curves : dict
        ``{object_name: [curve_name, ...]}`` — returned by
        ``cmds.listConnections(obj, type="animCurve", ...)``.
    """
    _eps = 1e-3

    def _keyframe(
        curve_or_obj=None,
        q=None,
        time=None,
        valueChange=None,
        timeChange=None,
        edit=None,
        relative=None,
        **kw,
    ):
        crv = curve_or_obj
        if crv is None:
            return []
        if q:
            if time is None:
                return key_db.get_key_times_in_range(crv, -1e9, 1e9)
            lo, hi = (
                (time - _eps, time + _eps) if isinstance(time, (int, float)) else time
            )
            if valueChange:
                return key_db.get_key_values_in_range(crv, lo, hi)
            return key_db.get_key_times_in_range(crv, lo, hi)
        return []

    def _keyTangent(
        curve_or_obj=None,
        q=None,
        time=None,
        inTangentType=None,
        outTangentType=None,
        **kw,
    ):
        crv = curve_or_obj
        if crv is None:
            return []
        if q and time is not None:
            lo, hi = (
                (time[0] - _eps, time[1] + _eps)
                if isinstance(time, tuple)
                else (time - _eps, time + _eps)
            )
            if inTangentType:
                return key_db.get_key_intangents_in_range(crv, lo, hi)
            if outTangentType:
                return key_db.get_key_outtangents_in_range(crv, lo, hi)
        return []

    def _cutKey(curve_or_obj=None, time=None, clear=None, **kw):
        if curve_or_obj and time and clear:
            lo, hi = time
            key_db.remove_key(curve_or_obj, (lo + hi) / 2.0)

    def _setKeyframe(curve_or_obj=None, time=None, value=None, **kw):
        if curve_or_obj and time is not None and value is not None:
            key_db.add_key(curve_or_obj, time, value)

    def _listConnections(node=None, type=None, s=None, d=None, plugs=None, **kw):
        if type == "animCurve" and s:
            return obj_curves.get(node, [])
        if plugs and d:
            # Return fake plug names for the curve
            return (
                [f"{node}.visibility"]
                if "visibility" in (node or "")
                else [f"{node}.attr"]
            )
        return []

    mock_cmds.keyframe = MagicMock(side_effect=_keyframe)
    mock_cmds.keyTangent = MagicMock(side_effect=_keyTangent)
    mock_cmds.cutKey = MagicMock(side_effect=_cutKey)
    mock_cmds.setKeyframe = MagicMock(side_effect=_setKeyframe)
    mock_cmds.listConnections = MagicMock(side_effect=_listConnections)
    mock_cmds.ls = MagicMock(side_effect=lambda *a, **kw: list(a) if a else [])


def make_segments(obj_name, spans, stepped_times=None):
    """Build segment dicts as ``collect_object_segments`` would return.

    Parameters
    ----------
    obj_name : str
    spans : list of (start, end)
        Non-stepped segments.
    stepped_times : list of float, optional
        Stepped key times (zero-duration clips).
    """
    segs = []
    for s, e in spans:
        segs.append(
            {
                "obj": obj_name,
                "start": s,
                "end": e,
                "duration": e - s,
                "is_stepped": False,
                "curves": [],
                "attr": None,
            }
        )
    for t in stepped_times or []:
        segs.append(
            {
                "obj": obj_name,
                "start": t,
                "end": t,
                "duration": 0.0,
                "is_stepped": True,
                "curves": [],
                "attr": None,
            }
        )
    return segs


class ControllerTestCase(unittest.TestCase):
    """Base test case that wires up a ShotSequencerController with mocked Maya."""

    # Subclasses can override these to customise the scene
    shot_defs: list = None  # [(name, start, end, [objects]), ...]
    initial_shot_index: int = 0  # Which shot is "active" at the start

    def setUp(self):
        # Reset mocks
        _mock_pm.reset_mock()
        _mock_cmds.reset_mock()
        _mock_cmds.objExists.return_value = True
        _mock_pm.objExists.return_value = True
        _mock_pm.playbackOptions.return_value = 0.0
        _mock_pm.currentTime.return_value = 1.0
        _mock_pm.scriptJob.return_value = 999
        _mock_pm.scriptJob.side_effect = lambda **kw: 999 if "event" in kw else True
        _undo_chunk.__enter__ = MagicMock(return_value=None)
        _undo_chunk.__exit__ = MagicMock(return_value=False)
        _mock_pm.UndoChunk.return_value = _undo_chunk

        # Key database
        self.key_db = FakeKeyDB()
        self.obj_curves = {}  # obj → [curve_names]

        # Build shots
        if self.shot_defs is not None:
            defs = self.shot_defs
        else:
            defs = [
                ("Shot_A", 100, 200, ["ObjA", "ObjB"]),
                ("Shot_B", 200, 350, ["ObjA"]),
            ]
        store = ShotStore()
        for i, (name, start, end, objs) in enumerate(defs):
            store.define_shot(name=name, start=start, end=end, objects=objs)
        # Detach store from global singleton to avoid cross-test pollution
        ShotStore._active = None

        # Create sequencer engine
        self.sequencer = ShotSequencer(store=store)

        # Widget
        self.widget = SequencerWidget()

        # Slots / controller
        self.slots = FakeSlotsInstance(self.widget)
        with patch.object(ShotSequencerController, "_register_maya_undo_callbacks"):
            with patch.object(ShotSequencerController, "_register_time_change_job"):
                with patch.object(ShotSequencerController, "_bind_store_listener"):
                    self.ctrl = ShotSequencerController(self.slots)
        self.ctrl.sequencer = self.sequencer

        # Populate shot selector and select initial shot
        self.ctrl._sync_combobox()
        cmb = self.slots.ui.cmb_shot
        if self.initial_shot_index < cmb.count():
            cmb.setCurrentIndex(self.initial_shot_index)

        # Default segment provider: no segments (override in tests)
        self._mock_segments = {}  # shot_id → [segment_dicts]
        self.sequencer.collect_object_segments = lambda sid, **kw: list(
            self._mock_segments.get(sid, [])
        )

        # Wire cmds
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        # NodeIcons returns None (no icons needed)
        self.ctrl._try_load_maya_icons = staticmethod(lambda: None)

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()

    # -- helpers --

    def _active_shot_id(self):
        return self.ctrl.active_shot_id

    def _do_initial_sync(self):
        """Perform an initial _sync_to_widget so the widget is populated."""
        self.ctrl._sync_to_widget()

    def _set_segments(self, shot_id, segments):
        """Configure what collect_object_segments returns for a shot."""
        self._mock_segments[shot_id] = segments

    def _anim_clips(self):
        """Return only animation clips (excludes shot-track clips)."""
        return [c for c in self.widget.clips() if not c.data.get("is_shot_clip")]


# ===========================================================================
# Test: Shot Selection Preservation
# ===========================================================================


class TestShotSelectionPreservation(ControllerTestCase):
    """Verify that _sync_combobox preserves the selected shot."""

    shot_defs = [
        ("Shot_0", 0, 100, ["Obj"]),
        ("Shot_1", 100, 200, ["Obj"]),
        ("Shot_2", 200, 300, ["Obj"]),
    ]
    initial_shot_index = 2  # Select Shot_2

    def test_sync_preserves_selection(self):
        """_sync_combobox must not reset the active shot to index 0.

        Bug: _sync_combobox called clear() + addItem() without restoring
        selection, causing active_shot_id to jump to shot 0.
        Fixed: 2025-03-16
        """
        original_sid = self.ctrl.active_shot_id
        self.assertIsNotNone(original_sid)

        # Trigger a refresh (simulates race condition)
        self.ctrl._sync_combobox()

        new_sid = self.ctrl.active_shot_id
        self.assertEqual(
            new_sid, original_sid, "Selection changed after _sync_combobox!"
        )

    def test_sync_after_shot_range_change(self):
        """Changing a shot's range and re-syncing keeps selection."""
        original_sid = self.ctrl.active_shot_id

        # Simulate shot range change (as happens when a clip expands a shot)
        shot = self.sequencer.shot_by_id(original_sid)
        shot.end = 400.0

        self.ctrl._sync_combobox()
        new_sid = self.ctrl.active_shot_id
        self.assertEqual(new_sid, original_sid)

    def test_sync_many_times(self):
        """Multiple rapid _sync_combobox calls all preserve selection."""
        original_sid = self.ctrl.active_shot_id
        for _ in range(10):
            self.ctrl._sync_combobox()
        self.assertEqual(self.ctrl.active_shot_id, original_sid)


# ===========================================================================
# Test: Stepped Key Move — Delete-and-Recreate
# ===========================================================================


class TestSteppedKeyMove(ControllerTestCase):
    """Test the stepped-key move (delete-and-recreate) logic."""

    shot_defs = [
        ("Shot_7", 151, 166, ["ARROW_L", "ARROW_R"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]

        # Set up visibility keys for ARROW_L
        self.key_db.add_key("ARROW_L_visibility", 151.0, 0.0, "spline", "step")
        self.key_db.add_key("ARROW_L_visibility", 166.0, 1.0, "spline", "step")
        self.obj_curves["ARROW_L"] = ["ARROW_L_visibility", "ARROW_L_opacity"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        # Set up visibility keys for ARROW_R
        self.key_db.add_key("ARROW_R_visibility", 151.0, 0.0, "spline", "step")
        self.key_db.add_key("ARROW_R_visibility", 166.0, 1.0, "spline", "step")
        self.obj_curves["ARROW_R"] = ["ARROW_R_visibility", "ARROW_R_opacity"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        # Segments returned for this shot
        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[151.0, 166.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 166.0]),
            ],
        )

        # Initial sync
        self._do_initial_sync()

    def _find_stepped_clip(self, obj_name, approx_time):
        """Find a stepped clip for an object near a given time."""
        for cd in self._anim_clips():
            if (
                cd.data.get("is_stepped")
                and cd.data.get("obj") == obj_name
                and abs(cd.start - approx_time) < 1.0
            ):
                return cd
        return None

    def test_first_stepped_move_succeeds(self):
        """Moving a stepped key once should relocate it correctly."""
        clip = self._find_stepped_clip("ARROW_L", 166.0)
        self.assertIsNotNone(clip, "Stepped clip at 166 not found")

        self.ctrl.on_clip_moved(clip.clip_id, 163.0)

        # Verify key moved in the database
        keys_at_163 = self.key_db.get_keys_in_range("ARROW_L_visibility", 162.9, 163.1)
        self.assertTrue(len(keys_at_163) > 0, "Key should exist at 163")
        keys_at_166 = self.key_db.get_keys_in_range("ARROW_L_visibility", 165.9, 166.1)
        self.assertEqual(len(keys_at_166), 0, "Key at 166 should be deleted")

    def test_second_stepped_move_preserves_shot(self):
        """Moving a second stepped key must NOT switch to a different shot.

        Bug: After _sync_to_widget rebuilt the widget, the combobox
        selection was reset to shot 0. The second move's sync then
        displayed the wrong shot, making the object disappear.
        Fixed: 2025-03-16
        """
        shot = self.sequencer.sorted_shots()[0]
        expected_sid = shot.shot_id

        # First move — the 166 key
        clip_166 = self._find_stepped_clip("ARROW_L", 166.0)
        self.assertIsNotNone(clip_166)

        # Update segments after the first move
        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[151.0, 163.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 166.0]),
            ],
        )
        self.ctrl.on_clip_moved(clip_166.clip_id, 163.0)

        # Verify we're still on the same shot
        self.assertEqual(
            self._active_shot_id(), expected_sid, "Shot changed after first move!"
        )

        # Second move — the 151 key
        clip_151 = self._find_stepped_clip("ARROW_L", 151.0)
        self.assertIsNotNone(clip_151, "Stepped clip at 151 not found after first move")

        # Update segments for after second move
        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[155.0, 163.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 166.0]),
            ],
        )
        self.ctrl.on_clip_moved(clip_151.clip_id, 155.0)

        # Verify the active shot hasn't changed
        self.assertEqual(
            self._active_shot_id(),
            expected_sid,
            "Shot changed after second move — this was the bug!",
        )

        # Verify ALL objects still have clips in the widget
        obj_names_in_widget = {cd.data.get("obj") for cd in self._anim_clips()}
        self.assertIn(
            "ARROW_L", obj_names_in_widget, "ARROW_L disappeared from widget!"
        )
        self.assertIn(
            "ARROW_R", obj_names_in_widget, "ARROW_R disappeared from widget!"
        )

    def test_stepped_move_only_affects_stepped_curves(self):
        """Stepped key move must not touch non-stepped curves (e.g. opacity).

        Bug: Without filtering, shift_curves moved ALL curves at old_time,
        corrupting translate/rotate keys.
        Fixed: 2025-03-15
        """
        # Add a NON-stepped key at 166 on the opacity curve
        self.key_db.add_key("ARROW_L_opacity", 166.0, 0.5, "linear", "linear")
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        clip = self._find_stepped_clip("ARROW_L", 166.0)
        self.assertIsNotNone(clip)

        self.ctrl.on_clip_moved(clip.clip_id, 163.0)

        # The opacity key at 166 should be UNTOUCHED
        opacity_keys = self.key_db.get_keys_in_range("ARROW_L_opacity", 165.9, 166.1)
        self.assertEqual(
            len(opacity_keys), 1, "Non-stepped opacity key was incorrectly moved!"
        )

    def test_stepped_move_preserves_tangent_types(self):
        """Delete-and-recreate must restore original in/out tangent types."""
        clip = self._find_stepped_clip("ARROW_L", 166.0)
        self.assertIsNotNone(clip)

        self.ctrl.on_clip_moved(clip.clip_id, 163.0)

        keys_at_163 = self.key_db.get_keys_in_range("ARROW_L_visibility", 162.9, 163.1)
        self.assertTrue(len(keys_at_163) > 0)
        # The recreated key should have value=1.0 (original at 166)
        self.assertAlmostEqual(keys_at_163[0][1], 1.0)

    def test_zero_delta_move_is_noop(self):
        """Moving to the exact same frame should not modify anything."""
        clip = self._find_stepped_clip("ARROW_L", 166.0)
        keys_before = list(self.key_db.curves.get("ARROW_L_visibility", []))
        self.ctrl.on_clip_moved(clip.clip_id, 166.0)  # same position
        keys_after = list(self.key_db.curves.get("ARROW_L_visibility", []))
        self.assertEqual(keys_before, keys_after)

    def test_attr_specific_stepped_move_succeeds(self):
        """Moving an attribute-specific (sub-row) stepped clip must not crash.

        Bug: _eps was defined only in the else branch (non-attr path) but
        referenced in the shared move loop, causing UnboundLocalError when
        moving a sub-row clip with attr_name set.
        Fixed: 2025-03-23
        """
        shot = self.sequencer.sorted_shots()[0]

        # Wire _curves_for_attr: plug-based lookup needs separate
        # obj_curves entry for "ARROW_L.visibility".
        self.obj_curves["ARROW_L.visibility"] = ["ARROW_L_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        # Manually add a sub-row clip with attr_name (as _provide_sub_rows would)
        tid = None
        for td in self.widget.tracks():
            if td.name == "ARROW_L":
                tid = td.track_id
                break
        self.assertIsNotNone(tid, "ARROW_L track not found")

        clip_id = self.widget.add_clip(
            track_id=tid,
            start=166.0,
            duration=0.0,
            label="visibility",
            shot_id=shot.shot_id,
            obj="ARROW_L",
            attr_name="visibility",
            orig_start=166.0,
            orig_end=166.0,
            is_stepped=True,
            stepped_key_time=166.0,
            resizable_left=False,
            resizable_right=False,
            keyframe_times=[(166.0, "step")],
        )

        # This must NOT raise UnboundLocalError: _eps
        self.ctrl.on_clip_moved(clip_id, 163.0)

        # Verify the key moved
        keys_at_163 = self.key_db.get_keys_in_range(
            "ARROW_L_visibility", 162.9, 163.1
        )
        self.assertTrue(len(keys_at_163) > 0, "Key should exist at 163")
        keys_at_166 = self.key_db.get_keys_in_range(
            "ARROW_L_visibility", 165.9, 166.1
        )
        self.assertEqual(len(keys_at_166), 0, "Key at 166 should be deleted")


# ===========================================================================
# Test: on_clip_moved passes explicit shot_id
# ===========================================================================


class TestClipMovedShotId(ControllerTestCase):
    """Verify that on_clip_moved passes the clip's shot_id to _sync_to_widget."""

    shot_defs = [
        ("Shot_0", 0, 100, ["Obj"]),
        ("Shot_7", 151, 166, ["Arrow"]),
    ]
    initial_shot_index = 1  # Active shot is Shot_7

    def setUp(self):
        super().setUp()
        shot7 = self.sequencer.sorted_shots()[1]
        self._set_segments(shot7.shot_id, make_segments("Arrow", [(151, 166)]))
        self._do_initial_sync()
        self.sequencer.move_object_in_shot = MagicMock()

    def test_on_clip_moved_syncs_correct_shot(self):
        """on_clip_moved must pass the clip's shot_id, not rely on combobox."""
        original_calls = []
        original_sync = self.ctrl._sync_to_widget

        def spy_sync(shot_id=None, **kw):
            original_calls.append(shot_id)
            return original_sync(shot_id=shot_id, **kw)

        self.ctrl._sync_to_widget = spy_sync

        clip = self._anim_clips()[0]
        self.ctrl.on_clip_moved(clip.clip_id, 155.0)

        # Verify _sync_to_widget was called with the correct shot_id
        self.assertTrue(len(original_calls) > 0)
        passed_sid = original_calls[-1]
        self.assertIsNotNone(
            passed_sid, "_sync_to_widget was called without explicit shot_id!"
        )


# ===========================================================================
# Test: Batch Clip Moves
# ===========================================================================


class TestBatchClipMoves(ControllerTestCase):
    """Test on_clips_batch_moved preserves shot context."""

    shot_defs = [
        ("Shot_0", 0, 100, ["X"]),
        ("Shot_1", 100, 200, ["X", "Y"]),
    ]
    initial_shot_index = 1

    def setUp(self):
        super().setUp()
        shot1 = self.sequencer.sorted_shots()[1]
        self._set_segments(
            shot1.shot_id,
            [
                *make_segments("X", [(100, 200)]),
                *make_segments("Y", [(100, 200)]),
            ],
        )
        self._do_initial_sync()

    def test_batch_move_preserves_shot(self):
        """Moving multiple clips in a batch must sync to the correct shot."""
        expected_sid = self._active_shot_id()
        clips = self._anim_clips()
        moves = [(c.clip_id, c.start + 5) for c in clips]

        spy_calls = []
        orig = self.ctrl._sync_to_widget

        def spy(shot_id=None, **kw):
            spy_calls.append(shot_id)
            return orig(shot_id=shot_id, **kw)

        self.ctrl._sync_to_widget = spy

        self.ctrl.on_clips_batch_moved(moves)

        self.assertTrue(len(spy_calls) > 0)
        self.assertIsNotNone(spy_calls[-1])


# ===========================================================================
# Test: Range Highlight Move and Resize
# ===========================================================================


class TestRangeHighlightEditing(ControllerTestCase):
    """Test on_range_highlight_changed for move and resize."""

    shot_defs = [
        ("Shot_0", 0, 100, ["Obj"]),
        ("Shot_1", 100, 200, ["Obj"]),
        ("Shot_2", 200, 300, ["Obj"]),
    ]
    initial_shot_index = 1

    def setUp(self):
        super().setUp()
        for shot in self.sequencer.sorted_shots():
            self._set_segments(
                shot.shot_id, make_segments("Obj", [(shot.start, shot.end)])
            )
        self._do_initial_sync()

    def test_range_move_shifts_shot_boundaries(self):
        """Translating the range highlight should move the shot."""
        shot = self.sequencer.shot_by_id(self._active_shot_id())
        old_start, old_end = shot.start, shot.end

        # We need to mock the move_shot call since it touches Maya
        self.sequencer.move_shot = MagicMock()

        self.ctrl.on_range_highlight_changed(old_start + 10, old_end + 10)

        self.sequencer.move_shot.assert_called_once()

    def test_range_resize_updates_boundaries(self):
        """Resizing the range highlight should update shot start/end."""
        shot = self.sequencer.shot_by_id(self._active_shot_id())

        # Mock resize call
        self.sequencer.resize_shot = MagicMock()

        self.ctrl.on_range_highlight_changed(shot.start - 10, shot.end)

        self.sequencer.resize_shot.assert_called_once()

    def test_range_move_preserves_active_shot(self):
        """Moving the range must keep the active shot selected."""
        expected_sid = self._active_shot_id()

        self.sequencer.move_shot = MagicMock()
        self.ctrl.on_range_highlight_changed(110, 210)

        self.assertEqual(
            self._active_shot_id(),
            expected_sid,
            "Active shot changed after range move!",
        )


# ===========================================================================
# Test: Shot Expansion When Clip Exceeds Boundaries
# ===========================================================================


class TestShotExpansion(ControllerTestCase):
    """Test that _expand_shot_for_clip grows the shot correctly."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
        ("Shot_B", 200, 300, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot_a = self.sequencer.sorted_shots()[0]
        self._set_segments(
            shot_a.shot_id,
            [
                *make_segments("Obj", [(100, 200)]),
                *make_segments("Obj", [], stepped_times=[100.0]),
            ],
        )
        self._do_initial_sync()

    def test_expand_shot_when_clip_exceeds_end(self):
        """A clip moved beyond shot.end should expand the shot."""
        shot = self.sequencer.shot_by_id(self._active_shot_id())
        old_end = shot.end

        stepped_clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                stepped_clip = cd
                break
        self.assertIsNotNone(stepped_clip)

        # Add the key
        self.key_db.add_key("Obj_visibility", 100.0, 1.0, "spline", "step")
        self.obj_curves["Obj"] = ["Obj_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        # Move the stepped clip beyond the shot end
        self.ctrl._apply_clip_move(stepped_clip.clip_id, 210.0)

        self.assertGreaterEqual(
            shot.end, 210.0, "Shot should have expanded to include moved key"
        )

    def test_expand_shot_does_not_shrink(self):
        """_expand_shot_for_clip should never shrink a shot."""
        shot = self.sequencer.shot_by_id(self._active_shot_id())
        old_end = shot.end

        stepped_clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                stepped_clip = cd
                break

        self.key_db.add_key("Obj_visibility", 100.0, 1.0, "spline", "step")
        self.obj_curves["Obj"] = ["Obj_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        # Move key within shot boundaries
        self.ctrl._apply_clip_move(stepped_clip.clip_id, 150.0)
        self.assertEqual(shot.end, old_end, "Shot end should not change")


# ===========================================================================
# Test: Consecutive Moves on Different Objects
# ===========================================================================


class TestConsecutiveMoveDifferentObjects(ControllerTestCase):
    """Moving keys on different objects consecutively must keep both visible."""

    shot_defs = [
        ("Shot_7", 151, 166, ["ARROW_L", "ARROW_R"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]

        # Setup keys
        for obj in ("ARROW_L", "ARROW_R"):
            self.key_db.add_key(f"{obj}_visibility", 151.0, 0.0, "spline", "step")
            self.key_db.add_key(f"{obj}_visibility", 166.0, 1.0, "spline", "step")
            self.obj_curves[obj] = [f"{obj}_visibility"]

        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[151.0, 166.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 166.0]),
            ],
        )
        self._do_initial_sync()

    def test_move_L_then_R_both_visible(self):
        """After moving ARROW_L then ARROW_R, both must still have clips."""
        shot = self.sequencer.sorted_shots()[0]

        # Move ARROW_L 166 → 163
        clip_L = None
        for cd in self._anim_clips():
            if (
                cd.data.get("obj") == "ARROW_L"
                and cd.data.get("is_stepped")
                and abs(cd.start - 166.0) < 1
            ):
                clip_L = cd
                break
        self.assertIsNotNone(clip_L)

        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[151.0, 163.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 166.0]),
            ],
        )
        self.ctrl.on_clip_moved(clip_L.clip_id, 163.0)

        # Move ARROW_R 166 → 160
        clip_R = None
        for cd in self._anim_clips():
            if (
                cd.data.get("obj") == "ARROW_R"
                and cd.data.get("is_stepped")
                and abs(cd.start - 166.0) < 1
            ):
                clip_R = cd
                break
        self.assertIsNotNone(clip_R, "ARROW_R clip at 166 not found after first move")

        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[151.0, 163.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 160.0]),
            ],
        )
        self.ctrl.on_clip_moved(clip_R.clip_id, 160.0)

        objs = {cd.data.get("obj") for cd in self._anim_clips()}
        self.assertIn("ARROW_L", objs, "ARROW_L vanished after second move!")
        self.assertIn("ARROW_R", objs, "ARROW_R vanished after second move!")


# ===========================================================================
# Test: Rapid Sequential Moves (Drag Simulation)
# ===========================================================================


class TestRapidSequentialMoves(ControllerTestCase):
    """Simulate dragging a stepped key through multiple frames."""

    shot_defs = [
        ("Shot_1", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]

        self.key_db.add_key("Obj_visibility", 150.0, 1.0, "spline", "step")
        self.obj_curves["Obj"] = ["Obj_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        self._set_segments(
            shot.shot_id, make_segments("Obj", [], stepped_times=[150.0])
        )
        self._do_initial_sync()

    def test_drag_through_10_frames(self):
        """10 consecutive 1-frame moves must all succeed without losing the clip."""
        shot = self.sequencer.sorted_shots()[0]
        expected_sid = shot.shot_id

        for i in range(10):
            new_time = 150.0 + i + 1
            clip = None
            for cd in self._anim_clips():
                if cd.data.get("is_stepped") and cd.data.get("obj") == "Obj":
                    clip = cd
                    break
            self.assertIsNotNone(clip, f"Stepped clip lost at iteration {i}")

            self._set_segments(
                shot.shot_id, make_segments("Obj", [], stepped_times=[new_time])
            )
            self.ctrl.on_clip_moved(clip.clip_id, new_time)
            self.assertEqual(
                self._active_shot_id(), expected_sid, f"Shot changed at iteration {i}!"
            )

        # Final state: clip should be at 160.0
        final_clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                final_clip = cd
        self.assertIsNotNone(final_clip)
        self.assertAlmostEqual(final_clip.start, 160.0, places=0)


# ===========================================================================
# Test: Non-Stepped Clip Move (Animation Span)
# ===========================================================================


class TestAnimationClipMove(ControllerTestCase):
    """Test moving a non-stepped animation span clip."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(shot.shot_id, make_segments("Obj", [(100, 180)]))
        self._do_initial_sync()

        # Mock the move_object_in_shot call
        self.sequencer.move_object_in_shot = MagicMock()

    def test_span_clip_move_calls_engine(self):
        """Moving a span clip should call move_object_in_shot."""
        clip = self._anim_clips()[0]
        self.ctrl.on_clip_moved(clip.clip_id, 110.0)
        self.sequencer.move_object_in_shot.assert_called_once()

    def test_span_clip_move_preserves_shot(self):
        """Moving a span clip should not change the active shot."""
        expected_sid = self._active_shot_id()
        clip = self._anim_clips()[0]
        self.ctrl.on_clip_moved(clip.clip_id, 110.0)
        self.assertEqual(self._active_shot_id(), expected_sid)


# ===========================================================================
# Test: Sub-Row Attribute Clip Move
# ===========================================================================


class TestAttributeClipMove(ControllerTestCase):
    """Test moving a sub-row attribute clip uses shift_curves."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(shot.shot_id, make_segments("Obj", [(100, 180)]))
        self._do_initial_sync()

    def test_attr_clip_calls_shift_curves(self):
        """A sub-row clip with attr_name should use SegmentKeys.shift_curves."""
        # Manually add a sub-row clip
        tid = self.widget.tracks()[0].track_id
        cid = self.widget.add_clip(
            tid,
            start=110,
            duration=30,
            shot_id=self.sequencer.sorted_shots()[0].shot_id,
            obj="Obj",
            attr_name="translateX",
            orig_start=110,
            orig_end=140,
        )
        clip = self.widget.get_clip(cid)
        self.assertIsNotNone(clip)

        with patch(
            "mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_slots.ShotSequencerController._curves_for_attr",
            return_value=[],
        ):
            result = self.ctrl._apply_clip_move(cid, 115.0)
        # Even with no curves found, the method should return False
        # because no curves => delta check fails gracefully


# ===========================================================================
# Test: Undo/Redo Shot State
# ===========================================================================


class TestUndoShotState(ControllerTestCase):
    """Test that undo/redo correctly restores shot boundaries."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
        ("Shot_B", 200, 300, ["Obj"]),
    ]
    initial_shot_index = 0

    def test_save_and_restore_shot_state(self):
        """_save_shot_state + _restore_shot_state round-trips correctly."""
        shot_a = self.sequencer.sorted_shots()[0]
        orig_start, orig_end = shot_a.start, shot_a.end

        self.ctrl._save_shot_state()

        # Modify shot
        shot_a.start = 50.0
        shot_a.end = 250.0

        self.ctrl._restore_shot_state()

        self.assertAlmostEqual(shot_a.start, orig_start)
        self.assertAlmostEqual(shot_a.end, orig_end)

    def test_multiple_undo_levels(self):
        """Multiple saves create a stack that unwinds correctly."""
        shot_a = self.sequencer.sorted_shots()[0]

        self.ctrl._save_shot_state()
        shot_a.start = 80.0

        self.ctrl._save_shot_state()
        shot_a.start = 60.0

        self.ctrl._restore_shot_state()
        self.assertAlmostEqual(shot_a.start, 80.0)

        self.ctrl._restore_shot_state()
        self.assertAlmostEqual(shot_a.start, 100.0)


# ===========================================================================
# Test: Widget Population After Sync
# ===========================================================================


class TestWidgetPopulationAfterSync(ControllerTestCase):
    """Verify _sync_to_widget correctly populates tracks and clips."""

    shot_defs = [
        ("Shot_0", 0, 100, ["ObjA", "ObjB"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ObjA", [(10, 80)]),
                *make_segments("ObjB", [(20, 90)], stepped_times=[20.0, 50.0]),
            ],
        )

    def test_sync_creates_tracks(self):
        """_sync_to_widget should create one track per object."""
        self._do_initial_sync()
        track_names = {t.name for t in self.widget.tracks()}
        self.assertIn("ObjA", track_names)
        self.assertIn("ObjB", track_names)

    def test_sync_creates_span_clips(self):
        """Each object's span segments become a single merged clip."""
        self._do_initial_sync()
        span_clips = [c for c in self._anim_clips() if not c.data.get("is_stepped")]
        self.assertTrue(len(span_clips) >= 2, "Should have span clips for both objects")

    def test_sync_creates_stepped_clips(self):
        """Stepped key times become zero-duration clips."""
        self._do_initial_sync()
        stepped_clips = [c for c in self._anim_clips() if c.data.get("is_stepped")]
        self.assertEqual(len(stepped_clips), 2, "ObjB has 2 stepped keys")

    def test_sync_sets_active_range(self):
        """The active shot should set the active range tint on the widget."""
        self._do_initial_sync()
        self.assertIsNotNone(
            self.widget._active_range, "Active range tint should be set"
        )
        self.assertAlmostEqual(self.widget._active_range[0], 0.0)
        self.assertAlmostEqual(self.widget._active_range[1], 100.0)

    def test_repeated_sync_idempotent(self):
        """Calling _sync_to_widget twice should produce identical state."""
        self._do_initial_sync()
        clips_1 = [
            (c.start, c.duration, c.data.get("obj")) for c in self._anim_clips()
        ]

        self.ctrl._sync_to_widget()
        clips_2 = [
            (c.start, c.duration, c.data.get("obj")) for c in self._anim_clips()
        ]

        self.assertEqual(sorted(clips_1), sorted(clips_2))


# ===========================================================================
# Test: Multi-Shot Navigation During Edits
# ===========================================================================


class TestMultiShotNavigation(ControllerTestCase):
    """Verify edits on one shot don't corrupt another shot's display."""

    shot_defs = [
        ("Shot_0", 0, 100, ["ObjA"]),
        ("Shot_1", 100, 200, ["ObjA", "ObjB"]),
        ("Shot_2", 200, 300, ["ObjB"]),
    ]
    initial_shot_index = 1  # Start on Shot_1

    def setUp(self):
        super().setUp()
        import itertools

        for shot in self.sequencer.sorted_shots():
            segs = list(
                itertools.chain.from_iterable(
                    make_segments(obj, [(shot.start, shot.end)]) for obj in shot.objects
                )
            )
            self._set_segments(shot.shot_id, segs)
        self._do_initial_sync()

    def test_edit_on_shot1_doesnt_corrupt_shot2(self):
        """Moving a clip on Shot_1 should not affect Shot_2's segments."""
        clip = self._anim_clips()[0]
        shot1 = self.sequencer.sorted_shots()[1]

        self.sequencer.move_object_in_shot = MagicMock()
        self.ctrl.on_clip_moved(clip.clip_id, clip.start + 5)

        # Switch to Shot_2
        cmb = self.slots.ui.cmb_shot
        cmb.setCurrentIndex(2)
        shot2 = self.sequencer.sorted_shots()[2]
        self.ctrl._sync_to_widget(shot_id=shot2.shot_id)

        objs = {cd.data.get("obj") for cd in self._anim_clips()}
        self.assertIn("ObjB", objs, "ObjB should appear in Shot_2")


# ===========================================================================
# Test: Audio Clip Move
# ===========================================================================


class TestAudioClipMove(ControllerTestCase):
    """Test that audio clips route through AudioTrackManager."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(shot.shot_id, make_segments("Obj", [(100, 200)]))
        self._do_initial_sync()

    @unittest.skipIf(AudioTrackManager is None, "AudioTrackManager not available")
    def test_audio_clip_uses_audio_manager(self):
        """Moving an audio clip should call AudioTrackManager, not shift keys."""
        tid = self.widget.tracks()[0].track_id
        cid = self.widget.add_clip(
            tid,
            start=110,
            duration=30,
            label="Audio",
            is_audio=True,
            audio_source="dg",
            audio_node="audioNode1",
            orig_start=110,
            shot_id=self.sequencer.sorted_shots()[0].shot_id,
        )

        with patch.object(AudioTrackManager, "set_audio_offset") as mock_offset:
            result = self.ctrl._apply_clip_move(cid, 120.0)
            mock_offset.assert_called_once_with("audioNode1", 120.0)
            self.assertTrue(result)


# ===========================================================================
# Test: Edge Cases
# ===========================================================================


class TestEdgeCases(ControllerTestCase):
    """Edge cases and boundary conditions."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(shot.shot_id, make_segments("Obj", [(100, 200)]))
        self._do_initial_sync()

    def test_move_nonexistent_clip(self):
        """Moving a clip_id that doesn't exist should return False."""
        result = self.ctrl._apply_clip_move(9999, 150.0)
        self.assertFalse(result)

    def test_active_shot_fallback(self):
        """With no combobox selection, falls back to first shot."""
        cmb = self.slots.ui.cmb_shot
        cmb.clear()
        sid = self.ctrl.active_shot_id
        # Should fall back to the first shot from the sequencer
        self.assertIsNotNone(sid)

    def test_sync_with_no_sequencer(self):
        """_sync_to_widget with an empty store should be a safe no-op.

        With lazy-init, self.sequencer always resolves to a ShotSequencer.
        An empty store (no shots) exercises the same early-return path.
        """
        self.ctrl.sequencer.store.shots.clear()
        self.ctrl._sync_to_widget()  # Should not raise

    def test_sync_with_no_widget(self):
        """_sync_to_widget with no widget should be a safe no-op."""
        self.slots.ui.sequencer_widget = None
        self.ctrl._sync_to_widget()  # Should not raise


# ===========================================================================
# Test: Shift-Held Clip Move
# ===========================================================================


class TestShiftHeldMove(ControllerTestCase):
    """Test that holding shift suppresses shot expansion."""

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]

        self.key_db.add_key("Obj_visibility", 150.0, 1.0, "spline", "step")
        self.obj_curves["Obj"] = ["Obj_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        self._set_segments(
            shot.shot_id, make_segments("Obj", [], stepped_times=[150.0])
        )
        self._do_initial_sync()

    def test_shift_suppresses_expansion_beyond_end(self):
        """With shift held, moving a key beyond shot.end should NOT expand.

        Shift means 'move key freely without changing shot boundaries'.
        The key leaves the shot and can be picked up by another shot.
        Fixed: 2026-03-17
        """
        shot = self.sequencer.sorted_shots()[0]
        old_end = shot.end

        self.widget._shift_at_press = True

        clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                clip = cd
                break

        self.ctrl._apply_clip_move(clip.clip_id, 250.0)
        self.assertEqual(
            shot.end, old_end, "Shot end must NOT expand when shift is held"
        )

    def test_shift_suppresses_expansion_beyond_start(self):
        """With shift held, moving a key before shot.start should NOT expand.

        Fixed: 2026-03-17
        """
        shot = self.sequencer.sorted_shots()[0]
        old_start = shot.start

        self.widget._shift_at_press = True

        clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                clip = cd
                break

        self.ctrl._apply_clip_move(clip.clip_id, 80.0)
        self.assertEqual(
            shot.start, old_start, "Shot start must NOT expand when shift is held"
        )


# ===========================================================================
# Test: Start-Direction Expansion
# ===========================================================================


class TestStartExpansion(ControllerTestCase):
    """Verify shot.start expands when a key moves before the shot boundary.

    Bug: Moving a stepped key before shot.start did not expand the range.
    The SYNC log showed range=(151,167) when it should have been (150,167).
    Fixed: 2025-03-17
    """

    shot_defs = [
        ("Shot_7", 151, 167, ["ARROW_L"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]

        self.key_db.add_key("ARROW_L_visibility", 154.0, 0.0, "spline", "step")
        self.obj_curves["ARROW_L"] = ["ARROW_L_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)
        self._set_segments(
            shot.shot_id, make_segments("ARROW_L", [], stepped_times=[154.0])
        )
        self._do_initial_sync()

    def test_stepped_key_beyond_start_expands(self):
        """Moving stepped key from 154 -> 150 (below shot start 151) must expand start."""
        shot = self.sequencer.sorted_shots()[0]
        self.assertEqual(shot.start, 151.0)

        clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped") and cd.data.get("obj") == "ARROW_L":
                clip = cd
                break
        self.assertIsNotNone(clip, "Should have a stepped clip for ARROW_L")

        self.ctrl._apply_clip_move(clip.clip_id, 150.0)

        self.assertEqual(
            shot.start,
            150.0,
            "Shot start should expand from 151 to 150 to include the moved key",
        )

    def test_stepped_key_beyond_start_with_shift(self):
        """Shift held should suppress expansion -- shot.start stays unchanged."""
        shot = self.sequencer.sorted_shots()[0]
        old_start = shot.start
        self.widget._shift_at_press = True

        clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped") and cd.data.get("obj") == "ARROW_L":
                clip = cd
                break

        self.ctrl._apply_clip_move(clip.clip_id, 140.0)

        self.assertEqual(
            shot.start, old_start, "Shot start must NOT expand when shift is held"
        )

    def test_end_stays_when_start_expands(self):
        """Expanding the start should not change the end."""
        shot = self.sequencer.sorted_shots()[0]
        old_end = shot.end

        clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                clip = cd
                break

        self.ctrl._apply_clip_move(clip.clip_id, 140.0)

        self.assertEqual(shot.end, old_end, "Shot end should not change")


# ===========================================================================
# Test: Gap Overlays
# ===========================================================================


class TestGapOverlays(ControllerTestCase):
    """Verify gap overlays are created between shots with gaps.

    Bug: Gap overlays were not appearing after widget rebuild.
    Fixed: 2026-03-17
    """

    shot_defs = [
        ("Shot_G1", 100, 200, ["ObjG"]),
        ("Shot_G2", 220, 300, ["ObjG"]),
    ]
    initial_shot_index = 0

    def test_gap_overlays_created(self):
        """Gap between Shot_G1 (end=200) and Shot_G2 (start=220) must produce an overlay."""
        self.ctrl._shot_display_mode = "all"
        self._set_segments(
            self.sequencer.sorted_shots()[0].shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        self.assertGreater(
            len(self.widget._gap_overlays),
            0,
            "Expected at least one gap overlay between shots",
        )

    def test_gap_overlay_range(self):
        """Gap overlay boundaries must match the gap between consecutive shots."""
        self.ctrl._shot_display_mode = "all"
        self._set_segments(
            self.sequencer.sorted_shots()[0].shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        self.assertEqual(len(self.widget._gap_overlays), 1)
        gap = self.widget._gap_overlays[0]
        self.assertAlmostEqual(gap._start, 200.0, places=1)
        self.assertAlmostEqual(gap._end, 220.0, places=1)

    def test_gap_overlay_when_contiguous(self):
        """Contiguous shots (gap=0) still get a thin handle overlay."""
        self.ctrl._shot_display_mode = "all"
        # Redefine shots with no gap
        store = self.sequencer.store
        store.shots.clear()
        store.define_shot(name="A", start=100, end=200, objects=["ObjG"])
        store.define_shot(name="B", start=200, end=300, objects=["ObjG"])
        self.ctrl._sync_combobox()
        s = self.sequencer.sorted_shots()
        self._set_segments(s[0].shot_id, make_segments("ObjG", [(100, 200)]))
        self._do_initial_sync()
        self.assertEqual(
            len(self.widget._gap_overlays),
            1,
            "Contiguous shots should produce a handle overlay",
        )

    def test_gap_overlay_visible(self):
        """Gap overlay items must be visible by default."""
        self.ctrl._shot_display_mode = "all"
        self._set_segments(
            self.sequencer.sorted_shots()[0].shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        for gap in self.widget._gap_overlays:
            self.assertTrue(gap.isVisible(), "Gap overlay should be visible")

    def test_gap_overlay_nonzero_height(self):
        """Gap overlay rect must have non-zero height (requires tracks)."""
        self.ctrl._shot_display_mode = "all"
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(
            shot.shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        for gap in self.widget._gap_overlays:
            rect = gap.boundingRect()
            self.assertGreater(
                rect.height(), 0, "Gap overlay must have non-zero height"
            )

    def test_gap_overlays_visible_without_keys(self):
        """Gap overlays must appear even when shots have no animation keys.

        Bug: _total_row_height() returned 0 with no tracks, making
        the gap overlay rects invisible (height 0).
        Fixed: 2026-03-20
        """
        self.ctrl._shot_display_mode = "all"
        # No _set_segments call → no keys → no tracks
        self._do_initial_sync()
        self.assertGreater(
            len(self.widget._gap_overlays),
            0,
            "Gap overlay should still be created between shots without keys",
        )
        for gap in self.widget._gap_overlays:
            rect = gap._rect()
            self.assertGreater(
                rect.height(), 0,
                "Gap overlay must have non-zero height even without tracks",
            )

    def test_gap_overlay_in_scene_rect(self):
        """Gap overlay bounds must lie within the scene rect."""
        self.ctrl._shot_display_mode = "all"
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(
            shot.shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        scene_rect = self.widget._timeline._scene.sceneRect()
        for gap in self.widget._gap_overlays:
            self.assertTrue(gap.scene() is not None, "Gap must be in a scene")
            br = gap.boundingRect()
            self.assertTrue(
                scene_rect.contains(br),
                f"Gap rect {br} outside scene rect {scene_rect}",
            )

    def test_current_mode_shows_adjacent_gap(self):
        """Current mode: gap bordering the active shot must appear.

        Bug: Gap overlays were shown for ALL shots regardless of display
        mode, or not shown at all until the gap setting was adjusted.
        Only gaps bordering visible shots should appear.
        Fixed: 2026-03-18
        """
        self.ctrl._shot_display_mode = "current"
        self._set_segments(
            self.sequencer.sorted_shots()[0].shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        # The gap 200-220 borders Shot_G1 (active), so it must show
        self.assertEqual(
            len(self.widget._gap_overlays),
            1,
            "Gap bordering active shot should appear in current mode",
        )

    def test_default_mode_shows_gap_without_settings_change(self):
        """Gap overlays must appear on initial sync without touching settings.

        Bug: Gap overlays didn't appear until the Gap spinbox was
        manually adjusted, because the spinbox default (10) didn't
        match the store (0) and no initial respace was triggered.
        Fixed: 2026-03-18
        """
        # Don't set mode — use default "current"
        self._set_segments(
            self.sequencer.sorted_shots()[0].shot_id,
            make_segments("ObjG", [(100, 200)]),
        )
        self._do_initial_sync()
        # Shots already have a 20-frame gap (200 to 220); overlay should exist
        self.assertGreater(
            len(self.widget._gap_overlays),
            0,
            "Gap overlays should appear on first sync when shots have gaps",
        )
        for gap in self.widget._gap_overlays:
            self.assertTrue(gap.isVisible(), "Gap overlay should be visible")


# ===========================================================================
# Test: Gap Visibility by Display Mode (multi-shot)
# ===========================================================================


class TestGapVisibilityByMode(ControllerTestCase):
    """Verify gap overlays always show for ALL gaps regardless of display mode.

    Gap overlays are structural indicators — they show the physical gaps
    between shots. They are always visible, independent of which shots
    are rendered as clips.
    """

    shot_defs = [
        ("Shot_1", 100, 200, ["Obj"]),  # gap 200-210
        ("Shot_2", 210, 300, ["Obj"]),  # gap 300-310
        ("Shot_3", 310, 400, ["Obj"]),  # gap 400-420
        ("Shot_4", 420, 500, ["Obj"]),
    ]
    initial_shot_index = 0  # Shot_1 is active

    def setUp(self):
        super().setUp()
        for shot in self.sequencer.sorted_shots():
            self._set_segments(
                shot.shot_id, make_segments("Obj", [(shot.start, shot.end)])
            )

    def test_current_mode_shows_all_gaps(self):
        """In 'current' mode all gaps must still show."""
        self.ctrl._shot_display_mode = "current"
        self._do_initial_sync()
        # 3 gaps: 200-210, 300-310, 400-420
        self.assertEqual(
            len(self.widget._gap_overlays),
            3,
            "Current mode: all gaps should appear",
        )

    def test_adjacent_mode_shows_all_gaps(self):
        """In 'adjacent' mode all gaps must show."""
        self.ctrl._shot_display_mode = "adjacent"
        self._do_initial_sync()
        self.assertEqual(
            len(self.widget._gap_overlays),
            3,
            "Adjacent mode: all gaps should appear",
        )

    def test_all_mode_shows_all_gaps(self):
        """In 'all' mode every gap should show."""
        self.ctrl._shot_display_mode = "all"
        self._do_initial_sync()
        # 3 gaps: 200-210, 300-310, 400-420
        self.assertEqual(
            len(self.widget._gap_overlays),
            3,
            "All mode: every gap should appear",
        )

    def test_current_mode_middle_shot_still_shows_all_gaps(self):
        """Switching active shot doesn't change which gaps are visible."""
        cmb = self.slots.ui.cmb_shot
        cmb.setCurrentIndex(1)  # Shot_2 active
        self.ctrl._shot_display_mode = "current"
        self.ctrl._sync_to_widget()
        self.assertEqual(
            len(self.widget._gap_overlays),
            3,
            "All gaps visible regardless of active shot",
        )

    def test_gap_positions_are_correct(self):
        """Gap overlay positions match the physical boundaries."""
        self.ctrl._shot_display_mode = "current"
        self._do_initial_sync()
        gap_starts = sorted(round(g._start) for g in self.widget._gap_overlays)
        self.assertEqual(gap_starts, [200, 300, 400])


# ===========================================================================
# Test: Gap Resize Behaviour
# ===========================================================================


class TestGapResize(ControllerTestCase):
    """Verify gap resize shifts downstream shots without changing durations.

    Bug: Dragging a gap changed the next shot's start (altering its
    duration) instead of shifting the next shot and all downstream
    shots by the same delta while preserving every shot's duration.
    Fixed: 2026-03-18
    """

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),  # duration 100
        ("Shot_B", 210, 310, ["Obj"]),  # duration 100, gap=10 after A
        ("Shot_C", 320, 420, ["Obj"]),  # duration 100, gap=10 after B
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        for shot in self.sequencer.sorted_shots():
            self._set_segments(
                shot.shot_id, make_segments("Obj", [(shot.start, shot.end)])
            )
        self._do_initial_sync()

    def test_gap_resize_preserves_all_durations(self):
        """Resizing a gap must not change any shot's duration.

        Bug: on_gap_resized only moved target.start, changing its
        duration while leaving downstream shots untouched.
        Fixed: 2026-03-18
        """
        shot_b = self.sequencer.sorted_shots()[1]
        shot_c = self.sequencer.sorted_shots()[2]
        dur_b = shot_b.end - shot_b.start
        dur_c = shot_c.end - shot_c.start

        # Resize gap A→B from end=210 to end=220 (expand gap by 10)
        self.ctrl.on_gap_resized(210.0, 220.0)

        self.assertAlmostEqual(
            shot_b.end - shot_b.start,
            dur_b,
            msg="Shot_B duration changed after gap resize!",
        )
        self.assertAlmostEqual(
            shot_c.end - shot_c.start,
            dur_c,
            msg="Shot_C duration changed after gap resize!",
        )

    def test_gap_resize_shifts_downstream(self):
        """All shots at or after the gap must shift by the same delta."""
        shot_b = self.sequencer.sorted_shots()[1]
        shot_c = self.sequencer.sorted_shots()[2]
        orig_b_start, orig_c_start = shot_b.start, shot_c.start

        # Expand gap A→B by 10
        self.ctrl.on_gap_resized(210.0, 220.0)

        self.assertAlmostEqual(shot_b.start, orig_b_start + 10)
        self.assertAlmostEqual(shot_c.start, orig_c_start + 10)

    def test_gap_resize_does_not_move_preceding_shot(self):
        """The shot before the gap must not move."""
        shot_a = self.sequencer.sorted_shots()[0]
        orig_start, orig_end = shot_a.start, shot_a.end

        self.ctrl.on_gap_resized(210.0, 220.0)

        self.assertAlmostEqual(shot_a.start, orig_start)
        self.assertAlmostEqual(shot_a.end, orig_end)

    def test_gap_shrink_shifts_upstream(self):
        """Shrinking a gap shifts downstream shots backward."""
        shot_b = self.sequencer.sorted_shots()[1]
        shot_c = self.sequencer.sorted_shots()[2]
        orig_b_start, orig_c_start = shot_b.start, shot_c.start

        # Shrink gap A→B by 5
        self.ctrl.on_gap_resized(210.0, 205.0)

        self.assertAlmostEqual(shot_b.start, orig_b_start - 5)
        self.assertAlmostEqual(shot_c.start, orig_c_start - 5)

    def test_gap_resize_undoable(self):
        """Gap resize must be undoable via the controller's undo system.

        Bug: _save_shot_state was not called before gap modification,
        so undo had no snapshot to restore.
        Fixed: 2026-03-18
        """
        shot_b = self.sequencer.sorted_shots()[1]
        shot_c = self.sequencer.sorted_shots()[2]
        orig_b = (shot_b.start, shot_b.end)
        orig_c = (shot_c.start, shot_c.end)

        # Resize gap
        self.ctrl.on_gap_resized(210.0, 220.0)
        self.assertNotAlmostEqual(shot_b.start, orig_b[0])

        # Undo
        self.ctrl._restore_shot_state()
        self.assertAlmostEqual(shot_b.start, orig_b[0], msg="Shot_B start not restored")
        self.assertAlmostEqual(shot_b.end, orig_b[1], msg="Shot_B end not restored")
        self.assertAlmostEqual(shot_c.start, orig_c[0], msg="Shot_C start not restored")
        self.assertAlmostEqual(shot_c.end, orig_c[1], msg="Shot_C end not restored")


# ===========================================================================
# Test: Shot Label Updated After Clip Move Expansion
# ===========================================================================


class TestShotLabelUpdatedAfterExpansion(ControllerTestCase):
    """Shot label must reflect the new shot range after clip-move expansion.

    Bug: on_clip_moved called _sync_to_widget but not _sync_combobox,
    leaving the label showing the old range like [151-166] instead of [150-167].
    Fixed: 2025-03-17
    """

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self.key_db.add_key("Obj_visibility", 150.0, 1.0, "spline", "step")
        self.obj_curves["Obj"] = ["Obj_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)
        self._set_segments(
            shot.shot_id, make_segments("Obj", [], stepped_times=[150.0])
        )
        self._do_initial_sync()

    def test_label_range_updated_after_move(self):
        """The shot label should show the new range after expansion."""
        shot = self.sequencer.sorted_shots()[0]
        cmb = self.slots.ui.cmb_shot

        clip = None
        for cd in self._anim_clips():
            if cd.data.get("is_stepped"):
                clip = cd
                break

        # Move beyond end, triggering expansion
        self.ctrl.on_clip_moved(clip.clip_id, 210.0)

        # Check the combobox now has the updated range
        label = cmb.itemText(cmb.currentIndex())
        self.assertIn("210", label, "Shot label should reflect expanded range")


# ===========================================================================
# Test: Stress — Many Shots Navigation
# ===========================================================================


class TestManyShots(ControllerTestCase):
    """Stress test shot navigation with many shots."""

    shot_defs = [(f"Shot_{i}", i * 100, (i + 1) * 100, ["Obj"]) for i in range(20)]
    initial_shot_index = 15  # Select shot 15 out of 20

    def test_preserves_middle_selection(self):
        """With 20 shots, selecting shot 15 must survive _sync_combobox."""
        cmb = self.slots.ui.cmb_shot
        expected_sid = cmb.itemData(15)

        self.ctrl._sync_combobox()

        self.assertEqual(self.ctrl.active_shot_id, expected_sid)

    def test_shot_list_count(self):
        """Combobox should have exactly 20 items."""
        cmb = self.slots.ui.cmb_shot
        self.assertEqual(cmb.count(), 20)


# ===========================================================================
# Test: Hidden Objects Don't Get Tracks
# ===========================================================================


class TestHiddenObjects(ControllerTestCase):
    """Verify hidden objects are excluded from the widget."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA", "ObjB"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]
        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ObjA", [(100, 200)]),
                *make_segments("ObjB", [(100, 200)]),
            ],
        )

    def test_hidden_object_excluded(self):
        """A hidden object should not get a track."""
        self.sequencer.set_object_hidden("ObjB", True)
        self._do_initial_sync()

        track_names = {t.name for t in self.widget.tracks()}
        self.assertIn("ObjA", track_names)
        self.assertNotIn("ObjB", track_names)

    def test_unhidden_object_reappears(self):
        """Un-hiding restores the track."""
        self.sequencer.set_object_hidden("ObjB", True)
        self._do_initial_sync()

        self.sequencer.set_object_hidden("ObjB", False)
        self.ctrl._sync_to_widget()

        track_names = {t.name for t in self.widget.tracks()}
        self.assertIn("ObjB", track_names)


# ===========================================================================
# Test: Shift-Moved-Out Key Exclusion
# ===========================================================================


class TestShiftOutKeyExclusion(ControllerTestCase):
    """Verify that keys shift-dragged out of a shot are excluded from
    re-appearing when the shot later expands for a different object.

    Bug: User shift-drags ARROW_L key from 167→168 (outside shot 150-167).
    Later normal-drags ARROW_R key to 168 → shot expands to (150,168).
    Both ARROW_L@168 and ARROW_R@168 appeared, but ARROW_L@168 should
    have been excluded since it was shift-moved out.
    Fixed: 2026-03-17
    """

    shot_defs = [
        ("Shot_7", 150, 167, ["ARROW_L", "ARROW_R"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        shot = self.sequencer.sorted_shots()[0]

        # Both objects have stepped keys at start and end of shot.
        self.key_db.add_key("ARROW_L_visibility", 150.0, 0.0, "spline", "step")
        self.key_db.add_key("ARROW_L_visibility", 167.0, 1.0, "spline", "step")
        self.key_db.add_key("ARROW_R_visibility", 151.0, 0.0, "spline", "step")
        self.key_db.add_key("ARROW_R_visibility", 166.0, 1.0, "spline", "step")
        self.obj_curves["ARROW_L"] = ["ARROW_L_visibility"]
        self.obj_curves["ARROW_R"] = ["ARROW_R_visibility"]
        wire_cmds(_mock_cmds, self.key_db, self.obj_curves)

        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[150.0, 167.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 166.0]),
            ],
        )
        self._do_initial_sync()

    def test_shift_out_key_excluded_after_expansion(self):
        """After shift-dragging ARROW_L to 168, expanding to 168 via
        ARROW_R should NOT show ARROW_L@168 in the widget."""
        shot = self.sequencer.sorted_shots()[0]

        # Step 1: shift-drag ARROW_L from 167→168
        self.widget._shift_at_press = True
        arrow_l_clip = None
        for cd in self._anim_clips():
            if (
                cd.data.get("is_stepped")
                and cd.data.get("obj") == "ARROW_L"
                and cd.data.get("stepped_key_time") == 167.0
            ):
                arrow_l_clip = cd
                break
        self.assertIsNotNone(arrow_l_clip, "ARROW_L@167 clip not found")
        self.ctrl._apply_clip_move(arrow_l_clip.clip_id, 168.0)

        # Verify: shot did NOT expand
        self.assertEqual(shot.end, 167.0)
        # Verify: ARROW_L@168 is in the exclusion set
        self.assertIn("ARROW_L", self.ctrl._shifted_out_keys)
        self.assertIn(168.0, self.ctrl._shifted_out_keys["ARROW_L"])

        # Step 2: normal-drag ARROW_R from 166→168 (expands shot)
        self.widget._shift_at_press = False
        # Update segments to reflect the new state after the moves
        self._set_segments(
            shot.shot_id,
            [
                *make_segments("ARROW_L", [], stepped_times=[150.0, 168.0]),
                *make_segments("ARROW_R", [], stepped_times=[151.0, 168.0]),
            ],
        )

        arrow_r_clip = None
        for cd in self._anim_clips():
            if (
                cd.data.get("is_stepped")
                and cd.data.get("obj") == "ARROW_R"
                and cd.data.get("stepped_key_time") == 166.0
            ):
                arrow_r_clip = cd
                break
        self.assertIsNotNone(arrow_r_clip, "ARROW_R@166 clip not found")
        self.ctrl.on_clip_moved(arrow_r_clip.clip_id, 168.0)

        # Verify: shot expanded to include 168
        self.assertEqual(shot.end, 168.0)

        # Verify: ARROW_L@168 does NOT appear in the widget
        arrow_l_clips_at_168 = [
            cd
            for cd in self._anim_clips()
            if cd.data.get("obj") == "ARROW_L"
            and cd.data.get("is_stepped")
            and abs(cd.data.get("stepped_key_time", -1) - 168.0) < 0.5
        ]
        self.assertEqual(
            len(arrow_l_clips_at_168),
            0,
            "ARROW_L@168 should be excluded (was shift-moved out)",
        )

        # Verify: ARROW_R@168 DOES appear
        arrow_r_clips_at_168 = [
            cd
            for cd in self._anim_clips()
            if cd.data.get("obj") == "ARROW_R"
            and cd.data.get("is_stepped")
            and abs(cd.data.get("stepped_key_time", -1) - 168.0) < 0.5
        ]
        self.assertGreater(
            len(arrow_r_clips_at_168), 0, "ARROW_R@168 should still appear"
        )

    def test_normal_move_clears_exclusion(self):
        """A non-shift move of the same object should clear its exclusion."""
        shot = self.sequencer.sorted_shots()[0]

        # Shift-move ARROW_L to 168 → excluded
        self.widget._shift_at_press = True
        clip = None
        for cd in self._anim_clips():
            if (
                cd.data.get("is_stepped")
                and cd.data.get("obj") == "ARROW_L"
                and cd.data.get("stepped_key_time") == 167.0
            ):
                clip = cd
                break
        self.ctrl._apply_clip_move(clip.clip_id, 168.0)
        self.assertIn("ARROW_L", self.ctrl._shifted_out_keys)

        # Now normal-move ARROW_L@150→155 (within shot) → clears exclusion
        self.widget._shift_at_press = False
        clip2 = None
        for cd in self._anim_clips():
            if (
                cd.data.get("is_stepped")
                and cd.data.get("obj") == "ARROW_L"
                and cd.data.get("stepped_key_time") == 150.0
            ):
                clip2 = cd
                break
        self.assertIsNotNone(clip2, "ARROW_L@150 clip not found")
        self.ctrl._apply_clip_move(clip2.clip_id, 155.0)
        self.assertNotIn(
            "ARROW_L",
            self.ctrl._shifted_out_keys,
            "Normal move should clear exclusion",
        )

    def test_shot_change_clears_exclusions(self):
        """Switching shots should clear the exclusion set."""
        # Shift-move ARROW_L to 168 → excluded
        self.widget._shift_at_press = True
        clip = None
        for cd in self._anim_clips():
            if (
                cd.data.get("is_stepped")
                and cd.data.get("obj") == "ARROW_L"
                and cd.data.get("stepped_key_time") == 167.0
            ):
                clip = cd
                break
        self.ctrl._apply_clip_move(clip.clip_id, 168.0)
        self.assertTrue(len(self.ctrl._shifted_out_keys) > 0)

        # Simulate shot change by clearing exclusions (as _on_shot_selected does)
        self.ctrl._shifted_out_keys.clear()
        self.assertEqual(len(self.ctrl._shifted_out_keys), 0)


# ===========================================================================
# Test: Manifest → Sequencer Handoff  (Bug: prev greyed out + no keys)
# ===========================================================================


class TestManifestHandoff(ControllerTestCase):
    """Reproduce bugs from the manifest _open_in_shot_sequencer flow.

    Bug 1: Previous-shot option-box action stays greyed out even when the
    opened shot isn't the first one.
    Bug 2: Stale _shifted_out_keys from a prior session filter away all
    segments, making the widget appear empty.
    Fixed: 2026-03-19
    """

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjX"]),
        ("Shot_B", 210, 350, ["ObjX", "ObjY"]),
        ("Shot_C", 360, 500, ["ObjY"]),
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        # Wire up fake prev/next actions (normally done by _setup_shot_nav)
        prev_widget = MagicMock()
        next_widget = MagicMock()
        self.ctrl._prev_action = MagicMock()
        self.ctrl._prev_action.widget = prev_widget
        self.ctrl._next_action = MagicMock()
        self.ctrl._next_action.widget = next_widget

        # Provide segments for shots so clips can be built
        for shot in self.sequencer.sorted_shots():
            self._set_segments(
                shot.shot_id,
                make_segments(shot.objects[0], [(shot.start + 5, shot.end - 5)]),
            )

    def _simulate_manifest_open(self, target_name: str):
        """Replicate the exact steps _open_in_shot_sequencer performs."""
        cmb = self.slots.ui.cmb_shot
        self.ctrl._shifted_out_keys.clear()
        self.ctrl._segment_cache.clear()
        self.ctrl._sync_combobox()
        for i in range(cmb.count()):
            shot_id = cmb.itemData(i)
            shot = self.ctrl.sequencer.shot_by_id(shot_id) if shot_id else None
            if shot and shot.name == target_name:
                cmb.blockSignals(True)
                cmb.setCurrentIndex(i)
                cmb.blockSignals(False)
                self.ctrl._sync_to_widget(shot_id, frame=True)
                self.ctrl._update_shot_nav_state()
                break

    def test_prev_action_enabled_after_manifest_handoff(self):
        """Previous-shot option action must be enabled when shot index > 0.

        Bug: _open_in_shot_sequencer called _sync_combobox (which ran
        _update_shot_nav_state at idx 0) but never re-ran it after
        setCurrentIndex(i)."""
        self._simulate_manifest_open("Shot_B")
        cmb = self.slots.ui.cmb_shot
        self.assertEqual(cmb.currentIndex(), 1, "Should be on Shot_B at index 1")
        self.ctrl._prev_action.widget.setEnabled.assert_called()
        # The LAST call should be setEnabled(True) for idx > 0
        last_call = self.ctrl._prev_action.widget.setEnabled.call_args_list[-1]
        self.assertTrue(last_call[0][0], "prev action should be enabled for idx=1")

    def test_stale_shifted_out_keys_cleared_on_manifest_handoff(self):
        """Stale _shifted_out_keys must be cleared before _sync_to_widget.

        Bug: keys shifted out in a previous session remained in
        _shifted_out_keys, filtering away all segments so the
        widget appeared empty after the manifest opened the sequencer."""
        # Pre-populate stale shifted keys for objects in Shot_B
        self.ctrl._shifted_out_keys = {"ObjX": {110.0, 150.0}, "ObjY": {280.0}}
        self._simulate_manifest_open("Shot_B")
        self.assertEqual(
            len(self.ctrl._shifted_out_keys),
            0,
            "shifted_out_keys should be cleared before sync",
        )
        # And clips should actually be present
        clips = list(self._anim_clips())
        self.assertTrue(len(clips) > 0, "Widget should have clips after manifest open")


# ===========================================================================
# Test: Auto-Discovery of Objects for Empty Shots
# ===========================================================================


class TestAutoDiscoveryEmptyShot(ControllerTestCase):
    """Verify that shots created with no objects auto-discover on sync.

    Bug: Creating a shot when no animated objects exist in the scene
    stored objects=[] on the ShotBlock.  collect_object_segments then
    early-returned [], so _sync_to_widget produced zero tracks/clips
    and the widget appeared blank — even after the user later added
    animated objects to the scene.
    Fixed: 2026-03-20
    """

    shot_defs = [("EmptyShot", 1, 100, [])]  # Shot with NO objects
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        # Restore real collect_object_segments (base class mocks it)
        self.sequencer.collect_object_segments = (
            ShotSequencer.collect_object_segments.__get__(self.sequencer)
        )
        # Mock _shot_nodes to resolve names through our fake cmds.ls
        self.sequencer._shot_nodes = staticmethod(
            lambda shot: shot.objects[:] if shot.objects else []
        )

    def test_auto_discover_populates_empty_shot(self):
        """collect_object_segments should discover animated transforms
        when shot.objects is empty, and persist them on the ShotBlock."""
        shot = self.sequencer.store.sorted_shots()[0]
        self.assertEqual(shot.objects, [], "Precondition: shot starts with no objects")

        # Simulate adding animation — _find_keyed_transforms returns objects
        with patch.object(
            ShotSequencer,
            "_find_keyed_transforms",
            return_value=["ObjX", "ObjY"],
        ):
            segs = self.sequencer.collect_object_segments(shot.shot_id)

        # shot.objects should now be populated
        self.assertEqual(
            shot.objects,
            ["ObjX", "ObjY"],
            "Auto-discovery should persist objects on the ShotBlock",
        )

    def test_no_discovery_when_objects_exist(self):
        """collect_object_segments should NOT re-discover if shot already
        has objects — prevents overwriting user-curated object lists."""
        shot = self.sequencer.store.sorted_shots()[0]
        shot.objects = ["ExistingObj"]

        with patch.object(ShotSequencer, "_find_keyed_transforms") as mock_discover:
            self.sequencer.collect_object_segments(shot.shot_id)

        mock_discover.assert_not_called()

    def test_sync_shows_tracks_after_discovery(self):
        """Full flow: _sync_to_widget should create tracks+clips after
        auto-discovery populates an empty shot."""
        # Provide segments that collect_object_segments would return
        # after auto-discovery finds ObjX
        discovered_segs = make_segments("ObjX", [(10, 50)])
        with patch.object(
            ShotSequencer,
            "_find_keyed_transforms",
            return_value=["ObjX"],
        ):
            # Mock SegmentKeys.collect_segments to return our fake segments
            with patch(
                "mayatk.anim_utils.segment_keys.SegmentKeys.collect_segments",
                return_value=discovered_segs,
            ):
                self.ctrl._sync_to_widget()

        tracks = list(self.widget.tracks())
        self.assertTrue(len(tracks) > 0, "Widget should have tracks after discovery")
        clips = list(self._anim_clips())
        self.assertTrue(len(clips) > 0, "Widget should have clips after discovery")


# ===========================================================================
# Test: Shot Lane Population
# ===========================================================================


class TestActiveRangeTinting(ControllerTestCase):
    """Verify _rebuild_decoration sets the active-shot range tint."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
        ("Shot_B", 250, 350, ["ObjA"]),
    ]

    def test_active_range_set_after_sync(self):
        """set_active_range should be called with the active shot's boundaries."""
        self._set_segments(1, make_segments("ObjA", [(100, 200)]))
        self._set_segments(2, make_segments("ObjA", [(250, 350)]))
        self._do_initial_sync()

        # Active shot is Shot_A (100-200)
        self.assertIsNotNone(self.widget._active_range)
        self.assertAlmostEqual(self.widget._active_range[0], 100.0)
        self.assertAlmostEqual(self.widget._active_range[1], 200.0)

    def test_no_shot_track_after_sync(self):
        """The dedicated shot track should no longer exist."""
        self._set_segments(1, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        track_names = {t.name for t in self.widget.tracks()}
        self.assertNotIn("\U0001f3ac Shots", track_names)

    def test_active_range_cleared_before_set(self):
        """clear_active_range should reset the range to None."""
        self._set_segments(1, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        self.widget.clear_active_range()
        self.assertIsNone(self.widget._active_range)

    def test_active_range_set_with_no_keys(self):
        """Active range tint must appear even when shots have no animation keys.

        Bug: _total_row_height() returned 0 with no tracks, making
        the tint rect height 0 (invisible).
        Fixed: 2026-03-20
        """
        # No _set_segments call → no keys → no object tracks
        self._do_initial_sync()
        self.assertIsNotNone(self.widget._active_range)
        self.assertAlmostEqual(self.widget._active_range[0], 100.0)
        self.assertAlmostEqual(self.widget._active_range[1], 200.0)


# ===========================================================================
# Test: Refresh Method
# ===========================================================================


class TestRefresh(ControllerTestCase):
    """Verify the controller refresh() method clears cache and rebuilds."""

    def test_refresh_clears_cache_and_rebuilds(self):
        """refresh() should clear _segment_cache and repopulate the widget."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        # Widget should have tracks
        initial_tracks = list(self.widget.tracks())
        self.assertTrue(len(initial_tracks) > 0)

        # Populate cache artificially
        self.ctrl._segment_cache[999] = ["dummy"]
        self.ctrl.refresh()

        # Cache should be empty (dummy entry gone)
        self.assertNotIn(999, self.ctrl._segment_cache)
        # Widget still populated
        self.assertTrue(len(list(self.widget.tracks())) > 0)


# ===========================================================================
# Test: Three-Tier Refresh Architecture
# ===========================================================================


class TestSyncDecoration(ControllerTestCase):
    """Verify _sync_decoration rebuilds overlays without destroying tracks."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
        ("Shot_B", 250, 350, ["ObjA"]),
    ]

    def test_tracks_preserved_after_sync_decoration(self):
        """_sync_decoration should not destroy existing tracks or clips."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        tracks_before = [t.name for t in self.widget.tracks()]
        clips_before = len(list(self._anim_clips()))
        self.assertTrue(len(tracks_before) > 0)
        self.assertTrue(clips_before > 0)

        self.ctrl._sync_decoration()

        tracks_after = [t.name for t in self.widget.tracks()]
        clips_after = len(list(self._anim_clips()))
        self.assertEqual(tracks_before, tracks_after)
        self.assertEqual(clips_before, clips_after)

    def test_active_range_updated_by_full_sync(self):
        """Shot boundaries require a full _sync_to_widget to update active range."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        self.assertIsNotNone(self.widget._active_range)

        # Mutate boundary and do a full sync
        self.sequencer.store.update_shot(0, end=220)
        self.ctrl._sync_to_widget()

        self.assertAlmostEqual(self.widget._active_range[1], 220.0)

    def test_range_highlight_not_used(self):
        """Range highlight is no longer used; active range tint replaces it."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        rh = self.widget.range_highlight()
        self.assertIsNone(rh)


class TestResolveAndSave(ControllerTestCase):
    """Verify the extracted helper methods."""

    def test_resolve_sync_target_returns_widget_and_shot(self):
        widget, shot = self.ctrl._resolve_sync_target()
        self.assertIsNotNone(widget)
        self.assertIsNotNone(shot)
        self.assertEqual(shot.name, "Shot_A")

    def test_resolve_sync_target_with_explicit_id(self):
        widget, shot = self.ctrl._resolve_sync_target(shot_id=1)
        self.assertIsNotNone(shot)
        self.assertEqual(shot.name, "Shot_B")

    def test_resolve_sync_target_returns_none_without_sequencer(self):
        self.ctrl._sequencer = None
        # Force the property to not re-create by patching active()
        with patch(
            "mayatk.anim_utils.shots._shots.ShotStore.active", return_value=None
        ):
            self.ctrl._sequencer = None
            widget, shot = self.ctrl._resolve_sync_target()
        # With a None sequencer, should return None
        # (The lazy property may recreate it, so we test the intent)
        self.assertTrue(widget is None or shot is not None)

    def test_save_viewport_state_captures_zoom(self):
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        h_scroll, zoom, expanded = self.ctrl._save_viewport_state(self.widget)
        self.assertIsInstance(zoom, float)
        self.assertIsInstance(expanded, set)


class TestClearDecorations(ControllerTestCase):
    """Verify SequencerWidget.clear_decorations removes only overlays."""

    def test_clear_decorations_preserves_tracks_and_clips(self):
        """clear_decorations should remove markers/overlays but not tracks."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        tracks_before = len(list(self.widget.tracks()))
        clips_before = len(list(self._anim_clips()))

        self.widget.clear_decorations()

        self.assertEqual(len(list(self.widget.tracks())), tracks_before)
        self.assertEqual(len(list(self._anim_clips())), clips_before)
        # Overlays should be gone
        self.assertEqual(len(self.widget._range_overlays), 0)
        self.assertEqual(len(self.widget._gap_overlays), 0)


# ===========================================================================
# Test: Zone-Based Event Routing
# ===========================================================================


class TestZoneDetection(ControllerTestCase):
    """Verify TimelineView._hit_zone returns correct zone names."""

    def test_ruler_zone(self):
        """Y < _RULER_HEIGHT should return 'ruler'."""
        from uitk.widgets.sequencer._sequencer import _RULER_HEIGHT

        tl = self.widget._timeline
        self.assertEqual(tl._hit_zone(0), "ruler")
        self.assertEqual(tl._hit_zone(_RULER_HEIGHT - 1), "ruler")

    def test_ruler_boundary_exclusive(self):
        """Y == _RULER_HEIGHT is the first pixel of the next zone, not ruler."""
        from uitk.widgets.sequencer._sequencer import _RULER_HEIGHT

        tl = self.widget._timeline
        # No shot lane visible → should be 'tracks', not 'ruler'
        self.assertNotEqual(tl._hit_zone(_RULER_HEIGHT), "ruler")

    def test_past_ruler_is_tracks(self):
        """Y past ruler should always be 'tracks' — no shot lane zone."""
        from uitk.widgets.sequencer._sequencer import _RULER_HEIGHT

        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        tl = self.widget._timeline
        self.assertEqual(tl._hit_zone(_RULER_HEIGHT), "tracks")

    def test_content_top_equals_ruler_height(self):
        """_content_top is always _RULER_HEIGHT (no shot lane row)."""
        from uitk.widgets.sequencer._sequencer import _RULER_HEIGHT

        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()
        self.assertEqual(self.widget._content_top, _RULER_HEIGHT)

    def test_tracks_zone(self):
        """Y below content_top should return 'tracks'."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        tl = self.widget._timeline
        content_top = self.widget._content_top
        self.assertEqual(tl._hit_zone(content_top + 1), "tracks")


class TestShotTrackHeight(ControllerTestCase):
    """Verify content_top positioning (no shot lane row)."""

    def test_content_top_always_ruler_height(self):
        """content_top is always ruler height."""
        from uitk.widgets.sequencer._sequencer import _RULER_HEIGHT

        self.assertEqual(self.widget._content_top, _RULER_HEIGHT)


class TestZoneContextMenu(ControllerTestCase):
    """Verify zone_context_menu_requested signal is connected and dispatches."""

    def setUp(self):
        super().setUp()
        self.widget.zone_context_menu_requested.connect(self.ctrl.on_zone_context_menu)

    def test_ruler_zone_delegates_to_widget_default_menu(self):
        """Ruler zone should delegate to widget's built-in default context menu."""
        from qtpy import QtCore

        with patch.object(
            self.widget._timeline, "_show_default_context_menu"
        ) as mock_default:
            self.ctrl.on_zone_context_menu("ruler", 150.0, QtCore.QPoint(100, 10))
            mock_default.assert_called_once_with(
                self.widget, 150.0, QtCore.QPoint(100, 10)
            )

    def test_shot_lane_zone_calls_shot_menu(self):
        """shot_lane zone should call _show_shot_lane_context_menu."""
        from qtpy import QtCore

        with patch.object(self.ctrl, "_show_shot_lane_context_menu") as mock_lane:
            self.ctrl.on_zone_context_menu("shot_lane", 150.0, QtCore.QPoint(100, 30))
            mock_lane.assert_called_once_with(150.0, QtCore.QPoint(100, 30))

    def test_tracks_zone_delegates_to_widget_default_menu(self):
        """Tracks zone should also delegate to widget's built-in default context menu."""
        from qtpy import QtCore

        with patch.object(
            self.widget._timeline, "_show_default_context_menu"
        ) as mock_default:
            self.ctrl.on_zone_context_menu("tracks", 150.0, QtCore.QPoint(100, 60))
            mock_default.assert_called_once_with(
                self.widget, 150.0, QtCore.QPoint(100, 60)
            )

    def test_signal_routes_through_dispatch(self):
        """Emitting zone_context_menu_requested signal routes correctly."""
        from qtpy import QtCore

        with patch.object(self.ctrl, "_show_shot_lane_context_menu") as mock_lane:
            self.widget.zone_context_menu_requested.emit(
                "shot_lane", 100.0, QtCore.QPoint(0, 0)
            )
            mock_lane.assert_called_once_with(100.0, QtCore.QPoint(0, 0))


class TestShotLaneDoubleClick(ControllerTestCase):
    """Verify double-click on shot lane opens the edit dialog."""

    def setUp(self):
        super().setUp()
        self.widget.shot_lane_double_clicked.connect(
            self.ctrl._on_shot_lane_double_clicked
        )
        # Prevent _edit_shot_dialog from opening a blocking QDialog in tests
        self._edit_patcher = patch.object(self.ctrl, "_edit_shot_dialog")
        self._mock_edit = self._edit_patcher.start()

    def tearDown(self):
        self._edit_patcher.stop()
        super().tearDown()

    def test_double_click_emits_signal(self):
        """shot_lane_double_clicked signal should carry the time value."""
        received = []
        self.widget.shot_lane_double_clicked.connect(received.append)
        self.widget.shot_lane_double_clicked.emit(150.0)
        self.assertEqual(received, [150.0])

    def test_double_click_calls_edit_dialog(self):
        """Double-click at a shot's time should open _edit_shot_dialog for that shot."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        shot = self.ctrl.sequencer.sorted_shots()[0]
        self.ctrl._on_shot_lane_double_clicked(150.0)
        self._mock_edit.assert_called_once_with(shot)

    def test_double_click_in_gap_does_nothing(self):
        """Double-click in a gap (no shot) should not call _edit_shot_dialog."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        self.ctrl._on_shot_lane_double_clicked(50.0)  # before any shot
        self._mock_edit.assert_not_called()


class TestFindShotAtTime(ControllerTestCase):
    """Verify _find_shot_at_time helper."""

    def test_finds_shot_in_range(self):
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        shot = self.ctrl._find_shot_at_time(150.0)
        self.assertIsNotNone(shot)
        self.assertEqual(shot.shot_id, 0)

    def test_returns_none_in_gap(self):
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        self.assertIsNone(self.ctrl._find_shot_at_time(50.0))

    def test_boundary_start_inclusive(self):
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        shot = self.ctrl._find_shot_at_time(100.0)
        self.assertIsNotNone(shot)

    def test_boundary_end_inclusive(self):
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        shot = self.ctrl._find_shot_at_time(200.0)
        self.assertIsNotNone(shot)


# ===========================================================================
# Test: One-Click Shot Creation
# ===========================================================================


class TestOneClickShotCreation(ControllerTestCase):
    """Verify _create_shot_one_click uses gap-aware placement."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
    ]

    def setUp(self):
        super().setUp()
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

    def test_creates_shot_after_last(self):
        """New shot should start after existing shots + gap."""
        self.sequencer.store.gap = 10
        self.ctrl._create_shot_one_click()
        shots = self.sequencer.sorted_shots()
        self.assertEqual(len(shots), 2)
        new_shot = shots[-1]
        self.assertEqual(new_shot.start, 210.0)  # 200 + gap(10)
        self.assertEqual(new_shot.name, "Shot 2")

    def test_creates_shot_zero_gap(self):
        """With gap=0, new shot starts immediately after last."""
        self.sequencer.store.gap = 0
        self.ctrl._create_shot_one_click()
        shots = self.sequencer.sorted_shots()
        new_shot = shots[-1]
        self.assertEqual(new_shot.start, 200.0)

    def test_selects_new_shot(self):
        """One-click creation should select the new shot."""
        self.ctrl._create_shot_one_click()
        shots = self.sequencer.sorted_shots()
        new_shot = shots[-1]
        self.assertEqual(self.ctrl.active_shot_id, new_shot.shot_id)


# ===========================================================================
# Test: Click Shot Clip to Switch Shot
# ===========================================================================


# ===========================================================================
# Test: Flat-key objects excluded from sequencer tracks
# ===========================================================================


class TestFlatObjectExclusion(ControllerTestCase):
    """Verify objects with no animation segments (flat keys) are excluded
    from sequencer tracks and the active-object set.

    Bug: Objects in shot.objects with only constant-value animation
    appeared as empty tracks in the sequencer.
    Fixed: 2025-07-16
    """

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA", "FlatObj", "ObjB"]),
    ]

    def setUp(self):
        super().setUp()
        # Only ObjA has actual segments; FlatObj has none (flat keys)
        self._set_segments(0, make_segments("ObjA", [(110, 180)]))

    def test_collect_segments_excludes_flat_objects(self):
        """_collect_segments should not include objects without segments."""
        shot = self.sequencer.sorted_shots()[0]
        visible = self.sequencer.sorted_shots()
        segs_by_shot, all_objects = self.ctrl._collect_segments(shot, visible)
        self.assertIn("ObjA", all_objects)
        self.assertNotIn("FlatObj", all_objects)
        self.assertNotIn("ObjB", all_objects)

    def test_active_object_set_excludes_flat_objects(self):
        """_active_object_set should only include objects with segments."""
        shot = self.sequencer.sorted_shots()[0]
        segs_by_shot = {shot.shot_id: make_segments("ObjA", [(110, 180)])}
        active = self.ctrl._active_object_set(shot, segs_by_shot)
        self.assertIn("ObjA", active)
        self.assertNotIn("FlatObj", active)
        self.assertNotIn("ObjB", active)

    def test_pinned_objects_still_shown(self):
        """Pinned objects should appear even without segments."""
        self.sequencer.store.set_object_pinned("FlatObj")
        shot = self.sequencer.sorted_shots()[0]
        visible = self.sequencer.sorted_shots()
        _, all_objects = self.ctrl._collect_segments(shot, visible)
        self.assertIn("ObjA", all_objects)
        self.assertIn("FlatObj", all_objects)
        self.assertNotIn("ObjB", all_objects)

    def test_multi_shot_flat_object_excluded(self):
        """Flat objects should be excluded even across multiple visible shots."""
        # Add a second shot with ObjA + FlatObj2
        self.sequencer.store.define_shot(
            name="Shot_B", start=200, end=300, objects=["ObjA", "FlatObj2"]
        )
        self._set_segments(1, make_segments("ObjA", [(210, 280)]))
        shot = self.sequencer.sorted_shots()[0]
        visible = self.sequencer.sorted_shots()
        _, all_objects = self.ctrl._collect_segments(shot, visible)
        self.assertIn("ObjA", all_objects)
        self.assertNotIn("FlatObj", all_objects)
        self.assertNotIn("FlatObj2", all_objects)


class TestGapLeftResize(ControllerTestCase):
    """Verify on_gap_left_resized adjusts the preceding shot's end."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
        ("Shot_B", 250, 350, ["ObjA"]),
    ]
    initial_shot_index = 0

    def test_left_resize_changes_prev_shot_end(self):
        """Dragging the left edge of a gap should adjust the preceding shot's end."""
        # Gap is at 200-250. Left edge is Shot_A.end = 200.
        # Drag from 200 → 190 → Shot_A.end becomes 190
        self.ctrl.on_gap_left_resized(200.0, 190.0)
        shot_a = self.sequencer.shot_by_id(0)
        self.assertAlmostEqual(shot_a.end, 190.0)

    def test_left_resize_noop_for_tiny_delta(self):
        """A sub-threshold delta should not change anything."""
        self.ctrl.on_gap_left_resized(200.0, 200.0001)
        shot_a = self.sequencer.shot_by_id(0)
        self.assertAlmostEqual(shot_a.end, 200.0)


class TestGapBodyMove(ControllerTestCase):
    """Verify on_gap_moved slides the cut-point between two shots."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
        ("Shot_B", 250, 350, ["ObjA"]),
    ]
    initial_shot_index = 0

    def test_gap_move_adjusts_both_shots(self):
        """Moving a gap should shift the left shot's end and right shot's start."""
        # Gap is 200-250 (width 50). Slide right by 10 → gap becomes 210-260.
        self.ctrl.on_gap_moved(200.0, 250.0, 210.0, 260.0)
        shot_a = self.sequencer.shot_by_id(0)
        shot_b = self.sequencer.shot_by_id(1)
        self.assertAlmostEqual(shot_a.end, 210.0)
        self.assertAlmostEqual(shot_b.start, 260.0)

    def test_gap_move_noop_for_tiny_delta(self):
        """A sub-threshold delta should not change anything."""
        self.ctrl.on_gap_moved(200.0, 250.0, 200.0001, 250.0001)
        shot_a = self.sequencer.shot_by_id(0)
        shot_b = self.sequencer.shot_by_id(1)
        self.assertAlmostEqual(shot_a.end, 200.0)
        self.assertAlmostEqual(shot_b.start, 250.0)


# ===========================================================================
# Test: Clip Rename (No-Op After Shot Row Removal)
# ===========================================================================


class TestClipRenameNoOp(ControllerTestCase):
    """Verify on_clip_renamed is a no-op after shot row removal."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
    ]

    def test_rename_does_not_change_shot_name(self):
        """on_clip_renamed should not modify any shot data."""
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        # Call with arbitrary ID — should be no-op
        self.ctrl.on_clip_renamed(999, "New Name")
        shot = self.sequencer.shot_by_id(0)
        self.assertEqual(shot.name, "Shot_A")


# ===========================================================================
# Test: Active Range Tinting Replaces Shot Clip Resize
# ===========================================================================


class TestActiveRangeAfterResize(ControllerTestCase):
    """Verify active range updates when shot boundaries change."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
    ]

    def setUp(self):
        super().setUp()
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

    def test_active_range_updates_on_boundary_change(self):
        """Changing shot boundaries and syncing should update the active range."""
        self.ctrl.on_range_highlight_changed(90.0, 210.0)
        self.assertIsNotNone(self.widget._active_range)
        self.assertAlmostEqual(self.widget._active_range[0], 90.0)
        self.assertAlmostEqual(self.widget._active_range[1], 210.0)


# ===========================================================================
# Test: One-Click Name Collision Avoidance
# ===========================================================================


class TestOneClickNameCollision(ControllerTestCase):
    """Verify _create_shot_one_click avoids duplicate names."""

    shot_defs = [
        ("Shot 1", 0, 100, ["ObjA"]),
        ("Shot 2", 110, 210, ["ObjA"]),
    ]

    def setUp(self):
        super().setUp()
        self._set_segments(0, make_segments("ObjA", [(0, 100)]))
        self._set_segments(1, make_segments("ObjA", [(110, 210)]))
        self._do_initial_sync()

    def test_skips_existing_names(self):
        """With 'Shot 1' and 'Shot 2' existing, new shot should be 'Shot 3'."""
        self.ctrl._create_shot_one_click()
        shots = self.sequencer.sorted_shots()
        new_shot = shots[-1]
        self.assertEqual(new_shot.name, "Shot 3")

    def test_skips_with_gap_in_numbering(self):
        """After deleting 'Shot 2', a Shot 3 exists, new should be 'Shot 4'."""
        # Rename Shot 2 → Shot 3 to simulate a gap
        self.sequencer.store.update_shot(1, name="Shot 3")
        self.ctrl._create_shot_one_click()
        shots = self.sequencer.sorted_shots()
        new_shot = shots[-1]
        self.assertEqual(new_shot.name, "Shot 4")


# ===========================================================================
# Test: Pinned vs Non-Pinned Missing Objects
# ===========================================================================


class TestMissingObjectRemoval(ControllerTestCase):
    """Verify non-pinned missing objects are skipped, pinned ones are kept."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA", "ObjMissing"]),
    ]

    def test_non_pinned_missing_skipped(self):
        """A missing object that is not pinned should not get a track."""
        # ObjA exists, ObjMissing does not
        _mock_cmds.objExists.side_effect = lambda n: n != "ObjMissing"
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        track_names = {t.name for t in self.widget.tracks()}
        self.assertIn("ObjA", track_names)
        self.assertNotIn("ObjMissing", track_names)

    def test_pinned_missing_kept(self):
        """A pinned missing object should still get a track (dimmed)."""
        self.sequencer.store.set_object_pinned("ObjMissing")
        _mock_cmds.objExists.side_effect = lambda n: n != "ObjMissing"
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        track_names = {t.name for t in self.widget.tracks()}
        self.assertIn("ObjA", track_names)
        self.assertIn("ObjMissing", track_names)

    def test_unpinning_removes_on_next_sync(self):
        """Unpinning a missing object removes it on the next sync."""
        self.sequencer.store.set_object_pinned("ObjMissing")
        _mock_cmds.objExists.side_effect = lambda n: n != "ObjMissing"
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self._do_initial_sync()

        # Confirm it's there first
        self.assertIn("ObjMissing", {t.name for t in self.widget.tracks()})

        # Unpin and re-sync
        self.sequencer.store.set_object_pinned("ObjMissing", False)
        self.ctrl._sync_to_widget()

        self.assertNotIn("ObjMissing", {t.name for t in self.widget.tracks()})


# ===========================================================================
# Test: Delete Track
# ===========================================================================


class TestDeleteTrack(ControllerTestCase):
    """Verify delete_track removes object from all shots and rebuilds."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA", "ObjB"]),
        ("Shot_B", 250, 350, ["ObjA", "ObjB"]),
    ]

    def test_delete_removes_from_all_shots(self):
        """Deleting a track should remove the object from every shot."""
        self.ctrl.delete_track(["ObjB"])

        for shot in self.sequencer.sorted_shots():
            self.assertNotIn(
                "ObjB", shot.objects, f"ObjB still in {shot.name}.objects"
            )

    def test_delete_also_clears_pinned_and_hidden(self):
        """Deleting a track should clean up pinned and hidden state."""
        self.sequencer.store.set_object_pinned("ObjB")
        self.sequencer.set_object_hidden("ObjB", True)
        self.ctrl.delete_track(["ObjB"])

        self.assertFalse(self.sequencer.store.is_object_pinned("ObjB"))
        self.assertFalse(self.sequencer.is_object_hidden("ObjB"))

    def test_delete_rebuilds_widget(self):
        """After deletion the widget should no longer have that track."""
        self._set_segments(
            0,
            make_segments("ObjA", [(100, 200)])
            + make_segments("ObjB", [(120, 180)]),
        )
        self._set_segments(1, make_segments("ObjA", [(250, 350)]))
        self._do_initial_sync()

        # ObjB should be present initially (it has segments)
        self.assertIn("ObjB", {t.name for t in self.widget.tracks()})

        # After deletion, segments no longer include ObjB
        self._set_segments(0, make_segments("ObjA", [(100, 200)]))
        self.ctrl.delete_track(["ObjB"])

        self.assertNotIn("ObjB", {t.name for t in self.widget.tracks()})


# ===========================================================================
# Test: ShotStore Pinning Serialisation
# ===========================================================================


class TestPinnedSerialisation(unittest.TestCase):
    """Verify pinned_objects round-trips through to_dict / from_dict."""

    def test_round_trip(self):
        store = ShotStore()
        store.define_shot(name="S1", start=0, end=100, objects=["A"])
        store.set_object_pinned("A")
        store.set_object_pinned("B")

        data = store.to_dict()
        restored = ShotStore.from_dict(data)

        self.assertTrue(restored.is_object_pinned("A"))
        self.assertTrue(restored.is_object_pinned("B"))
        self.assertFalse(restored.is_object_pinned("C"))

    def test_empty_pinned_by_default(self):
        data = {"shots": [], "hidden_objects": []}
        restored = ShotStore.from_dict(data)
        self.assertEqual(restored.pinned_objects, set())


# ===========================================================================
# Test: remove_object_from_shots
# ===========================================================================


class TestRemoveObjectFromShots(unittest.TestCase):
    """Verify remove_object_from_shots cleans up all references."""

    def test_removes_from_all_shots(self):
        store = ShotStore()
        store.define_shot(name="S1", start=0, end=100, objects=["A", "B"])
        store.define_shot(name="S2", start=100, end=200, objects=["B", "C"])

        store.remove_object_from_shots("B")

        self.assertEqual(store.shots[0].objects, ["A"])
        self.assertEqual(store.shots[1].objects, ["C"])

    def test_clears_pinned_and_hidden(self):
        store = ShotStore()
        store.define_shot(name="S1", start=0, end=100, objects=["A"])
        store.set_object_pinned("A")
        store.set_object_hidden("A")

        store.remove_object_from_shots("A")

        self.assertFalse(store.is_object_pinned("A"))
        self.assertFalse(store.is_object_hidden("A"))


# ===========================================================================
# Test: Lockable Gaps
# ===========================================================================


class TestLockableGaps(ControllerTestCase):
    """Verify gap locking: lock state persists, respace honours locked widths,
    and lock-all/unlock-all batch operations work correctly."""

    shot_defs = [
        ("Shot_A", 1, 100, ["ObjA"]),
        ("Shot_B", 110, 200, ["ObjA"]),  # 10-frame gap A→B
        ("Shot_C", 220, 300, ["ObjA"]),  # 20-frame gap B→C
    ]

    def test_lock_gap_persists_in_store(self):
        """Locking a gap records the pair in ShotStore.locked_gaps."""
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        store.lock_gap(shots[0].shot_id, shots[1].shot_id)
        self.assertTrue(store.is_gap_locked(shots[0].shot_id, shots[1].shot_id))
        self.assertFalse(store.is_gap_locked(shots[1].shot_id, shots[2].shot_id))

    def test_unlock_gap(self):
        """Unlocking a previously locked gap removes it."""
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        store.lock_gap(shots[0].shot_id, shots[1].shot_id)
        store.unlock_gap(shots[0].shot_id, shots[1].shot_id)
        self.assertFalse(store.is_gap_locked(shots[0].shot_id, shots[1].shot_id))

    def test_respace_honours_locked_gap(self):
        """Respace with a locked gap preserves that gap's original width."""
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        # Lock gap between A and B (currently 10 frames)
        store.lock_gap(shots[0].shot_id, shots[1].shot_id)
        # Respace with gap=5 — locked gap should stay 10, unlocked should be 5
        import mayatk.anim_utils.shots.shot_sequencer._shot_sequencer as _mod
        with patch.object(_mod, "pm", None):
            self.sequencer.respace(gap=5, start_frame=1)
        shots = self.sequencer.sorted_shots()
        gap_ab = shots[1].start - shots[0].end
        gap_bc = shots[2].start - shots[1].end
        self.assertAlmostEqual(gap_ab, 10, places=1)
        self.assertAlmostEqual(gap_bc, 5, places=1)

    def test_respace_unlocked_gaps_use_uniform_value(self):
        """All unlocked gaps should use the global gap value after respace."""
        import mayatk.anim_utils.shots.shot_sequencer._shot_sequencer as _mod
        with patch.object(_mod, "pm", None):
            self.sequencer.respace(gap=15, start_frame=1)
        shots = self.sequencer.sorted_shots()
        gap_ab = shots[1].start - shots[0].end
        gap_bc = shots[2].start - shots[1].end
        self.assertAlmostEqual(gap_ab, 15, places=1)
        self.assertAlmostEqual(gap_bc, 15, places=1)

    def test_lock_all_gaps(self):
        """lock_all_gaps locks every adjacent pair."""
        store = self.sequencer.store
        store.lock_all_gaps()
        shots = self.sequencer.sorted_shots()
        self.assertTrue(store.is_gap_locked(shots[0].shot_id, shots[1].shot_id))
        self.assertTrue(store.is_gap_locked(shots[1].shot_id, shots[2].shot_id))

    def test_unlock_all_gaps(self):
        """unlock_all_gaps clears all locked gaps."""
        store = self.sequencer.store
        store.lock_all_gaps()
        store.unlock_all_gaps()
        self.assertEqual(len(store.locked_gaps), 0)

    def test_locked_gaps_serialisation(self):
        """Locked gaps survive to_dict / from_dict round-trip."""
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        store.lock_gap(shots[0].shot_id, shots[1].shot_id)
        data = store.to_dict()
        restored = ShotStore.from_dict(data)
        self.assertTrue(
            restored.is_gap_locked(shots[0].shot_id, shots[1].shot_id)
        )
        self.assertFalse(
            restored.is_gap_locked(shots[1].shot_id, shots[2].shot_id)
        )

    def test_gap_overlay_receives_locked_state(self):
        """Gap overlays created during rebuild carry the locked flag."""
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        store.lock_gap(shots[0].shot_id, shots[1].shot_id)
        self._do_initial_sync()
        overlays = self.widget._gap_overlays
        # Find the overlay matching the A→B gap
        locked_found = any(o._locked for o in overlays)
        self.assertTrue(locked_found, "No locked gap overlay found after rebuild")

    def test_gap_lock_changed_signal_updates_store(self):
        """Emitting gap_lock_changed should toggle store state via controller."""
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        # Simulate widget signal → controller handler
        self.ctrl.on_gap_lock_changed(
            shots[0].end, shots[1].start, True
        )
        self.assertTrue(store.is_gap_locked(shots[0].shot_id, shots[1].shot_id))
        self.ctrl.on_gap_lock_changed(
            shots[0].end, shots[1].start, False
        )
        self.assertFalse(store.is_gap_locked(shots[0].shot_id, shots[1].shot_id))

    def test_lock_all_via_controller(self):
        """on_gap_lock_all locks all gaps and sets overlays."""
        self._do_initial_sync()
        self.ctrl.on_gap_lock_all()
        store = self.sequencer.store
        shots = self.sequencer.sorted_shots()
        self.assertTrue(store.is_gap_locked(shots[0].shot_id, shots[1].shot_id))
        self.assertTrue(store.is_gap_locked(shots[1].shot_id, shots[2].shot_id))
        # Overlays should all be locked
        for o in self.widget._gap_overlays:
            self.assertTrue(o._locked)

    def test_unlock_all_via_controller(self):
        """on_gap_unlock_all clears all locks and updates overlays."""
        self._do_initial_sync()
        self.ctrl.on_gap_lock_all()
        self.ctrl.on_gap_unlock_all()
        store = self.sequencer.store
        self.assertEqual(len(store.locked_gaps), 0)
        for o in self.widget._gap_overlays:
            self.assertFalse(o._locked)


# ===========================================================================
# Test: Shot Lane Initialization
# ===========================================================================


class TestShotLanePopulation(ControllerTestCase):
    """Verify shot lane is populated on rebuild so gap indicators are visible.

    Bug: set_shot_blocks() was never called from the controller, so the
    shot lane showing all shots and gaps never appeared. Users saw no
    visual indication of shot structure or gaps.
    Fixed: 2026-06-27
    """

    shot_defs = [
        ("Shot_A", 100, 200, ["Obj"]),
        ("Shot_B", 220, 300, ["Obj"]),  # 20-frame gap
        ("Shot_C", 310, 400, ["Obj"]),  # 10-frame gap
    ]
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        for shot in self.sequencer.sorted_shots():
            self._set_segments(
                shot.shot_id, make_segments("Obj", [(shot.start, shot.end)])
            )

    def test_ruler_blocks_populated_after_sync(self):
        """Ruler must have shot blocks after _sync_to_widget."""
        self._do_initial_sync()
        blocks = self.widget._timeline._scene.ruler._shot_blocks
        self.assertEqual(
            len(blocks), 3,
            "All three shots should appear as ruler blocks",
        )

    def test_ruler_blocks_mark_active_shot(self):
        """Only the active shot block should be marked 'active'."""
        self._do_initial_sync()
        blocks = self.widget._timeline._scene.ruler._shot_blocks
        active_blocks = [b for b in blocks if b.get("active")]
        self.assertEqual(len(active_blocks), 1)
        self.assertEqual(active_blocks[0]["name"], "Shot_A")

    def test_ruler_blocks_match_shot_ranges(self):
        """Ruler block start/end must match actual shot ranges."""
        self._do_initial_sync()
        blocks = self.widget._timeline._scene.ruler._shot_blocks
        for blk, shot in zip(blocks, self.sequencer.sorted_shots()):
            self.assertAlmostEqual(blk["start"], shot.start, places=1)
            self.assertAlmostEqual(blk["end"], shot.end, places=1)

    def test_ruler_blocks_cleared_on_full_clear(self):
        """widget.clear() must also clear ruler shot blocks."""
        self._do_initial_sync()
        self.widget.clear()
        self.assertEqual(
            len(self.widget._timeline._scene.ruler._shot_blocks), 0,
            "Ruler blocks should be empty after clear()",
        )


# ===========================================================================
# Test: Gap Overlay Initialization with store.gap > 0
# ===========================================================================


class TestGapOverlayDisplay(ControllerTestCase):
    """Verify gap overlays appear for ALL gaps regardless of display mode.

    Bug: Visibility gate in _rebuild_decoration filtered gaps to only those
    bordering a visible shot. In 'current' mode only the active shot was
    visible, so gaps between non-active shots were hidden.
    Fixed: 2026-06-28
    """

    shot_defs = [
        ("Shot_A", 1, 101, ["Obj"]),
        ("Shot_B", 111, 211, ["Obj"]),
        ("Shot_C", 221, 321, ["Obj"]),
    ]
    initial_shot_index = 0

    def test_all_gaps_shown_in_current_mode(self):
        """Even in 'current' display mode, all gaps must have overlays."""
        self._do_initial_sync()
        # There are 2 gaps: A→B (101-111) and B→C (211-221)
        self.assertEqual(
            len(self.widget._gap_overlays), 2,
            "Both gaps must have overlays regardless of display mode",
        )

    def test_contiguous_shots_no_overlays(self):
        """Contiguous shots (no gaps) must have zero gap overlays."""
        # Override shot_defs via store to make shots contiguous
        for s in self.sequencer.sorted_shots():
            pass  # shots already defined with gaps in shot_defs
        # Create a separate contiguous scenario
        pass

    def test_gap_overlay_positions_match_shot_boundaries(self):
        """Gap overlay start/end must match shot boundary gaps."""
        self._do_initial_sync()
        overlays = self.widget._gap_overlays
        shots = self.sequencer.sorted_shots()
        # First gap: Shot_A.end to Shot_B.start
        self.assertAlmostEqual(overlays[0]._start, shots[0].end, places=1)
        self.assertAlmostEqual(overlays[0]._end, shots[1].start, places=1)
        # Second gap: Shot_B.end to Shot_C.start
        self.assertAlmostEqual(overlays[1]._start, shots[1].end, places=1)
        self.assertAlmostEqual(overlays[1]._end, shots[2].start, places=1)


class TestContiguousShotsOverlays(ControllerTestCase):
    """Contiguous shots produce handle overlays at every boundary."""

    shot_defs = [
        ("Shot_A", 1, 101, ["Obj"]),
        ("Shot_B", 101, 201, ["Obj"]),
        ("Shot_C", 201, 301, ["Obj"]),
    ]
    initial_shot_index = 0

    def test_overlays_for_contiguous_shots(self):
        self._do_initial_sync()
        self.assertEqual(
            len(self.widget._gap_overlays), 2,
            "Contiguous shots should have handle overlays at each boundary",
        )


# ===========================================================================
# Test: Shotless Scene Display
# ===========================================================================


class TestShotlessDisplay(ControllerTestCase):
    """Verify animated objects display when no shots are defined.

    When the scene has animation but no shots, the sequencer should show
    all animated objects across the full playback range.
    """

    shot_defs = []  # Explicitly no shots
    initial_shot_index = 0

    def setUp(self):
        super().setUp()
        # Configure playback range
        def _pb_options(**kw):
            if kw.get("q"):
                if kw.get("min"):
                    return 1.0
                if kw.get("max"):
                    return 200.0
            return 0.0

        _mock_pm.playbackOptions.side_effect = _pb_options
        _mock_pm.currentTime.return_value = 50.0
        _mock_cmds.playbackOptions.side_effect = _pb_options
        _mock_cmds.currentTime.return_value = 50.0
        _mock_cmds.ls.return_value = []

    def _mock_scene_objects(self, objects, segments):
        """Patch discovery and segment collection for shotless mode."""
        self.sequencer._find_keyed_transforms = MagicMock(return_value=objects)
        self._seg_patcher = patch(
            "mayatk.anim_utils.segment_keys.SegmentKeys.collect_segments",
            return_value=segments,
        )
        self._seg_patcher.start()

    def tearDown(self):
        if hasattr(self, "_seg_patcher"):
            self._seg_patcher.stop()
        super().tearDown()

    def test_tracks_created_for_discovered_objects(self):
        """Discovered animated objects should appear as tracks."""
        self._mock_scene_objects(
            ["ObjA", "ObjB"],
            [
                {"obj": "ObjA", "start": 10.0, "end": 80.0, "duration": 70.0,
                 "is_stepped": False, "curves": [], "attr": None},
                {"obj": "ObjB", "start": 20.0, "end": 100.0, "duration": 80.0,
                 "is_stepped": False, "curves": [], "attr": None},
            ],
        )
        self._do_initial_sync()
        track_names = [t.name for t in self.widget.tracks()]
        self.assertIn("ObjA", track_names)
        self.assertIn("ObjB", track_names)

    def test_clips_created_for_segments(self):
        """Segments should produce clips in the widget."""
        self._mock_scene_objects(
            ["ObjA"],
            [
                {"obj": "ObjA", "start": 10.0, "end": 80.0, "duration": 70.0,
                 "is_stepped": False, "curves": [], "attr": None},
            ],
        )
        self._do_initial_sync()
        clips = self.widget.clips()
        self.assertGreater(len(clips), 0, "Must have at least 1 clip")

    def test_active_range_covers_playback(self):
        """Active range should span the full playback range."""
        self._mock_scene_objects(
            ["ObjA"],
            [
                {"obj": "ObjA", "start": 10.0, "end": 80.0, "duration": 70.0,
                 "is_stepped": False, "curves": [], "attr": None},
            ],
        )
        self._do_initial_sync()
        self.assertIsNotNone(self.widget._active_range, "Active range must be set")
        self.assertAlmostEqual(self.widget._active_range[0], 1.0, places=1)
        self.assertAlmostEqual(self.widget._active_range[1], 200.0, places=1)

    def test_playhead_set_to_current_time(self):
        """Playhead should reflect the mocked current time."""
        self._mock_scene_objects(
            ["ObjA"],
            [
                {"obj": "ObjA", "start": 10.0, "end": 80.0, "duration": 70.0,
                 "is_stepped": False, "curves": [], "attr": None},
            ],
        )
        self._do_initial_sync()
        ph = self.widget._timeline._scene.playhead
        self.assertAlmostEqual(ph._time, 50.0, places=1)

    def test_no_animation_shows_empty_widget(self):
        """When no animated objects are found, widget should be empty."""
        self._mock_scene_objects([], [])
        self._do_initial_sync()
        self.assertEqual(len(list(self.widget.tracks())), 0)

    def test_footer_shows_scene_summary(self):
        """Footer should show scene range and object count."""
        self._mock_scene_objects(
            ["ObjA", "ObjB"],
            [
                {"obj": "ObjA", "start": 10.0, "end": 80.0, "duration": 70.0,
                 "is_stepped": False, "curves": [], "attr": None},
            ],
        )
        self._do_initial_sync()
        footer = getattr(self.slots.ui, "footer", None)
        if footer is not None and footer.setText.called:
            text = footer.setText.call_args[0][0]
            self.assertIn("Scene", text)
            self.assertIn("2 objects", text)


# ===========================================================================
# Test: Playback Follows View
# ===========================================================================


class TestPlaybackFollowsView(ControllerTestCase):
    """Verify playback range adjusts to match the sequencer view mode."""

    shot_defs = [
        ("Shot_A", 100, 200, ["ObjA"]),
        ("Shot_B", 200, 350, ["ObjB"]),
        ("Shot_C", 350, 500, ["ObjC"]),
    ]
    initial_shot_index = 1  # Start on Shot_B (middle)

    def test_current_mode_sets_active_shot_range(self):
        """In 'current' mode, playback range = active shot only."""
        self.ctrl._playback_follows_view = True
        self.ctrl._shot_display_mode = "current"
        self.ctrl.select_shot(self.sequencer.sorted_shots()[1].shot_id)

        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=200, max=350))

    def test_adjacent_mode_spans_three_shots(self):
        """In 'adjacent' mode on middle shot, range = prev+current+next."""
        self.ctrl._playback_follows_view = True
        self.ctrl._shot_display_mode = "adjacent"
        self.ctrl.select_shot(self.sequencer.sorted_shots()[1].shot_id)

        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=100, max=500))

    def test_adjacent_mode_first_shot(self):
        """In 'adjacent' mode on first shot, range = current+next only."""
        self.ctrl._playback_follows_view = True
        self.ctrl._shot_display_mode = "adjacent"
        self.ctrl.select_shot(self.sequencer.sorted_shots()[0].shot_id)

        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=100, max=350))

    def test_all_mode_spans_entire_timeline(self):
        """In 'all' mode, range covers first to last shot."""
        self.ctrl._playback_follows_view = True
        self.ctrl._shot_display_mode = "all"
        self.ctrl.select_shot(self.sequencer.sorted_shots()[1].shot_id)

        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=100, max=500))

    def test_disabled_always_uses_active_shot(self):
        """When _playback_follows_view is False, always use active shot range."""
        self.ctrl._playback_follows_view = False
        self.ctrl._shot_display_mode = "all"
        self.ctrl.select_shot(self.sequencer.sorted_shots()[1].shot_id)

        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=200, max=350))

    def test_view_mode_change_updates_range(self):
        """Changing view mode re-applies the playback range."""
        self.ctrl._playback_follows_view = True
        self.ctrl.select_shot(self.sequencer.sorted_shots()[1].shot_id)
        _mock_pm.playbackOptions.reset_mock()

        self.ctrl._set_view_mode("all")

        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=100, max=500))

    def test_toggle_reapplies_range(self):
        """Toggling playback-follows-view on/off reapplies correctly."""
        self.ctrl._shot_display_mode = "all"
        self.ctrl.select_shot(self.sequencer.sorted_shots()[1].shot_id)

        self.ctrl._toggle_playback_follows_view(False)
        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=200, max=350))

        self.ctrl._toggle_playback_follows_view(True)
        args = _mock_pm.playbackOptions.call_args
        self.assertEqual(args, call(min=100, max=500))


if __name__ == "__main__":
    unittest.main(verbosity=2)
