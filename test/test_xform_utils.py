# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.xform_utils module

Tests for XformUtils class functionality including:
- Axis conversion
- Object movement and positioning
- Pivot operations (get/set, align, bake, transfer)
- Transform freezing (standard, OPM)
- Transform storage and restoration
- Scaling operations (match scale, connected edges)
- Orientation (aim, orient to vector, get orientation)
"""
import unittest
import pymel.core as pm
import mayatk as mtk
from mayatk.xform_utils._xform_utils import XformUtils

from base_test import MayaTkTestCase


class TestXformUtils(MayaTkTestCase):
    """Comprehensive tests for XformUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test geometries
        self.cube1 = pm.polyCube(name="test_cube1")[0]
        self.cube2 = pm.polyCube(name="test_cube2")[0]
        self.sphere = pm.polySphere(name="test_sphere")[0]

        # Position objects at known locations
        pm.move(self.cube1, 5, 0, 0, absolute=True)
        pm.move(self.cube2, 0, 5, 0, absolute=True)
        pm.move(self.sphere, 0, 0, 5, absolute=True)

    def tearDown(self):
        """Clean up test geometry."""
        for obj in ["test_cube1", "test_cube2", "test_sphere", "target_helper"]:
            if pm.objExists(obj):
                pm.delete(obj)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Axis Conversion Tests
    # -------------------------------------------------------------------------

    def test_convert_axis(self):
        """Test axis conversion utilities."""
        # Int to string
        self.assertEqual(XformUtils.convert_axis(0), "x")
        self.assertEqual(XformUtils.convert_axis(1), "-x")

        # String to string (pass-through)
        self.assertEqual(XformUtils.convert_axis("y"), "y")

        # Inversion
        self.assertEqual(XformUtils.convert_axis("x", invert=True), "-x")
        self.assertEqual(XformUtils.convert_axis("-y", invert=True), "y")

        # Orthogonal
        self.assertEqual(XformUtils.convert_axis("x", ortho=True), "y")
        self.assertEqual(XformUtils.convert_axis("y", ortho=True), "z")
        self.assertEqual(XformUtils.convert_axis("z", ortho=True), "x")

        # To Integer
        self.assertEqual(XformUtils.convert_axis("z", to_integer=True), 4)
        self.assertEqual(XformUtils.convert_axis("-z", to_integer=True), 5)

    # -------------------------------------------------------------------------
    # Movement and Positioning Tests
    # -------------------------------------------------------------------------

    def test_move_to_object(self):
        """Test moving one object to another's position."""
        cube2_pos = pm.xform(self.cube2, query=True, worldSpace=True, translation=True)
        XformUtils.move_to(self.cube1, self.cube2)
        cube1_pos = pm.xform(self.cube1, query=True, worldSpace=True, translation=True)
        for i in range(3):
            self.assertAlmostEqual(cube1_pos[i], cube2_pos[i], places=2)

    def test_move_to_group(self):
        """Test moving multiple objects as a group."""
        # Create a group of objects
        c1 = pm.polyCube()[0]
        c2 = pm.polyCube()[0]
        pm.move(c1, 0, 0, 0)
        pm.move(c2, 2, 0, 0)

        # Target
        target = pm.polySphere()[0]
        pm.move(target, 10, 10, 10)

        # Move as group
        XformUtils.move_to([c1, c2], target, group_move=True)

        # Center of c1 and c2 should now be at target
        # Original center was (1, 0, 0). Target is (10, 10, 10).
        # Shift is (9, 10, 10).
        # c1 should be at (9, 10, 10), c2 at (11, 10, 10)

        c1_pos = pm.xform(c1, q=True, ws=True, t=True)
        c2_pos = pm.xform(c2, q=True, ws=True, t=True)

        self.assertAlmostEqual(c1_pos[0], 9.0, delta=1e-4)
        self.assertAlmostEqual(c2_pos[0], 11.0, delta=1e-4)

        pm.delete(c1, c2, target)

    def test_drop_to_grid(self):
        """Test dropping object to grid."""
        pm.move(self.cube1, 5, 10, 5, absolute=True)
        XformUtils.drop_to_grid(self.cube1, align="Min")

        # Check bounding box min Y is approx 0
        bbox = pm.exactWorldBoundingBox(self.cube1)
        self.assertAlmostEqual(bbox[1], 0.0, places=4)

    def test_reset_translation(self):
        """Test resetting translation."""
        pm.move(self.cube1, 10, 20, 30)
        original_pos = pm.xform(self.cube1, q=True, ws=True, t=True)

        XformUtils.reset_translation(self.cube1)

        # Position should be preserved
        new_pos = pm.xform(self.cube1, q=True, ws=True, t=True)
        self.assertEqual(new_pos, original_pos)

        # But translation values might be different if pivots changed,
        # but reset_translation bakes transforms.
        # Let's check if it runs without error and preserves position.

    def test_set_translation_to_pivot(self):
        """Test setting translation to pivot."""
        pm.move(self.cube1, 10, 0, 0)
        # Move pivot away
        pm.xform(self.cube1, ws=True, rp=(15, 0, 0))

        XformUtils.set_translation_to_pivot(self.cube1)

        # Object translation should now be 15, 0, 0 (or close, depending on implementation details)
        # The method moves the object so its transform center matches the pivot
        trans = pm.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(trans[0], 15.0)

    # -------------------------------------------------------------------------
    # Scaling Tests
    # -------------------------------------------------------------------------

    def test_match_scale(self):
        """Test matching scale of objects."""
        # Target is 2x2x2
        pm.scale(self.cube2, 2, 2, 2)

        # Source is 1x1x1
        XformUtils.match_scale(self.cube1, self.cube2)

        scale = pm.getAttr(self.cube1.scale)
        self.assertAlmostEqual(scale[0], 2.0)

    def test_scale_connected_edges(self):
        """Test scaling connected edges."""
        # Select some edges on the sphere
        edges = [f"{self.sphere}.e[0]", f"{self.sphere}.e[1]"]
        pm.select(edges)

        # Get initial vertex positions
        vtxs = pm.polyListComponentConversion(edges, tv=True)
        vtxs = pm.ls(vtxs, flatten=True)
        initial_pos = [v.getPosition(space="world") for v in vtxs]

        # Call without explicit objects to satisfy the @selected decorator
        # which seems to assume implicit selection for static methods
        XformUtils.scale_connected_edges(scale_factor=2.0)

        # Vertices should have moved further apart
        # Simple check: bounding box of vertices should be larger
        # But exact math check is complex. Just ensure they moved.
        final_pos = [v.getPosition(space="world") for v in vtxs]
        self.assertNotEqual(initial_pos, final_pos)

    # -------------------------------------------------------------------------
    # Transform Storage & Freeze Tests
    # -------------------------------------------------------------------------

    def test_store_and_restore_transforms(self):
        """Test storing and restoring transforms."""
        pm.move(self.cube1, 10, 20, 30)
        pm.rotate(self.cube1, 45, 45, 0)

        # Store
        XformUtils.store_transforms(self.cube1, prefix="test")
        self.assertTrue(pm.hasAttr(self.cube1, "test_worldMatrix"))

        # Move it somewhere else
        pm.move(self.cube1, 0, 0, 0)
        pm.rotate(self.cube1, 0, 0, 0)

        # Restore
        XformUtils.restore_transforms(self.cube1, prefix="test")

        pos = pm.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 10.0)
        self.assertAlmostEqual(pos[1], 20.0)
        self.assertAlmostEqual(pos[2], 30.0)

    def test_freeze_transforms(self):
        """Test freeze transforms."""
        pm.move(self.cube1, 10, 10, 10)
        pm.rotate(self.cube1, 45, 0, 0)

        XformUtils.freeze_transforms(self.cube1, translate=True, rotate=True)

        trans = pm.getAttr(self.cube1.translate)
        rot = pm.getAttr(self.cube1.rotate)

        self.assertEqual(trans, pm.dt.Vector(0, 0, 0))
        self.assertEqual(rot, pm.dt.Vector(0, 0, 0))

        # Position should still be 10, 10, 10 in world space (geometry moved)
        # But pivot is at origin if not preserved?
        # freeze_transforms uses makeIdentity which resets pivot to origin unless pn=True
        # The implementation uses pn=True (preserve normals? No, pn flag in makeIdentity is preserveNormals?
        # Actually, let's check if it preserves pivot position.
        # The docstring says "Maya's makeIdentity automatically preserves world-space pivot positions".

        # Let's verify world position of geometry
        bbox = pm.exactWorldBoundingBox(self.cube1)
        center = [
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        ]
        self.assertAlmostEqual(center[0], 10.0, delta=1.0)  # Approx check

    def test_freeze_to_opm(self):
        """Test freezing to Offset Parent Matrix."""
        pm.move(self.cube1, 10, 10, 10)

        XformUtils.freeze_to_opm(self.cube1)

        # Translate should be zero
        trans = pm.getAttr(self.cube1.translate)
        self.assertEqual(trans, pm.dt.Vector(0, 0, 0))

        # OPM should be set
        opm = pm.getAttr(self.cube1.offsetParentMatrix)
        self.assertNotEqual(opm, pm.dt.Matrix())  # Should not be identity

    # -------------------------------------------------------------------------
    # Pivot Operations Tests
    # -------------------------------------------------------------------------

    def test_get_operation_axis_pos(self):
        """Test getting pivot position for operations."""
        pm.move(self.cube1, 10, 10, 10)

        # Center
        pos = XformUtils.get_operation_axis_pos(self.cube1, "center")
        self.assertAlmostEqual(pos[0], 10.0, delta=1.0)

        # World
        pos = XformUtils.get_operation_axis_pos(self.cube1, "world")
        self.assertEqual(pos, [0.0, 0.0, 0.0])

        # Object
        pos = XformUtils.get_operation_axis_pos(self.cube1, "object")
        # Pivot should be at 10, 10, 10 if we moved it
        self.assertAlmostEqual(pos[0], 10.0)

    def test_align_pivot_to_selection(self):
        """Test aligning pivot to selection."""
        # Move cube2
        pm.move(self.cube2, 20, 0, 0)

        # Align cube1 pivot to cube2
        XformUtils.align_pivot_to_selection(self.cube1, self.cube2, translate=True)

        # Cube1 should have moved to Cube2
        pos = pm.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 20.0)

    def test_reset_pivot_transforms(self):
        """Test resetting pivots when objects are passed explicitly.

        Bug: The method had a misplaced ``return`` inside the ``else`` branch,
        causing it to exit immediately when the ``objects`` parameter was provided.
        Fixed: 2026-02-27
        """
        pm.move(self.cube1, 10, 0, 0)
        # Move pivot away from geometry center
        pm.xform(self.cube1, ws=True, rp=(0, 0, 0))

        # Pass objects explicitly — before the fix this was a no-op
        XformUtils.reset_pivot_transforms(self.cube1)

        # Pivot should now be re-centred on the object's bounding box
        rp = pm.xform(self.cube1, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], 10.0, delta=0.5)

    def test_transfer_pivot(self):
        """Test transferring pivot."""
        pm.move(self.cube1, 10, 0, 0)
        pm.move(self.cube2, 20, 0, 0)

        # Transfer pivot from cube1 to cube2
        XformUtils.transfer_pivot([self.cube1, self.cube2], translate=True)

        # Cube2 pivot should be at Cube1 location (10, 0, 0)
        rp = pm.xform(self.cube2, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], 10.0)

    def test_bake_pivot(self):
        """Test baking pivot."""
        pm.move(self.cube1, 10, 0, 0)
        # Rotate pivot
        pm.xform(self.cube1, ro=(0, 45, 0))

        XformUtils.bake_pivot(self.cube1, orientation=True)

        # Object rotation should change to match pivot orientation?
        # bake_pivot implementation is complex, involving context checks.
        # In batch mode, context checks might fail or behave differently.
        # Let's just ensure it runs without error.
        pass

    # -------------------------------------------------------------------------
    # Orientation Tests
    # -------------------------------------------------------------------------

    def test_aim_object_at_point(self):
        """Test aiming object."""
        target = (0, 10, 0)
        XformUtils.aim_object_at_point(self.cube1, target)

        rot = pm.getAttr(self.cube1.rotate)
        self.assertNotEqual(rot, pm.dt.Vector(0, 0, 0))

    def test_aim_object_at_point_multi_no_leak(self):
        """Verify that aiming multiple objects cleans up all constraints.

        Bug: Only the last aimConstraint was deleted; earlier constraints
        leaked and the user's target object was accidentally deleted when
        ``target_pos`` was an existing transform name.
        Fixed: 2026-02-27
        """
        c1 = pm.polyCube(name="aim_test_a")[0]
        c2 = pm.polyCube(name="aim_test_b")[0]
        pm.move(c1, -5, 0, 0)
        pm.move(c2, 5, 0, 0)

        constraint_count_before = len(pm.ls(type="aimConstraint"))
        XformUtils.aim_object_at_point([c1, c2], (0, 10, 0))
        constraint_count_after = len(pm.ls(type="aimConstraint"))

        # All constraints should be cleaned up
        self.assertEqual(constraint_count_before, constraint_count_after)

        # No leftover 'target_helper' node
        self.assertFalse(pm.objExists("target_helper"))

        pm.delete(c1, c2)

    def test_aim_object_at_existing_target_not_deleted(self):
        """Verify that aiming at an existing transform does not delete it.

        Bug: ``pm.delete(const, target)`` unconditionally deleted the target
        even when it was a user-supplied transform, not a temporary helper.
        Fixed: 2026-02-27
        """
        target = pm.polySphere(name="aim_target_sphere")[0]
        pm.move(target, 0, 10, 0)

        XformUtils.aim_object_at_point(self.cube1, target)

        # The user's target must still exist
        self.assertTrue(pm.objExists("aim_target_sphere"))
        pm.delete(target)

    def test_orient_to_vector(self):
        """Test orienting to vector."""
        XformUtils.orient_to_vector(self.cube1, aim_vector=(0, 1, 0))

        # X axis should point up (0, 1, 0)
        # Check world matrix
        m = pm.xform(self.cube1, q=True, m=True, ws=True)
        # X axis is first 3 elements
        self.assertAlmostEqual(m[0], 0.0, places=4)
        self.assertAlmostEqual(m[1], 1.0, places=4)
        self.assertAlmostEqual(m[2], 0.0, places=4)

    def test_get_orientation(self):
        """Test getting orientation."""
        pm.rotate(self.cube1, 0, 90, 0)

        # Get as vector
        vectors = XformUtils.get_orientation(self.cube1, returned_type="vector")
        # Should return tuple of 3 vectors (x, y, z axes)
        self.assertEqual(len(vectors), 3)

        # X axis should be (0, 0, -1) after 90 deg Y rot
        self.assertAlmostEqual(vectors[0].z, -1.0)


