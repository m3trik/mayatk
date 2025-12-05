# !/usr/bin/python
# coding=utf-8
"""
Comprehensive unit tests for StingrayArnoldShader class.
Tests shader network creation, texture filtering, and Arnold integration.
"""
import unittest
import os
import sys
import tempfile
from typing import List

# Ensure proper path setup - add _scripts to path for all imports
scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# Add mayatk to path so imports work
mayatk_dir = os.path.join(scripts_dir, 'mayatk')
if mayatk_dir not in sys.path:
    sys.path.insert(0, mayatk_dir)

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

import pythontk as ptk
import mayatk as mtk

# Access StingrayArnoldShader through mayatk (now properly exposed)
StingrayArnoldShader = mtk.StingrayArnoldShader


class StingrayArnoldShaderTest(unittest.TestCase):
    """Test suite for StingrayArnoldShader functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up test environment once for all tests."""
        cls.temp_dir = tempfile.mkdtemp()
        cls.shader = StingrayArnoldShader()
        
    def setUp(self):
        """Set up clean Maya scene for each test."""
        pm.mel.file(new=True, force=True)
        self.test_messages = []
        
    def tearDown(self):
        """Clean up after each test."""
        # Clean up any created nodes
        pm.mel.file(new=True, force=True)

    def test_callback(self, msg, progress=None):
        """Mock callback function for testing."""
        self.test_messages.append(msg)
        
    # -------------------------------------------------------------------------
    # Test Normal Map Filtering
    # -------------------------------------------------------------------------
    
    def test_filter_opengl_normal_existing(self):
        """Test filtering when OpenGL normal map exists."""
        textures = [
            "model_BaseColor.png",
            "model_Normal_OpenGL.png",
            "model_Roughness.png",
        ]
        result = self.shader.filter_for_correct_normal_map(textures, "OpenGL")
        
        self.assertIn("model_Normal_OpenGL.png", result)
        self.assertEqual(len([t for t in result if "Normal" in t]), 1)
        
    def test_filter_directx_normal_existing(self):
        """Test filtering when DirectX normal map exists."""
        textures = [
            "model_BaseColor.png",
            "model_Normal_DirectX.png",
            "model_Roughness.png",
        ]
        result = self.shader.filter_for_correct_normal_map(textures, "DirectX")
        
        self.assertIn("model_Normal_DirectX.png", result)
        self.assertEqual(len([t for t in result if "Normal" in t]), 1)
    
    def test_filter_generic_normal_fallback(self):
        """Test fallback to generic normal map."""
        textures = [
            "model_BaseColor.png",
            "model_Normal.png",
            "model_Roughness.png",
        ]
        result = self.shader.filter_for_correct_normal_map(textures, "OpenGL")
        
        self.assertIn("model_Normal.png", result)
        
    def test_filter_no_normal_maps(self):
        """Test behavior when no normal maps exist."""
        textures = [
            "model_BaseColor.png",
            "model_Roughness.png",
        ]
        result = self.shader.filter_for_correct_normal_map(textures, "OpenGL")
        
        self.assertEqual(len(result), 2)
        self.assertNotIn("Normal", str(result))
    
    # -------------------------------------------------------------------------
    # Test Metallic Map Filtering
    # -------------------------------------------------------------------------
    
    def test_filter_metallic_smoothness_existing(self):
        """Test when metallic smoothness map already exists."""
        textures = [
            "model_BaseColor.png",
            "model_MetallicSmoothness.png",
            "model_Metallic.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=True, output_extension="png"
        )
        
        self.assertIn("model_MetallicSmoothness.png", result)
        self.assertNotIn("model_Metallic.png", result)
        
    def test_filter_metallic_roughness_combine(self):
        """Test combining metallic and roughness maps."""
        # Create mock texture files
        textures = [
            "model_BaseColor.png",
            "model_Metallic.png",
            "model_Roughness.png",
        ]
        
        # Note: This test assumes pack_smoothness_into_metallic creates the file
        # In a real environment, we'd need actual image files
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=True, output_extension="png"
        )
        
        # Should have combined map
        self.assertTrue(any("MetallicSmoothness" in t for t in result))
        
    def test_filter_remove_smoothness_maps(self):
        """Test removal of smoothness maps when not using metallic smoothness."""
        textures = [
            "model_BaseColor.png",
            "model_Metallic.png",
            "model_Smoothness.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=False, output_extension="png"
        )
        
        self.assertNotIn("model_Smoothness.png", result)
        self.assertIn("model_Metallic.png", result)
        
    def test_output_extension_jpg(self):
        """Test that JPG extension is used correctly."""
        textures = [
            "model_BaseColor.jpg",
            "model_Metallic.jpg",
            "model_Roughness.jpg",
        ]
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=True, output_extension="jpg"
        )
        
        # Verify output uses correct extension
        combined = [t for t in result if "MetallicSmoothness" in t]
        if combined:
            self.assertTrue(combined[0].endswith(".jpg"))
            
    def test_output_extension_tga(self):
        """Test that TGA extension is used correctly."""
        textures = [
            "model_BaseColor.tga",
            "model_Metallic.tga",
            "model_Roughness.tga",
        ]
        result = self.shader.filter_for_correct_metallic_map(
            textures, use_metallic_smoothness=True, output_extension="tga"
        )
        
        combined = [t for t in result if "MetallicSmoothness" in t]
        if combined:
            self.assertTrue(combined[0].endswith(".tga"))
    
    # -------------------------------------------------------------------------
    # Test Base Color Map Filtering
    # -------------------------------------------------------------------------
    
    def test_filter_albedo_transparency_existing(self):
        """Test when albedo transparency map exists."""
        textures = [
            "model_Albedo_Transparency.png",
            "model_Albedo.png",  # Should be removed
            "model_Opacity.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=True
        )
        
        self.assertIn("model_Albedo_Transparency.png", result)
        self.assertNotIn("model_Albedo.png", result)
        self.assertNotIn("model_Opacity.png", result)
        
    def test_filter_albedo_transparency_combine(self):
        """Test combining albedo and opacity maps."""
        textures = [
            "model_Albedo.png",
            "model_Opacity.png",
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=True
        )
        
        # Should create combined map
        self.assertTrue(any("Albedo_Transparency" in t for t in result))
        
    def test_filter_no_albedo_transparency(self):
        """Test when not using albedo transparency."""
        textures = [
            "model_Albedo.png",
            "model_Albedo_Transparency.png",  # Should be removed
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=False
        )
        
        self.assertNotIn("model_Albedo_Transparency.png", result)
        self.assertIn("model_Albedo.png", result)
        
    def test_filter_diffuse_fallback(self):
        """Test fallback to diffuse map when no base color exists."""
        textures = [
            "model_Diffuse.png",
            "model_Roughness.png",
        ]
        result = self.shader.filter_for_correct_base_color_map(
            textures, use_albedo_transparency=False
        )
        
        # Diffuse should be converted/used as base color
        self.assertTrue(any("Diffuse" in t or "BaseColor" in t for t in result))
    
    # -------------------------------------------------------------------------
    # Test Shader Node Setup
    # -------------------------------------------------------------------------
    
    def test_setup_stingray_node_basic(self):
        """Test basic Stingray PBS node creation."""
        result = self.shader.setup_stringray_node("test_material", opacity=False)
        
        self.assertIsNotNone(result)
        self.assertTrue(pm.objExists(result))
        self.assertEqual(pm.nodeType(result), "StingrayPBS")
        
    def test_setup_stingray_node_with_opacity(self):
        """Test Stingray PBS node creation with opacity."""
        result = self.shader.setup_stringray_node("test_opacity_mat", opacity=True)
        
        self.assertIsNotNone(result)
        self.assertTrue(pm.objExists(result))
        
    def test_setup_arnold_nodes(self):
        """Test Arnold shader nodes creation."""
        sr_node = self.shader.setup_stringray_node("test_arnold", opacity=False)
        ai_node, mult_node, bump_node = self.shader.setup_arnold_nodes("test_arnold", sr_node)
        
        self.assertIsNotNone(ai_node)
        self.assertIsNotNone(mult_node)
        self.assertIsNotNone(bump_node)
        
        self.assertTrue(pm.objExists(ai_node))
        self.assertTrue(pm.objExists(mult_node))
        self.assertTrue(pm.objExists(bump_node))
        
        # Check node types
        self.assertEqual(pm.nodeType(ai_node), "aiStandardSurface")
        self.assertEqual(pm.nodeType(mult_node), "multiplyDivide")
        self.assertEqual(pm.nodeType(bump_node), "bump2d")
    
    # -------------------------------------------------------------------------
    # Test Connection Methods
    # -------------------------------------------------------------------------
    
    def test_connect_stingray_base_color(self):
        """Test connecting base color texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_connect", opacity=False)
        
        # Create a file node as mock texture
        file_node = pm.shadingNode("file", asTexture=True)
        texture_path = "model_BaseColor.png"
        
        success = self.shader.connect_stingray_nodes(
            texture_path, "BaseColor", sr_node
        )
        
        self.assertTrue(success)
        
    def test_connect_stingray_metallic(self):
        """Test connecting metallic texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_metallic", opacity=False)
        texture_path = "model_Metallic.png"
        
        success = self.shader.connect_stingray_nodes(
            texture_path, "Metallic", sr_node
        )
        
        self.assertTrue(success)
        
    def test_connect_stingray_roughness(self):
        """Test connecting roughness texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_roughness", opacity=False)
        texture_path = "model_Roughness.png"
        
        success = self.shader.connect_stingray_nodes(
            texture_path, "Roughness", sr_node
        )
        
        self.assertTrue(success)
        
    def test_connect_stingray_normal(self):
        """Test connecting normal map to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_normal", opacity=False)
        texture_path = "model_Normal_OpenGL.png"
        
        success = self.shader.connect_stingray_nodes(
            texture_path, "Normal_OpenGL", sr_node
        )
        
        self.assertTrue(success)
        
    def test_connect_stingray_emissive(self):
        """Test connecting emissive texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_emissive", opacity=False)
        texture_path = "model_Emissive.png"
        
        success = self.shader.connect_stingray_nodes(
            texture_path, "Emissive", sr_node
        )
        
        self.assertTrue(success)
        
    def test_connect_stingray_ao(self):
        """Test connecting AO texture to Stingray node."""
        sr_node = self.shader.setup_stringray_node("test_ao", opacity=False)
        texture_path = "model_AO.png"
        
        success = self.shader.connect_stingray_nodes(
            texture_path, "AO", sr_node
        )
        
        self.assertTrue(success)
    
    # -------------------------------------------------------------------------
    # Test Full Network Creation
    # -------------------------------------------------------------------------
    
    def test_create_network_basic(self):
        """Test basic shader network creation."""
        textures = [
            "model_BaseColor.png",
            "model_Metallic.png",
            "model_Roughness.png",
        ]
        
        result = self.shader.create_network(
            textures,
            name="test_basic_network",
            callback=self.test_callback
        )
        
        # Check that shader was created
        self.assertTrue(pm.objExists("test_basic_network"))
        
    def test_create_network_with_arnold(self):
        """Test shader network creation with Arnold."""
        textures = [
            "model_BaseColor.png",
            "model_Metallic.png",
            "model_Roughness.png",
        ]
        
        result = self.shader.create_network(
            textures,
            name="test_arnold_network",
            create_arnold=True,
            callback=self.test_callback
        )
        
        # Check that both Stingray and Arnold shaders exist
        self.assertTrue(pm.objExists("test_arnold_network"))
        # Arnold shader should have same base name
        arnold_shaders = pm.ls(type="aiStandardSurface")
        self.assertTrue(len(arnold_shaders) > 0)
        
    def test_create_network_pbr_metal_roughness(self):
        """Test PBR Metal Roughness workflow."""
        textures = [
            "model_BaseColor.png",
            "model_Metallic.png",
            "model_Roughness.png",
            "model_Normal_OpenGL.png",
        ]
        
        result = self.shader.create_network(
            textures,
            name="test_pbr",
            normal_type="OpenGL",
            albedo_transparency=False,
            metallic_smoothness=False,
            output_extension="png",
            callback=self.test_callback
        )
        
        self.assertTrue(pm.objExists("test_pbr"))
        
    def test_create_network_unity_urp(self):
        """Test Unity URP workflow (with albedo transparency and metallic smoothness)."""
        textures = [
            "model_Albedo.png",
            "model_Opacity.png",
            "model_Metallic.png",
            "model_Smoothness.png",
        ]
        
        result = self.shader.create_network(
            textures,
            name="test_unity",
            albedo_transparency=True,
            metallic_smoothness=True,
            output_extension="png",
            callback=self.test_callback
        )
        
        self.assertTrue(pm.objExists("test_unity"))
        
    def test_create_network_empty_textures(self):
        """Test error handling for empty texture list."""
        result = self.shader.create_network(
            [],
            callback=self.test_callback
        )
        
        self.assertIsNone(result)
        self.assertTrue(len(self.test_messages) > 0)
        self.assertTrue(any("Error" in msg for msg in self.test_messages))
        
    def test_create_network_different_extensions(self):
        """Test network creation with various image extensions."""
        extensions = ["png", "jpg", "tga", "bmp", "tiff"]
        
        for ext in extensions:
            with self.subTest(extension=ext):
                textures = [
                    f"model_BaseColor.{ext}",
                    f"model_Metallic.{ext}",
                    f"model_Roughness.{ext}",
                ]
                
                result = self.shader.create_network(
                    textures,
                    name=f"test_{ext}_network",
                    output_extension=ext,
                    callback=self.test_callback
                )
                
                self.assertTrue(pm.objExists(f"test_{ext}_network"))
    
    # -------------------------------------------------------------------------
    # Test Edge Cases and Error Handling
    # -------------------------------------------------------------------------
    
    def test_unknown_texture_type(self):
        """Test handling of unknown texture types."""
        textures = [
            "model_BaseColor.png",
            "model_Unknown_Type.png",  # Unknown type
        ]
        
        self.test_messages = []
        result = self.shader.create_network(
            textures,
            name="test_unknown",
            callback=self.test_callback
        )
        
        # Should still create shader despite unknown texture
        self.assertTrue(pm.objExists("test_unknown"))
        
    def test_multiple_normal_maps_same_type(self):
        """Test handling multiple normal maps of the same type."""
        textures = [
            "model_BaseColor.png",
            "model_Normal_OpenGL.png",
            "model_Normal_OpenGL_2.png",  # Duplicate type
        ]
        
        result = self.shader.filter_for_correct_normal_map(textures, "OpenGL")
        
        # Should handle gracefully (implementation may vary)
        opengl_maps = [t for t in result if "Normal_OpenGL" in t]
        self.assertTrue(len(opengl_maps) >= 1)
        
    def test_missing_required_maps(self):
        """Test creation with minimal texture set."""
        textures = [
            "model_BaseColor.png",  # Only base color
        ]
        
        result = self.shader.create_network(
            textures,
            name="test_minimal",
            callback=self.test_callback
        )
        
        # Should still create shader with just base color
        self.assertTrue(pm.objExists("test_minimal"))
        
    def test_shader_name_auto_generation(self):
        """Test automatic shader name generation from texture."""
        textures = [
            "/path/to/textures/character_BaseColor.png",
        ]
        
        result = self.shader.create_network(
            textures,
            name="",  # Empty name - should auto-generate
            callback=self.test_callback
        )
        
        # Should create shader with auto-generated name
        shaders = pm.ls(type="StingrayPBS")
        self.assertTrue(len(shaders) > 0)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import importlib
    import mayatk as mtk
    from mayatk.mat_utils import stingray_arnold_shader
    
    # Reload module to get latest changes
    importlib.reload(stingray_arnold_shader)
    
    # Clear any previous test output
    mtk.clear_scrollfield_reporters()
    
    # Create test suite
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(StingrayArnoldShaderTest))
    
    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print("\n" + "="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print("="*70)
    
    if result.wasSuccessful():
        print("✓ ALL TESTS PASSED!")
    else:
        print("✗ SOME TESTS FAILED")


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
# Test Coverage:
# - Normal map filtering (OpenGL, DirectX, generic, missing)
# - Metallic map filtering (combine, smoothness, various extensions)
# - Base color map filtering (albedo transparency, diffuse fallback)
# - Stingray node creation
# - Arnold node creation
# - Texture connections (base color, metallic, roughness, normal, emissive, AO)
# - Full network creation (basic, with Arnold, PBR, Unity URP)
# - Various output extensions (PNG, JPG, TGA, BMP, TIFF)
# - Error handling (empty textures, unknown types, minimal sets)
# - Auto-name generation
