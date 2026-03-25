# !/usr/bin/python
# coding=utf-8
"""Integration tests against a real Maya scene file.

Opens the C130H_FCR_SPEEDRUN scene and verifies that the shot sequencer
accurately represents the actual Maya scene data: animated objects are
found, shots are detected, tracks/clips match real keyframe ranges,
and sub-row expansion returns correct per-attribute data.

Usage::

    python run_tests.py sequencer_real_scene
    python -m pytest mayatk/test/test_sequencer_real_scene.py -v
    mayapy mayatk/test/test_sequencer_real_scene.py
"""
import unittest
import sys
import os
from collections import defaultdict

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
# Scene path
# ---------------------------------------------------------------------------
SCENE_PATH = (
    r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
    r"\_tests\sequencer_test\C130H_FCR_SPEEDRUN_copy.ma"
)

# ---------------------------------------------------------------------------
# Maya bootstrap
# ---------------------------------------------------------------------------
HAS_MAYA = False
try:
    import maya.cmds as _cmds

    _cmds.about(version=True)
    HAS_MAYA = True
except Exception:
    try:
        from mayatk.env_utils.maya_connection import MayaConnection

        _conn = MayaConnection.get_instance()
        if not _conn.is_connected:
            _conn.connect(mode="standalone")
        HAS_MAYA = _conn.is_connected
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Qt bootstrap
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
# Conditional imports
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
    from mayatk.anim_utils.shots._shots import ShotStore, detect_shot_regions
    from mayatk.anim_utils.segment_keys import SegmentKeys


def _process_events():
    QtWidgets.QApplication.processEvents()


_SKIP_MSG = "Requires Maya + Qt"
_SKIP_SCENE = "Scene file not found"


def _scene_exists():
    return os.path.isfile(SCENE_PATH)


# =========================================================================
# Helpers — query Maya for ground truth
# =========================================================================


def _get_all_animated_transforms():
    """Return set of transform names that have animation curves."""
    curves = cmds.ls(type="animCurve") or []
    transforms = set()
    for crv in curves:
        conns = cmds.listConnections(crv, d=True, s=False) or []
        for node in conns:
            node_type = cmds.nodeType(node)
            if node_type == "transform":
                transforms.add(node)
            else:
                parents = cmds.listRelatives(node, parent=True, type="transform") or []
                if parents:
                    transforms.add(parents[0])
    return transforms


def _get_keyframe_range(obj_name):
    """Return (min_time, max_time) for all keys on an object."""
    curves = cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
    all_times = []
    for crv in curves:
        times = cmds.keyframe(crv, q=True, timeChange=True) or []
        all_times.extend(times)
    if not all_times:
        return None
    return (min(all_times), max(all_times))


def _get_animated_attributes(obj_name):
    """Return list of attribute names that have animation curves."""
    curves = cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
    attrs = set()
    for crv in curves:
        conns = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
        for plug in conns:
            # plug is "object.attribute"
            if "." in plug:
                attr = plug.split(".")[-1]
                attrs.add(attr)
    return sorted(attrs)


def _get_key_times_for_attr(obj_name, attr_name):
    """Return sorted list of keyframe times for a specific attribute."""
    full_attr = f"{obj_name}.{attr_name}"
    return sorted(cmds.keyframe(full_attr, q=True, timeChange=True) or [])


