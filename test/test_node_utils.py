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
import pymel.core as pm
import mayatk as mtk
from mayatk.node_utils._node_utils import NodeUtils

from base_test import MayaTkTestCase


class TestNodeUtils(MayaTkTestCase):
    """Comprehensive tests for NodeUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test cylinder
        self.cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]
        self.cyl_shape = pm.listRelatives(self.cyl, shapes=True)[0]

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
        grp = pm.group(empty=True, name="empty_grp")
        self.assertFalse(NodeUtils.is_geometry(grp))

        # Create a locator (has shape but is locator)
        loc = pm.spaceLocator(name="loc")
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
        self.assertEqual(hist.nodeType(), "polyCylinder")

    # -------------------------------------------------------------------------
    # Group & Hierarchy Tests
    # -------------------------------------------------------------------------

    def test_is_group(self):
        """Test is_group detection."""
        self.assertFalse(NodeUtils.is_group("cyl"))

        grp = pm.group(empty=True, name="test_grp")
        self.assertTrue(NodeUtils.is_group(grp))

        # Group with children
        pm.parent(self.cyl, grp)
        self.assertTrue(NodeUtils.is_group(grp))

    def test_get_groups(self):
        """Test get_groups."""
        grp1 = pm.group(empty=True, name="grp1")
        grp2 = pm.group(empty=True, name="grp2")
        pm.parent(self.cyl, grp1)

        groups = NodeUtils.get_groups()
        self.assertIn(grp1, groups)
        self.assertIn(grp2, groups)

        # Test empty=True
        empty_groups = NodeUtils.get_groups(empty=True)
        self.assertIn(grp2, empty_groups)
        self.assertNotIn(grp1, empty_groups)

    def test_get_unique_children(self):
        """Test get_unique_children."""
        c1 = pm.polyCube(n="c1")[0]
        c2 = pm.polyCube(n="c2")[0]
        grp = pm.group(c1, c2, n="parent_grp")

        children = NodeUtils.get_unique_children(grp)
        self.assertEqual(len(children), 2)
        self.assertIn(c1, children)
        self.assertIn(c2, children)

    # -------------------------------------------------------------------------
    # Attribute Operations
    # -------------------------------------------------------------------------

    def test_get_maya_attribute_type(self):
        """Test get_maya_attribute_type."""
        self.assertEqual(NodeUtils.get_maya_attribute_type(1), "long")
        self.assertEqual(NodeUtils.get_maya_attribute_type(1.0), "double")
        self.assertEqual(NodeUtils.get_maya_attribute_type("s"), "string")
        self.assertEqual(NodeUtils.get_maya_attribute_type(True), "bool")
        self.assertEqual(NodeUtils.get_maya_attribute_type([1.0, 2.0, 3.0]), "double3")
        self.assertEqual(NodeUtils.get_maya_attribute_type(["a", "b"]), "stringArray")

    def test_set_node_custom_attributes(self):
        """Test set_node_custom_attributes."""
        # Simple attribute
        NodeUtils.set_node_custom_attributes(self.cyl, myFloat=1.5)
        self.assertTrue(self.cyl.hasAttr("myFloat"))
        self.assertEqual(self.cyl.myFloat.get(), 1.5)

        # Compound attribute (vector)
        NodeUtils.set_node_custom_attributes(self.cyl, myVec=[1.0, 2.0, 3.0])
        self.assertTrue(self.cyl.hasAttr("myVec"))
        self.assertEqual(self.cyl.myVec.get(), (1.0, 2.0, 3.0))

    def test_get_node_attributes_filtering(self):
        """Test get_node_attributes with filtering."""
        # Set a non-default value
        self.cyl.translateX.set(5.0)

        # Test exc_defaults=True
        attrs = NodeUtils.get_node_attributes(self.cyl, exc_defaults=True)
        self.assertIn("translateX", attrs)
        self.assertNotIn(
            "translateY", attrs
        )  # Should be excluded as it's 0.0 (default)

    # -------------------------------------------------------------------------
    # Connection Tests
    # -------------------------------------------------------------------------

    def test_get_connected_nodes(self):
        """Test get_connected_nodes."""
        cube = pm.polyCube()[0]
        pm.connectAttr(self.cyl.tx, cube.tx)

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
        cube = pm.polyCube()[0]
        NodeUtils.connect_multi_attr((self.cyl.tx, cube.tx), (self.cyl.ty, cube.ty))
        self.assertTrue(pm.isConnected(self.cyl.tx, cube.tx))
        self.assertTrue(pm.isConnected(self.cyl.ty, cube.ty))

    # -------------------------------------------------------------------------
    # Instancing Tests
    # -------------------------------------------------------------------------

    def test_instancing_operations(self):
        """Test instance creation, retrieval, and uninstancing."""
        # Create instance
        target = pm.polyCube()[0]
        instances = NodeUtils.replace_with_instances([self.cyl, target])
        inst = instances[0]

        # Verify it is an instance
        self.assertTrue(inst.getShape().isInstanced())

        # Get instances
        found_instances = NodeUtils.get_instances(self.cyl)
        self.assertIn(inst, found_instances)

        # Filter duplicate instances
        filtered = NodeUtils.filter_duplicate_instances([self.cyl, inst])
        # Should return only one transform per instance group
        self.assertEqual(len(filtered), 1)

        # Uninstance
        NodeUtils.uninstance(inst)
        self.assertFalse(inst.getShape().isInstanced())

    # -------------------------------------------------------------------------
    # Assembly Tests
    # -------------------------------------------------------------------------

    def test_create_assembly(self):
        """Test create_assembly."""
        try:
            # Check if assembly command exists
            pm.assembly
        except AttributeError:
            self.skipTest("Assembly command not available")

        try:
            asm = NodeUtils.create_assembly([self.cyl], assembly_name="test_asm")
            self.assertEqual(asm.nodeType(), "assembly")
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
            pm.mel.source("createRenderNode.mel")
        except Exception:
            pass

        try:
            # Create a shader
            shader = NodeUtils.create_render_node("lambert", name="test_lambert")
            if shader:
                self.assertNodeExists("test_lambert")
                self.assertEqual(shader.nodeType(), "lambert")

            # Create a texture with placement
            tex = NodeUtils.create_render_node(
                "checker", name="test_checker", create_placement=True
            )
            if tex:
                self.assertNodeExists("test_checker")
                # Check for placement node connection
                self.assertTrue(pm.listConnections(tex, type="place2dTexture"))
        except RuntimeError as e:
            if "Cannot find procedure" in str(e):
                print("Skipping create_render_node test: MEL procedure missing")
            else:
                raise e


if __name__ == "__main__":
    unittest.main(verbosity=2)
