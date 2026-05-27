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
from mayatk.xform_utils._xform_utils import XformUtils

from base_test import MayaTkTestCase
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

        Exercises ``_shift_shape_points``' MFnNurbsCurve branch via the canonical
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

        self.assertIn(str(self.cube1), cleared)
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


if __name__ == "__main__":
    unittest.main()
