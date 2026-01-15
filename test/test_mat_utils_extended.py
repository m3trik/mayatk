# !/usr/bin/python
# coding=utf-8
"""
Extended Test Suite for mayatk.mat_utils module

Tests for advanced MatUtils functionality including:
- Texture remapping and migration
- Duplicate material handling
- Normal map conversion
- Texture file management
"""
import os
import shutil
import unittest
import pymel.core as pm
import maya.cmds as cmds
from mayatk.mat_utils._mat_utils import MatUtils
from base_test import MayaTkTestCase


class TestMatUtilsExtended(MayaTkTestCase):
    """Tests for advanced MatUtils features."""

    def setUp(self):
        super().setUp()
        self.temp_dir = os.path.join(os.environ["TEMP"], "mayatk_test_textures")
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        # Create dummy texture files
        self.tex1 = os.path.join(self.temp_dir, "texture1.jpg").replace("\\", "/")
        self.tex2 = os.path.join(self.temp_dir, "texture2.jpg").replace("\\", "/")
        with open(self.tex1, "w") as f:
            f.write("dummy content 1")
        with open(self.tex2, "w") as f:
            f.write("dummy content 2")

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _create_textured_material(self, name, texture_path):
        """Helper to create a material with a file node."""
        mat = pm.shadingNode("lambert", asShader=True, name=name)
        file_node = pm.shadingNode("file", asTexture=True, name=f"{name}_file")
        file_node.fileTextureName.set(texture_path)
        pm.connectAttr(file_node.outColor, mat.color)
        return mat, file_node

    # -------------------------------------------------------------------------
    # Duplicate Material Tests
    # -------------------------------------------------------------------------

    def test_find_materials_with_duplicate_textures(self):
        """Test identifying materials that share the same textures."""
        # Create two materials using the same texture
        mat1, _ = self._create_textured_material("mat1", self.tex1)
        mat2, _ = self._create_textured_material("mat2", self.tex1)
        # Create one unique material
        mat3, _ = self._create_textured_material("mat3", self.tex2)

        duplicates = MatUtils.find_materials_with_duplicate_textures()

        # Should find that mat1 and mat2 are duplicates
        # The key will be one of them, the value list will contain the other
        self.assertTrue(len(duplicates) > 0)

        # Check if mat1 or mat2 is the key
        if mat1.name() in duplicates:
            self.assertIn(mat2.name(), duplicates[mat1.name()])
        elif mat2.name() in duplicates:
            self.assertIn(mat1.name(), duplicates[mat2.name()])
        else:
            self.fail("Neither mat1 nor mat2 found as duplicate key")

    def test_reassign_duplicate_materials(self):
        """Test consolidating duplicate materials."""
        mat1, _ = self._create_textured_material("mat1", self.tex1)
        mat2, _ = self._create_textured_material("mat2", self.tex1)

        # Assign mat1 to an object (so it has a SG)
        sphere = pm.polySphere(name="test_sphere")[0]
        MatUtils.assign_mat(sphere, mat1.name())

        # Assign mat2 to an object
        cube = pm.polyCube(name="test_cube")[0]
        MatUtils.assign_mat(cube, mat2.name())

        # Consolidate
        MatUtils.reassign_duplicate_materials(delete=True)

        # mat2 should be deleted, cube should have mat1 assigned
        self.assertFalse(pm.objExists("mat2"))
        self.assertTrue(pm.objExists("mat1"))

        assigned = MatUtils.get_mats(cube)
        self.assertEqual(assigned[0], mat1.name())

    # -------------------------------------------------------------------------
    # Texture Remapping Tests
    # -------------------------------------------------------------------------

    def test_remap_texture_paths(self):
        """Test remapping texture paths to a new directory."""
        mat1, file_node = self._create_textured_material("remap_mat", self.tex1)

        new_dir = os.path.join(self.temp_dir, "remapped")
        os.makedirs(new_dir)

        # Copy texture to new location so it exists
        new_tex = os.path.join(new_dir, "texture1.jpg").replace("\\", "/")
        shutil.copy(self.tex1, new_tex)

        # Remap
        MatUtils.remap_texture_paths(
            materials=[mat1.name()], new_dir=new_dir, silent=True
        )

        # Check if path updated
        current_path = file_node.fileTextureName.get().replace("\\", "/")
        # Note: Maya might resolve paths, so we check endswith or equality
        self.assertTrue(current_path.lower() == new_tex.lower())

    def test_filter_materials_by_objects(self):
        """Test filtering materials by object assignment."""
        mat1, _ = self._create_textured_material("obj_mat1", self.tex1)
        mat2, _ = self._create_textured_material("obj_mat2", self.tex2)

        cube1 = pm.polyCube()[0]
        cube2 = pm.polyCube()[0]

        MatUtils.assign_mat(cube1, mat1.name())
        MatUtils.assign_mat(cube2, mat2.name())

        result = MatUtils.filter_materials_by_objects([cube1.name()])
        self.assertIn(mat1, result)
        self.assertNotIn(mat2, result)

    # -------------------------------------------------------------------------
    # Normal Map Tests
    # -------------------------------------------------------------------------

    def test_convert_bump_to_normal(self):
        """Test converting a bump map setup to a normal map setup."""
        # Create a bump setup
        bump_file = pm.shadingNode("file", asTexture=True, name="bump_file")
        bump_file.fileTextureName.set(self.tex1)

        # Convert
        normal_node = MatUtils.convert_bump_to_normal(
            bump_file,
            create_file_node=False,  # Just return the bump2d node for testing logic
        )

        self.assertTrue(pm.objExists(normal_node))
        self.assertEqual(normal_node.bumpInterp.get(), 1)  # 1 = Tangent Space Normal

        # Check connection
        inputs = normal_node.bumpValue.inputs()
        self.assertEqual(inputs[0], bump_file)

    def test_validate_normal_map_setup(self):
        """Test validation of normal map nodes."""
        # Create a valid normal map node
        normal_file = pm.shadingNode("file", asTexture=True, name="normal_file")
        normal_file.fileTextureName.set(self.tex1)
        normal_file.colorSpace.set("Raw")

        # Create a material and connect it
        mat = pm.shadingNode("standardSurface", asShader=True)
        pm.connectAttr(normal_file.outColor, mat.normalCamera)

        result = MatUtils.validate_normal_map_setup(normal_file, mat)

        self.assertTrue(result["valid"])
        self.assertTrue(result["connected_to_normal"])
        self.assertEqual(result["color_space"], "Raw")

        # Test invalid setup (sRGB color space)
        normal_file.colorSpace.set("sRGB")
        result = MatUtils.validate_normal_map_setup(normal_file, mat)
        self.assertTrue(any("Color space" in w for w in result["warnings"]))