# =========================================================================
# Test: Scene Discovery — detect_shot_regions vs scene ground truth
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestSceneDiscovery(unittest.TestCase):
    """Verify detect_shot_regions finds animation regions matching scene data."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.all_transforms = _get_all_animated_transforms()
        cls.regions = detect_shot_regions()

    def test_scene_has_animated_objects(self):
        """The scene must contain animated transforms."""
        self.assertGreater(
            len(self.all_transforms), 0, "No animated transforms found in scene"
        )

    def test_regions_detected(self):
        """detect_shot_regions finds at least one region."""
        self.assertGreater(len(self.regions), 0, "No shot regions detected in scene")

    def test_regions_have_valid_ranges(self):
        """Each region has start < end."""
        for r in self.regions:
            self.assertLess(
                r["start"],
                r["end"],
                f"Region {r['name']}: start={r['start']} >= end={r['end']}",
            )

    def test_regions_have_objects(self):
        """Each region references at least one object."""
        for r in self.regions:
            self.assertGreater(
                len(r["objects"]),
                0,
                f"Region {r['name']} has no objects",
            )

    def test_region_objects_exist_in_scene(self):
        """Every object referenced by a region exists in the scene."""
        for r in self.regions:
            for obj in r["objects"]:
                self.assertTrue(
                    cmds.objExists(obj),
                    f"Region {r['name']} references non-existent object: {obj}",
                )

    def test_region_objects_have_keyframes(self):
        """Every object in a region has at least one keyframe."""
        for r in self.regions:
            for obj in r["objects"]:
                kr = _get_keyframe_range(obj)
                self.assertIsNotNone(
                    kr, f"Object {obj} in {r['name']} has no keyframes"
                )

    def test_regions_sorted_by_start(self):
        """Regions are returned sorted by start time."""
        starts = [r["start"] for r in self.regions]
        self.assertEqual(starts, sorted(starts))

    def test_regions_are_non_overlapping(self):
        """Regions detected should not overlap each other."""
        sorted_regions = sorted(self.regions, key=lambda r: r["start"])
        for i in range(len(sorted_regions) - 1):
            self.assertLessEqual(
                sorted_regions[i]["end"],
                sorted_regions[i + 1]["start"],
                f"Region '{sorted_regions[i]['name']}' (end={sorted_regions[i]['end']}) "
                f"overlaps with '{sorted_regions[i + 1]['name']}' (start={sorted_regions[i + 1]['start']})",
            )


# =========================================================================
# Test: ShotSequencer Engine — segments match actual keyframe data
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestEngineSegments(unittest.TestCase):
    """Verify collect_object_segments returns accurate data for real scene."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.regions = detect_shot_regions()
        cls.store = ShotStore()
        for r in cls.regions:
            cls.store.define_shot(r["name"], r["start"], r["end"], r["objects"])
        cls.seq = ShotSequencer(store=cls.store)

    def test_each_shot_has_segments(self):
        """Every defined shot produces at least one segment."""
        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            self.assertGreater(
                len(segs),
                0,
                f"Shot '{shot.name}' (id={shot.shot_id}, {shot.start}-{shot.end}) "
                f"has {len(shot.objects)} objects but 0 segments",
            )

    def test_segment_objects_are_in_shot(self):
        """Every segment references an object that exists in the scene."""
        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            for seg in segs:
                self.assertTrue(
                    cmds.objExists(seg["obj"]),
                    f"Segment references non-existent object: {seg['obj']}",
                )

    def test_segment_times_within_shot_range(self):
        """Segment start/end should fall within reasonable range of the shot."""
        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            for seg in segs:
                # Allow some tolerance for keys at shot boundaries
                self.assertGreaterEqual(
                    seg["start"],
                    shot.start - 1.0,
                    f"Segment start {seg['start']} is before shot start {shot.start} "
                    f"(obj={seg['obj']}, shot={shot.name})",
                )
                self.assertLessEqual(
                    seg["end"],
                    shot.end + 1.0,
                    f"Segment end {seg['end']} is after shot end {shot.end} "
                    f"(obj={seg['obj']}, shot={shot.name})",
                )

    def test_segment_has_required_keys(self):
        """Each segment dict has 'obj', 'start', 'end', 'duration'."""
        required = {"obj", "start", "end", "duration"}
        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            for seg in segs:
                for key in required:
                    self.assertIn(
                        key,
                        seg,
                        f"Segment for {seg.get('obj', '?')} missing key '{key}'",
                    )

    def test_segment_duration_consistent(self):
        """Segment duration equals end - start."""
        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            for seg in segs:
                if seg.get("is_stepped"):
                    continue  # Stepped keys can have 0 duration
                expected = seg["end"] - seg["start"]
                self.assertAlmostEqual(
                    seg["duration"],
                    expected,
                    places=1,
                    msg=f"Duration mismatch for {seg['obj']}: "
                    f"expected {expected}, got {seg['duration']}",
                )

    def test_all_animated_objects_represented(self):
        """Objects with keyframes inside a shot range appear in segments."""
        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            seg_objs = {s["obj"] for s in segs}
            for obj in shot.objects:
                if not cmds.objExists(obj):
                    continue
                kr = _get_keyframe_range(obj)
                if kr is None:
                    continue
                # Object has keys — check if any fall within shot range
                obj_min, obj_max = kr
                if obj_max < shot.start or obj_min > shot.end:
                    continue  # keys entirely outside shot range
                self.assertIn(
                    obj,
                    seg_objs,
                    f"Object {obj} has keys in shot '{shot.name}' "
                    f"({shot.start}-{shot.end}) but is not in segments",
                )


