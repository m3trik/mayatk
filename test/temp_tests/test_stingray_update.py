import unittest
import pymel.core as pm
import os
from mayatk.mat_utils.game_shader import GameShader
from mayatk.env_utils._env_utils import EnvUtils


class TestStingrayUpdate(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

        self.shader_utils = GameShader()

        # Create StingrayPBS node using the class method to ensure graph is loaded
        # This verifies the fix for batch mode graph loading
        self.shader = self.shader_utils.setup_stringray_node(
            "StingrayPBS1", opacity=False
        )

        # Create dummy textures
        self.textures = {
            "MSAO": "C:/temp/test_MSAO.png",
            "Metallic": "C:/temp/test_Metallic.png",
            "Roughness": "C:/temp/test_Roughness.png",
        }
        for path in self.textures.values():
            if not os.path.exists(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write("dummy")

    def tearDown(self):
        pm.newFile(force=True)
        # Cleanup dummy files
        for path in self.textures.values():
            if os.path.exists(path):
                os.remove(path)

    def _get_inputs(self, node, attr_name):
        """Helper to get inputs from an attribute, checking children if necessary"""
        if not pm.attributeQuery(attr_name, node=node, exists=True):
            return []

        # Check parent connection first
        inputs = pm.listConnections(
            f"{node}.{attr_name}", source=True, destination=False
        )
        if inputs:
            return inputs

        # Check children
        children = pm.attributeQuery(attr_name, node=node, listChildren=True)
        if children:
            for child in children:
                child_inputs = pm.listConnections(
                    f"{node}.{child}", source=True, destination=False
                )
                if child_inputs:
                    inputs.extend(child_inputs)

        return list(set(inputs))  # Unique inputs

    def test_update_network_msao_precedence(self):
        """Test that MSAO map takes precedence over individual maps"""
        textures = [
            self.textures["MSAO"],
            self.textures["Metallic"],
            self.textures["Roughness"],
        ]

        connected = self.shader_utils.update_network(self.shader, textures)

        self.assertIn("MSAO", connected)
        self.assertNotIn("Metallic", connected)
        self.assertNotIn("Roughness", connected)

        # Check if connected
        metallic_inputs = self._get_inputs(self.shader, "TEX_metallic_map")
        self.assertTrue(
            metallic_inputs, "Metallic map not connected to TEX_metallic_map"
        )

    def test_update_network_no_msao(self):
        """Test that individual maps are connected when no MSAO is present"""
        textures = [self.textures["Metallic"], self.textures["Roughness"]]

        connected = self.shader_utils.update_network(self.shader, textures)

        self.assertIn("Metallic", connected)
        self.assertIn("Roughness", connected)

        metallic_inputs = self._get_inputs(self.shader, "TEX_metallic_map")
        self.assertTrue(metallic_inputs, "Metallic map not connected")


if __name__ == "__main__":
    unittest.main()
