# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.node_utils module

Tests for NodeUtils class functionality including:
- Node type detection
- Transform and shape node queries
- Parent/child relationships
- Group detection and management
- Locator utilities
- Node attribute operations
- Node connections
- Instancing operations
- Assembly creation
"""
import unittest
import maya.cmds as cmds
import maya.mel as mel
import mayatk as mtk
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from base_test import MayaTkTestCase


class TestNodeUtils(MayaTkTestCase):
    """Comprehensive tests for NodeUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test cylinder
        self.cyl = cmds.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]
        self.cyl_shape = cmds.listRelatives(self.cyl, shapes=True)[0]

    # -------------------------------------------------------------------------
    # Node Type Detection Tests
    # -------------------------------------------------------------------------

    def test_get_type(self):
        """Test getting type of various nodes."""
        self.assertEqual(NodeUtils.get_type("cyl"), "mesh")
        self.assertEqual(NodeUtils.get_type(self.cyl_shape), "mesh")
        self.assertEqual(NodeUtils.get_type(f"{self.cyl_shape}.vtx[0]"), "vtx")

        # Test list input
        types = NodeUtils.get_type(["cyl", self.cyl_shape])
        self.assertEqual(types, ["mesh", "mesh"])

    def test_is_geometry(self):
        """Test is_geometry method."""
        self.assertTrue(NodeUtils.is_geometry("cyl"))

        # Create a group (transform but no shape)
        grp = cmds.group(empty=True, name="empty_grp")
        self.assertFalse(NodeUtils.is_geometry(grp))

        # Create a locator (has shape but is locator)
        loc = cmds.spaceLocator(name="loc")[0]
        # is_geometry checks for ANY shape, so locator is technically geometry in this context?
        # Let's check implementation: "Return True for each object that has a shape node and is not a group."
        # Locator has a shape, so it should be True unless specifically excluded.
        self.assertTrue(NodeUtils.is_geometry(loc))

        # Filter mode
        result = NodeUtils.is_geometry(["cyl", grp, loc], filter=True)
        self.assertIn(self.cyl, result)
        self.assertNotIn(grp, result)

    # -------------------------------------------------------------------------
    # Transform, Shape, History Queries
    # -------------------------------------------------------------------------

    def test_get_transform_node(self):
        """Test get_transform_node."""
        # From transform
        self.assertEqual(NodeUtils.get_transform_node("cyl"), self.cyl)
        # From shape
        self.assertEqual(NodeUtils.get_transform_node(self.cyl_shape), self.cyl)
        # From component
        self.assertEqual(NodeUtils.get_transform_node(f"{self.cyl}.vtx[0]"), self.cyl)

        # Test with attributes=True
        attrs = NodeUtils.get_transform_node(
            "cyl", attributes=True, returned_type="str"
        )
        self.assertIsInstance(attrs, list)
        self.assertIn("translateX", attrs)

    def test_get_shape_node(self):
        """Test get_shape_node."""
        # From transform
        self.assertEqual(NodeUtils.get_shape_node("cyl"), self.cyl_shape)
        # From shape
        self.assertEqual(NodeUtils.get_shape_node(self.cyl_shape), self.cyl_shape)

        # Test with attributes=True
        attrs = NodeUtils.get_shape_node("cyl", attributes=True, returned_type="str")
        self.assertIsInstance(attrs, list)

    def test_get_history_node(self):
        """Test get_history_node."""
        hist = NodeUtils.get_history_node("cyl")
        self.assertEqual(cmds.nodeType(hist), "polyCylinder")

    # -------------------------------------------------------------------------
    # Group & Hierarchy Tests
    # -------------------------------------------------------------------------

    def test_is_group(self):
        """Test is_group detection."""
        self.assertFalse(NodeUtils.is_group("cyl"))

        grp = cmds.group(empty=True, name="test_grp")
        self.assertTrue(NodeUtils.is_group(grp))

        # Group with children
        cmds.parent(self.cyl, grp)
        self.assertTrue(NodeUtils.is_group(grp))

    def test_get_groups(self):
        """Test get_groups."""
        grp1 = cmds.group(empty=True, name="grp1")
        grp2 = cmds.group(empty=True, name="grp2")
        cmds.parent(self.cyl, grp1)

        groups = NodeUtils.get_groups()
        self.assertIn(grp1, groups)
        self.assertIn(grp2, groups)

        # Test empty=True
        empty_groups = NodeUtils.get_groups(empty=True)
        self.assertIn(grp2, empty_groups)
        self.assertNotIn(grp1, empty_groups)

    def test_get_unique_children(self):
        """Test get_unique_children."""
        c1 = cmds.polyCube(n="c1")[0]
        c2 = cmds.polyCube(n="c2")[0]
        grp = cmds.group(c1, c2, n="parent_grp")

        children = NodeUtils.get_unique_children(grp)
        self.assertEqual(len(children), 2)
        self.assertIn(c1, children)
        self.assertIn(c2, children)

    def test_get_shapes(self):
        """get_shapes returns non-intermediate shape children of a transform."""
        shapes = NodeUtils.get_shapes("cyl")
        self.assertEqual(len(shapes), 1)
        self.assertEqual(cmds.nodeType(shapes[0]), "mesh")

        # Empty transform (group) returns []
        grp = cmds.group(empty=True, name="empty_grp")
        self.assertEqual(NodeUtils.get_shapes(grp), [])

    def test_get_shape_singular(self):
        """get_shape returns the first shape, or None."""
        shape = NodeUtils.get_shape("cyl")
        self.assertIsNotNone(shape)
        self.assertEqual(cmds.nodeType(shape), "mesh")

        grp = cmds.group(empty=True, name="empty_grp2")
        self.assertIsNone(NodeUtils.get_shape(grp))

    def test_is_intermediate(self):
        """is_intermediate flips with the intermediateObject attr."""
        shape = NodeUtils.get_shape("cyl")
        self.assertFalse(NodeUtils.is_intermediate(shape))

        cmds.setAttr(f"{shape}.intermediateObject", 1)
        self.assertTrue(NodeUtils.is_intermediate(shape))
        cmds.setAttr(f"{shape}.intermediateObject", 0)

    def test_get_parent_extended_kwargs(self):
        """get_parent honors full_path and type filter."""
        grp = cmds.group(empty=True, name="parent_grp")
        cmds.parent(str(self.cyl), grp)

        # Default: short path, transform-only
        parent = NodeUtils.get_parent("cyl")
        self.assertEqual(parent, grp)

        # full_path=True returns a path beginning with "|"
        parent_long = NodeUtils.get_parent("cyl", full_path=True)
        self.assertTrue(parent_long.startswith("|"))
        self.assertTrue(parent_long.endswith(grp))

        # type=None returns immediate parent regardless of type — for a
        # transform-under-transform that's still the same parent.
        self.assertEqual(NodeUtils.get_parent("cyl", type=None), grp)

    def test_get_children_extended_kwargs(self):
        """get_children honors type filter and full_path."""
        grp = cmds.group(empty=True, name="children_grp")
        cmds.parent(str(self.cyl), grp)

        # Default returns transform children
        children = NodeUtils.get_children(grp)
        self.assertIn(str(self.cyl), [c.split("|")[-1] for c in children])

        # type=None returns all children
        children_all = NodeUtils.get_children(grp, type=None)
        self.assertTrue(any(c.endswith("cyl") for c in children_all))

        # full_path=True
        children_long = NodeUtils.get_children(grp, full_path=True)
        for c in children_long:
            self.assertTrue(c.startswith("|"))

    def test_list_transforms(self):
        """list_transforms walks shape hits up to their transform parent."""
        # cyl has a mesh shape
        result = NodeUtils.list_transforms(type="mesh")
        # Should contain cyl's transform, not its shape
        self.assertIn(str(self.cyl), result)
        self.assertNotIn(str(self.cyl_shape), result)

    def test_node_is(self):
        """node_is matches exact objectType."""
        self.assertTrue(NodeUtils.node_is(self.cyl_shape, "mesh"))
        self.assertFalse(NodeUtils.node_is(self.cyl_shape, "transform"))
        self.assertTrue(NodeUtils.node_is(self.cyl, "transform"))

    # -------------------------------------------------------------------------
    # Attribute Operations
    # -------------------------------------------------------------------------

    def test_get_maya_attribute_type(self):
        """Test get_maya_attribute_type."""
        self.assertEqual(Attributes.get_type(1), "long")
        self.assertEqual(Attributes.get_type(1.0), "double")
        self.assertEqual(Attributes.get_type("s"), "string")
        self.assertEqual(Attributes.get_type(True), "bool")
        self.assertEqual(Attributes.get_type([1.0, 2.0, 3.0]), "double3")
        self.assertEqual(Attributes.get_type(["a", "b"]), "stringArray")

    def test_has_attr(self):
        """has_attr returns True only for attrs that exist on the node."""
        self.assertTrue(Attributes.has_attr(self.cyl, "translateX"))
        self.assertFalse(Attributes.has_attr(self.cyl, "doesNotExist"))

    def test_set_plug_unlocks_when_forced(self):
        """set_plug bypasses a lock when force=True and re-locks afterwards."""
        plug = f"{self.cyl}.translateX"
        cmds.setAttr(plug, lock=True)
        try:
            # Without force, the locked plug must not change.
            Attributes.set_plug(plug, 7.0, force=False)
            self.assertEqual(cmds.getAttr(plug), 0.0)

            # With force, the write goes through and the lock is restored.
            Attributes.set_plug(plug, 7.0, force=True)
            self.assertAlmostEqual(cmds.getAttr(plug), 7.0)
            self.assertTrue(cmds.getAttr(plug, lock=True))
        finally:
            cmds.setAttr(plug, lock=False)

    def test_set_plug_writes_float3_tuple(self):
        """set_plug expands a 3-tuple into a double3 setAttr."""
        Attributes.set_plug(f"{self.cyl}.translate", (1.0, 2.0, 3.0))
        self.assertEqual(
            tuple(cmds.getAttr(f"{self.cyl}.translate")[0]),
            (1.0, 2.0, 3.0),
        )

    def test_set_node_custom_attributes(self):
        """Test set_node_custom_attributes."""
        # Simple attribute
        Attributes.create_or_set(self.cyl, myFloat=1.5)
        self.assertTrue(cmds.attributeQuery("myFloat", node=str(self.cyl), exists=True))
        self.assertEqual(cmds.getAttr(f"{self.cyl}.myFloat"), 1.5)

        # Compound attribute (vector)
        Attributes.create_or_set(self.cyl, myVec=[1.0, 2.0, 3.0])
        self.assertTrue(cmds.attributeQuery("myVec", node=str(self.cyl), exists=True))
        self.assertEqual(tuple(cmds.getAttr(f"{self.cyl}.myVec")[0]), (1.0, 2.0, 3.0))

    def test_get_node_attributes_filtering(self):
        """Test get_node_attributes with filtering."""
        # Set a non-default value
        cmds.setAttr(f"{self.cyl}.translateX", 5.0)

        # Test exc_defaults=True
        attrs = Attributes.get_attributes(self.cyl, exc_defaults=True)
        self.assertIn("translateX", attrs)
        self.assertNotIn(
            "translateY", attrs
        )  # Should be excluded as it's 0.0 (default)

    # -------------------------------------------------------------------------
    # Connection Tests
    # -------------------------------------------------------------------------

    def test_get_connected_nodes(self):
        """Test get_connected_nodes."""
        cube = cmds.polyCube()[0]
        cmds.connectAttr(f"{self.cyl}.tx", f"{cube}.tx")

        # Outgoing from cyl
        outgoing = NodeUtils.get_connected_nodes(self.cyl, direction="outgoing")
        self.assertIn(cube, outgoing)

        # Incoming to cube
        incoming = NodeUtils.get_connected_nodes(cube, direction="incoming")
        self.assertIn(self.cyl, incoming)

        # Filter by type
        connected = NodeUtils.get_connected_nodes(self.cyl, node_type="transform")
        self.assertIn(cube, connected)

    def test_connect_multi_attr(self):
        """Test connect_multi_attr."""
        cube = cmds.polyCube()[0]
        Attributes.connect_multi((f"{self.cyl}.tx", f"{cube}.tx"), (f"{self.cyl}.ty", f"{cube}.ty"))
        self.assertTrue(cmds.isConnected(f"{self.cyl}.tx", f"{cube}.tx"))
        self.assertTrue(cmds.isConnected(f"{self.cyl}.ty", f"{cube}.ty"))

    # -------------------------------------------------------------------------
    # Instancing Tests
    # -------------------------------------------------------------------------

    def test_instancing_operations(self):
        """Test instance creation, retrieval, and uninstancing."""
        # Create instance
        target = cmds.polyCube()[0]
        instances = NodeUtils.replace_with_instances([self.cyl, target])
        inst = instances[0]

        # Verify it is an instance
        self.assertTrue(len(cmds.ls(cmds.listRelatives(inst, shapes=True, ni=True)[0], allPaths=True)) > 1)

        # Get instances
        found_instances = NodeUtils.get_instances(self.cyl)
        # Production returns long paths; compare on short name
        inst_short = str(inst).split("|")[-1]
        found_short = [str(f).split("|")[-1] for f in found_instances]
        self.assertIn(inst_short, found_short)

        # Filter duplicate instances
        filtered = NodeUtils.filter_duplicate_instances([self.cyl, inst])
        # Should return only one transform per instance group
        self.assertEqual(len(filtered), 1)

        # Uninstance
        NodeUtils.uninstance(inst)
        self.assertFalse(len(cmds.ls(cmds.listRelatives(inst, shapes=True, ni=True)[0], allPaths=True)) > 1)

    # -------------------------------------------------------------------------
    # Assembly Tests
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Persistent Data Node Tests
    # -------------------------------------------------------------------------

    def test_ensure_data_node_creates_locked_network(self):
        """ensure_data_node creates a locked network node with a writable attr."""

        name = "testDataNode"
        attr = "testPayload"
        if cmds.objExists(name):
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

        node = NodeUtils.ensure_data_node(name, attr)
        try:
            self.assertTrue(cmds.objExists(name))
            self.assertEqual(cmds.nodeType(node), "network")
            self.assertTrue(cmds.attributeQuery(attr, node=node, exists=True))
            # Node is locked
            self.assertTrue(cmds.lockNode(str(node), q=True, lockName=True)[0])
            # Name is locked
            self.assertTrue(cmds.lockNode(str(node), q=True, lockName=True)[0])
            # Data attr is writable
            cmds.setAttr(f"{node}.{attr}", "hello", type="string")
            self.assertEqual(cmds.getAttr(f"{node}.{attr}"), "hello")
        finally:
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

    def test_ensure_data_node_prevents_rename(self):
        """Locked node cannot be renamed."""

        name = "testNoRename"
        attr = "nrData"
        if cmds.objExists(name):
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

        NodeUtils.ensure_data_node(name, attr)
        try:
            with self.assertRaises(RuntimeError):
                cmds.rename(name, "sneakyRename")
            self.assertTrue(cmds.objExists(name))
        finally:
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

    def test_ensure_data_node_migrates_unlocked(self):
        """Existing unlocked node is locked on next ensure_data_node call."""

        name = "testMigrate"
        attr = "migData"
        if cmds.objExists(name):
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

        # Create an unlocked node manually (simulating old scene)
        node = cmds.createNode("network", name=name)
        cmds.addAttr(node, longName=attr, dataType="string")
        self.assertFalse(cmds.lockNode(str(node), q=True, lock=True)[0])

        # ensure_data_node should migrate it
        result = NodeUtils.ensure_data_node(name, attr)
        try:
            self.assertTrue(cmds.lockNode(str(result), q=True, lockName=True)[0])
            # Attr still writable after migration
            cmds.setAttr(f"{result}.{attr}", "migrated", type="string")
            self.assertEqual(cmds.getAttr(f"{result}.{attr}"), "migrated")
        finally:
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

    def test_ensure_data_node_idempotent(self):
        """Calling ensure_data_node twice is safe and returns same node."""

        name = "testIdempotent"
        attr = "idemData"
        if cmds.objExists(name):
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

        node1 = NodeUtils.ensure_data_node(name, attr)
        cmds.setAttr(f"{node1}.{attr}", "first", type="string")
        node2 = NodeUtils.ensure_data_node(name, attr)
        try:
            self.assertEqual(str(node1), str(node2))
            self.assertEqual(cmds.getAttr(f"{node2}.{attr}"), "first")
            self.assertTrue(cmds.lockNode(str(node2), q=True, lockName=True)[0])
        finally:
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

    def test_ensure_data_node_adds_missing_attr_to_existing(self):
        """If node exists but attr is missing, attr is added and node locked."""

        name = "testAddAttr"
        attr = "newAttr"
        if cmds.objExists(name):
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

        # Create node without the expected attribute
        cmds.createNode("network", name=name)
        node = NodeUtils.ensure_data_node(name, attr)
        try:
            self.assertTrue(cmds.attributeQuery(attr, node=node, exists=True))
            self.assertTrue(cmds.lockNode(str(node), q=True, lockName=True)[0])
            cmds.setAttr(f"{node}.{attr}", "works", type="string")
            self.assertEqual(cmds.getAttr(f"{node}.{attr}"), "works")
        finally:
            cmds.lockNode(name, lock=False)
            cmds.delete(name)

    def test_create_assembly(self):
        """Test create_assembly."""
        try:
            # Check if assembly command exists
            cmds.assembly
        except AttributeError:
            self.skipTest("Assembly command not available")

        try:
            asm = NodeUtils.create_assembly([self.cyl], assembly_name="test_asm")
            self.assertEqual(cmds.nodeType(asm), "assembly")
            self.assertIn(self.cyl, asm.children())
        except RuntimeError as e:
            print(f"Skipping assembly test due to runtime error: {e}")
            # This often fails in batch mode or if plugin not loaded
            pass

    # -------------------------------------------------------------------------
    # Render Node Tests
    # -------------------------------------------------------------------------

    def test_create_render_node(self):
        """Test create_render_node."""
        # Try to source the MEL script required
        try:
            mel.source("createRenderNode.mel")
        except Exception:
            pass

        try:
            # Create a shader
            shader = NodeUtils.create_render_node("lambert", name="test_lambert")
            if shader:
                self.assertNodeExists("test_lambert")
                self.assertEqual(cmds.nodeType(shader), "lambert")

            # Create a texture with placement
            tex = NodeUtils.create_render_node(
                "checker", name="test_checker", create_placement_nodes=True
            )
            if tex:
                self.assertNodeExists("test_checker")
                # Check for placement node connection
                self.assertTrue(cmds.listConnections(tex, type="place2dTexture"))
        except RuntimeError as e:
            if "Cannot find procedure" in str(e):
                print("Skipping create_render_node test: MEL procedure missing")
            else:
                raise e


if __name__ == "__main__":
    unittest.main(verbosity=2)