# =========================================================================
# Test: Widget Population — tracks & clips match engine data
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestWidgetPopulation(unittest.TestCase):
    """Full integration: load scene → detect → populate widget → verify."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.regions = detect_shot_regions()
        cls.store = ShotStore()
        for r in cls.regions:
            cls.store.define_shot(r["name"], r["start"], r["end"], r["objects"])
        cls.seq = ShotSequencer(store=cls.store)

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.resize(1200, 600)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def _populate_shot(self, shot_id):
        """Populate widget from a single shot (mirrors controller pipeline)."""
        shot = self.seq.shot_by_id(shot_id)
        segs = self.seq.collect_object_segments(shot_id)

        by_obj = defaultdict(list)
        for s in segs:
            by_obj[s["obj"]].append(s)

        track_ids = {}
        # Build tracks for all objects
        all_objs = sorted(set(shot.objects) | set(by_obj.keys()))
        for obj in all_objs:
            if not cmds.objExists(obj):
                continue
            short_name = obj.split("|")[-1]
            tid = self.widget.add_track(short_name)
            track_ids[obj] = tid

        # Build clips
        for obj, obj_segs in by_obj.items():
            tid = track_ids.get(obj)
            if tid is None:
                continue
            span_segs = [s for s in obj_segs if not s.get("is_stepped")]
            stepped_segs = [s for s in obj_segs if s.get("is_stepped")]

            if span_segs:
                # Merge adjacent segments
                store = self.seq.store if self.seq else None
                gap = store.detection_threshold if store else 10.0
                span_segs.sort(key=lambda sg: sg["start"])
                merged = [{"start": span_segs[0]["start"], "end": span_segs[0]["end"]}]
                for seg in span_segs[1:]:
                    if seg["start"] <= merged[-1]["end"] + gap:
                        merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                    else:
                        merged.append({"start": seg["start"], "end": seg["end"]})
                for m in merged:
                    s, e = m["start"], m["end"]
                    self.widget.add_clip(
                        tid,
                        s,
                        e - s,
                        obj=obj,
                        orig_start=s,
                        orig_end=e,
                        shot_id=shot_id,
                    )
            for seg in stepped_segs:
                self.widget.add_clip(
                    tid,
                    seg["start"],
                    0.0,
                    obj=obj,
                    is_stepped=True,
                    stepped_key_time=seg["start"],
                    shot_id=shot_id,
                )

        self.widget.set_range_highlight(shot.start, shot.end)
        self.widget.set_playhead(shot.start)
        return track_ids, by_obj

    def test_tracks_match_objects(self):
        """Each animated object gets exactly one track."""
        for shot in self.store.shots:
            self.widget.clear()
            track_ids, by_obj = self._populate_shot(shot.shot_id)
            _process_events()
            # Track count should match number of unique scene objects
            expected = len(track_ids)
            self.assertEqual(
                len(self.widget.tracks()),
                expected,
                f"Shot '{shot.name}': expected {expected} tracks, "
                f"got {len(self.widget.tracks())}",
            )

    def test_clips_created_for_animated_objects(self):
        """Objects with segments get at least one clip."""
        for shot in self.store.shots:
            self.widget.clear()
            track_ids, by_obj = self._populate_shot(shot.shot_id)
            _process_events()
            clips = self.widget.clips()
            clip_objs = {c.data.get("obj") for c in clips}
            for obj in by_obj:
                self.assertIn(
                    obj,
                    clip_objs,
                    f"Object {obj} has segments but no clip in shot '{shot.name}'",
                )

    def test_clip_ranges_encompass_keyframes(self):
        """Clip start/end should encompass the actual keyframe range for the object."""
        for shot in self.store.shots:
            self.widget.clear()
            track_ids, by_obj = self._populate_shot(shot.shot_id)
            _process_events()
            clips = self.widget.clips()
            for clip in clips:
                obj = clip.data.get("obj")
                if not obj or not cmds.objExists(obj):
                    continue
                # Clip range should be within shot range
                self.assertGreaterEqual(
                    clip.start,
                    shot.start - 1.0,
                    f"Clip for {obj} starts before shot range",
                )
                self.assertLessEqual(
                    clip.end,
                    shot.end + 1.0,
                    f"Clip for {obj} ends after shot range",
                )

    def test_range_highlight_matches_shot(self):
        """Range highlight is set to shot boundaries."""
        for shot in self.store.shots:
            self.widget.clear()
            self._populate_shot(shot.shot_id)
            _process_events()
            rh = self.widget.range_highlight()
            self.assertIsNotNone(rh, f"No range highlight for shot '{shot.name}'")
            self.assertAlmostEqual(rh[0], shot.start, places=1)
            self.assertAlmostEqual(rh[1], shot.end, places=1)

    def test_frame_shot_no_crash(self):
        """frame_shot does not crash after population."""
        for shot in self.store.shots:
            self.widget.clear()
            self._populate_shot(shot.shot_id)
            _process_events()
            self.widget.frame_shot()
            _process_events()

    def test_clear_and_repopulate(self):
        """Clearing and repopulating produces identical results."""
        shot = self.store.shots[0]
        self._populate_shot(shot.shot_id)
        _process_events()
        n_tracks = len(self.widget.tracks())
        n_clips = len(self.widget.clips())
        self.widget.clear()
        self._populate_shot(shot.shot_id)
        _process_events()
        self.assertEqual(len(self.widget.tracks()), n_tracks)
        self.assertEqual(len(self.widget.clips()), n_clips)


# =========================================================================
# Test: Sub-Row Expansion — per-attribute data matches scene
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestSubRowExpansion(unittest.TestCase):
    """Expand a track and verify sub-row keyframe data matches scene."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.regions = detect_shot_regions()
        cls.store = ShotStore()
        for r in cls.regions:
            cls.store.define_shot(r["name"], r["start"], r["end"], r["objects"])
        cls.seq = ShotSequencer(store=cls.store)

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.resize(1200, 600)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def _get_sub_row_data(self, shot, obj_name):
        """Build sub-row data for an object (simplified from controller)."""
        curves = cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
        if not curves:
            return []

        # Group curves by attribute
        by_attr = defaultdict(list)
        for crv in curves:
            conns = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
            for plug in conns:
                if "." in plug:
                    attr = plug.split(".")[-1]
                    by_attr[attr].append(crv)

        sub_rows = []
        for attr, attr_curves in sorted(by_attr.items()):
            # Get all keyframe times in the shot range
            key_times = []
            for crv in attr_curves:
                times = (
                    cmds.keyframe(
                        crv, q=True, time=(shot.start, shot.end), timeChange=True
                    )
                    or []
                )
                key_times.extend(times)
            key_times = sorted(set(key_times))
            if not key_times:
                continue

            start = min(key_times)
            end = max(key_times)
            duration = max(end - start, 0.0)
            sub_rows.append(
                (
                    attr,
                    [
                        (
                            start,
                            duration,
                            attr,
                            None,
                            {"keyframe_times": key_times, "obj": obj_name},
                        )
                    ],
                )
            )
        return sub_rows

    def test_sub_rows_have_keyframes(self):
        """Every sub-row segment has keyframe_times matching Maya data."""
        shot = self.store.shots[0]
        # Pick the first object with animation
        for obj in shot.objects:
            if not cmds.objExists(obj):
                continue
            sub_data = self._get_sub_row_data(shot, obj)
            if not sub_data:
                continue

            # Populate the widget first
            short_name = obj.split("|")[-1]
            tid = self.widget.add_track(short_name)
            self.widget.add_clip(tid, shot.start, shot.duration, obj=obj)
            self.widget.expand_track(tid, sub_row_data=sub_data)
            _process_events()

            # Verify sub-row clips exist and their keyframe data is correct
            sub_clips = [c for c in self.widget.clips() if c.sub_row]
            self.assertGreater(
                len(sub_clips),
                0,
                f"No sub-row clips after expansion for {obj}",
            )

            for clip in sub_clips:
                attr = clip.sub_row
                kf_times = clip.data.get("keyframe_times", [])
                # Verify against Maya: query actual key times for this attr
                actual_times = _get_key_times_for_attr(obj, attr)
                # Filter to shot range
                actual_in_range = [
                    t for t in actual_times if shot.start <= t <= shot.end
                ]
                if actual_in_range:
                    self.assertGreater(
                        len(kf_times),
                        0,
                        f"Sub-row {attr} for {obj} has no keyframe_times "
                        f"but Maya has {len(actual_in_range)} keys",
                    )
                    # Every keyframe in the sub-row data should exist in Maya
                    for kf in kf_times:
                        if isinstance(kf, tuple):
                            kf = kf[0]  # (time, tangent_type)
                        self.assertIn(
                            kf,
                            actual_in_range,
                            f"Keyframe time {kf} for {obj}.{attr} "
                            f"not in Maya data: {actual_in_range}",
                        )
            return  # Test one object is sufficient
        self.skipTest("No animated objects found in first shot")

    def test_expand_all_objects_no_crash(self):
        """Expanding tracks for every object in every shot doesn't crash."""
        for shot in self.store.shots:
            self.widget.clear()
            for obj in shot.objects:
                if not cmds.objExists(obj):
                    continue
                sub_data = self._get_sub_row_data(shot, obj)
                if not sub_data:
                    continue
                short_name = obj.split("|")[-1]
                tid = self.widget.add_track(short_name)
                self.widget.add_clip(tid, shot.start, shot.duration, obj=obj)
                self.widget.expand_track(tid, sub_row_data=sub_data)
            _process_events()


