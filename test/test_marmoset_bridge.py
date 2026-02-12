import sys
from unittest.mock import MagicMock

# 1. Mock Maya and Pymel modules BEFORE importing mayatk
mock_maya = MagicMock()
mock_cmds = MagicMock()
mock_pm = MagicMock()
mock_pymel = MagicMock()

sys.modules["maya"] = mock_maya
sys.modules["maya.cmds"] = mock_cmds
# LINK mocks to ensure consistency
mock_maya.cmds = mock_cmds
sys.modules["maya.mel"] = MagicMock()
sys.modules["maya.api"] = MagicMock()
sys.modules["maya.api.OpenMaya"] = MagicMock()
sys.modules["maya.OpenMaya"] = MagicMock()
sys.modules["pymel"] = mock_pymel
sys.modules["pymel.core"] = mock_pm

# 2. Setup common mocks
# Mock pm.ls() to return list of mocks
mock_pm.ls.return_value = []
# Mock pm.selected()
mock_pm.selected.return_value = []

import unittest
import os
import json

# Now import the modules under test
from mayatk.mat_utils.mat_manifest import MatManifest
from mayatk.mat_utils.marmoset_exporter import MarmosetExporter

# We might need to mock _mat_utils imports if they fail
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap


class TestMarmosetBridgeStandalone(unittest.TestCase):
    def setUp(self):
        # Reset mocks
        mock_pm.reset_mock()
        mock_cmds.reset_mock()
        # Ensure standardSurface is in shader attribute map so our assumption about "baseColor" holds
        # (It should be by default if imported correctly)

    def test_mat_manifest_structure(self):
        """Test that MatManifest produces the correct dictionary structure."""

        # Scenario: 1 Object, 1 Material, 1 Dictionary

        # Mock Object
        mock_obj = MagicMock()
        mock_obj.name.return_value = "pCube1"

        # Mock Shader
        mock_shader_name = "M_Standard"

        # NOTE: We must patch where ManifestBuilder imported it, or the class itself.
        # ManifestBuilder imports MatUtils.
        # But patching 'mayatk.mat_utils._mat_utils.MatUtils.get_mats' should work if imported as 'from ... import MatUtils'

        with unittest.mock.patch(
            "mayatk.mat_utils._mat_utils.MatUtils.get_mats",
            return_value=[mock_shader_name],
        ) as mock_get_mats:
            # Mock cmds.nodeType to return "standardSurface"
            mock_cmds.nodeType.return_value = "standardSurface"

            # Mock MatUtils.get_texture_file_node
            # We'll simulate finding a file node for "baseColor"
            def side_effect_get_tex(mat, attr):
                # print(f"DEBUG: get_texture_file_node called with {mat}, {attr}")
                if mat == mock_shader_name and attr == "baseColor":
                    return "fileNode1"
                return None

            with unittest.mock.patch(
                "mayatk.mat_utils._mat_utils.MatUtils.get_texture_file_node",
                side_effect=side_effect_get_tex,
            ) as mock_get_tex:
                # Mock MatUtils._paths_from_file_nodes to return path
                with unittest.mock.patch(
                    "mayatk.mat_utils._mat_utils.MatUtils._paths_from_file_nodes",
                    return_value=["C:/textures/diffuse.png"],
                ) as mock_paths:

                    # Run Build
                    manifest = ManifestBuilder.build([mock_obj])

                    # print(f"DEBUG: Manifest result: {manifest}")

                    # Verify Structure
                    self.assertIn("materials", manifest)
                    # If process_material returns data, it should be here
                    self.assertIn(mock_shader_name, manifest["materials"])
                    mat_data = manifest["materials"][mock_shader_name]

                    # Check "baseColor" is mapped
                    self.assertEqual(
                        mat_data.get("baseColor"), "C:/textures/diffuse.png"
                    )

    def test_fbx_export_call(self):
        """Test that MarmosetExporter calls FbxUtils correctly."""
        # Patch FbxUtils
        with unittest.mock.patch(
            "mayatk.mat_utils.marmoset_exporter.FbxUtils"
        ) as mock_fbx:
            # Patch ManifestBuilder
            with unittest.mock.patch(
                "mayatk.mat_utils.marmoset_exporter.ManifestBuilder"
            ) as mock_builder:
                mock_builder.build.return_value = {"materials": {}}

                # Mock AppLauncher
                with unittest.mock.patch(
                    "mayatk.mat_utils.marmoset_exporter.AppLauncher"
                ) as mock_launcher:
                    # Mock output dir creation (pass through) and open
                    # We DON'T patch makedirs, let it happen in temp.
                    # But we patch open to avoid writing files.

                    with unittest.mock.patch(
                        "builtins.open", unittest.mock.mock_open()
                    ) as mock_file:
                        exporter = MarmosetExporter()
                        # Override resolve logic to avoid cmds listing
                        # exporter._resolve_objects = MagicMock(return_value=["pCube1"])

                        # Run
                        exporter.send(
                            objects=["pCube1"], toolbag_exe="fake_toolbag.exe"
                        )

                        # Check FBX export called
                        self.assertTrue(mock_fbx.export.called)
                        args, _ = mock_fbx.export.call_args
                        if args:
                            self.assertTrue(args[0].endswith(".fbx"))

    def test_manifest_builder_map_consistency(self):
        """Test that unknown shader attributes are skipped."""
        mock_obj = MagicMock()
        mock_shader_name = "M_Weird"

        # Patch internals
        with unittest.mock.patch(
            "mayatk.mat_utils._mat_utils.MatUtils.get_mats",
            return_value=[mock_shader_name],
        ):
            # Force unknown type
            mock_cmds.nodeType.return_value = "unknownShader_type_xyz"

            manifest = ManifestBuilder.build([mock_obj])

            self.assertIn("materials", manifest)
            # Should NOT be in materials if unknown type
            self.assertNotIn(mock_shader_name, manifest["materials"])


if __name__ == "__main__":
    unittest.main()
