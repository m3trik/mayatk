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
import mayatk as mtk
from mayatk.xform_utils._xform_utils import XformUtils, _XformUtilsInternal

from base_test import MayaTkTestCase, skipIfBatch
import maya.cmds as cmds


class TestXformUtils(MayaTkTestCase):
    """Comprehensive tests for XformUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test geometries
        self.cube1 = cmds.polyCube(name="test_cube1")[0]
        self.cube2 = cmds.polyCube(name="test_cube2")[0]
        self.sphere = cmds.polySphere(name="test_sphere")[0]

        # Position objects at known locations
        cmds.move(5, 0, 0, self.cube1, absolute=True)
        cmds.move(0, 5, 0, self.cube2, absolute=True)
        cmds.move(0, 0, 5, self.sphere, absolute=True)

    def tearDown(self):
        """Clean up test geometry."""
        for obj in ["test_cube1", "test_cube2", "test_sphere", "target_helper"]:
            if cmds.objExists(obj):
                cmds.delete(obj)
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
        cube2_pos = cmds.xform(self.cube2, query=True, worldSpace=True, translation=True)
        XformUtils.move_to(self.cube1, self.cube2)
        cube1_pos = cmds.xform(self.cube1, query=True, worldSpace=True, translation=True)
        for i in range(3):
            self.assertAlmostEqual(cube1_pos[i], cube2_pos[i], places=2)

    def test_move_to_pivot_center(self):
        """move_to defaults to the target bounding-box center."""
        XformUtils.move_to(self.cube1, self.cube2, pivot="center")
        p = cmds.xform(self.cube1, q=True, ws=True, t=True)
        center = XformUtils.get_bounding_box(self.cube2, "center")
        for i in range(3):
            self.assertAlmostEqual(p[i], center[i], places=2)

    def test_move_to_pivot_bbox_extent(self):
        """move_to honors a bounding-box extent pivot on the target."""
        XformUtils.move_to(self.cube1, self.cube2, pivot="ymax")
        p = cmds.xform(self.cube1, q=True, ws=True, t=True)
        center = XformUtils.get_bounding_box(self.cube2, "center")
        ymax = float(XformUtils.get_bounding_box(self.cube2, "ymax"))
        self.assertAlmostEqual(p[0], center[0], places=2)  # x at center
        self.assertAlmostEqual(p[1], ymax, places=2)  # y at the +Y extent
        self.assertAlmostEqual(p[2], center[2], places=2)  # z at center

    def test_move_to_pivot_object(self):
        """move_to with pivot='object' aligns to the target's pivot."""
        XformUtils.move_to(self.cube1, self.cube2, pivot="object")
        p = cmds.xform(self.cube1, q=True, ws=True, t=True)
        obj_pivot = cmds.xform(self.cube2, q=True, ws=True, rp=True)
        for i in range(3):
            self.assertAlmostEqual(p[i], obj_pivot[i], places=2)

    def test_move_to_empty_target(self):
        """move_to is a no-op (no raise) when the target list is empty."""
        before = cmds.xform(self.cube1, q=True, ws=True, t=True)
        XformUtils.move_to(self.cube1, [])
        after = cmds.xform(self.cube1, q=True, ws=True, t=True)
        for i in range(3):
            self.assertAlmostEqual(before[i], after[i], places=4)

    def test_move_to_group(self):
        """Test moving multiple objects as a group."""
        # Create a group of objects
        c1 = cmds.polyCube()[0]
        c2 = cmds.polyCube()[0]
        cmds.move(0, 0, 0, c1)
        cmds.move(2, 0, 0, c2)

        # Target
        target = cmds.polySphere()[0]
        cmds.move(10, 10, 10, target)

        # Move as group
        XformUtils.move_to([c1, c2], target, group_move=True)

        # Center of c1 and c2 should now be at target
        # Original center was (1, 0, 0). Target is (10, 10, 10).
        # Shift is (9, 10, 10).
        # c1 should be at (9, 10, 10), c2 at (11, 10, 10)

        c1_pos = cmds.xform(c1, q=True, ws=True, t=True)
        c2_pos = cmds.xform(c2, q=True, ws=True, t=True)

        self.assertAlmostEqual(c1_pos[0], 9.0, delta=1e-4)
        self.assertAlmostEqual(c2_pos[0], 11.0, delta=1e-4)

        cmds.delete(c1, c2, target)

    def test_drop_to_grid(self):
        """Test dropping object to grid."""
        cmds.move(5, 10, 5, self.cube1, absolute=True)
        XformUtils.drop_to_grid(self.cube1, align="Min")

        # Check bounding box min Y is approx 0
        bbox = cmds.exactWorldBoundingBox(self.cube1)
        self.assertAlmostEqual(bbox[1], 0.0, places=4)

    def test_reset_translation(self):
        """Test resetting translation."""
        cmds.move(10, 20, 30, self.cube1)
        original_pos = cmds.xform(self.cube1, q=True, ws=True, t=True)

        XformUtils.reset_translation(self.cube1)

        # Position should be preserved
        new_pos = cmds.xform(self.cube1, q=True, ws=True, t=True)
        self.assertEqual(new_pos, original_pos)

        # But translation values might be different if pivots changed,
        # but reset_translation bakes transforms.
        # Let's check if it runs without error and preserves position.

    def test_set_translation_to_pivot(self):
        """Test setting translation to pivot."""
        cmds.move(10, 0, 0, self.cube1)
        # Move pivot away
        cmds.xform(self.cube1, ws=True, rp=(15, 0, 0))

        XformUtils.set_translation_to_pivot(self.cube1)

        # Object translation should now be 15, 0, 0 (or close, depending on implementation details)
        # The method moves the object so its transform center matches the pivot
        trans = cmds.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(trans[0], 15.0)

    # -------------------------------------------------------------------------
    # Scaling Tests
    # -------------------------------------------------------------------------

    def test_match_scale(self):
        """Test matching scale of objects."""
        # Target is 2x2x2
        cmds.scale(2, 2, 2, self.cube2)

        # Source is 1x1x1
        XformUtils.match_scale(self.cube1, self.cube2)

        scale = cmds.getAttr(f"{self.cube1}.scale")[0]
        self.assertAlmostEqual(scale[0], 2.0)

    def test_scale_connected_edges(self):
        """Test scaling connected edges."""
        # Select some edges on the sphere
        edges = [f"{self.sphere}.e[0]", f"{self.sphere}.e[1]"]
        cmds.select(edges)

        # Get initial vertex positions
        vtxs = cmds.polyListComponentConversion(edges, tv=True)
        vtxs = cmds.ls(vtxs, flatten=True)
        initial_pos = [cmds.pointPosition(v, world=True) for v in vtxs]

        # Call without explicit objects to satisfy the @selected decorator
        # which seems to assume implicit selection for static methods
        XformUtils.scale_connected_edges(scale_factor=2.0)

        # Vertices should have moved further apart
        # Simple check: bounding box of vertices should be larger
        # But exact math check is complex. Just ensure they moved.
        final_pos = [cmds.pointPosition(v, world=True) for v in vtxs]
        self.assertNotEqual(initial_pos, final_pos)

    # -------------------------------------------------------------------------
    # Transform Storage & Freeze Tests
    # -------------------------------------------------------------------------

    def test_store_and_restore_transforms(self):
        """Round-trip: store -> move to 0 -> restore composes back to original.

        Under cumulative semantics, after store_transforms captures the
        bake history and the user moves to 0, restore_transforms composes
        stored + 0 = stored, so the object lands back at its stored pose.
        """
        cmds.move(10, 20, 30, self.cube1)
        cmds.rotate(45, 45, 0, self.cube1)

        # Store
        XformUtils.store_transforms(self.cube1, prefix="test")
        self.assertTrue(
            cmds.attributeQuery("test_T_bake", node=str(self.cube1), exists=True)
        )

        # Move it somewhere else
        cmds.move(0, 0, 0, self.cube1)
        cmds.rotate(0, 0, 0, self.cube1)

        # Restore
        XformUtils.restore_transforms(self.cube1, prefix="test")

        pos = cmds.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 10.0)
        self.assertAlmostEqual(pos[1], 20.0)
        self.assertAlmostEqual(pos[2], 30.0)

    def test_store_transforms_attrs_hidden_from_channel_box(self):
        """Stored bake attrs must be non-keyable and not in the channel box."""
        XformUtils.store_transforms(self.cube1, prefix="test")
        for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
            plug = f"{self.cube1}.{attr}"
            self.assertFalse(
                cmds.getAttr(plug, keyable=True),
                f"{attr} should not be keyable",
            )
            self.assertFalse(
                cmds.getAttr(plug, channelBox=True),
                f"{attr} should not be in the channel box",
            )

    def test_restore_transforms_handles_locked_translate(self):
        """Locked translate channels must not silently swallow the restore.

        Maya's cmds.xform skips locked channels silently. restore_transforms
        must temporarily unlock TRS so the full world matrix gets written.
        """
        cmds.move(10, 5, 0, self.cube1)
        XformUtils.store_transforms(self.cube1, prefix="test")
        cmds.move(0, 0, 0, self.cube1, absolute=True)
        for axis in "XYZ":
            cmds.setAttr(f"{self.cube1}.translate{axis}", lock=True)

        XformUtils.restore_transforms(self.cube1, prefix="test")

        pos = cmds.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 10.0, delta=1e-3)
        self.assertAlmostEqual(pos[1], 5.0, delta=1e-3)
        self.assertAlmostEqual(pos[2], 0.0, delta=1e-3)
        self.assertTrue(
            cmds.getAttr(f"{self.cube1}.translateX", lock=True),
            "Lock state should be preserved through restore",
        )

    def test_restore_transforms_preserves_nurbs_curve_world_position(self):
        """Verify the vectorized NURBS curve path through a freeze-restore cycle.

        Exercises the shape-point snapshot/write helpers' MFnNurbsCurve branch via the canonical
        store -> freeze -> restore workflow that this function is designed for.
        """
        curve = cmds.circle(
            name="testCircle", normal=(0, 1, 0), constructionHistory=False
        )[0]
        try:
            cmds.move(10, 0, 5, curve)
            shape = cmds.listRelatives(curve, shapes=True, fullPath=True)[0]
            cv_world_before = cmds.xform(
                f"{shape}.cv[0]", q=True, ws=True, t=True
            )

            XformUtils.store_transforms(curve, prefix="test")
            XformUtils.freeze_transforms(curve)

            XformUtils.restore_transforms(curve, prefix="test")

            pos = cmds.xform(curve, q=True, ws=True, t=True)
            self.assertAlmostEqual(pos[0], 10.0, delta=1e-3)
            self.assertAlmostEqual(pos[2], 5.0, delta=1e-3)
            cv_world_after = cmds.xform(
                f"{shape}.cv[0]", q=True, ws=True, t=True
            )
            for b, a in zip(cv_world_before, cv_world_after):
                self.assertAlmostEqual(b, a, delta=1e-3)
        finally:
            if cmds.objExists(curve):
                cmds.delete(curve)

    def test_restore_transforms_deletes_attrs_by_default(self):
        """Default delete_attrs=True keeps the scene clean after restoration."""
        cmds.move(10, 5, 0, self.cube1)
        XformUtils.store_transforms(self.cube1, prefix="test")
        cmds.move(0, 0, 0, self.cube1)

        XformUtils.restore_transforms(self.cube1, prefix="test")

        for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
            self.assertFalse(
                cmds.attributeQuery(attr, node=str(self.cube1), exists=True),
                f"{attr} should be deleted after default restore",
            )

    def test_restore_transforms_keeps_attrs_when_delete_attrs_false(self):
        """Opt-out: delete_attrs=False preserves the stored attrs for re-restoration."""
        cmds.move(10, 5, 0, self.cube1)
        XformUtils.store_transforms(self.cube1, prefix="test")
        cmds.move(0, 0, 0, self.cube1)

        XformUtils.restore_transforms(self.cube1, prefix="test", delete_attrs=False)

        for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
            self.assertTrue(
                cmds.attributeQuery(attr, node=str(self.cube1), exists=True),
                f"{attr} should be preserved with delete_attrs=False",
            )

    def test_clear_stored_transforms_removes_attrs(self):
        """Explicit cleanup without restoration."""
        XformUtils.store_transforms(self.cube1, prefix="test")
        for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
            self.assertTrue(
                cmds.attributeQuery(attr, node=str(self.cube1), exists=True)
            )

        cleared = XformUtils.clear_stored_transforms(self.cube1, prefix="test")

        # clear_stored_transforms reports cleared objects as full DAG paths
        # (unambiguous for the attr ops it performs); compare by leaf name.
        self.assertIn(str(self.cube1), [c.split("|")[-1] for c in cleared])
        for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
            self.assertFalse(
                cmds.attributeQuery(attr, node=str(self.cube1), exists=True),
                f"{attr} should be gone after clear_stored_transforms",
            )

    def test_clear_stored_transforms_safe_on_objects_without_attrs(self):
        """Calling clear on an object that has no stored attrs is a silent no-op."""
        cleared = XformUtils.clear_stored_transforms(self.cube1, prefix="never_stored")
        self.assertEqual(cleared, [])

    def test_store_transforms_traverse_writes_to_descendants(self):
        """traverse=True must write bake attrs on every descendant transform.

        Without this, a freeze_children=True cascade leaves child LOC/GEO
        with no bake attrs and restore_transforms warns + skips.
        """
        # Build GRP > LOC > GEO chain.
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        geo = cmds.polyCube(name="rig_GEO")[0]
        cmds.parent(loc, grp)
        cmds.parent(geo, loc)
        cmds.move(7, 0, 0, grp, absolute=True)
        cmds.move(0, 3, 0, loc, relative=True)
        cmds.move(0, 0, 2, geo, relative=True)
        try:
            XformUtils.store_transforms(grp, prefix="test", traverse=True)

            for node in (grp, loc, geo):
                for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
                    self.assertTrue(
                        cmds.attributeQuery(attr, node=node, exists=True),
                        f"{node} should have {attr} after traverse=True",
                    )

            # The T_bake on the LOC records its local translate channel,
            # not the world-space accumulation.
            stored_loc_t = cmds.getAttr(f"{loc}.test_T_bake")[0]
            self.assertAlmostEqual(stored_loc_t[0], 0.0, delta=1e-4)
            self.assertAlmostEqual(stored_loc_t[1], 3.0, delta=1e-4)
        finally:
            for n in (grp, loc, geo):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_store_transforms_traverse_false_skips_descendants(self):
        """traverse=False (default) must NOT touch descendants — guards the contract."""
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        cmds.parent(loc, grp)
        try:
            XformUtils.store_transforms(grp, prefix="test")  # default traverse=False
            self.assertTrue(
                cmds.attributeQuery("test_T_bake", node=grp, exists=True),
            )
            self.assertFalse(
                cmds.attributeQuery("test_T_bake", node=loc, exists=True),
                "Descendants must be untouched when traverse=False",
            )
        finally:
            for n in (grp, loc):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_store_then_freeze_then_restore_full_chain(self):
        """End-to-end: store(traverse) → freeze(children) → restore each node.

        Under the cumulative per-channel contract, restoration operates on
        a node's LOCAL channels.  Restoring a child without its ancestors
        only recovers the child's local TRS, so the chain has to be
        restored top-down for the original world positions to come back.
        (Transform channels only: GEOMETRY compensation is snapshot-based
        per call, so hierarchies should be restored in ONE call — a list
        or ``traverse=True`` — for vertices to land back exactly; see
        ``test_restore_transforms_traverse_rotated_hierarchy_geometry``.)

        Reproduces the original user-reported regression: with
        traverse=True at store time every node has its bake attrs and
        ``restore_transforms`` never warns about missing data on a child.
        """
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        geo = cmds.polyCube(name="rig_GEO")[0]
        cmds.parent(loc, grp)
        cmds.parent(geo, loc)
        cmds.move(4, 0, 0, grp, absolute=True)
        cmds.move(0, 2, 0, loc, relative=True)
        cmds.move(0, 0, 1, geo, relative=True)

        loc_world_before = cmds.xform(loc, q=True, ws=True, t=True)
        geo_world_before = cmds.xform(geo, q=True, ws=True, t=True)

        try:
            XformUtils.store_transforms(grp, prefix="test", traverse=True)
            XformUtils.freeze_transforms(grp, freeze_children=True)

            # Every node must still have its bake attrs so restore works.
            for node in (grp, loc, geo):
                self.assertTrue(
                    cmds.attributeQuery("test_T_bake", node=node, exists=True),
                )

            # Restore top-down — parent first so children inherit the
            # restored ancestor world space.
            XformUtils.restore_transforms(grp, prefix="test")
            XformUtils.restore_transforms(loc, prefix="test")
            XformUtils.restore_transforms(geo, prefix="test")

            loc_world_after = cmds.xform(loc, q=True, ws=True, t=True)
            geo_world_after = cmds.xform(geo, q=True, ws=True, t=True)
            for a, b in zip(loc_world_before, loc_world_after):
                self.assertAlmostEqual(a, b, delta=1e-3)
            for a, b in zip(geo_world_before, geo_world_after):
                self.assertAlmostEqual(a, b, delta=1e-3)
        finally:
            for n in (grp, loc, geo):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_store_transforms_traverse_no_duplicate_on_already_listed_descendant(self):
        """Passing both parent and child should not error or double-process."""
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        cmds.parent(loc, grp)
        cmds.move(1, 2, 3, grp, absolute=True)
        try:
            # Both passed explicitly + traverse=True; should be a no-op merge.
            XformUtils.store_transforms([grp, loc], prefix="test", traverse=True)
            self.assertTrue(
                cmds.attributeQuery("test_T_bake", node=grp, exists=True),
            )
            self.assertTrue(
                cmds.attributeQuery("test_T_bake", node=loc, exists=True),
            )
        finally:
            for n in (grp, loc):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_restore_transforms_preserves_world_pivot(self):
        """Un-freeze must keep the object's pivot at its world position.

        A freeze keeps the world pivot in place (via the pivot-translate
        compensation makeIdentity writes). Restore must not discard it —
        the pivot has to land back at the same world position after the
        channels are recovered.
        """
        cmds.rotate(30, 45, 0, self.cube1)
        cmds.xform(self.cube1, ws=True, rotatePivot=(6, 1, 2))
        cmds.xform(self.cube1, ws=True, scalePivot=(6, 1, 2))

        XformUtils.store_transforms(self.cube1, prefix="test")
        XformUtils.freeze_transforms(self.cube1, translate=True, rotate=True, scale=True)

        rp_frozen = cmds.xform(self.cube1, q=True, ws=True, rotatePivot=True)
        vert_before = cmds.pointPosition(f"{self.cube1}.vtx[0]", world=True)

        XformUtils.restore_transforms(self.cube1, prefix="test")

        rp_after = cmds.xform(self.cube1, q=True, ws=True, rotatePivot=True)
        sp_after = cmds.xform(self.cube1, q=True, ws=True, scalePivot=True)
        for expected, actual in zip(rp_frozen, rp_after):
            self.assertAlmostEqual(expected, actual, delta=1e-3)
        for expected, actual in zip(rp_frozen, sp_after):
            self.assertAlmostEqual(expected, actual, delta=1e-3)

        # Pivot restoration must not displace the object.
        vert_after = cmds.pointPosition(f"{self.cube1}.vtx[0]", world=True)
        for expected, actual in zip(vert_before, vert_after):
            self.assertAlmostEqual(expected, actual, delta=1e-3)

        # Channels must still round-trip.
        pos = cmds.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 5.0, delta=1e-3)
        rot = cmds.getAttr(f"{self.cube1}.rotate")[0]
        self.assertAlmostEqual(rot[0], 30.0, delta=1e-3)
        self.assertAlmostEqual(rot[1], 45.0, delta=1e-3)

    def test_restore_transforms_traverse_restores_descendants(self):
        """traverse=True restores a whole hierarchy from one root call, top-down.

        Mirrors ``store_transforms(traverse=True)`` / ``freeze_transforms
        (freeze_children=True)`` so the UI's Unfreeze Children option is a
        single call instead of a manual top-down walk.
        """
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        geo = cmds.polyCube(name="rig_GEO")[0]
        cmds.parent(loc, grp)
        cmds.parent(geo, loc)
        cmds.move(4, 0, 0, grp, absolute=True)
        cmds.move(0, 2, 0, loc, relative=True)
        cmds.move(0, 0, 1, geo, relative=True)

        loc_world_before = cmds.xform(loc, q=True, ws=True, t=True)
        geo_world_before = cmds.xform(geo, q=True, ws=True, t=True)

        try:
            XformUtils.store_transforms(grp, prefix="test", traverse=True)
            XformUtils.freeze_transforms(grp, freeze_children=True)

            # One call on the root; traverse handles the descendants.
            XformUtils.restore_transforms(grp, prefix="test", traverse=True)

            loc_world_after = cmds.xform(loc, q=True, ws=True, t=True)
            geo_world_after = cmds.xform(geo, q=True, ws=True, t=True)
            for a, b in zip(loc_world_before, loc_world_after):
                self.assertAlmostEqual(a, b, delta=1e-3)
            for a, b in zip(geo_world_before, geo_world_after):
                self.assertAlmostEqual(a, b, delta=1e-3)

            # Local channels came back on every node in the chain.
            self.assertAlmostEqual(
                cmds.getAttr(f"{grp}.translateX"), 4.0, delta=1e-3
            )
            self.assertAlmostEqual(
                cmds.getAttr(f"{loc}.translateY"), 2.0, delta=1e-3
            )
            self.assertAlmostEqual(
                cmds.getAttr(f"{geo}.translateZ"), 1.0, delta=1e-3
            )

            # Bake attrs consumed on the whole chain.
            for node in (grp, loc, geo):
                self.assertFalse(
                    cmds.attributeQuery("test_T_bake", node=node, exists=True),
                    f"{node} bake attrs should be consumed by traverse restore",
                )
        finally:
            for n in (grp, loc, geo):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_restore_transforms_traverse_rotated_hierarchy_geometry(self):
        """Traverse restore must not drift child geometry under rotated ancestors.

        The old single-pass restore read each object's world points AFTER its
        ancestors had already been restored, so child vertices (and pivots)
        re-absorbed the ancestors' restored transforms — ~4.8 units of drift
        on this scene. The restore must snapshot all world points and pivots
        before any transform is written (two-phase apply).
        """
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        geo = cmds.polyCube(name="rig_GEO")[0]
        cmds.parent(loc, grp)
        cmds.parent(geo, loc)
        cmds.move(4, 0, 0, grp, absolute=True)
        cmds.rotate(0, 0, 45, grp)
        cmds.move(0, 2, 0, loc, relative=True)
        cmds.rotate(0, 30, 0, loc)
        cmds.move(0, 0, 1, geo, relative=True)
        cmds.rotate(15, 0, 0, geo)
        cmds.scale(2, 1, 1, geo)

        n_verts = cmds.polyEvaluate(geo, vertex=True)
        verts_before = [
            cmds.pointPosition(f"{geo}.vtx[{i}]", world=True) for i in range(n_verts)
        ]
        rp_before = cmds.xform(geo, q=True, ws=True, rotatePivot=True)

        try:
            XformUtils.store_transforms(grp, prefix="test", traverse=True)
            XformUtils.freeze_transforms(grp, freeze_children=True)

            XformUtils.restore_transforms(grp, prefix="test", traverse=True)

            verts_after = [
                cmds.pointPosition(f"{geo}.vtx[{i}]", world=True)
                for i in range(n_verts)
            ]
            for before, after in zip(verts_before, verts_after):
                for a, b in zip(before, after):
                    self.assertAlmostEqual(a, b, delta=1e-3)

            rp_after = cmds.xform(geo, q=True, ws=True, rotatePivot=True)
            for a, b in zip(rp_before, rp_after):
                self.assertAlmostEqual(a, b, delta=1e-3)

            self.assertAlmostEqual(
                cmds.getAttr(f"{grp}.rotateZ"), 45.0, delta=1e-3
            )
        finally:
            for n in (grp, loc, geo):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_restore_transforms_traverse_false_skips_descendants(self):
        """Default traverse=False must not consume descendant bake history."""
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        cmds.parent(loc, grp)
        cmds.move(1, 2, 3, grp, absolute=True)
        cmds.move(0, 1, 0, loc, relative=True)
        try:
            XformUtils.store_transforms(grp, prefix="test", traverse=True)
            XformUtils.freeze_transforms(grp, freeze_children=True)

            XformUtils.restore_transforms(grp, prefix="test")  # default

            self.assertTrue(
                cmds.attributeQuery("test_T_bake", node=loc, exists=True),
                "Descendant bake history must survive a traverse=False restore",
            )
        finally:
            for n in (grp, loc):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_store_transforms_heals_legacy_keyable_attrs(self):
        """Re-storing on attrs created keyable (legacy scenes) should normalize them."""
        # Simulate legacy state: attrs added with keyable=True.
        cmds.addAttr(self.cube1, ln="test_T_bake", dt="double3", keyable=True)
        cmds.addAttr(self.cube1, ln="test_R_bake", at="matrix", keyable=True)
        cmds.addAttr(self.cube1, ln="test_S_bake", dt="double3", keyable=True)

        XformUtils.store_transforms(self.cube1, prefix="test")

        for attr in ("test_T_bake", "test_R_bake", "test_S_bake"):
            plug = f"{self.cube1}.{attr}"
            self.assertFalse(cmds.getAttr(plug, keyable=True))
            self.assertFalse(cmds.getAttr(plug, channelBox=True))

    def test_freeze_transforms(self):
        """Test freeze transforms."""
        cmds.move(10, 10, 10, self.cube1)
        cmds.rotate(45, 0, 0, self.cube1)

        XformUtils.freeze_transforms(self.cube1, translate=True, rotate=True)

        trans = cmds.getAttr(f"{self.cube1}.translate")[0]
        rot = cmds.getAttr(f"{self.cube1}.rotate")[0]

        self.assertEqual(tuple(trans), (0.0, 0.0, 0.0))
        self.assertEqual(tuple(rot), (0.0, 0.0, 0.0))

        # Position should still be 10, 10, 10 in world space (geometry moved)
        # But pivot is at origin if not preserved?
        # freeze_transforms uses makeIdentity which resets pivot to origin unless pn=True
        # The implementation uses pn=True (preserve normals? No, pn flag in makeIdentity is preserveNormals?
        # Actually, let's check if it preserves pivot position.
        # The docstring says "Maya's makeIdentity automatically preserves world-space pivot positions".

        # Let's verify world position of geometry
        bbox = cmds.exactWorldBoundingBox(self.cube1)
        center = [
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        ]
        self.assertAlmostEqual(center[0], 10.0, delta=1.0)  # Approx check

    def test_freeze_transforms_skips_instanced_by_default(self):
        """Baking into a shared shape would rewrite every sibling's geometry —
        an instanced object must be skipped, leaving BOTH halves untouched."""
        import mayatk as mtk

        src = cmds.polyCube(name="inst_src", width=2, height=1, depth=1)[0]
        inst = mtk.EditUtils.mirror_instance(src, axis="x", pivot=(3.0, 0.0, 0.0))[0]
        src_before = cmds.exactWorldBoundingBox(src)

        XformUtils.freeze_transforms(inst, scale=True, force=True)

        self.assertNotEqual(
            tuple(cmds.getAttr(f"{inst}.scale")[0]), (1.0, 1.0, 1.0), "scale was baked"
        )
        for axis, b, a in zip("xyzXYZ", src_before, cmds.exactWorldBoundingBox(src)):
            self.assertAlmostEqual(a, b, places=4, msg=f"source geometry moved ({axis})")

    def test_freeze_disconnect_strategy_finds_child_plug_blockers(self):
        """A per-axis connection (``drv.rotateZ -> cube.rotateZ``) must be
        found and broken by ``connection_strategy='disconnect'``. Compound
        ``listConnections`` queries don't see child-plug connections — anim
        curves and constraints connect per-axis — so the old compound-only
        blocker scan found nothing and silently skipped the node."""
        drv = cmds.spaceLocator(name="fz_drv")[0]
        cmds.setAttr(f"{drv}.rotateZ", 20.0)
        cmds.connectAttr(f"{drv}.rotateZ", f"{self.cube1}.rotateZ")

        XformUtils.freeze_transforms(
            self.cube1, rotate=True, connection_strategy="disconnect", force=True
        )

        self.assertFalse(
            cmds.listConnections(f"{self.cube1}.rotateZ", source=True, plugs=True),
            "child-plug driver should have been disconnected",
        )
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube1}.rotateZ"), 0.0, places=5)

    def test_uninstance_freeze_bakes_to_engine_safe_geometry(self):
        """uninstance(freeze=True) breaks the link AND bakes in one step: the
        mirrored instance becomes independent geometry with a POSITIVE scale,
        while the source keeps its own geometry.

        Breaking the link alone is not enough — the transform would still carry
        the negative scale that exporters object to.
        """
        import mayatk as mtk

        src = cmds.polyCube(name="bake_src", width=2, height=1, depth=1)[0]
        inst = mtk.EditUtils.mirror_instance(src, axis="x", pivot=(3.0, 0.0, 0.0))[0]
        src_before = cmds.exactWorldBoundingBox(src)
        inst_before = cmds.exactWorldBoundingBox(inst)

        # Link-break alone leaves the negative scale in place.
        mtk.NodeUtils.uninstance(inst)
        self.assertLess(
            min(cmds.getAttr(f"{inst}.scale")[0]),
            0,
            "uninstance alone should not touch the transform",
        )

        mtk.NodeUtils.uninstance(inst, freeze=True)

        # Scale baked away, so nothing negative survives for an exporter to trip on.
        for axis, v in zip("xyz", cmds.getAttr(f"{inst}.scale")[0]):
            self.assertAlmostEqual(v, 1.0, places=4, msg=f"scale {axis} not baked")
        # Shape is now unique to the instance.
        shape = cmds.listRelatives(inst, shapes=True, ni=True, fullPath=True)[0]
        self.assertEqual(
            len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []), 1
        )
        # Both halves stayed where they were.
        for label, before, node in (("src", src_before, src), ("inst", inst_before, inst)):
            for v_before, v_after in zip(before, cmds.exactWorldBoundingBox(node)):
                self.assertAlmostEqual(
                    v_after, v_before, places=4, msg=f"{label} moved"
                )

    def test_uninstance_freeze_both_siblings_in_one_batch(self):
        """Both halves of an instanced pair in a single call: each must end up
        with its own shape and a baked scale, with neither half moving."""
        import mayatk as mtk

        src = cmds.polyCube(name="pair_src", width=2, height=1, depth=1)[0]
        inst = mtk.EditUtils.mirror_instance(src, axis="x", pivot=(3.0, 0.0, 0.0))[0]
        before = {n: cmds.exactWorldBoundingBox(n) for n in (src, inst)}

        mtk.NodeUtils.uninstance([src, inst], freeze=True)

        for node in (src, inst):
            shape = cmds.listRelatives(node, shapes=True, ni=True, fullPath=True)[0]
            self.assertEqual(
                len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []),
                1,
                f"{node} shape still shared",
            )
            for v in cmds.getAttr(f"{node}.scale")[0]:
                self.assertAlmostEqual(v, 1.0, places=4, msg=f"{node} scale not baked")
            for v_before, v_after in zip(before[node], cmds.exactWorldBoundingBox(node)):
                self.assertAlmostEqual(v_after, v_before, places=4, msg=f"{node} moved")

    def test_freeze_to_opm(self):
        """Test freezing to Offset Parent Matrix."""
        cmds.move(10, 10, 10, self.cube1)

        XformUtils.freeze_to_opm(self.cube1)

        # Translate should be zero
        trans = cmds.getAttr(f"{self.cube1}.translate")[0]
        self.assertEqual(tuple(trans), (0.0, 0.0, 0.0))

        # OPM should be set — flat 16-element list; identity has 1s on the diagonal.
        opm = cmds.getAttr(f"{self.cube1}.offsetParentMatrix")
        identity = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        self.assertNotEqual(opm, identity)

    def test_unfreeze_to_parent_restores_locator_rig(self):
        """Lifting LOC's local matrix up to a frozen GRP restores the rig layout."""
        # Build the post-freeze state of a GRP > LOC > GEO rig: GRP at identity,
        # LOC holds the world-space transform, GEO sits under LOC.
        grp = cmds.group(empty=True, name="rig_GRP")
        loc = cmds.spaceLocator(name="rig_LOC")[0]
        cmds.parent(loc, grp)
        geo = cmds.polyCube(name="rig_GEO")[0]
        cmds.parent(geo, loc)

        cmds.setAttr(f"{loc}.translate", 7.0, 3.0, -2.0)
        cmds.setAttr(f"{loc}.rotate", 0.0, 45.0, 0.0)
        cmds.setAttr(f"{geo}.translate", 0.5, 0.0, 0.0)

        geo_world_before = cmds.xform(geo, q=True, ws=True, t=True)

        result = XformUtils.unfreeze_to_parent(loc)

        self.assertIn("rig_GRP", result[0])
        self.assertAlmostEqual(cmds.getAttr(f"{loc}.translateX"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{loc}.translateY"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{loc}.translateZ"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{loc}.rotateY"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.translateX"), 7.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.translateY"), 3.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.translateZ"), -2.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.rotateY"), 45.0, places=5)

        geo_world_after = cmds.xform(geo, q=True, ws=True, t=True)
        for before, after in zip(geo_world_before, geo_world_after):
            self.assertAlmostEqual(before, after, places=4)

        cmds.delete(grp)

    def test_unfreeze_to_parent_traverse_preserves_root(self):
        """traverse=True walks the subtree and leaves the input root at identity."""
        root = cmds.group(empty=True, name="RIG_ROOT")
        grp_a = cmds.group(empty=True, name="rigA_GRP", parent=root)
        loc_a = cmds.spaceLocator(name="rigA_LOC")[0]
        cmds.parent(loc_a, grp_a)
        geo_a = cmds.polyCube(name="rigA_GEO")[0]
        cmds.parent(geo_a, loc_a)
        cmds.setAttr(f"{loc_a}.translate", -4.0, 1.5, 2.0)
        cmds.setAttr(f"{geo_a}.translate", 0.25, 0.0, 0.0)

        grp_b = cmds.group(empty=True, name="rigB_GRP", parent=root)
        loc_b = cmds.spaceLocator(name="rigB_LOC")[0]
        cmds.parent(loc_b, grp_b)
        cmds.setAttr(f"{loc_b}.translate", 8.0, 0.0, -3.0)

        geo_a_world_before = cmds.xform(geo_a, q=True, ws=True, t=True)
        result = XformUtils.unfreeze_to_parent(root, traverse=True)

        # Root container stays at identity.
        self.assertAlmostEqual(cmds.getAttr(f"{root}.translateX"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{root}.translateY"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{root}.translateZ"), 0.0, places=5)

        # Each GRP absorbed its LOC; each LOC is now zero.
        self.assertAlmostEqual(cmds.getAttr(f"{grp_a}.translateX"), -4.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp_a}.translateY"), 1.5, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{loc_a}.translateX"), 0.0, places=5)

        self.assertAlmostEqual(cmds.getAttr(f"{grp_b}.translateX"), 8.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp_b}.translateZ"), -3.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{loc_b}.translateX"), 0.0, places=5)

        # Geo descendant world position preserved.
        geo_a_world_after = cmds.xform(geo_a, q=True, ws=True, t=True)
        for before, after in zip(geo_a_world_before, geo_a_world_after):
            self.assertAlmostEqual(before, after, places=4)

        self.assertEqual(len(result), 2)

        cmds.delete(root)

    def test_unfreeze_to_parent_preserve_root_skips_direct_loc_child(self):
        """preserve_root=True silently skips a locator that is a direct child of an input root."""
        root = cmds.group(empty=True, name="rigD_ROOT")
        loc = cmds.spaceLocator(name="rigD_LOC")[0]
        cmds.parent(loc, root)
        cmds.setAttr(f"{loc}.translate", 3.0, 0.0, 0.0)

        result = XformUtils.unfreeze_to_parent(root, traverse=True)

        # Root and locator are unchanged — nothing eligible to lift.
        self.assertEqual(result, [])
        self.assertAlmostEqual(cmds.getAttr(f"{root}.translateX"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{loc}.translateX"), 3.0, places=5)

        cmds.delete(root)

    def test_unfreeze_to_parent_traverse_preserve_root_false(self):
        """preserve_root=False lets the input root receive a direct child's matrix."""
        grp = cmds.group(empty=True, name="rigC_GRP")
        loc = cmds.spaceLocator(name="rigC_LOC")[0]
        cmds.parent(loc, grp)
        cmds.setAttr(f"{loc}.translate", -4.0, 1.5, 2.0)

        XformUtils.unfreeze_to_parent(grp, traverse=True, preserve_root=False)

        self.assertAlmostEqual(cmds.getAttr(f"{loc}.translateX"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.translateX"), -4.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.translateY"), 1.5, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{grp}.translateZ"), 2.0, places=5)

        cmds.delete(grp)

    # -------------------------------------------------------------------------
    # Pivot Operations Tests
    # -------------------------------------------------------------------------

    def test_get_operation_axis_pos(self):
        """Test getting pivot position for operations."""
        cmds.move(10, 10, 10, self.cube1)

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

    @skipIfBatch("align_pivot_to_selection uses snap3PointsTo3Points, a GUI-sourced MEL proc")
    def test_align_pivot_to_selection(self):
        """Test aligning pivot to selection."""
        # Move cube2
        cmds.move(20, 0, 0, self.cube2)

        # Align cube1 pivot to cube2
        XformUtils.align_pivot_to_selection(self.cube1, self.cube2, translate=True)

        # Cube1 should have moved to Cube2
        pos = cmds.xform(self.cube1, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 20.0)

    def test_reset_pivot_transforms(self):
        """Test resetting pivots when objects are passed explicitly.

        Bug: The method had a misplaced ``return`` inside the ``else`` branch,
        causing it to exit immediately when the ``objects`` parameter was provided.
        Fixed: 2026-02-27
        """
        cmds.move(10, 0, 0, self.cube1)
        # Move pivot away from geometry center
        cmds.xform(self.cube1, ws=True, rp=(0, 0, 0))

        # Pass objects explicitly — before the fix this was a no-op
        XformUtils.reset_pivot_transforms(self.cube1)

        # Pivot should now be re-centred on the object's bounding box
        rp = cmds.xform(self.cube1, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], 10.0, delta=0.5)

    def test_transfer_pivot(self):
        """Test transferring pivot."""
        cmds.move(10, 0, 0, self.cube1)
        cmds.move(20, 0, 0, self.cube2)

        # Transfer pivot from cube1 to cube2
        XformUtils.transfer_pivot([self.cube1, self.cube2], translate=True)

        # Cube2 pivot should be at Cube1 location (10, 0, 0)
        rp = cmds.xform(self.cube2, q=True, ws=True, rp=True)
        self.assertAlmostEqual(rp[0], 10.0)

    def test_bake_pivot(self):
        """Test baking pivot."""
        cmds.move(10, 0, 0, self.cube1)
        # Rotate pivot
        cmds.xform(self.cube1, ro=(0, 45, 0))

        XformUtils.bake_pivot(self.cube1, orientation=True)

        # Object rotation should change to match pivot orientation?
        # bake_pivot implementation is complex, involving context checks.
        # In batch mode, context checks might fail or behave differently.
        # Let's just ensure it runs without error.
        pass

    # -------------------------------------------------------------------------
    # World-Aligned Pivot Tests
    # -------------------------------------------------------------------------

    def test_world_align_pivot_skips_non_dag_in_selection(self):
        """World-align pivot must ignore non-DAG nodes in the active selection.

        Bug: ``world_align_pivot`` fed the raw ``cmds.ls(selection=True)``
        straight to ``cmds.xform``. With a material / construction-history
        node selected (alone or mixed with a real object), ``xform`` raised
        ``RuntimeError: No valid objects supplied to 'xform' command.`` —
        crashing the Pivot panel's ``tb003`` slot.
        Fixed: 2026-06-08
        """
        cmds.move(7, 0, 0, self.cube1)
        cmds.xform(self.cube1, rotateAxis=(0, 30, 0))  # dirty the rotate axis
        mat = cmds.shadingNode("lambert", asShader=True, name="test_lambert")

        # Real object mixed with a non-DAG material — the reported crash case.
        cmds.select([self.cube1, mat], replace=True)
        result = XformUtils.world_align_pivot(mode="set", pivot_type="object")

        self.assertTrue(result)
        # Proves cube1 was processed (object path zeroes the rotate axis) and
        # the material was skipped rather than raising.
        ra = cmds.xform(self.cube1, q=True, rotateAxis=True)
        self.assertEqual([round(v, 4) for v in ra], [0.0, 0.0, 0.0])

    def test_world_align_pivot_only_non_dag_returns_false(self):
        """A selection with no transform-able object returns False, not a crash."""
        mat = cmds.shadingNode("lambert", asShader=True, name="test_lambert2")
        cmds.select(mat, replace=True)
        result = XformUtils.world_align_pivot(mode="set", pivot_type="object")
        self.assertFalse(result)

    def test_world_align_pivot_component_selection(self):
        """A component selection resolves to its transform and aligns the pivot."""
        cmds.select(f"{self.cube1}.f[1]", replace=True)
        result = XformUtils.world_align_pivot(mode="set", pivot_type="object")
        self.assertTrue(result)

    def test_world_align_pivot_keeps_component_selection(self):
        """World-align pivot must leave a component selection in component mode.

        Bug: ``world_align_pivot`` opened with ``cmds.select(transforms, replace=True)``,
        so clicking Pivot > Align World with faces/verts selected kicked Maya back to
        object mode (and the ``manip`` branch never restored the selection at all).
        Fixed: 2026-07-26
        """
        face = f"{self.cube1}.f[1]"
        for pivot_type in ("manip", "object"):
            with self.subTest(pivot_type=pivot_type):
                cmds.select(face, replace=True)
                result = XformUtils.world_align_pivot(
                    mode="set", pivot_type=pivot_type
                )
                self.assertTrue(result)
                selection = cmds.ls(sl=True, flatten=True) or []
                self.assertEqual(selection, cmds.ls(face, flatten=True))

    def test_world_align_pivot_object_uses_component_center(self):
        """The permanent pivot lands on the selected components, not the object pivot."""
        face = f"{self.cube1}.f[1]"
        expected = XformUtils.get_bounding_box(face, "center")
        cmds.select(face, replace=True)

        self.assertTrue(XformUtils.world_align_pivot(mode="set", pivot_type="object"))

        rp = cmds.xform(self.cube1, q=True, rotatePivot=True, worldSpace=True)
        for actual, want in zip(rp, expected):
            self.assertAlmostEqual(actual, want, places=4)
        # The face itself must not have moved — only the pivot.
        for actual, want in zip(
            XformUtils.get_bounding_box(face, "center"), expected
        ):
            self.assertAlmostEqual(actual, want, places=4)

    def test_world_align_pivot_get_reports_component_center(self):
        """``mode='get'`` reports the component extent and the components it used."""
        faces = [f"{self.cube1}.f[0]", f"{self.cube1}.f[1]"]
        cmds.select(faces, replace=True)

        result = XformUtils.world_align_pivot(mode="get")

        expected = XformUtils.get_bounding_box(faces, "center")
        for actual, want in zip(result["position"], expected):
            self.assertAlmostEqual(actual, want, places=4)
        self.assertEqual(result["orientation"], [0, 0, 0])
        # Components come back unexpanded (Maya consolidates the pair into one
        # "f[0:1]" range) — flatten before counting.
        self.assertEqual(
            cmds.ls(result["components"], flatten=True), cmds.ls(faces, flatten=True)
        )
        self.assertEqual(len(result["objects"]), 1)

    def test_world_align_pivot_components_on_multiple_objects(self):
        """Each object pivots on its own components; the manip pivot spans them all."""
        f1, f2 = f"{self.cube1}.f[1]", f"{self.cube2}.f[1]"
        want1 = XformUtils.get_bounding_box(f1, "center")
        want2 = XformUtils.get_bounding_box(f2, "center")
        cmds.select([f1, f2], replace=True)

        self.assertTrue(XformUtils.world_align_pivot(mode="set", pivot_type="object"))

        for cube, want in ((self.cube1, want1), (self.cube2, want2)):
            rp = cmds.xform(cube, q=True, rotatePivot=True, worldSpace=True)
            for actual, expected in zip(rp, want):
                self.assertAlmostEqual(actual, expected, places=4)

    def test_resolve_transforms_dedupes_object_mixed_with_its_components(self):
        """An object listed alongside its own components must resolve to ONE entry.

        Bug: the shape->parent branch used ``listRelatives(path=True)``, which returns
        the *shortest unique* name, while the transform branch returned long paths. So
        ``["test_cube1", "test_cube1.f[1]"]`` produced both "|test_cube1" and
        "test_cube1" — two spellings ``dict.fromkeys`` can't merge. ``bake_pivot``
        then ran its relative move twice on the same object.
        Fixed: 2026-07-26
        """
        resolved = _XformUtilsInternal._resolve_transforms(
            [self.cube1, f"{self.cube1}.f[1]"]
        )
        self.assertEqual(len(resolved), 1)
        self.assertTrue(all(p.startswith("|") for p in resolved))  # long paths

    def test_world_align_pivot_unmeasurable_components_keep_object_pivot(self):
        """Components with no measurable extent must not collapse the pivot to origin.

        Bug: ``exactWorldBoundingBox`` reports "nothing to measure" as an inverted
        sentinel box (min +1e20 / max -1e20) instead of raising, so averaging it
        produced exactly (0, 0, 0). A NURBS surface-face selection — a legitimately
        masked component type — therefore yanked the pivot to the world origin.
        Fixed: 2026-07-26
        """
        srf = cmds.nurbsPlane(name="test_srf")[0]
        cmds.move(8, 2, 0, srf, absolute=True)
        expected = cmds.xform(srf, q=True, rotatePivot=True, worldSpace=True)
        self.assertIsNone(  # the sentinel box the guard has to catch
            XformUtils._component_center([f"{srf}.sf[0][0]"])
        )

        cmds.select(f"{srf}.sf[0][0]", replace=True)
        self.assertTrue(XformUtils.world_align_pivot(mode="set", pivot_type="object"))

        rp = cmds.xform(srf, q=True, rotatePivot=True, worldSpace=True)
        for actual, want in zip(rp, expected):
            self.assertAlmostEqual(actual, want, places=4)
        self.assertNotAlmostEqual(rp[0], 0.0, places=4)  # not yanked to the origin

    def test_world_align_pivot_ignores_non_geometry_component_masks(self):
        """A selected rotate-pivot handle is a manipulator, not geometry to center on."""
        cmds.move(6, 3, 0, self.cube1, absolute=True)
        self.assertEqual(
            _XformUtilsInternal._group_components_by_transform(
                [f"{self.cube1}.rotatePivot", f"{self.cube1}.scalePivot"]
            ),
            {},
        )

    def test_world_align_pivot_object_selection_unchanged(self):
        """A whole-object selection still pivots on the object's own rotate pivot."""
        cmds.move(3, 4, 5, self.cube1, absolute=True)
        expected = cmds.xform(self.cube1, q=True, rotatePivot=True, worldSpace=True)
        cmds.select(self.cube1, replace=True)

        self.assertTrue(XformUtils.world_align_pivot(mode="set", pivot_type="object"))

        rp = cmds.xform(self.cube1, q=True, rotatePivot=True, worldSpace=True)
        for actual, want in zip(rp, expected):
            self.assertAlmostEqual(actual, want, places=4)
        self.assertEqual(cmds.ls(sl=True), cmds.ls(self.cube1))

    # -------------------------------------------------------------------------
    # Orientation Tests
    # -------------------------------------------------------------------------

    def test_aim_object_at_point(self):
        """Test aiming object."""
        target = (0, 10, 0)
        XformUtils.aim_object_at_point(self.cube1, target)

        rot = cmds.getAttr(f"{self.cube1}.rotate")[0]
        self.assertNotEqual(tuple(rot), (0.0, 0.0, 0.0))

    def test_aim_object_at_point_multi_no_leak(self):
        """Verify that aiming multiple objects cleans up all constraints.

        Bug: Only the last aimConstraint was deleted; earlier constraints
        leaked and the user's target object was accidentally deleted when
        ``target_pos`` was an existing transform name.
        Fixed: 2026-02-27
        """
        c1 = cmds.polyCube(name="aim_test_a")[0]
        c2 = cmds.polyCube(name="aim_test_b")[0]
        cmds.move(-5, 0, 0, c1)
        cmds.move(5, 0, 0, c2)

        constraint_count_before = len(cmds.ls(type="aimConstraint"))
        XformUtils.aim_object_at_point([c1, c2], (0, 10, 0))
        constraint_count_after = len(cmds.ls(type="aimConstraint"))

        # All constraints should be cleaned up
        self.assertEqual(constraint_count_before, constraint_count_after)

        # No leftover 'target_helper' node
        self.assertFalse(cmds.objExists("target_helper"))

        cmds.delete(c1, c2)

    def test_aim_object_at_existing_target_not_deleted(self):
        """Verify that aiming at an existing transform does not delete it.

        Bug: ``cmds.delete(const, target)`` unconditionally deleted the target
        even when it was a user-supplied transform, not a temporary helper.
        Fixed: 2026-02-27
        """
        target = cmds.polySphere(name="aim_target_sphere")[0]
        cmds.move(0, 10, 0, target)

        XformUtils.aim_object_at_point(self.cube1, target)

        # The user's target must still exist
        self.assertTrue(cmds.objExists("aim_target_sphere"))
        cmds.delete(target)

    def test_orient_to_vector(self):
        """Test orienting to vector."""
        XformUtils.orient_to_vector(self.cube1, aim_vector=(0, 1, 0))

        # X axis should point up (0, 1, 0)
        # Check world matrix
        m = cmds.xform(self.cube1, q=True, m=True, ws=True)
        # X axis is first 3 elements
        self.assertAlmostEqual(m[0], 0.0, places=4)
        self.assertAlmostEqual(m[1], 1.0, places=4)
        self.assertAlmostEqual(m[2], 0.0, places=4)

    def test_get_orientation(self):
        """Test getting orientation."""
        cmds.rotate(0, 90, 0, self.cube1)

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
        self.cube1 = cmds.polyCube(name="test_cube1")[0]

    def tearDown(self):
        """Clean up."""
        if cmds.objExists("test_cube1"):
            cmds.delete("test_cube1")
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
        cmds.setAttr(f"{self.cube1}.translateX", lock=True)
        # Should unlock, freeze, and relock (if force=True)
        XformUtils.freeze_transforms(self.cube1, translate=True, force=True)
        self.assertEqual(cmds.getAttr(f"{self.cube1}.translateX"), 0.0)
        self.assertTrue(cmds.getAttr(f"{self.cube1}.translateX", lock=True))

    def test_align_using_three_points_identity(self):
        """Verify 3-point align maps source frame onto target frame.

        Bug: Original implementation always rotated around the Z axis via
        ``MEulerRotation(0, 0, angle)`` regardless of the actual rotation
        axis, producing incorrect results for most configurations.
        Fixed: 2026-02-27
        """
        # Source plane at origin, target plane at (10, 0, 0) with a 90-deg Y rotation
        src = cmds.polyPlane(name="src_plane", w=4, h=4, sx=1, sy=1, ax=(0, 1, 0))[0]
        tgt = cmds.polyPlane(name="tgt_plane", w=4, h=4, sx=1, sy=1, ax=(0, 1, 0))[0]
        cmds.move(10, 0, 0, tgt)
        cmds.rotate(0, 90, 0, tgt)

        src_verts = cmds.ls(f"{src}.vtx[0:2]", flatten=True)
        tgt_verts = cmds.ls(f"{tgt}.vtx[0:2]", flatten=True)

        XformUtils.align_using_three_points(src_verts + tgt_verts)

        # After alignment, the first 3 source vertices should be very close
        # to the corresponding target vertices.
        for sv, tv in zip(src_verts, tgt_verts):
            sp = cmds.pointPosition(sv, w=True)
            tp = cmds.pointPosition(tv, w=True)
            for i in range(3):
                self.assertAlmostEqual(sp[i], tp[i], places=3)

        cmds.delete(src, tgt)

    def test_align_vertices_no_selection(self):
        """Verify align_vertices doesn't crash when nothing is selected.

        Bug: Selection validation happened after indexing into the reference
        position list, causing IndexError when fewer than 2 vertices were
        selected.
        Fixed: 2026-02-27
        """
        cmds.select(clear=True)
        # Should return gracefully (inViewMessage), not IndexError
        XformUtils.align_vertices(mode=3)

    def test_align_vertices_single_selection(self):
        """Verify align_vertices returns early with only a single vertex.

        Bug: Same IndexError as test_align_vertices_no_selection — the guard
        ran after the position was already accessed.
        Fixed: 2026-02-27
        """
        cube = cmds.polyCube(name="align_vert_test")[0]
        cmds.select(f"{cube}.vtx[0]")
        # Should not raise
        XformUtils.align_vertices(mode=3)
        cmds.delete(cube)

    def test_align_vertices_mode_x(self):
        """Verify align_vertices mode=3 (X) aligns X coords to last selected."""
        cube = cmds.polyCube(name="align_mode_test", sx=2, sy=2, sz=2)[0]
        verts = cmds.ls(f"{cube}.vtx[*]", flatten=True)

        # Select 3 vertices — the last one's X will be the reference
        cmds.select([verts[0], verts[1], verts[2]])
        ref_x = cmds.xform(verts[2], q=True, t=True, ws=True)[0]

        XformUtils.align_vertices(mode=3)  # align X

        # All selected verts should now share the reference X
        for v in [verts[0], verts[1], verts[2]]:
            pos = cmds.xform(v, q=True, t=True, ws=True)
            self.assertAlmostEqual(pos[0], ref_x, places=4)

        cmds.delete(cube)


class TestFreezeInstanceStrategy(MayaTkTestCase):
    """freeze_transforms(instance_strategy=...) — skip / preserve / uninstance."""

    def _make_group(self, n=3, name="fis"):
        src = cmds.polyCube(name=f"{name}_m0")[0]
        members = [src]
        for i in range(1, n):
            members.append(cmds.instance(src, name=f"{name}_m{i}")[0])
        for i, m in enumerate(members):
            # Every member non-identity — including the first, so "the master
            # got frozen" assertions can never pass vacuously.
            cmds.setAttr(
                f"{m}.translate", (i + 1) * 3.0, 0.5 * (i + 1), 0.0, type="double3"
            )
            cmds.setAttr(f"{m}.rotateY", 25.0 * (i + 1))
        return cmds.ls(members, long=True)

    def _world_verts(self, obj, count=8):
        return [
            cmds.xform(f"{obj}.vtx[{i}]", q=True, ws=True, t=True)
            for i in range(count)
        ]

    def _shared_parent_count(self, member):
        shape = cmds.listRelatives(member, shapes=True, fullPath=True)[0]
        return len(cmds.listRelatives(shape, allParents=True) or [])

    def test_default_skip_leaves_instances_untouched(self):
        members = self._make_group()
        XformUtils.freeze_transforms(members)
        # Default behavior unchanged: instanced objects are skipped in place.
        self.assertAlmostEqual(
            cmds.getAttr(f"{members[1]}.translateX"), 6.0, places=4
        )
        for m in members:
            self.assertEqual(self._shared_parent_count(m), 3)

    def test_preserve_keeps_instancing(self):
        members = self._make_group(name="fisp")
        before = {m: self._world_verts(m) for m in members}

        XformUtils.freeze_transforms(members, instance_strategy="preserve")

        for m in cmds.ls(members, long=True):
            self.assertEqual(self._shared_parent_count(m), 3)
            for va, vb in zip(before[m], self._world_verts(m)):
                for x, y in zip(va, vb):
                    self.assertAlmostEqual(x, y, places=3)
        # The group's operated member is identity-frozen.
        master = cmds.ls(members[0], long=True)[0]
        for v in cmds.getAttr(f"{master}.translate")[0]:
            self.assertAlmostEqual(v, 0.0, places=4)
        for v in cmds.getAttr(f"{master}.rotate")[0]:
            self.assertAlmostEqual(v, 0.0, places=4)

    def test_uninstance_breaks_and_freezes(self):
        members = self._make_group(name="fisu")
        before = {m: self._world_verts(m) for m in members}

        XformUtils.freeze_transforms(members, instance_strategy="uninstance")

        for m in cmds.ls(members, long=True):
            self.assertEqual(self._shared_parent_count(m), 1)
            for va, vb in zip(before[m], self._world_verts(m)):
                for x, y in zip(va, vb):
                    self.assertAlmostEqual(x, y, places=3)
            for v in cmds.getAttr(f"{m}.translate")[0]:
                self.assertAlmostEqual(v, 0.0, places=4)

    def test_preserve_keeps_per_instance_shading(self):
        """Regression (production scene): the fork/relink design renumbered
        instObjGroups and broke per-instance shading — 'Connection not made
        … SG.dagSetMembers[n]' — leaving siblings on baked geometry with
        un-compensated matrices. The in-place bake touches no DAG edge."""
        members = self._make_group(3, name="fsg")
        sgs = []
        for i, m in enumerate(members):
            mat = cmds.shadingNode("lambert", asShader=True, name=f"fsg_mat{i}")
            sg = cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True, name=f"fsg_SG{i}"
            )
            cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
            cmds.sets(m, e=True, forceElement=sg)
            sgs.append(sg)

        XformUtils.freeze_transforms(members, instance_strategy="preserve")

        for m, sg in zip(cmds.ls(members, long=True), sgs):
            shape = cmds.listRelatives(
                m, shapes=True, fullPath=True, noIntermediate=True
            )[0]
            self.assertIn(sg, cmds.listSets(object=shape, type=1) or [])
        # No fork happened: no '__uninst_tmp' wreckage in the scene.
        self.assertEqual(cmds.ls("*__uninst_tmp*") or [], [])

    def test_preserve_freezes_instance_with_shared_intermediate(self):
        """A shared intermediate (orphaned history) shape used to make the
        object permanently un-freezable. Baking in place ignores it."""
        src = cmds.polyCube(name="fsi_m0")[0]
        sib = cmds.instance(src, name="fsi_m1")[0]
        cmds.setAttr(f"{sib}.translateX", 8)
        orig = cmds.createNode("mesh", parent=src, name="fsi_m0ShapeOrig")
        cmds.setAttr(f"{orig}.intermediateObject", 1)
        cmds.parent(cmds.ls(orig, long=True)[0], sib, add=True, shape=True)
        cmds.setAttr(f"{src}.rotateY", 30)
        cmds.setAttr(f"{src}.scaleX", 2)
        members = cmds.ls([src, sib], long=True)
        before = {m: self._world_verts(m) for m in members}

        XformUtils.freeze_transforms(members, instance_strategy="preserve")

        master = cmds.ls(members[0], long=True)[0]
        for v in cmds.getAttr(f"{master}.rotate")[0]:
            self.assertAlmostEqual(v, 0.0, places=4)
        for m in cmds.ls(members, long=True):
            self.assertEqual(self._shared_parent_count(m), 2)
            for va, vb in zip(before[m], self._world_verts(m)):
                for x, y in zip(va, vb):
                    self.assertAlmostEqual(x, y, places=3)

    def test_driven_sibling_leaves_group_untouched(self):
        """A driven sibling would have the compensation overwritten on the
        next evaluation, leaving it displaced against baked geometry — so
        the whole group is skipped rather than half-applied."""
        members = self._make_group(2, name="fsd")
        driver = cmds.spaceLocator(name="fsd_driver")[0]
        cmds.connectAttr(f"{driver}.translateX", f"{members[1]}.translateX")
        before = {m: self._world_verts(m) for m in members}

        XformUtils.freeze_transforms(members, instance_strategy="preserve")

        for m in cmds.ls(members, long=True):
            for va, vb in zip(before[m], self._world_verts(m)):
                for x, y in zip(va, vb):
                    self.assertAlmostEqual(x, y, places=3)
        self.assertAlmostEqual(
            cmds.getAttr(f"{members[0]}.translateX"), 3.0, places=4
        )

    def test_orphan_intermediate_sharing_does_not_drop_objects(self):
        """The group is resolved from the shapes a bake writes to. An
        orphaned intermediate shared more widely than the visible shape must
        not make unrelated objects count as handled — they would silently
        never be frozen."""
        a = cmds.polyCube(name="fso_a")[0]
        b = cmds.polyCube(name="fso_b")[0]
        a_inst = cmds.instance(a, name="fso_a2")[0]
        orphan = cmds.createNode("mesh", parent=a, name="fso_orphanShape")
        cmds.setAttr(f"{orphan}.intermediateObject", 1)
        cmds.parent(cmds.ls(orphan, long=True)[0], b, add=True, shape=True)
        for obj in (a, a_inst, b):
            cmds.setAttr(f"{obj}.translateY", 4.0)
        members = cmds.ls([a, a_inst, b], long=True)

        XformUtils.freeze_transforms(members, instance_strategy="preserve")

        # b only ever shared the orphan, so it must have been frozen itself.
        self.assertAlmostEqual(
            cmds.getAttr(f"{cmds.ls(b, long=True)[0]}.translateY"), 0.0, places=4
        )

    def test_invalid_strategy_raises(self):
        members = self._make_group(name="fisx")
        with self.assertRaises(ValueError):
            XformUtils.freeze_transforms(members, instance_strategy="bogus")


if __name__ == "__main__":
    unittest.main()
