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

    def test_snap_to_surface_with_transform_input(self):
        """Regression: snap_to_surface's transform/mesh handling on string input.

        Bug fixed 2026-05-07: ``transform.type() == "mesh"`` (PyMEL idiom)
        crashed on cmds-style string nodes. Replaced with ``cmds.objectType``.
        """
        target = cmds.polyPlane(name="snap_target", w=4, h=4)[0]
        source = cmds.polyCube(name="snap_source")[0]
        cmds.move(0, 5, 0, source)  # source above target

        # Move some vertices below the plane to force snap movement.
        cmds.move(0, -3, 0, f"{source}.vtx[0]")

        # Should not raise — exercises the .objectType("mesh") branch transitively.
        Snap.snap_to_surface(source_meshes=source, target_mesh=target, offset=0.0)

        # Source still exists post-snap.
        self.assertTrue(cmds.objExists(source))

    def test_snap_to_surface_with_shape_input(self):
        """Regression: snap_to_surface explicitly handles mesh-shape inputs.

        Passing the shape directly used to crash on ``transform.type()``.
        Now the code calls ``cmds.objectType(transform)`` and walks up to the
        parent transform.
        """
        target = cmds.polyPlane(name="snap_target_2", w=4, h=4)[0]
        source_xform = cmds.polyCube(name="snap_src_2")[0]
        source_shape = cmds.listRelatives(source_xform, shapes=True)[0]
        cmds.move(0, -3, 0, f"{source_xform}.vtx[0]")

        # Pass the shape, not the transform — exercises the .objectType branch.
        Snap.snap_to_surface(source_meshes=source_shape, target_mesh=target, offset=0.0)

        self.assertTrue(cmds.objExists(source_xform))


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
        before = cmds.polyEvaluate(cube, face=True)
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="center", use_object_axes=True
        )
        after = cmds.polyEvaluate(cube, face=True)
        # A single cut through the middle of the cube splits the +X and -X
        # faces in half each: 6 → 8 faces.
        self.assertGreater(after, before)

    def test_manip_pivot_does_not_crash(self):
        """Regression: tool default pivot was 'manip' but PyMel-style
        node.getMatrix() crashed immediately on string nodes.
        """
        cube = cmds.polyCube(name="cut_manip")[0]
        cmds.select(cube)
        # Should complete without raising.
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="manip", use_object_axes=True
        )
        self.assertTrue(cmds.objExists(cube))

    def test_manip_pivot_falls_back_to_rotate_pivot_on_moved_cube(self):
        """Regression: cmds.manipPivot returns (0,0,0) when no transform tool
        is active, so a moved primitive's manip-pivot cut was happening at
        world origin instead of at the object's pivot. We now fall back to
        the object's rotate pivot when manipPivot reports the default origin.
        """
        cube = cmds.polyCube(name="cut_manip_moved", w=2, h=1, d=1)[0]
        cmds.move(5, 0, 0, cube)  # Cube center at world (5, 0, 0)
        cmds.select(cube)

        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="manip",
            delete=True, use_object_axes=True,
        )
        # The cut should be at the cube's center (world X=5), so deleting the
        # +X half leaves a cube spanning [4, 5] in X — not a slice through
        # world X=0 that would either be a no-op or destroy the whole cube.
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertAlmostEqual(bbox[3], 5.0, places=3,
            msg=f"Expected xmax≈5 (cube center), got {bbox[3]}")
        self.assertAlmostEqual(bbox[0], 4.0, places=3,
            msg=f"Expected xmin≈4, got {bbox[0]}")

    def test_all_six_axes_work(self):
        for axis in ("x", "-x", "y", "-y", "z", "-z"):
            with self.subTest(axis=axis):
                cube = cmds.polyCube(name=f"cut_{axis.replace('-', 'n')}")[0]
                before = cmds.polyEvaluate(cube, face=True)
                CutOnAxis.perform_cut_on_axis(
                    [cube], axis=axis, cuts=1, pivot="center", use_object_axes=True
                )
                after = cmds.polyEvaluate(cube, face=True)
                self.assertGreater(after, before, f"Cut along {axis} failed")

    def test_delete_removes_half(self):
        """A single center cut + delete on a unit cube should remove one half."""
        cube = cmds.polyCube(name="cut_del", w=2, h=2, d=2)[0]
        # 6 faces initially. Cut at center along X with delete=True:
        # the +X face is removed and the cap from the cut closes the body, so
        # final face count should be < initial.
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="center", delete=True, use_object_axes=True
        )
        bbox = cmds.exactWorldBoundingBox(cube)
        # +X half deleted, so the cube extent should be only on the -X side.
        self.assertLess(bbox[3], 0.01, f"Expected xmax≈0 after deleting +X, got {bbox[3]}")
        self.assertAlmostEqual(bbox[0], -1.0, places=3)

    def test_delete_negative_axis_removes_other_half(self):
        cube = cmds.polyCube(name="cut_del_neg", w=2, h=2, d=2)[0]
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="-x", cuts=1, pivot="center", delete=True, use_object_axes=True
        )
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertGreater(bbox[0], -0.01, f"Expected xmin≈0 after deleting -X, got {bbox[0]}")
        self.assertAlmostEqual(bbox[3], 1.0, places=3)

    def test_multi_cuts_evenly_spaced(self):
        cube = cmds.polyCube(name="cut_multi", w=4, h=1, d=1)[0]
        before = cmds.polyEvaluate(cube, face=True)
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=3, pivot="center", use_object_axes=True
        )
        after = cmds.polyEvaluate(cube, face=True)
        # 3 cuts split each of the +X and -X faces into 4 strips: net +6 faces.
        self.assertGreater(after, before + 4, "3 cuts should add several faces")

    def test_offset_shifts_cut(self):
        """Offset along positive axis should push the cut toward +X."""
        cube_a = cmds.polyCube(name="cut_off_a", w=2, h=1, d=1)[0]
        cube_b = cmds.polyCube(name="cut_off_b", w=2, h=1, d=1)[0]
        # Cut+delete with no offset
        CutOnAxis.perform_cut_on_axis(
            [cube_a], axis="x", cuts=1, pivot="center",
            cut_offset=0.0, delete=True, use_object_axes=True,
        )
        # Cut+delete with positive offset
        CutOnAxis.perform_cut_on_axis(
            [cube_b], axis="x", cuts=1, pivot="center",
            cut_offset=0.3, delete=True, use_object_axes=True,
        )
        bbox_a = cmds.exactWorldBoundingBox(cube_a)
        bbox_b = cmds.exactWorldBoundingBox(cube_b)
        # b's +X side should be offset further from -X (i.e., wider remaining half).
        self.assertGreater(bbox_b[3], bbox_a[3])

    def test_rotated_cube_object_axis_cut(self):
        """Cut along rotated object's local X axis — should bisect along its
        own X (which points to world -Z), not world X.
        """
        cube = cmds.polyCube(name="cut_rotated", w=2, h=1, d=1)[0]
        cmds.rotate(0, 90, 0, cube)  # Local +X now points along world -Z

        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="object",
            delete=True, use_object_axes=True,
        )
        # After deleting the +local-X half (which is world -Z), the remaining
        # half should sit on the +world-Z side (zmax > 0, zmin ≈ 0).
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertGreater(bbox[5], 0.5,
            f"Expected +world-Z half to remain, got zmax={bbox[5]}")
        self.assertGreater(bbox[2], -0.01,
            f"Expected zmin≈0, got {bbox[2]}")

    def test_rotated_cube_world_axis_cut(self):
        """With use_object_axes=False, cut should follow world axis even on
        a rotated object.
        """
        cube = cmds.polyCube(name="cut_rotated_world", w=2, h=1, d=1)[0]
        cmds.rotate(0, 90, 0, cube)

        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="world",
            delete=True, use_object_axes=False,
        )
        # World X cut at world origin removes everything with X > 0. After 90°
        # Y rotation, the cube spans X in [-0.5, 0.5] (since local Z=±0.5
        # rotates to world X=∓0.5). Deleting +world-X removes the world-+X
        # half, leaving xmax ≈ 0.
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertLess(bbox[3], 0.01,
            f"Expected xmax≈0 after world-X delete, got {bbox[3]}")

    def test_world_pivot(self):
        """Cube offset from origin, cut at world origin should slice off only
        the half that crosses world X=0.
        """
        cube = cmds.polyCube(name="cut_world", w=2, h=1, d=1)[0]
        cmds.move(0.3, 0, 0, cube)  # Cube spans X in [-0.7, 1.3]
        CutOnAxis.perform_cut_on_axis(
            [cube], axis="x", cuts=1, pivot="world",
            delete=True, use_object_axes=False,
        )
        # +X half (relative to world origin) deleted: keep [-0.7, 0]
        bbox = cmds.exactWorldBoundingBox(cube)
        self.assertLess(bbox[3], 0.01)
        self.assertAlmostEqual(bbox[0], -0.7, places=3)


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