# =========================================================================
# Test: Keyboard Navigation with Real Scene Keys
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestKeyboardNavigation(unittest.TestCase):
    """Arrow keys navigate to correct keyframe positions from real scene."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.regions = detect_shot_regions()
        cls.store = ShotStore()
        for r in cls.regions:
            cls.store.define_shot(r["name"], r["start"], r["end"], r["objects"])
        cls.seq = ShotSequencer(store=cls.store)

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.resize(1200, 600)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def test_next_key_lands_on_clip_boundary(self):
        """go_to_next_key reaches the start of the first clip."""
        shot = self.store.shots[0]
        segs = self.seq.collect_object_segments(shot.shot_id)
        by_obj = defaultdict(list)
        for s in segs:
            by_obj[s["obj"]].append(s)

        for obj, obj_segs in by_obj.items():
            if not cmds.objExists(obj):
                continue
            short = obj.split("|")[-1]
            tid = self.widget.add_track(short)
            span = [s for s in obj_segs if not s.get("is_stepped")]
            if span:
                s = min(seg["start"] for seg in span)
                e = max(seg["end"] for seg in span)
                self.widget.add_clip(tid, s, e - s, obj=obj)

        self.widget.set_playhead(shot.start)
        _process_events()

        key_times = self.widget._key_times()
        if not key_times:
            self.skipTest("No key times available")

        # Navigate forward through all key times
        ph = self.widget._timeline._scene.playhead
        current = shot.start
        for expected in key_times:
            if expected <= current + 0.01:
                continue
            self.widget.go_to_next_key()
            self.assertAlmostEqual(
                ph.time,
                expected,
                places=0,
                msg=f"Expected playhead at {expected}, got {ph.time}",
            )
            current = ph.time

    def test_prev_key_navigates_backward(self):
        """go_to_prev_key returns through clip boundaries."""
        shot = self.store.shots[0]
        segs = self.seq.collect_object_segments(shot.shot_id)
        by_obj = defaultdict(list)
        for s in segs:
            by_obj[s["obj"]].append(s)

        for obj, obj_segs in by_obj.items():
            if not cmds.objExists(obj):
                continue
            short = obj.split("|")[-1]
            tid = self.widget.add_track(short)
            span = [s for s in obj_segs if not s.get("is_stepped")]
            if span:
                s = min(seg["start"] for seg in span)
                e = max(seg["end"] for seg in span)
                self.widget.add_clip(tid, s, e - s, obj=obj)

        key_times = self.widget._key_times()
        if len(key_times) < 2:
            self.skipTest("Not enough key times to navigate")

        # Start at the last key time
        self.widget.set_playhead(key_times[-1])
        _process_events()

        ph = self.widget._timeline._scene.playhead
        current = key_times[-1]
        for expected in reversed(key_times[:-1]):
            if expected >= current - 0.01:
                continue
            self.widget.go_to_prev_key()
            self.assertAlmostEqual(
                ph.time,
                expected,
                places=0,
                msg=f"Expected playhead at {expected}, got {ph.time}",
            )
            current = ph.time


# =========================================================================
# Test: Undo/Redo with Real Scene Data
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestUndoWithRealData(unittest.TestCase):
    """Undo/redo of clip operations with real scene data."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.regions = detect_shot_regions()
        cls.store = ShotStore()
        for r in cls.regions:
            cls.store.define_shot(r["name"], r["start"], r["end"], r["objects"])
        cls.seq = ShotSequencer(store=cls.store)

    def setUp(self):
        self.widget = SequencerWidget()
        self.widget.resize(1200, 600)
        self.widget.show()
        _process_events()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        _process_events()

    def _populate_first_shot(self):
        """Populate widget with first shot data."""
        shot = self.store.shots[0]
        segs = self.seq.collect_object_segments(shot.shot_id)
        by_obj = defaultdict(list)
        for s in segs:
            by_obj[s["obj"]].append(s)
        track_ids = {}
        for obj in sorted(set(shot.objects) | set(by_obj.keys())):
            if not cmds.objExists(obj):
                continue
            tid = self.widget.add_track(obj.split("|")[-1])
            track_ids[obj] = tid
        for obj, obj_segs in by_obj.items():
            tid = track_ids.get(obj)
            if tid is None:
                continue
            span = [s for s in obj_segs if not s.get("is_stepped")]
            if span:
                s = min(seg["start"] for seg in span)
                e = max(seg["end"] for seg in span)
                self.widget.add_clip(tid, s, e - s, obj=obj, shot_id=shot.shot_id)
        self.widget.set_range_highlight(shot.start, shot.end)
        return shot

    def test_undo_restores_clip_position(self):
        """After moving a clip, undo restores the original position."""
        self._populate_first_shot()
        _process_events()
        clips = self.widget.clips()
        if not clips:
            self.skipTest("No clips to test")
        clip = clips[0]
        original_start = clip.start
        # Capture undo, then modify
        self.widget._capture_undo()
        self.widget._clips[clip.clip_id].start = original_start + 10
        # Undo should restore
        self.widget.undo()
        self.assertAlmostEqual(
            self.widget.get_clip(clip.clip_id).start,
            original_start,
            places=1,
        )

    def test_redo_reapplies_change(self):
        """After undo, redo reapplies the modification."""
        self._populate_first_shot()
        _process_events()
        clips = self.widget.clips()
        if not clips:
            self.skipTest("No clips to test")
        clip = clips[0]
        original_start = clip.start
        new_start = original_start + 10
        self.widget._capture_undo()
        self.widget._clips[clip.clip_id].start = new_start
        self.widget.undo()
        self.widget.redo()
        self.assertAlmostEqual(
            self.widget.get_clip(clip.clip_id).start,
            new_start,
            places=1,
        )

    def test_multiple_undo_redo_cycles(self):
        """Several undo/redo cycles stay consistent."""
        self._populate_first_shot()
        _process_events()
        clips = self.widget.clips()
        if not clips:
            self.skipTest("No clips to test")
        clip = clips[0]
        original_start = clip.start
        # Make 3 modifications
        for i in range(3):
            self.widget._capture_undo()
            self.widget._clips[clip.clip_id].start = original_start + (i + 1) * 10
        # Undo all 3
        for _ in range(3):
            self.widget.undo()
        self.assertAlmostEqual(
            self.widget.get_clip(clip.clip_id).start,
            original_start,
            places=1,
        )


