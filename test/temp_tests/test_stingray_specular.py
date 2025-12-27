import unittest
import pymel.core as pm
import os
from mayatk.mat_utils.game_shader import GameShader
from test.base_test import MayaTkTestCase


class TestStingraySpecular(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.shader = GameShader()

        # Load plugin
        if not pm.pluginInfo("shaderFXPlugin", query=True, loaded=True):
            try:
                pm.loadPlugin("shaderFXPlugin")
            except:
                self.skipTest("shaderFXPlugin not available")

        self.sr_node = pm.createNode("StingrayPBS")

        # Manually add the attributes to simulate Specular preset
        # In a real scenario, loading the .sfx graph would do this
        if not self.sr_node.hasAttr("TEX_specular_map"):
            self.sr_node.addAttr("TEX_specular_map", at="float3", usedAsColor=True)
            self.sr_node.addAttr(
                "TEX_specular_mapR", at="float", parent="TEX_specular_map"
            )
            self.sr_node.addAttr(
                "TEX_specular_mapG", at="float", parent="TEX_specular_map"
            )
            self.sr_node.addAttr(
                "TEX_specular_mapB", at="float", parent="TEX_specular_map"
            )

        if not self.sr_node.hasAttr("use_specular_map"):
            self.sr_node.addAttr("use_specular_map", at="float")

        if not self.sr_node.hasAttr("TEX_glossiness_map"):
            self.sr_node.addAttr("TEX_glossiness_map", at="float")

        if not self.sr_node.hasAttr("use_glossiness_map"):
            self.sr_node.addAttr("use_glossiness_map", at="float")

    def test_connect_specular(self):
        tex_path = os.path.abspath("temp_specular.png")

        result = self.shader.connect_stingray_nodes(tex_path, "Specular", self.sr_node)
        self.assertTrue(result, "Should return True when connecting Specular map")

        # Verify connection
        connections = self.sr_node.TEX_specular_map.inputs()
        self.assertTrue(connections, "TEX_specular_map should have an input")
        self.assertEqual(connections[0].fileTextureName.get(), tex_path)
        self.assertEqual(self.sr_node.use_specular_map.get(), 1.0)

    def test_connect_glossiness(self):
        tex_path = os.path.abspath("temp_gloss.png")

        result = self.shader.connect_stingray_nodes(
            tex_path, "Glossiness", self.sr_node
        )
        self.assertTrue(result, "Should return True when connecting Glossiness map")

        # Verify connection
        connections = self.sr_node.TEX_glossiness_map.inputs()
        self.assertTrue(connections, "TEX_glossiness_map should have an input")
        self.assertEqual(connections[0].fileTextureName.get(), tex_path)
        self.assertEqual(self.sr_node.use_glossiness_map.get(), 1.0)

    def test_connect_specular_missing_attr(self):
        # Create a node WITHOUT the attributes (Standard preset simulation)
        std_node = pm.createNode("StingrayPBS")
        # Ensure it doesn't have the attributes
        if std_node.hasAttr("TEX_specular_map"):
            std_node.deleteAttr("TEX_specular_map")

        tex_path = os.path.abspath("temp_specular.png")
        result = self.shader.connect_stingray_nodes(tex_path, "Specular", std_node)

        self.assertFalse(result, "Should return False if TEX_specular_map is missing")


if __name__ == "__main__":
    unittest.main()
