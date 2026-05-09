# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.rig_utils module

Tests for RigUtils class functionality including:
- Locator creation and management
- Attribute locking
- Group creation
- Rigging utilities (telescope, switch attributes)
- Joint chain operations
- Skin cluster operations
"""
import unittest
from mayatk.node_utils.attributes._attributes import Attributes
import maya.cmds as cmds

# Handle lazy loading of RigUtils
try:
    import mayatk as mtk
    from mayatk.rig_utils._rig_utils import RigUtils
    from mayatk.rig_utils.telescope_rig import TelescopeRig
except ImportError:
    import sys
    import os

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import mayatk as mtk
    from mayatk.rig_utils._rig_utils import RigUtils
    from mayatk.rig_utils.telescope_rig import TelescopeRig

from base_test import MayaTkTestCase


class TestRigUtils(MayaTkTestCase):
    """Tests for RigUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        # Common test objects
        self.cube = self.create_test_cube("test_cube")
        self.sphere = self.create_test_sphere("test_sphere")

    def tearDown(self):
        """Clean up."""
        super().tearDown()

    def test_create_helper(self):
        """Test create_helper method."""
        # Test 1: Create locator
        loc = RigUtils.create_helper("helper_loc", helper_type="locator")
        self.assertNodeExists("helper_loc")
        self.assertTrue(cmds.objectType(str(loc)) == "transform")

        # Test 2: Create joint
        jnt = RigUtils.create_helper("helper_jnt", helper_type="joint")
        self.assertNodeExists("helper_jnt")
        self.assertEqual(cmds.nodeType(jnt), "joint")

        # Test 3: Create with parent
        child = RigUtils.create_helper("child_helper", parent=self.cube)
        self.assertEqual((cmds.listRelatives(str(child), parent=True) or [None])[0], self.cube)

        # Test 4: Cleanup existing
        RigUtils.create_helper("cleanup_me")
        result = RigUtils.create_helper("cleanup_me", cleanup=True)
        self.assertFalse(cmds.objExists("cleanup_me"))
        self.assertIsNone(result)

    def test_create_group(self):
        """Test create_group method."""
        # Test 1: Empty group
        grp = RigUtils.create_group(name="empty_grp")
        self.assertNodeExists("empty_grp")
        self.assertEqual(len((cmds.listRelatives(str(grp), children=True) or [])), 0)

        # Test 2: Group with objects
        grp2 = RigUtils.create_group(objects=[self.cube, self.sphere], name="obj_grp")
        self.assertNodeExists("obj_grp")
        self.assertIn(self.cube, (cmds.listRelatives(str(grp2), children=True) or []))
        self.assertIn(self.sphere, (cmds.listRelatives(str(grp2), children=True) or []))

        # Test 3: Zero transforms
        # Move cube first
        cmds.move(10, 10, 10, self.cube)
        grp3 = RigUtils.create_group(
            objects=[self.cube], name="zero_grp", zero_translation=True
        )
        self.assertEqual(cmds.getAttr(f"{grp3}.tx"), 0)
        self.assertEqual(cmds.getAttr(f"{grp3}.ty"), 0)
        self.assertEqual(cmds.getAttr(f"{grp3}.tz"), 0)

    def test_create_locator(self):
        """Test create_locator method."""
        # Test 1: Basic creation
        loc = RigUtils.create_locator(name="basic_loc")
        self.assertNodeExists("basic_loc")

        # Test 2: Scale
        loc_scaled = RigUtils.create_locator(name="scaled_loc", scale=5.0)
        self.assertEqual(cmds.getAttr(f"{loc_scaled}.scaleX"), 5.0)

        # Test 3: Position from object
        cmds.move(5, 5, 5, self.cube)
        loc_pos = RigUtils.create_locator(name="pos_loc", position=self.cube)
        pos = cmds.xform(loc_pos, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 5.0)

    def test_create_locator_at_object(self):
        """Test create_locator_at_object method."""
        # Setup
        cmds.move(10, 0, 0, self.cube)

        # Test 1: Basic rig
        RigUtils.create_locator_at_object(
            self.cube, grp_suffix="_GRP", loc_suffix="_LOC", obj_suffix="_GEO"
        )

        # Verify hierarchy: GRP -> LOC -> GEO
        grp_name = "test_cube_GRP"
        loc_name = "test_cube_LOC"
        geo_name = "test_cube_GEO"

        self.assertNodeExists(grp_name)
        self.assertNodeExists(loc_name)
        self.assertNodeExists(geo_name)

        grp = grp_name
        loc = loc_name
        geo = geo_name

        self.assertEqual((cmds.listRelatives(str(loc), parent=True) or [None])[0], grp)
        self.assertEqual((cmds.listRelatives(str(geo), parent=True) or [None])[0], loc)

        # Verify positions match
        self.assertAlmostEqual(cmds.xform(loc, query=True, worldSpace=True, translation=True)[0], 10.0)

    def test_create_locator_at_group_preserves_position(self):
        """Verify locator is placed at the group's content center, not scene root.

        Bug: When create_locator_at_object was called on a group node whose own
        transforms were at origin (but whose children were offset), the locator
        was placed at scene root (0,0,0) instead of at the center of the group's
        children. This happened because bake_pivot was skipped for groups and
        get_manip_pivot_matrix returned the group's identity transform.
        Fixed: 2026-02-23
        """
        # Create a group at origin with a child mesh offset to (10, 0, 0)
        child = self.create_test_cube("child_mesh")
        cmds.move(10, 0, 0, child)
        grp = cmds.group(em=True, n="org_group")
        cmds.parent(child, grp)

        # Group is at origin, child is at (10, 0, 0) world
        grp_pos = cmds.xform(grp, q=True, ws=True, t=True)
        self.assertAlmostEqual(grp_pos[0], 0.0, places=3)

        # Run create_locator_at_object on the group
        RigUtils.create_locator_at_object(grp)

        # The locator should be at the child's center (~10, 0, 0), NOT at scene root
        loc = "org_group_LOC"
        loc_pos = cmds.xform(loc, q=True, ws=True, t=True)
        self.assertAlmostEqual(
            loc_pos[0], 10.0, places=1,
            msg=f"Locator X should be ~10 (children center), got {loc_pos[0]}"
        )

    def test_create_locator_at_group_with_transforms(self):
        """Verify locator matches position for a group that has non-zero transforms."""
        # Create a group at (5, 10, 0) with a child at (0,0,0) relative
        child = self.create_test_cube("offset_child")
        grp = cmds.group(child, n="offset_group")
        cmds.move(5, 10, 0, grp)

        RigUtils.create_locator_at_object(grp)

        loc = "offset_group_LOC"
        loc_pos = cmds.xform(loc, q=True, ws=True, t=True)
        self.assertAlmostEqual(loc_pos[0], 5.0, places=1)
        self.assertAlmostEqual(loc_pos[1], 10.0, places=1)

    def test_create_locator_at_group_preserves_orientation(self):
        """Verify locator orientation matches the group's manip pivot orientation.

        Bug: For groups, bake_pivot was skipped (correct) but the code only
        read the group's world-transform matrix for orientation, missing any
        custom manipulator-pivot orientation.  The locator ended up with
        identity rotation even when the group had a visible orientation.
        Fixed: 2026-02-23
        """
        # Create a rotated group with a child
        child = self.create_test_cube("rot_child")
        grp = cmds.group(child, n="rotated_group")
        cmds.rotate(0, 45, 0, grp, ws=True)

        # Before calling create_locator_at_object, the group is rotated 45 Y
        grp_rot = cmds.xform(grp, q=True, ws=True, ro=True)
        self.assertAlmostEqual(grp_rot[1], 45.0, places=1)

        RigUtils.create_locator_at_object(grp)

        loc = "rotated_group_LOC"
        # The locator (or its parent GRP) should reflect the 45° Y rotation
        grp_node = "rotated_group_GRP"
        grp_rot_result = cmds.xform(grp_node, q=True, ws=True, ro=True)
        self.assertAlmostEqual(
            grp_rot_result[1], 45.0, places=1,
            msg=f"Locator rig Y rotation should be ~45°, got {grp_rot_result[1]}"
        )

    def test_remove_locator(self):
        """Test remove_locator method."""
        # Setup hierarchy: GRP -> LOC -> CUBE
        grp = cmds.group(em=True, n="test_GRP")
        loc = cmds.spaceLocator(n="test_LOC")[0]
        cmds.parent(loc, grp)
        cmds.parent(self.cube, loc)

        # Test removal
        RigUtils.remove_locator(loc)

        self.assertFalse(cmds.objExists("test_LOC"))
        self.assertTrue(cmds.objExists("test_cube"))
        # Cube should be parented to GRP now
        self.assertEqual((cmds.listRelatives(str(self.cube), parent=True) or [None])[0], grp)

    def test_attr_lock_state(self):
        """Test get_lock_state and set_lock_state via Attributes."""
        # Setup
        cmds.setAttr(f"{self.cube}.tx", lock=True)
        cmds.setAttr(f"{self.cube}.ry", lock=True)

        # Test Get
        state = Attributes.get_lock_state([self.cube])
        cube_state = state[self.cube]
        self.assertTrue(cube_state["tx"])
        self.assertTrue(cube_state["ry"])
        self.assertFalse(cube_state["tz"])

        # Test Unlock via Get
        Attributes.get_lock_state([self.cube], unlock=True)
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))

        # Test Set Bulk
        Attributes.set_lock_state(self.cube, translate=True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.tx", lock=True))
        self.assertTrue(cmds.getAttr(f"{self.cube}.ty", lock=True))
        self.assertTrue(cmds.getAttr(f"{self.cube}.tz", lock=True))

    def test_attr_lock_state_namespaced_duplicates(self):
        """Round-trip lock state for two transforms sharing a leaf name across
        namespaces — mirrors the referenced-module scene structure where
        ``short_name`` keying silently collided.
        """
        cmds.namespace(add="nsA")
        cmds.namespace(add="nsB")
        a = cmds.polyCube(n="nsA:dup_xform")[0]
        b = cmds.polyCube(n="nsB:dup_xform")[0]

        # Distinct lock states: A locks tx, B locks ry — no overlap so a
        # collision-driven overwrite would leave one of them wrongly locked
        # or wrongly unlocked after restore.
        cmds.setAttr(f"{a}.tx", lock=True)
        cmds.setAttr(f"{b}.ry", lock=True)

        with Attributes.temporarily_unlock([a, b]):
            self.assertFalse(cmds.getAttr(f"{a}.tx", lock=True))
            self.assertFalse(cmds.getAttr(f"{b}.ry", lock=True))

        # Original state must be restored independently for each.
        self.assertTrue(cmds.getAttr(f"{a}.tx", lock=True))
        self.assertFalse(cmds.getAttr(f"{a}.ry", lock=True))
        self.assertFalse(cmds.getAttr(f"{b}.tx", lock=True))
        self.assertTrue(cmds.getAttr(f"{b}.ry", lock=True))

    def test_setup_telescope_rig(self):
        """Test setup_telescope_rig method."""
        # Setup
        base = cmds.spaceLocator(n="base_loc")[0]
        end = cmds.spaceLocator(n="end_loc")[0]
        cmds.move(0, 10, 0, end)

        seg1 = cmds.polyCube(n="seg1")[0]
        seg2 = cmds.polyCube(n="seg2")[0]
        seg3 = cmds.polyCube(n="seg3")[0]

        # Test
        rig = TelescopeRig()
        rig.setup_telescope_rig(base, end, [seg1, seg2, seg3])

        # Verify distance node created
        self.assertTrue(cmds.objExists("strut_distance"))

        # Verify constraints
        self.assertTrue(cmds.listConnections(base, type="aimConstraint"))

        # Verify driven keys (scaleY should be connected to animCurve)
        self.assertTrue(cmds.listConnections(f"{seg2}.scaleY", type="animCurve"))

    def test_create_switch(self):
        """Test create_switch method via Attributes."""
        # Test 1: Bool — production returns the plug string
        # ("<node>.<attr>"); query type via cmds.getAttr.
        attr = Attributes.create_switch(self.cube, "mySwitch")
        self.assertTrue(cmds.attributeQuery("mySwitch", node=str(self.cube), exists=True))
        self.assertEqual(cmds.getAttr(str(attr), type=True), "bool")

        # Test 2: Weighted
        attr2 = Attributes.create_switch(self.cube, "myWeight", weighted=True)
        self.assertEqual(cmds.getAttr(str(attr2), type=True), "double")

    def test_connect_switch_to_constraint(self):
        """Test connect_switch_to_constraint method."""
        # Setup
        target1 = cmds.spaceLocator(n="t1")[0]
        target2 = cmds.spaceLocator(n="t2")[0]
        const = cmds.parentConstraint(target1, target2, self.cube)[0]

        # Test 1: Weighted blend (2 targets)
        res = RigUtils.connect_switch_to_constraint(
            const, attr_name="blend_switch", weighted=True
        )
        self.assertIn("reverse_node", res)
        self.assertTrue(cmds.attributeQuery("blend_switch", node=str(self.cube), exists=True))

        # Test 2: Enum switch (add 3rd target)
        target3 = cmds.spaceLocator(n="t3")[0]
        const2 = cmds.parentConstraint(target1, target2, target3, self.sphere)[0]
        res2 = RigUtils.connect_switch_to_constraint(const2, attr_name="enum_switch")
        self.assertEqual(
            cmds.getAttr(f"{self.sphere}.enum_switch", type=True), "enum"
        )
        self.assertIn("condition_node_0", res2)

    def test_joint_chain_ops(self):
        """Test get_joint_chain_from_root and invert_joint_chain."""
        # Setup chain: j1 -> j2 -> j3
        cmds.select(cl=True)
        j1 = cmds.joint(p=(0, 0, 0), n="j1")
        j2 = cmds.joint(p=(0, 1, 0), n="j2")
        j3 = cmds.joint(p=(0, 2, 0), n="j3")

        # Test Get Chain
        chain = RigUtils.get_joint_chain_from_root(j1)
        self.assertEqual(chain, [j1, j2, j3])

        # Test Invert Chain
        inv_chain = RigUtils.invert_joint_chain(j1, keep_original=True)
        self.assertEqual(len(inv_chain), 3)

        root = inv_chain[0]
        # Production returns plain strings — query world translation via cmds.
        root_t = cmds.xform(str(root), q=True, ws=True, t=True)
        j3_t = cmds.xform(str(j3), q=True, ws=True, t=True)
        for a, b in zip(root_t, j3_t):
            self.assertAlmostEqual(a, b, places=4)

    def test_rebind_skin_clusters(self):
        """Test rebind_skin_clusters method."""
        # Setup skinned mesh
        cmds.select(cl=True)
        j1 = cmds.joint(p=(0, -1, 0))
        j2 = cmds.joint(p=(0, 1, 0))
        skin = cmds.skinCluster(j1, j2, self.cube)[0]
        # Compare via UUID — rebind preserves the cluster's *name* but
        # produces a new underlying node. PyNode equality on same-name
        # nodes can re-resolve and falsely pass the assertion.
        old_uuid = cmds.ls(str(skin), uuid=True)[0]

        # Test
        RigUtils.rebind_skin_clusters([self.cube])

        # Verify skinCluster still exists (it's a new one, but exists)
        new_skin = cmds.ls(cmds.listHistory(self.cube), type="skinCluster")
        self.assertTrue(new_skin)
        new_uuid = cmds.ls(str(new_skin[0]), uuid=True)[0]
        self.assertNotEqual(new_uuid, old_uuid)  # Should be a new node


if __name__ == "__main__":
    unittest.main(verbosity=2)