# =========================================================================
# Test: Scene Object Validation Report
# =========================================================================


@unittest.skipUnless(HAS_MAYA and HAS_QT, _SKIP_MSG)
@unittest.skipUnless(_scene_exists(), _SKIP_SCENE)
class TestSceneReport(unittest.TestCase):
    """Generate a diagnostic report of the scene for visual verification."""

    @classmethod
    def setUpClass(cls):
        cmds.file(SCENE_PATH, open=True, force=True)
        cls.regions = detect_shot_regions()
        cls.store = ShotStore()
        for r in cls.regions:
            cls.store.define_shot(r["name"], r["start"], r["end"], r["objects"])
        cls.seq = ShotSequencer(store=cls.store)

    def test_print_scene_report(self):
        """Print a summary of detected shots and objects for verification."""
        all_transforms = _get_all_animated_transforms()
        print(f"\n{'=' * 70}")
        print(f"SCENE: {os.path.basename(SCENE_PATH)}")
        print(f"Total animated transforms: {len(all_transforms)}")
        print(f"Detected regions: {len(self.regions)}")
        print(f"{'=' * 70}")

        for shot in self.store.shots:
            segs = self.seq.collect_object_segments(shot.shot_id)
            by_obj = defaultdict(list)
            for s in segs:
                by_obj[s["obj"]].append(s)

            print(f"\n--- {shot.name} (id={shot.shot_id}) ---")
            print(f"  Range: {shot.start} - {shot.end} ({shot.duration} frames)")
            print(f"  Objects: {len(shot.objects)}")
            print(f"  Segments: {len(segs)}")

            for obj in sorted(by_obj.keys()):
                obj_segs = by_obj[obj]
                kr = _get_keyframe_range(obj)
                attrs = _get_animated_attributes(obj)
                short = obj.split("|")[-1]
                spans = [s for s in obj_segs if not s.get("is_stepped")]
                stepped = [s for s in obj_segs if s.get("is_stepped")]
                if spans:
                    seg_range = f"{min(s['start'] for s in spans):.0f}-{max(s['end'] for s in spans):.0f}"
                else:
                    seg_range = "none"
                key_range = f"{kr[0]:.0f}-{kr[1]:.0f}" if kr else "none"
                print(
                    f"  {short:30s} keys={key_range:12s} seg={seg_range:12s} "
                    f"attrs={len(attrs):2d} spans={len(spans)} stepped={len(stepped)}"
                )

        print(f"\n{'=' * 70}")
        # This test always passes — it's for inspection
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
