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
import pymel.core as pm

# Handle lazy loading of RigUtils
try:
    import mayatk as mtk
    from mayatk.rig_utils._rig_utils import RigUtils
except ImportError:
    import sys
    import os

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import mayatk as mtk
    from mayatk.rig_utils._rig_utils import RigUtils

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
        self.assertTrue(isinstance(loc, pm.nt.Transform))

        # Test 2: Create joint
        jnt = RigUtils.create_helper("helper_jnt", helper_type="joint")
        self.assertNodeExists("helper_jnt")
        self.assertEqual(jnt.nodeType(), "joint")

        # Test 3: Create with parent
        child = RigUtils.create_helper("child_helper", parent=self.cube)
        self.assertEqual(child.getParent(), self.cube)

        # Test 4: Cleanup existing
        RigUtils.create_helper("cleanup_me")
        result = RigUtils.create_helper("cleanup_me", cleanup=True)
        self.assertFalse(pm.objExists("cleanup_me"))
        self.assertIsNone(result)

    def test_create_group(self):
        """Test create_group method."""
        # Test 1: Empty group
        grp = RigUtils.create_group(name="empty_grp")
        self.assertNodeExists("empty_grp")
        self.assertEqual(len(grp.getChildren()), 0)

        # Test 2: Group with objects
        grp2 = RigUtils.create_group(objects=[self.cube, self.sphere], name="obj_grp")
        self.assertNodeExists("obj_grp")
        self.assertIn(self.cube, grp2.getChildren())
        self.assertIn(self.sphere, grp2.getChildren())

        # Test 3: Zero transforms
        # Move cube first
        pm.move(self.cube, 10, 10, 10)
        grp3 = RigUtils.create_group(
            objects=[self.cube], name="zero_grp", zero_translation=True
        )
        self.assertEqual(grp3.tx.get(), 0)
        self.assertEqual(grp3.ty.get(), 0)
        self.assertEqual(grp3.tz.get(), 0)

    def test_create_locator(self):
        """Test create_locator method."""
        # Test 1: Basic creation
        loc = RigUtils.create_locator(name="basic_loc")
        self.assertNodeExists("basic_loc")

        # Test 2: Scale
        loc_scaled = RigUtils.create_locator(name="scaled_loc", scale=5.0)
        self.assertEqual(loc_scaled.scaleX.get(), 5.0)

        # Test 3: Position from object
        pm.move(self.cube, 5, 5, 5)
        loc_pos = RigUtils.create_locator(name="pos_loc", position=self.cube)
        pos = pm.xform(loc_pos, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 5.0)

    def test_create_locator_at_object(self):
        """Test create_locator_at_object method."""
        # Setup
        pm.move(self.cube, 10, 0, 0)

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

        grp = pm.PyNode(grp_name)
        loc = pm.PyNode(loc_name)
        geo = pm.PyNode(geo_name)

        self.assertEqual(loc.getParent(), grp)
        self.assertEqual(geo.getParent(), loc)

        # Verify positions match
        self.assertAlmostEqual(loc.getTranslation(space="world")[0], 10.0)

    def test_remove_locator(self):
        """Test remove_locator method."""
        # Setup hierarchy: GRP -> LOC -> CUBE
        grp = pm.group(em=True, n="test_GRP")
        loc = pm.spaceLocator(n="test_LOC")
        pm.parent(loc, grp)
        pm.parent(self.cube, loc)

        # Test removal
        RigUtils.remove_locator(loc)

        self.assertFalse(pm.objExists("test_LOC"))
        self.assertTrue(pm.objExists("test_cube"))
        # Cube should be parented to GRP now
        self.assertEqual(self.cube.getParent(), grp)

    def test_attr_lock_state(self):
        """Test get_attr_lock_state and set_attr_lock_state."""
        # Setup
        pm.setAttr(self.cube.tx, lock=True)
        pm.setAttr(self.cube.ry, lock=True)

        # Test Get
        state = RigUtils.get_attr_lock_state([self.cube])
        cube_state = state[self.cube.name()]
        self.assertTrue(cube_state["tx"])
        self.assertTrue(cube_state["ry"])
        self.assertFalse(cube_state["tz"])

        # Test Unlock via Get
        RigUtils.get_attr_lock_state([self.cube], unlock=True)
        self.assertFalse(pm.getAttr(self.cube.tx, lock=True))

        # Test Set Bulk
        RigUtils.set_attr_lock_state(self.cube, translate=True)
        self.assertTrue(pm.getAttr(self.cube.tx, lock=True))
        self.assertTrue(pm.getAttr(self.cube.ty, lock=True))
        self.assertTrue(pm.getAttr(self.cube.tz, lock=True))

    def test_setup_telescope_rig(self):
        """Test setup_telescope_rig method."""
        # Setup
        base = pm.spaceLocator(n="base_loc")
        end = pm.spaceLocator(n="end_loc")
        pm.move(end, 0, 10, 0)

        seg1 = pm.polyCube(n="seg1")[0]
        seg2 = pm.polyCube(n="seg2")[0]
        seg3 = pm.polyCube(n="seg3")[0]

        # Test
        RigUtils.setup_telescope_rig(base, end, [seg1, seg2, seg3])

        # Verify distance node created
        self.assertTrue(pm.objExists("strut_distance"))

        # Verify constraints
        self.assertTrue(pm.listConnections(base, type="aimConstraint"))

        # Verify driven keys (scaleY should be connected to animCurve)
        self.assertTrue(pm.listConnections(seg2.scaleY, type="animCurve"))

    def test_create_switch_attr(self):
        """Test create_switch_attr method."""
        # Test 1: Bool
        attr = RigUtils.create_switch_attr(self.cube, "mySwitch")
        self.assertTrue(self.cube.hasAttr("mySwitch"))
        self.assertEqual(attr.type(), "bool")

        # Test 2: Weighted
        attr2 = RigUtils.create_switch_attr(self.cube, "myWeight", weighted=True)
        self.assertEqual(attr2.type(), "double")

    def test_connect_switch_to_constraint(self):
        """Test connect_switch_to_constraint method."""
        # Setup
        target1 = pm.spaceLocator(n="t1")
        target2 = pm.spaceLocator(n="t2")
        const = pm.parentConstraint(target1, target2, self.cube)

        # Test 1: Weighted blend (2 targets)
        res = RigUtils.connect_switch_to_constraint(
            const, attr_name="blend_switch", weighted=True
        )
        self.assertIn("reverse_node", res)
        self.assertTrue(self.cube.hasAttr("blend_switch"))

        # Test 2: Enum switch (add 3rd target)
        target3 = pm.spaceLocator(n="t3")
        const2 = pm.parentConstraint(target1, target2, target3, self.sphere)
        res2 = RigUtils.connect_switch_to_constraint(const2, attr_name="enum_switch")
        self.assertEqual(self.sphere.attr("enum_switch").type(), "enum")
        self.assertIn("condition_node_0", res2)

    def test_joint_chain_ops(self):
        """Test get_joint_chain_from_root and invert_joint_chain."""
        # Setup chain: j1 -> j2 -> j3
        pm.select(cl=True)
        j1 = pm.joint(p=(0, 0, 0), n="j1")
        j2 = pm.joint(p=(0, 1, 0), n="j2")
        j3 = pm.joint(p=(0, 2, 0), n="j3")

        # Test Get Chain
        chain = RigUtils.get_joint_chain_from_root(j1)
        self.assertEqual(chain, [j1, j2, j3])

        # Test Invert Chain
        inv_chain = RigUtils.invert_joint_chain(j1, keep_original=True)
        self.assertEqual(len(inv_chain), 3)

        root = inv_chain[0]
        self.assertEqual(
            root.getTranslation(space="world"), j3.getTranslation(space="world")
        )

    def test_rebind_skin_clusters(self):
        """Test rebind_skin_clusters method."""
        # Setup skinned mesh
        pm.select(cl=True)
        j1 = pm.joint(p=(0, -1, 0))
        j2 = pm.joint(p=(0, 1, 0))
        skin = pm.skinCluster(j1, j2, self.cube)

        # Test
        RigUtils.rebind_skin_clusters([self.cube])

        # Verify skinCluster still exists (it's a new one, but exists)
        new_skin = pm.listHistory(self.cube, type="skinCluster")
        self.assertTrue(new_skin)
        self.assertNotEqual(new_skin[0], skin)  # Should be a new node


if __name__ == "__main__":
    unittest.main(verbosity=2)
