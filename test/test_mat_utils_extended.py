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
        mat = cmds.shadingNode("lambert", asShader=True, name=name)
        file_node = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{file_node}.fileTextureName", texture_path, type="string")
        cmds.connectAttr(f"{file_node}.outColor", f"{mat}.color")
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
        # The key will be one of them, the value list will contain the other.
        # Production returns plain string keys/values; coerce inputs to match.
        self.assertTrue(len(duplicates) > 0)
        mat1_n, mat2_n = str(mat1), str(mat2)

        if mat1_n in duplicates:
            self.assertIn(mat2_n, duplicates[mat1_n])
        elif mat2_n in duplicates:
            self.assertIn(mat1_n, duplicates[mat2_n])
        else:
            self.fail("Neither mat1 nor mat2 found as duplicate key")

    def test_reassign_duplicate_materials(self):
        """Test consolidating duplicate materials."""
        mat1, _ = self._create_textured_material("mat1", self.tex1)
        mat2, _ = self._create_textured_material("mat2", self.tex1)

        # Assign mat1 to an object (so it has a SG)
        sphere = cmds.polySphere(name="test_sphere")[0]
        MatUtils.assign_mat(sphere, mat1)

        # Assign mat2 to an object
        cube = cmds.polyCube(name="test_cube")[0]
        MatUtils.assign_mat(cube, mat2)

        # Consolidate
        MatUtils.reassign_duplicate_materials(delete=True)

        # mat2 should be deleted, cube should have mat1 assigned
        self.assertFalse(cmds.objExists("mat2"))
        self.assertTrue(cmds.objExists("mat1"))

        assigned = MatUtils.get_mats(cube)
        self.assertEqual(assigned[0], mat1)

    def test_different_attr_same_texture_not_duplicate(self):
        """Verify materials using the same texture on different attributes
        are NOT flagged as duplicates.

        Bug: find_materials_with_duplicate_textures compared only texture
        filenames without tracking which attribute they connected to.
        This caused protractor materials to be merged with yoke/brake
        materials when they shared texture filenames on different channels.
        Fixed: 2026-03-08
        """
        # mat_a: texture1 → color (diffuse)
        mat_a, _ = self._create_textured_material("matA", self.tex1)
        # mat_b: texture1 → transparency (different attribute, same file)
        mat_b = cmds.shadingNode("lambert", asShader=True, name="matB")
        file_b = cmds.shadingNode("file", asTexture=True, name="matB_file")
        cmds.setAttr(f"{file_b}.fileTextureName", self.tex1, type="string")
        cmds.connectAttr(f"{file_b}.outColor", f"{mat_b}.transparency")

        duplicates = MatUtils.find_materials_with_duplicate_textures()

        # They should NOT be detected as duplicates
        for original, dup_list in duplicates.items():
            combined = [original] + dup_list
            self.assertFalse(
                mat_a in combined and mat_b in combined,
                "Materials with same texture on different attributes "
                "should not be duplicates",
            )

    def test_different_material_type_not_duplicate(self):
        """Verify materials of different shader types are never duplicates,
        even when their textures and attributes match.

        Fixed: 2026-03-08
        """
        # lambert with texture1 → color
        mat_lam, _ = self._create_textured_material("matLambert", self.tex1)
        # phong with texture1 → color
        mat_phong = cmds.shadingNode("phong", asShader=True, name="matPhong")
        file_ph = cmds.shadingNode("file", asTexture=True, name="matPhong_file")
        cmds.setAttr(f"{file_ph}.fileTextureName", self.tex1, type="string")
        cmds.connectAttr(f"{file_ph}.outColor", f"{mat_phong}.color")

        duplicates = MatUtils.find_materials_with_duplicate_textures()

        for original, dup_list in duplicates.items():
            combined = [original] + dup_list
            self.assertFalse(
                mat_lam in combined and mat_phong in combined,
                "Materials of different types should not be duplicates",
            )

    def test_different_textures_behind_utility_nodes_not_duplicate(self):
        """Verify materials with different texture sets connected through
        utility nodes (bump2d) are NOT flagged as duplicates.

        Bug: The old algorithm used listConnections(material, type='file')
        which only found directly-connected file nodes. When textures were
        behind utility nodes (bump2d, colorCorrect, etc.), both materials
        appeared to have zero or partial textures and could be falsely
        merged. listHistory is now used to traverse the full upstream graph.
        Fixed: 2026-03-08
        """
        # mat_x: texture1 → bump2d → normalCamera
        mat_x = cmds.shadingNode("lambert", asShader=True, name="matX")
        file_x = cmds.shadingNode("file", asTexture=True, name="matX_file")
        cmds.setAttr(f"{file_x}.fileTextureName", self.tex1, type="string")
        bump_x = cmds.shadingNode("bump2d", asUtility=True, name="matX_bump")
        cmds.connectAttr(f"{file_x}.outAlpha", f"{bump_x}.bumpValue")
        cmds.connectAttr(f"{bump_x}.outNormal", f"{mat_x}.normalCamera")

        # mat_y: texture2 → bump2d → normalCamera (different texture)
        mat_y = cmds.shadingNode("lambert", asShader=True, name="matY")
        file_y = cmds.shadingNode("file", asTexture=True, name="matY_file")
        cmds.setAttr(f"{file_y}.fileTextureName", self.tex2, type="string")
        bump_y = cmds.shadingNode("bump2d", asUtility=True, name="matY_bump")
        cmds.connectAttr(f"{file_y}.outAlpha", f"{bump_y}.bumpValue")
        cmds.connectAttr(f"{bump_y}.outNormal", f"{mat_y}.normalCamera")

        duplicates = MatUtils.find_materials_with_duplicate_textures()

        for original, dup_list in duplicates.items():
            combined = [original] + dup_list
            self.assertFalse(
                mat_x in combined and mat_y in combined,
                "Materials with different textures behind utility nodes "
                "should not be duplicates",
            )

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
            materials=[mat1], new_dir=new_dir, silent=True
        )

        # Check if path updated
        current_path = cmds.getAttr(f"{file_node}.fileTextureName").replace("\\", "/")
        # Note: Maya might resolve paths, so we check endswith or equality
        self.assertTrue(current_path.lower() == new_tex.lower())

    def test_filter_materials_by_objects(self):
        """Test filtering materials by object assignment."""
        mat1, _ = self._create_textured_material("obj_mat1", self.tex1)
        mat2, _ = self._create_textured_material("obj_mat2", self.tex2)

        cube1 = cmds.polyCube()[0]
        cube2 = cmds.polyCube()[0]

        MatUtils.assign_mat(cube1, mat1)
        MatUtils.assign_mat(cube2, mat2)

        result = MatUtils.filter_materials_by_objects([cube1])
        self.assertIn(mat1, result)
        self.assertNotIn(mat2, result)

    # -------------------------------------------------------------------------
    # Normal Map Tests
    # -------------------------------------------------------------------------

    def test_convert_bump_to_normal(self):
        """convert_bump_to_normal must write a REAL normal map and wire a
        Raw-colorspace file node to it. (The old implementation built a
        bump2d/reverse network that never produced a file — and inverted
        all three channels for 'directx'.)"""
        from PIL import Image

        bump_path = os.path.join(self.temp_dir, "test_Bump.png").replace("\\", "/")
        Image.new("L", (32, 32), 128).save(bump_path)

        bump_file = cmds.shadingNode("file", asTexture=True, name="bump_file")
        cmds.setAttr(f"{bump_file}.fileTextureName", bump_path, type="string")

        out_path = os.path.join(self.temp_dir, "test_Normal_OpenGL.png").replace(
            "\\", "/"
        )
        normal_node = MatUtils.convert_bump_to_normal(
            bump_file, output_path=out_path, create_file_node=True
        )

        self.assertTrue(cmds.objExists(normal_node))
        self.assertTrue(os.path.exists(out_path), "normal map was not written")
        self.assertEqual(
            cmds.getAttr(f"{normal_node}.fileTextureName"), out_path
        )
        self.assertEqual(cmds.getAttr(f"{normal_node}.colorSpace"), "Raw")
        # Flat bump -> neutral normal (128, 128, 255).
        px = Image.open(out_path).convert("RGB").getpixel((16, 16))
        self.assertEqual(px, (127, 127, 255))

        # create_file_node=False returns the written path instead.
        out2 = MatUtils.convert_bump_to_normal(
            bump_file, create_file_node=False
        )
        self.assertTrue(os.path.exists(out2))

    def test_validate_normal_map_setup(self):
        """Test validation of normal map nodes."""
        # Create a valid normal map node
        normal_file = cmds.shadingNode("file", asTexture=True, name="normal_file")
        cmds.setAttr(f"{normal_file}.fileTextureName", self.tex1, type="string")
        cmds.setAttr(f"{normal_file}.colorSpace", "Raw", type="string")

        # Create a material and connect it
        mat = cmds.shadingNode("standardSurface", asShader=True)
        cmds.connectAttr(f"{normal_file}.outColor", f"{mat}.normalCamera")

        result = MatUtils.validate_normal_map_setup(normal_file, mat)

        self.assertTrue(result["valid"])
        self.assertTrue(result["connected_to_normal"])
        self.assertEqual(result["color_space"], "Raw")

        # Test invalid setup (sRGB color space)
        cmds.setAttr(f"{normal_file}.colorSpace", "sRGB", type="string")
        result = MatUtils.validate_normal_map_setup(normal_file, mat)
        self.assertTrue(any("Color space" in w for w in result["warnings"]))
