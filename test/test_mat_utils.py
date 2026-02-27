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
        # Handle both string and PyNode return types
        mat_name = random_mat.name() if hasattr(random_mat, "name") else random_mat
        self.assertTrue(mat_name.startswith("random_mat"))

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

    def test_module_exposure(self):
        """Test that MatUtils methods are exposed at the module level."""
        # Test assign_mat exposure
        mtk.assign_mat(self.cube, "exposed_mat")
        self.assertTrue(pm.objExists("exposed_mat"))

        # Test create_mat exposure
        mat = mtk.create_mat("blinn", name="exposed_blinn")
        self.assertTrue(pm.objExists("exposed_blinn"))

        # Test get_mats exposure
        mats = mtk.get_mats(self.cube)
        self.assertTrue(mats)
        self.assertEqual(mats[0].name(), "exposed_mat")

        # Test find_by_mat_id exposure
        # Ensure we pass the name string, as find_by_mat_id expects a string name or we need to verify PyNode support
        found = mtk.find_by_mat_id(mats[0].name(), [self.cube])
        self.assertTrue(found)

    def test_assign_mat_with_pynode(self):
        """Test assign_mat with PyNode input for material."""
        # Create a material
        mat = MatUtils.create_mat("blinn", name="pynode_mat")
        # Assign using the PyNode object
        MatUtils.assign_mat(self.cube, mat)

        mats = MatUtils.get_mats(self.cube)
        self.assertEqual(mats[0], mat)

    def test_find_by_mat_id_with_pynode(self):
        """Test find_by_mat_id with PyNode input for material."""
        # Assign material
        MatUtils.assign_mat(self.cube, self.lambert1)

        # Search using PyNode
        # This is expected to fail if find_by_mat_id doesn't handle PyNodes,
        # but we want to know if it does or if we need to fix it.
        try:
            found = MatUtils.find_by_mat_id(self.lambert1, [self.cube])
            self.assertTrue(found)
        except TypeError:
            self.fail("find_by_mat_id failed with PyNode input")

        # Test get_scene_mats exposure
        scene_mats = mtk.get_scene_mats()
        self.assertTrue(len(scene_mats) > 0)

        # Test get_mat_swatch_icon exposure
        try:
            mtk.get_mat_swatch_icon(scene_mats[0])
        except Exception as e:
            self.fail(f"get_mat_swatch_icon raised exception: {e}")

        # Test reload_textures exposure
        try:
            mtk.reload_textures()
        except Exception as e:
            self.fail(f"reload_textures raised exception: {e}")

    # -------------------------------------------------------------------------
    # Regression: _resolve_texture_targets traverses utility nodes
    # -------------------------------------------------------------------------

    def test_resolve_texture_targets_finds_file_nodes_behind_utility_nodes(self):
        """Verify _resolve_texture_targets finds file nodes connected through
        intermediate utility nodes (bump2d, colorCorrect, etc.).

        Bug: listConnections(material, type='file') only finds directly
        connected file nodes. File nodes behind bump2d, colorCorrect,
        aiNormalMap, etc. were silently missed, causing find_texture_files
        and related helpers to skip one or two textures.
        Fixed: 2026-02-23
        """
        from maya import cmds

        # Create material with a directly-connected diffuse file node
        mat = cmds.shadingNode("lambert", asShader=True, name="resolve_test_mat")
        diffuse_file = cmds.shadingNode("file", asTexture=True, name="diffuse_file")
        cmds.setAttr(f"{diffuse_file}.fileTextureName", "diffuse.png", type="string")
        cmds.connectAttr(f"{diffuse_file}.outColor", f"{mat}.color", force=True)

        # Create a bump map behind a bump2d node (indirect connection)
        bump_file = cmds.shadingNode("file", asTexture=True, name="bump_file")
        cmds.setAttr(f"{bump_file}.fileTextureName", "bump.png", type="string")
        bump2d = cmds.shadingNode("bump2d", asUtility=True, name="test_bump2d")
        cmds.connectAttr(f"{bump_file}.outAlpha", f"{bump2d}.bumpValue", force=True)
        cmds.connectAttr(f"{bump2d}.outNormal", f"{mat}.normalCamera", force=True)

        # Resolve — both file nodes should be found
        result = MatUtils._resolve_texture_targets(materials=[mat], as_strings=True)
        file_node_names = result["file_nodes"]

        self.assertIn(
            "diffuse_file",
            file_node_names,
            "Directly connected file node should be found",
        )
        self.assertIn(
            "bump_file",
            file_node_names,
            "File node behind bump2d should be found (was missed by listConnections)",
        )

    def test_resolve_texture_targets_finds_file_behind_color_correct(self):
        """Verify file nodes behind colorCorrect utility nodes are found.

        Bug: Same as above — listConnections missed any file node not
        directly connected to the material.
        Fixed: 2026-02-23
        """
        from maya import cmds

        mat = cmds.shadingNode("lambert", asShader=True, name="cc_test_mat")

        # File -> gammaCorrect -> material.color
        # (gammaCorrect is universally available; colorCorrect may lack
        # the expected attribute name across Maya versions)
        cc_file = cmds.shadingNode("file", asTexture=True, name="cc_file")
        cmds.setAttr(f"{cc_file}.fileTextureName", "diffuse_cc.png", type="string")
        gc = cmds.shadingNode("gammaCorrect", asUtility=True, name="test_gc")
        cmds.connectAttr(f"{cc_file}.outColor", f"{gc}.value", force=True)
        cmds.connectAttr(f"{gc}.outValue", f"{mat}.color", force=True)

        result = MatUtils._resolve_texture_targets(materials=[mat], as_strings=True)
        self.assertIn(
            "cc_file",
            result["file_nodes"],
            "File node behind colorCorrect should be found",
        )

    # -------------------------------------------------------------------------
    # Regression: get_file_nodes shader deduplication optimization
    # -------------------------------------------------------------------------

    def test_get_file_nodes_shared_shader_across_shading_engines(self):
        """Verify get_file_nodes returns correct results when a shader is
        connected to multiple shading engines.

        Bug: get_file_nodes called listHistory for every shading engine
        connection, causing redundant work when the same shader appeared
        in multiple SGs. With the deduplication fix, each unique shader
        is traversed only once.
        Fixed: 2026-02-27
        """
        from maya import cmds

        # Create a shader with a file node
        shared_mat = cmds.shadingNode("lambert", asShader=True, name="shared_mat")
        shared_file = cmds.shadingNode("file", asTexture=True, name="shared_file")
        cmds.setAttr(f"{shared_file}.fileTextureName", "shared_tex.png", type="string")
        cmds.connectAttr(f"{shared_file}.outColor", f"{shared_mat}.color", force=True)

        # Connect the same shader to TWO shading engines
        sg1 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="shared_sg1"
        )
        cmds.connectAttr(f"{shared_mat}.outColor", f"{sg1}.surfaceShader", force=True)
        sg2 = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="shared_sg2"
        )
        cmds.connectAttr(f"{shared_mat}.outColor", f"{sg2}.surfaceShader", force=True)

        # Query file nodes — shared_file should appear exactly once
        result = MatUtils.get_file_nodes(
            materials=[shared_mat], return_type="shaderName|fileNodeName"
        )
        file_node_names = [row[1] for row in result]
        self.assertIn(
            "shared_file",
            file_node_names,
            "File node connected to shared shader must be found",
        )
        self.assertEqual(
            file_node_names.count("shared_file"),
            1,
            "File node should appear exactly once despite shader in multiple SGs",
        )

    def test_get_file_nodes_batch_type_filter(self):
        """Verify get_file_nodes correctly filters file nodes using batch
        cmds.ls(type='file') instead of per-node cmds.nodeType() calls.

        Bug: Per-node nodeType calls were O(N) in the shader history size
        and added massive overhead in heavy scenes. Replaced with batch
        cmds.ls(history, type='file').
        Fixed: 2026-02-27
        """
        from maya import cmds

        # Create a shader with a file node AND a non-file utility node
        mat = cmds.shadingNode("lambert", asShader=True, name="batch_mat")
        file_node = cmds.shadingNode("file", asTexture=True, name="batch_file")
        cmds.setAttr(f"{file_node}.fileTextureName", "batch_tex.png", type="string")
        # Insert a bump2d between file and material
        bump = cmds.shadingNode("bump2d", asUtility=True, name="batch_bump")
        cmds.connectAttr(f"{file_node}.outAlpha", f"{bump}.bumpValue", force=True)
        cmds.connectAttr(f"{bump}.outNormal", f"{mat}.normalCamera", force=True)

        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="batch_sg"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        # get_file_nodes should find the file node through utility chain
        result = MatUtils.get_file_nodes(
            materials=[mat], return_type="shaderName|fileNodeName"
        )
        file_node_names = [row[1] for row in result]
        self.assertIn(
            "batch_file",
            file_node_names,
            "File node behind utility node must be found via batch ls filter",
        )

    def test_get_file_nodes_multiple_files_per_shader(self):
        """Verify get_file_nodes returns all file nodes when a single shader
        has multiple file textures (e.g. diffuse + bump).

        Ensures the deduplication optimization doesn't accidentally skip
        file nodes that share the same parent shader.
        Fixed: 2026-02-27
        """
        from maya import cmds

        mat = cmds.shadingNode("lambert", asShader=True, name="multi_mat")

        # Diffuse file node
        diff_file = cmds.shadingNode("file", asTexture=True, name="multi_diffuse")
        cmds.setAttr(f"{diff_file}.fileTextureName", "diffuse.png", type="string")
        cmds.connectAttr(f"{diff_file}.outColor", f"{mat}.color", force=True)

        # Bump file node (through bump2d)
        bump_file = cmds.shadingNode("file", asTexture=True, name="multi_bump")
        cmds.setAttr(f"{bump_file}.fileTextureName", "bump.png", type="string")
        bump = cmds.shadingNode("bump2d", asUtility=True, name="multi_bump2d")
        cmds.connectAttr(f"{bump_file}.outAlpha", f"{bump}.bumpValue", force=True)
        cmds.connectAttr(f"{bump}.outNormal", f"{mat}.normalCamera", force=True)

        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="multi_sg"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        result = MatUtils.get_file_nodes(materials=[mat], return_type="fileNodeName")
        self.assertIn("multi_diffuse", result, "Diffuse file node must be found")
        self.assertIn("multi_bump", result, "Bump file node must be found")
        self.assertEqual(len(result), 2, "Exactly two file nodes expected")

    def test_get_file_nodes_no_duplicates_in_scene_scan(self):
        """Verify a full scene scan (no material filter) returns each file
        node exactly once, even when the same file is reachable through
        multiple shading engines.

        Fixed: 2026-02-27
        """
        from maya import cmds

        mat = cmds.shadingNode("lambert", asShader=True, name="dedup_mat")
        fn = cmds.shadingNode("file", asTexture=True, name="dedup_file")
        cmds.setAttr(f"{fn}.fileTextureName", "dedup.png", type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color", force=True)

        for i in range(3):
            sg = cmds.sets(
                renderable=True,
                noSurfaceShader=True,
                empty=True,
                name=f"dedup_sg{i}",
            )
            cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        # Full scene scan
        result = MatUtils.get_file_nodes(return_type="shaderName|fileNodeName")
        dedup_rows = [r for r in result if r[1] == "dedup_file"]
        self.assertEqual(
            len(dedup_rows),
            1,
            "File node connected via 3 SGs should appear exactly once",
        )


if __name__ == "__main__":
    unittest.main()
