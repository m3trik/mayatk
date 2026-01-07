# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.material_updater module

Tests for MaterialUpdater class functionality including:
- API flexibility (string presets, dicts, objects)
- Configuration overrides
- Integration with MapFactory
"""
import os
import unittest
from unittest.mock import MagicMock, patch
import pymel.core as pm
import pythontk as ptk

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

    @patch("pythontk.MapFactory.prepare_maps")
    def test_preset_string_config(self, mock_prepare):
        """Test passing a preset string to update_materials."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat], config="Unity HDRP", verbose=False
        )

        # Verify config passed to factory
        if mock_prepare.call_count == 0:
            self.fail("MapFactory.prepare_maps was not called")

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

    @patch("pythontk.MapFactory.prepare_maps")
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

    @patch("pythontk.MapFactory.prepare_maps")
    def test_explicit_args_override(self, mock_prepare):
        """Test explicit args overriding preset."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat],
            config={"preset": "Unity HDRP", "max_size": 512},
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertEqual(
            config_obj.get("max_size"), 512, "Explicit arg should override preset"
        )

    @patch("pythontk.MapFactory.prepare_maps")
    def test_dry_run(self, mock_prepare):
        """Test dry_run flag."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat],
            config={"dry_run": True},
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs
        self.assertTrue(config_obj.get("dry_run"))

    @patch("pythontk.MapFactory.prepare_maps")
    def test_optimize_flag(self, mock_prepare):
        """Test optimize flag propagation."""
        mock_prepare.return_value = [self.temp_tex]

        self.updater.update_materials(
            materials=[self.mat], config={"optimize": False}, verbose=False
        )

        args, kwargs = mock_prepare.call_args
        config_obj = kwargs

        self.assertFalse(config_obj.get("optimize"))

    @patch("pythontk.MapFactory.prepare_maps")
    def test_return_value(self, mock_prepare):
        """Test return value structure."""
        mock_prepare.return_value = [self.temp_tex]

        results = self.updater.update_materials(materials=[self.mat], verbose=False)

        self.assertIsInstance(results, dict)
        self.assertIn(self.mat.name(), results)
        self.assertIn("textures", results[self.mat.name()])
        self.assertIn("connected", results[self.mat.name()])
        self.assertEqual(results[self.mat.name()]["textures"], [self.temp_tex])

    def test_relative_path_resolution(self):
        """Test that relative move_to_folder paths are resolved to sourceimages."""
        relative_path = "SubFolder/Textures"
        fake_sourceimages = "C:/Maya/Project/sourceimages"
        expected_path = os.path.join(
            fake_sourceimages, "SubFolder", "Textures"
        ).replace("\\", "/")

        # Mock EnvUtils.get_env_info
        with patch(
            "mayatk.mat_utils.material_updater.EnvUtils.get_env_info"
        ) as mock_env:
            mock_env.return_value = fake_sourceimages

            # Mock MapFactory.prepare_maps to verify the config passed to it
            with patch(
                "pythontk.img_utils.map_factory.MapFactory.prepare_maps"
            ) as mock_prepare:
                mock_prepare.return_value = {}

                config = {
                    "move_to_folder": relative_path,
                    "dry_run": True,  # Skip actual processing
                }

                self.updater.update_materials(materials=[self.mat], config=config)

                # Verify prepare_maps was called with resolved path
                args, kwargs = mock_prepare.call_args
                self.assertEqual(
                    kwargs.get("move_to_folder").replace("\\", "/"), expected_path
                )

    @patch("pythontk.MapFactory.prepare_maps")
    def test_materials_none(self, mock_prepare):
        """Test materials=None behavior."""
        mock_prepare.return_value = [self.temp_tex]

        # Ensure we have at least one material (self.mat)
        results = self.updater.update_materials(materials=None, verbose=False)

        self.assertIsInstance(results, dict)
        self.assertIn(self.mat.name(), results)

    def test_move_to_folder_behavior(self):
        """Test if unmodified files are moved to the output folder."""
        output_folder = os.path.join(os.environ["TEMP"], "test_output")
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        config = {
            "move_to_folder": output_folder,
            "convert": False,
            "optimize": False,
            "resize": False,
            "rename": False,  # Default behavior
        }

        # Run without mocking prepare_maps to test real file system logic
        results = self.updater.update_materials(
            materials=[self.mat], config=config, verbose=False
        )

        # Get the connected texture path
        mat_results = results.get(self.mat.name(), {})
        connected = mat_results.get("connected", {})

        # Since we have a standardSurface with a file connected to baseColor,
        # it should be resolved as Base_Color.
        # Since convert/optimize are False, it should be passed through.

        # Check if any file is in the output folder
        # If rename is False (default), it should return the original path

        # Note: The test texture is self.temp_tex

        # We expect the path to be the ORIGINAL path
        for map_type, path in connected.items():
            self.assertEqual(os.path.normpath(path), os.path.normpath(self.temp_tex))
            self.assertFalse(path.startswith(output_folder))

    @patch(
        "mayatk.mat_utils.material_updater.MaterialUpdater.disconnect_associated_attributes"
    )
    @patch("mayatk.mat_utils.material_updater.MaterialUpdater.update_network")
    @patch("pythontk.MapFactory.prepare_maps")
    def test_max_size_resize_logic(self, mock_prepare, mock_update, mock_disconnect):
        """Test that resize flag is correctly derived from max_size."""
        mock_prepare.return_value = [self.temp_tex]

        # Case 1: max_size is None -> resize should be False
        self.updater.update_materials(
            materials=[self.mat],
            config={"max_size": None},
            verbose=False,
        )
        args, kwargs = mock_prepare.call_args
        config_obj = kwargs
        self.assertFalse(
            config_obj.get("resize"), "resize should be False when max_size is None"
        )
        self.assertIsNone(config_obj.get("max_size"), "max_size should be None")

        # Case 2: max_size is 1024 -> resize should be True
        self.updater.update_materials(
            materials=[self.mat],
            config={"max_size": 1024},
            verbose=False,
        )
        args, kwargs = mock_prepare.call_args
        config_obj = kwargs
        self.assertTrue(
            config_obj.get("resize"), "resize should be True when max_size is 1024"
        )
        self.assertEqual(config_obj.get("max_size"), 1024, "max_size should be 1024")

    @patch(
        "mayatk.mat_utils.material_updater.MaterialUpdater.disconnect_associated_attributes"
    )
    @patch("mayatk.mat_utils.material_updater.MaterialUpdater.update_network")
    @patch("pythontk.MapFactory.prepare_maps")
    def test_copy_all_parameter_passing(
        self, mock_prepare, mock_update, mock_disconnect
    ):
        """Test that 'rename' parameter is passed to MapFactory when set in config."""
        mock_prepare.return_value = [self.temp_tex]

        # Test with rename=True (Copy All)
        self.updater.update_materials(
            materials=[self.mat],
            config={"rename": True},
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        self.assertTrue(kwargs.get("rename"), "rename=True should be passed to factory")

        # Test with rename=False
        self.updater.update_materials(
            materials=[self.mat],
            config={"rename": False},
            verbose=False,
        )

        args, kwargs = mock_prepare.call_args
        self.assertFalse(
            kwargs.get("rename"), "rename=False should be passed to factory"
        )


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

        with patch("pythontk.MapFactory.prepare_maps") as mock_prepare:
            mock_prepare.return_value = list(self.textures.values())

            updater.update_materials(
                materials=[self.mat],
                config={"preset": "Unity HDRP", "convert": True, "optimize": True},
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
            "pythontk.MapFactory.prepare_maps", return_value=processed_files
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


class TestMaterialUpdaterMoveLogic(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.mat = pm.shadingNode("standardSurface", asShader=True, name="test_mat")
        self.temp_dir = os.path.normpath(
            os.path.join(os.environ["TEMP"], "test_move_logic")
        )
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        self.source_file = os.path.join(self.temp_dir, "source.png")
        with open(self.source_file, "w") as f:
            f.write("dummy")

        self.file_node = pm.shadingNode("file", asTexture=True, name="test_file")
        self.file_node.fileTextureName.set(self.source_file)
        if hasattr(self.mat, "baseColor"):
            pm.connectAttr(self.file_node.outColor, self.mat.baseColor)

    def tearDown(self):
        super().tearDown()
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.move_file")
    def test_skip_move_if_in_target(self, mock_move, mock_prepare):
        """Test that files already in target folder are NOT moved."""
        # Setup: processed file is SAME as source file (already in target)
        mock_prepare.return_value = [self.source_file]

        # Config: move_to_folder is the temp dir (where source file is)
        config = {"move_to_folder": self.temp_dir, "copy_all": True}

        MaterialUpdater.update_materials([self.mat], config=config)

        # Verify move_file was NOT called because file is already there
        mock_move.assert_not_called()

    @patch("pythontk.MapFactory.prepare_maps")
    @patch("pythontk.FileUtils.move_file")
    def test_move_if_not_in_target(self, mock_move, mock_prepare):
        """Test that files NOT in target folder ARE moved."""
        # Setup: processed file is in a different folder
        other_dir = os.path.join(self.temp_dir, "other")
        os.makedirs(other_dir, exist_ok=True)
        other_file = os.path.join(other_dir, "other.png")
        with open(other_file, "w") as f:
            f.write("dummy")

        mock_prepare.return_value = [other_file]
        mock_move.return_value = [os.path.join(self.temp_dir, "other.png")]

        # Config: move_to_folder is the temp dir
        config = {"move_to_folder": self.temp_dir, "copy_all": True}

        MaterialUpdater.update_materials([self.mat], config=config)

        # Verify move_file WAS called
        mock_move.assert_called()
        args, _ = mock_move.call_args
        self.assertIn(other_file, args[0])
        self.assertEqual(args[1], self.temp_dir)


if __name__ == "__main__":
    unittest.main()
