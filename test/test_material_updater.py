# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.material_updater module

Tests for MaterialUpdater class functionality including:
- API flexibility (string presets, dicts, objects)
- Configuration overrides
- Integration with TextureMapFactory
"""
import os
import unittest
from unittest.mock import MagicMock, patch
import pymel.core as pm

from base_test import MayaTkTestCase
from mayatk.mat_utils.material_updater import MaterialUpdater


class TestMaterialUpdater(MayaTkTestCase):
    """Tests for MaterialUpdater class."""

    def setUp(self):
        super().setUp()
        # Load Stingray plugin if available (optional, but good for completeness)
        try:
            if not pm.pluginInfo("shaderFXPlugin", query=True, loaded=True):
                pm.loadPlugin("shaderFXPlugin")
        except Exception:
            pass

        # Create a dummy material and texture
        # We need a valid file path for os.path.exists check in MaterialUpdater
        self.temp_tex = os.path.join(os.environ["TEMP"], "test_texture.png")
        with open(self.temp_tex, "w") as f:
            f.write("dummy")

        # Use standardSurface which is always available and supported
        self.mat = pm.shadingNode("standardSurface", asShader=True, name="test_mat")
        self.file_node = pm.shadingNode("file", asTexture=True, name="test_file")
        self.file_node.fileTextureName.set(self.temp_tex)

        # Connect to baseColor
        if hasattr(self.mat, "baseColor"):
            pm.connectAttr(self.file_node.outColor, self.mat.baseColor)

        self.updater = MaterialUpdater()

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.temp_tex):
            try:
                os.remove(self.temp_tex)
            except OSError:
                pass

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_preset_string_config(self, mock_prepare):
        """Test passing a preset string to update_materials."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat], config="Unity HDRP", verbose=False
        )

        # Verify config passed to factory
        if mock_prepare.call_count == 0:
            self.fail("TextureMapFactory.prepare_maps was not called")

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertIsInstance(config_obj, dict)
        # Unity HDRP preset has mask_map=True and albedo_transparency=True
        self.assertTrue(
            config_obj.get("mask_map"), "Unity HDRP should have mask_map=True"
        )
        self.assertTrue(
            config_obj.get("albedo_transparency"),
            "Unity HDRP should have albedo_transparency=True",
        )

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_dict_config(self, mock_prepare):
        """Test passing a dictionary to update_materials."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat],
            config={"mask_map": True, "max_size": 1024},
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertTrue(config_obj.get("mask_map"))
        self.assertEqual(config_obj.get("max_size"), 1024)

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_explicit_args_override(self, mock_prepare):
        """Test explicit args overriding preset."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat],
            config="Unity HDRP",
            max_size=512,  # Explicit arg
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertEqual(
            config_obj.get("max_size"), 512, "Explicit arg should override preset"
        )

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_dry_run(self, mock_prepare):
        """Test dry_run flag."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat],
            dry_run=True,
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs
        self.assertTrue(config_obj.get("dry_run"))

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_optimize_flag(self, mock_prepare):
        """Test optimize flag propagation."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat], optimize=False, verbose=False
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertFalse(config_obj.get("optimize"))

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_convert_flag(self, mock_prepare):
        """Test convert flag propagation."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat], convert=False, verbose=False
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertFalse(config_obj.get("convert"))

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_return_value(self, mock_prepare):
        """Test return value structure."""
        mock_prepare.return_value = [self.temp_tex]

        results = self.updater.update_materials(materials=[self.mat], verbose=False)

        self.assertIsInstance(results, dict)
        self.assertIn(self.mat.name(), results)
        self.assertIn("textures", results[self.mat.name()])
        self.assertIn("connected", results[self.mat.name()])
        self.assertEqual(results[self.mat.name()]["textures"], [self.temp_tex])

    @patch("pythontk.TextureMapFactory.prepare_maps")
    def test_materials_none(self, mock_prepare):
        """Test materials=None behavior."""
        mock_prepare.return_value = [self.temp_tex]

        # Ensure we have at least one material (self.mat)
        results = self.updater.update_materials(materials=None, verbose=False)

        self.assertIsInstance(results, dict)
        self.assertIn(self.mat.name(), results)


class TestMaterialUpdaterStingray(MayaTkTestCase):
    """Tests for MaterialUpdater with StingrayPBS shader."""

    def setUp(self):
        super().setUp()
        # Load Stingray plugin
        try:
            if not pm.pluginInfo("shaderFXPlugin", query=True, loaded=True):
                pm.loadPlugin("shaderFXPlugin")
        except Exception:
            pass

        # Create Stingray Material
        self.mat = pm.shadingNode(
            "StingrayPBS", asShader=True, name="test_stingray_mat"
        )
        try:
            self.mat.initgraph.set(True)
        except Exception:
            pass

        # Create dummy files
        self.temp_dir = os.path.join(os.environ["TEMP"], "mat_updater_test_stingray")
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        self.textures = {
            "Base_Color": os.path.join(self.temp_dir, "test_BaseColor.png"),
            "Metallic": os.path.join(self.temp_dir, "test_Metallic.png"),
            "Roughness": os.path.join(self.temp_dir, "test_Roughness.png"),
            "AO": os.path.join(self.temp_dir, "test_AO.png"),
        }

        for path in self.textures.values():
            with open(path, "w") as f:
                f.write("dummy data")

    def tearDown(self):
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        super().tearDown()

    def test_explicit_args_mapping(self):
        """Test that explicit arguments 'convert' and 'optimize' are handled."""
        updater = MaterialUpdater()

        with patch("pythontk.TextureMapFactory.prepare_maps") as mock_prepare:
            mock_prepare.return_value = list(self.textures.values())

            updater.update_materials(
                materials=[self.mat],
                config="Unity HDRP",
                convert=True,  # Explicit arg
                optimize=True,  # Explicit arg
                verbose=True,
            )

            self.assertTrue(mock_prepare.called)
            args, kwargs = mock_prepare.call_args
            config_obj = kwargs

            self.assertTrue(
                config_obj.get("convert"),
                "Explicit 'convert=True' did not enable 'convert'",
            )
            self.assertTrue(
                config_obj.get("optimize"),
                "Explicit 'optimize=True' did not enable 'optimize'",
            )

    def test_msao_connection(self):
        """Test that MSAO map is connected correctly for Unity HDRP."""
        updater = MaterialUpdater()

        msao_path = os.path.join(self.temp_dir, "test_texture_MSAO.png")
        with open(msao_path, "w") as f:
            f.write("dummy")

        processed_files = [
            self.textures["Base_Color"],
            msao_path,
            self.textures["Roughness"],
        ]

        with patch(
            "pythontk.TextureMapFactory.prepare_maps", return_value=processed_files
        ):
            updater.update_materials(materials=[self.mat], config="Unity HDRP")

            # Check Metallic (R of MSAO)
            conn_metal = pm.listConnections(
                self.mat.TEX_metallic_map, plugs=True, source=True
            )
            if not conn_metal:
                conn_metal = pm.listConnections(
                    self.mat.TEX_metallic_mapX, plugs=True, source=True
                )

            self.assertTrue(
                conn_metal, "Nothing connected to TEX_metallic_map or TEX_metallic_mapX"
            )
            self.assertIn(
                "MSAO",
                conn_metal[0].node().fileTextureName.get(),
                "MSAO not connected to Metallic",
            )

            # Check AO (G of MSAO)
            conn_ao = pm.listConnections(self.mat.TEX_ao_map, plugs=True, source=True)
            if not conn_ao:
                conn_ao = pm.listConnections(
                    self.mat.TEX_ao_mapX, plugs=True, source=True
                )
            self.assertTrue(conn_ao, "Nothing connected to TEX_ao_map")
            self.assertIn(
                "MSAO",
                conn_ao[0].node().fileTextureName.get(),
                "MSAO not connected to AO",
            )

            # Check Roughness (Reverse of Alpha of MSAO)
            conn_rough = pm.listConnections(
                self.mat.TEX_roughness_map, plugs=True, source=True
            )
            if not conn_rough:
                conn_rough = pm.listConnections(
                    self.mat.TEX_roughness_mapX, plugs=True, source=True
                )
            self.assertTrue(conn_rough, "Nothing connected to TEX_roughness_map")


if __name__ == "__main__":
    unittest.main()