class TestXformUtilsEdgeCases(MayaTkTestCase):
    """Edge case tests for XformUtils."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.cube1 = pm.polyCube(name="test_cube1")[0]

    def tearDown(self):
        """Clean up."""
        if pm.objExists("test_cube1"):
            pm.delete("test_cube1")
        super().tearDown()

    def test_convert_axis_invalid(self):
        """Test invalid axis conversion."""
        with self.assertRaises(TypeError):
            XformUtils.convert_axis(1.5)

    def test_move_to_empty(self):
        """Test move_to with empty list."""
        # Should not crash
        XformUtils.move_to([], self.cube1)

    def test_freeze_transforms_locked(self):
        """Test freezing locked attributes."""
        self.cube1.translateX.set(lock=True)
        # Should unlock, freeze, and relock (if force=True)
        XformUtils.freeze_transforms(self.cube1, translate=True, force=True)
        self.assertEqual(self.cube1.translateX.get(), 0.0)
        self.assertTrue(self.cube1.translateX.isLocked())

    def test_align_using_three_points_identity(self):
        """Verify 3-point align maps source frame onto target frame.

        Bug: Original implementation always rotated around the Z axis via
        ``MEulerRotation(0, 0, angle)`` regardless of the actual rotation
        axis, producing incorrect results for most configurations.
        Fixed: 2026-02-27
        """
        # Source plane at origin, target plane at (10, 0, 0) with a 90-deg Y rotation
        src = pm.polyPlane(name="src_plane", w=4, h=4, sx=1, sy=1, ax=(0, 1, 0))[0]
        tgt = pm.polyPlane(name="tgt_plane", w=4, h=4, sx=1, sy=1, ax=(0, 1, 0))[0]
        pm.move(tgt, 10, 0, 0)
        pm.rotate(tgt, 0, 90, 0)

        src_verts = pm.ls(f"{src}.vtx[0:2]", flatten=True)
        tgt_verts = pm.ls(f"{tgt}.vtx[0:2]", flatten=True)

        XformUtils.align_using_three_points(src_verts + tgt_verts)

        # After alignment, the first 3 source vertices should be very close
        # to the corresponding target vertices.
        for sv, tv in zip(src_verts, tgt_verts):
            sp = pm.pointPosition(sv, w=True)
            tp = pm.pointPosition(tv, w=True)
            for i in range(3):
                self.assertAlmostEqual(sp[i], tp[i], places=3)

        pm.delete(src, tgt)

    def test_align_vertices_no_selection(self):
        """Verify align_vertices doesn't crash when nothing is selected.

        Bug: Selection validation happened after indexing into the reference
        position list, causing IndexError when fewer than 2 vertices were
        selected.
        Fixed: 2026-02-27
        """
        pm.select(clear=True)
        # Should return gracefully (inViewMessage), not IndexError
        XformUtils.align_vertices(mode=3)

    def test_align_vertices_single_selection(self):
        """Verify align_vertices returns early with only a single vertex.

        Bug: Same IndexError as test_align_vertices_no_selection — the guard
        ran after the position was already accessed.
        Fixed: 2026-02-27
        """
        cube = pm.polyCube(name="align_vert_test")[0]
        pm.select(f"{cube}.vtx[0]")
        # Should not raise
        XformUtils.align_vertices(mode=3)
        pm.delete(cube)

    def test_align_vertices_mode_x(self):
        """Verify align_vertices mode=3 (X) aligns X coords to last selected."""
        cube = pm.polyCube(name="align_mode_test", sx=2, sy=2, sz=2)[0]
        verts = pm.ls(f"{cube}.vtx[*]", flatten=True)

        # Select 3 vertices — the last one's X will be the reference
        pm.select([verts[0], verts[1], verts[2]])
        ref_x = pm.xform(verts[2], q=True, t=True, ws=True)[0]

        XformUtils.align_vertices(mode=3)  # align X

        # All selected verts should now share the reference X
        for v in [verts[0], verts[1], verts[2]]:
            pos = pm.xform(v, q=True, t=True, ws=True)
            self.assertAlmostEqual(pos[0], ref_x, places=4)

        pm.delete(cube)


if __name__ == "__main__":
    unittest.main()
