# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils module

Tests for MatUtils class functionality including:
- Material querying and assignment
- Scene material management
- Material creation
- Material ID operations
- Shading group operations
- File node and texture path operations
"""
import os
import unittest
import pymel.core as pm
import mayatk as mtk
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.node_utils._node_utils import NodeUtils

from base_test import MayaTkTestCase


class TestMatUtils(MayaTkTestCase):
    """Comprehensive tests for MatUtils class."""

    def setUp(self):
        """Set up test scene with geometries and materials."""
        super().setUp()
        # Create test geometries
        self.sphere = pm.polySphere(name="test_sphere")[0]
        self.cube = pm.polyCube(name="test_cube")[0]

        # Create test materials
        self.lambert1 = pm.shadingNode("lambert", asShader=True, name="test_lambert1")
        self.lambert2 = pm.shadingNode("lambert", asShader=True, name="test_lambert2")

        # Create shading groups
        self.sg1 = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_sg1"
        )
        self.lambert1.outColor.connect(self.sg1.surfaceShader)

        self.sg2 = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="test_sg2"
        )
        self.lambert2.outColor.connect(self.sg2.surfaceShader)

    def tearDown(self):
        """Clean up test materials and geometry."""
        super().tearDown()

    # -------------------------------------------------------------------------
    # Material Query Tests
    # -------------------------------------------------------------------------

    def test_get_mats_from_object(self):
        """Test getting materials assigned to an object."""
        pm.sets(self.sg1, forceElement=self.sphere)
        mats = MatUtils.get_mats(self.sphere)
        self.assertIn(self.lambert1, mats)

    def test_get_mats_from_face(self):
        """Test getting materials from a face component."""
        # Assign to face explicitly to ensure component-level assignment is tested
        pm.sets(self.sg1, forceElement=self.sphere.f[0])
        face = self.sphere.f[0]
        face_mats = MatUtils.get_mats(face)
        self.assertIn(self.lambert1, face_mats)

    def test_get_mats_with_no_assignment(self):
        """Test getting materials from object with only default material."""
        # Cube has initialShadingGroup by default
        mats = MatUtils.get_mats(self.cube)
        self.assertTrue(len(mats) > 0)
        # Check that we got a valid material node
        self.assertTrue(isinstance(mats[0], pm.nt.ShadingDependNode))

    def test_get_scene_mats(self):
        """Test getting all materials in the scene."""
        scene_mats = MatUtils.get_scene_mats()
        self.assertIn(self.lambert1, scene_mats)
        self.assertIn(self.lambert2, scene_mats)

        # Test filtering
        filtered_mats = MatUtils.get_scene_mats(inc=["*lambert1*"])
        self.assertIn(self.lambert1, filtered_mats)
        self.assertNotIn(self.lambert2, filtered_mats)

    def test_get_fav_mats(self):
        """Test getting favorite materials."""
        try:
            fav_mats = MatUtils.get_fav_mats()
            self.assertIsInstance(fav_mats, (list, tuple))
        except (AttributeError, NotImplementedError, ImportError):
            self.skipTest("get_fav_mats not implemented or unavailable")

    # -------------------------------------------------------------------------
    # Material Creation & Assignment Tests
    # -------------------------------------------------------------------------

    def test_create_mat_random(self):
        """Test creating a random material type."""
        random_mat = MatUtils.create_mat(mat_type="random", name="random_mat")
        self.assertTrue(pm.objExists(random_mat))
        self.assertTrue(random_mat.name().startswith("random_mat"))

    def test_create_mat_specific(self):
        """Test creating specific material types."""
        blinn = MatUtils.create_mat("blinn", name="test_blinn")
        self.assertEqual(pm.nodeType(blinn), "blinn")

        # Test standardSurface if available (Maya 2020+)
        try:
            std = MatUtils.create_mat("standardSurface", name="test_std")
            self.assertEqual(pm.nodeType(std), "standardSurface")
        except pm.MayaNodeError:
            pass  # standardSurface might not be available in older Maya versions

    def test_assign_mat(self):
        """Test assigning material to objects."""
        # Assign existing material
        MatUtils.assign_mat(self.cube, "test_lambert1")
        mats = MatUtils.get_mats(self.cube)
        self.assertIn(self.lambert1, mats)

        # Assign new material (should be created)
        MatUtils.assign_mat(self.cube, "new_created_mat")
        self.assertTrue(pm.objExists("new_created_mat"))
        mats = MatUtils.get_mats(self.cube)
        self.assertEqual(mats[0].name(), "new_created_mat")

    def test_is_connected(self):
        """Test checking if material is connected to shading group."""
        # lambert1 is connected in setUp
        # Note: is_connected returns True if the material is NOT connected (unused)
        self.assertFalse(MatUtils.is_connected(self.lambert1))

        # Create unconnected material
        unconnected = pm.shadingNode("blinn", asShader=True, name="unconnected_mat")
        self.assertTrue(MatUtils.is_connected(unconnected))

        # Test delete option
        self.assertTrue(
            MatUtils.is_connected(unconnected, delete=True)
        )  # Returns True if deleted
        self.assertFalse(pm.objExists("unconnected_mat"))

    # -------------------------------------------------------------------------
    # Texture & File Node Tests
    # -------------------------------------------------------------------------

    def test_get_connected_shaders(self):
        """Test retrieving shaders connected to file nodes."""
        file_node = pm.shadingNode("file", asTexture=True, name="test_file")
        pm.connectAttr(file_node.outColor, self.lambert1.color, force=True)

        shaders = MatUtils.get_connected_shaders(file_node)
        self.assertIn(self.lambert1, shaders)

    def test_get_file_nodes(self):
        """Test retrieving file nodes from materials."""
        file_node = pm.shadingNode("file", asTexture=True, name="test_file_node")
        file_node.fileTextureName.set("c:/test/texture.jpg")
        pm.connectAttr(file_node.outColor, self.lambert1.color, force=True)

        # Test basic retrieval
        nodes = MatUtils.get_file_nodes(materials=[self.lambert1.name()])
        # Default return type is 'fileNode' (object)
        self.assertIn(file_node, nodes)

        # Test return types
        info = MatUtils.get_file_nodes(
            materials=[self.lambert1.name()], return_type="shaderName|fileNodeName"
        )
        self.assertTrue(len(info) > 0)
        self.assertEqual(info[0], (self.lambert1.name(), file_node.name()))

    def test_collect_material_paths(self):
        """Test collecting file paths from materials."""
        file_node = pm.shadingNode("file", asTexture=True, name="path_test_file")
        test_path = "c:/textures/test.jpg"
        file_node.fileTextureName.set(test_path)
        pm.connectAttr(file_node.outColor, self.lambert1.color, force=True)

        # Test collection
        paths = MatUtils.collect_material_paths(materials=[self.lambert1.name()])
        # Note: Paths might be normalized/resolved, so check for substring or basename
        # collect_material_paths returns a list of tuples
        self.assertTrue(any("test.jpg" in p[0] for p in paths))

    # -------------------------------------------------------------------------
    # Material ID Tests
    # -------------------------------------------------------------------------

    def test_find_by_mat_id(self):
        """Test finding objects by material assignment."""
        pm.sets(self.sg1, forceElement=self.sphere)
        pm.sets(self.sg2, forceElement=self.cube)

        # Find sphere by lambert1
        found = MatUtils.find_by_mat_id(self.lambert1.name())
        # Result might be faces or transforms depending on assignment
        # Since we assigned to whole object, it might return the transform or shape
        transforms = [NodeUtils.get_transform_node(x) for x in found]
        self.assertIn(self.sphere, transforms)

        # Test shell=True (should return transforms)
        found_shell = MatUtils.find_by_mat_id(self.lambert1.name(), shell=True)
        self.assertIn(self.sphere, found_shell)

        # Test face assignment
        pm.sets(self.sg2, forceElement=self.sphere.f[0])
        found_faces = MatUtils.find_by_mat_id(
            self.lambert2.name(), objects=[self.sphere.name()], shell=False
        )
        self.assertTrue(len(found_faces) > 0)
        self.assertTrue(isinstance(found_faces[0], pm.MeshFace))


if __name__ == "__main__":
    unittest.main()
