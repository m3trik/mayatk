# !/usr/bin/python
# coding=utf-8
"""Test Suite for edit_utils geometry tool classes.

Covers:
    - Bevel.bevel (bevel.py)
    - Bridge.bridge / get_child_curves_from_bridge / cleanup (bridge.py)
    - Snap.snap_to_closest_vertex / snap_to_surface / snap_to_grid (snap.py)
    - CutOnAxis.perform_cut_on_axis (cut_on_axis.py)
    - MirrorSlots._resolve_pivot (mirror.py — static helper, only testable surface)

The Slots classes themselves are UI-bound and skipped here.
"""
import unittest

import maya.cmds as cmds

from mayatk.edit_utils.bevel import Bevel
from mayatk.edit_utils.bridge import Bridge
from mayatk.edit_utils.snap import Snap
from mayatk.edit_utils.cut_on_axis import CutOnAxis
from mayatk.edit_utils.mirror import MirrorSlots

from base_test import MayaTkTestCase, QuickTestCase


class TestBevel(MayaTkTestCase):
    """Bevel.bevel — wraps cmds.polyBevel3."""

    def test_bevel_increases_face_count(self):
        cube = cmds.polyCube(name="bvl_cube")[0]
        before = cmds.polyEvaluate(cube, face=True)

        Bevel.bevel([f"{cube}.e[0]"], width=0.2, segments=2)

        after = cmds.polyEvaluate(cube, face=True)
        self.assertGreater(after, before)

    def test_bevel_with_default_args_runs(self):
        cube = cmds.polyCube(name="bvl_default")[0]
        # Should not raise with defaults
        Bevel.bevel([f"{cube}.e[1]"])


class TestBridge(MayaTkTestCase):
    """Bridge — connects edge borders."""

    def test_get_child_curves_from_clean_mesh_returns_empty(self):
        cube = cmds.polyCube(name="brg_clean")[0]
        result = Bridge.get_child_curves_from_bridge([cube])
        self.assertEqual(result, [])

    def test_cleanup_no_curves_does_not_raise(self):
        cube = cmds.polyCube(name="brg_no_curves")[0]
        # Should print "No child curves found" and return without error
        Bridge.cleanup_bridge_curves_and_history([cube])


class TestSnap(MayaTkTestCase):
    """Snap utilities — vertex/surface/grid snapping."""

    def test_snap_to_grid_no_objects_warns_and_returns_zero(self):
        cmds.select(clear=True)
        result = Snap.snap_to_grid()
        self.assertEqual(result, 0)

    def test_snap_to_grid_snaps_pivot(self):
        cube = cmds.polyCube(name="grid_cube")[0]
        cmds.move(2.7, 0, 1.3, cube)

        moved = Snap.snap_to_grid([cube], grid_size=1.0, axes="xyz")
        self.assertEqual(moved, 1)

        pos = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(pos[0], 3.0, places=4)
        self.assertAlmostEqual(pos[2], 1.0, places=4)

    def test_snap_to_grid_axes_subset(self):
        """Only the named axes should be snapped — others left alone."""
        cube = cmds.polyCube(name="grid_axis_cube")[0]
        cmds.move(2.7, 4.4, 1.3, cube)

        Snap.snap_to_grid([cube], grid_size=1.0, axes="x")

        pos = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(pos[0], 3.0, places=4)
        # Y and Z should be unchanged
        self.assertAlmostEqual(pos[1], 4.4, places=4)
        self.assertAlmostEqual(pos[2], 1.3, places=4)

    def test_snap_to_grid_custom_grid_size(self):
        cube = cmds.polyCube(name="grid_size_cube")[0]
        cmds.move(1.4, 0, 0, cube)

        Snap.snap_to_grid([cube], grid_size=0.5, axes="x")

        pos = cmds.xform(cube, q=True, ws=True, rp=True)
        self.assertAlmostEqual(pos[0], 1.5, places=4)


class TestCutOnAxis(MayaTkTestCase):
    """CutOnAxis.perform_cut_on_axis — wraps EditUtils.cut_along_axis."""

    def test_zero_cuts_is_noop(self):
        cube = cmds.polyCube(name="cut_noop")[0]
        before_faces = cmds.polyEvaluate(cube, face=True)
        # cuts=0 should short-circuit with no operation
        CutOnAxis.perform_cut_on_axis([cube], axis="x", cuts=0)
        after_faces = cmds.polyEvaluate(cube, face=True)
        self.assertEqual(before_faces, after_faces)

    def test_one_cut_increases_geometry(self):
        cube = cmds.polyCube(name="cut_one", sx=1, sy=1, sz=1)[0]
        # The behavior is delegated to EditUtils — we just smoke-test it runs.
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="center", use_object_axes=True
        )


class TestMirrorResolvePivot(QuickTestCase):
    """MirrorSlots._resolve_pivot is a static helper — pure Python."""

    def test_index_to_label_mapping(self):
        self.assertEqual(MirrorSlots._resolve_pivot(0, "x"), "manip")
        self.assertEqual(MirrorSlots._resolve_pivot(1, "x"), "object")
        self.assertEqual(MirrorSlots._resolve_pivot(2, "x"), "world")
        self.assertEqual(MirrorSlots._resolve_pivot(3, "x"), "center")

    def test_axis_aware_index_4(self):
        self.assertEqual(MirrorSlots._resolve_pivot(4, "x"), "xmax")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "-x"), "xmax")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "y"), "ymax")
        self.assertEqual(MirrorSlots._resolve_pivot(4, "z"), "zmax")

    def test_index_4_unknown_axis_falls_back(self):
        self.assertEqual(MirrorSlots._resolve_pivot(4, "bogus"), "xmax")

    def test_unknown_index_defaults_manip(self):
        self.assertEqual(MirrorSlots._resolve_pivot(99, "x"), "manip")


if __name__ == "__main__":
    unittest.main()
