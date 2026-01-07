import unittest
import logging
import os
import sys

try:
    import maya.cmds as cmds
    import pymel.core as pm
except ImportError:
    pass

from mayatk.mat_utils.game_shader import GameShader
from mayatk.test.base_test import MayaTkTestCase


class TestMsaoFbxExport(MayaTkTestCase):
    def setUp(self):
        super(TestMsaoFbxExport, self).setUp()
        self.gs = GameShader()

    def test_msao_connection_creates_dummy_attr(self):
        """Verify that connecting an MSAO map creates the dummy attribute for FBX export."""
        if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
            cmds.loadPlugin("shaderFXPlugin")

        # Create shader
        shader = self.gs.setup_stringray_node("TestShader", opacity=False)

        # Create dummy texture file
        tex_path = os.path.abspath("test_msao.png")
        # We don't need the file to exist for the connection logic, but let's be safe
        with open(tex_path, "w") as f:
            f.write("dummy")

        try:
            # Connect MSAO
            self.gs.connect_stingray_nodes(tex_path, "MSAO", shader)

            # Check if dummy attribute exists
            self.assertTrue(
                shader.hasAttr("MSAO_Map"), "MSAO_Map attribute should exist"
            )

            # Check connection
            # Find file node
            file_nodes = pm.ls(type="file")
            self.assertTrue(len(file_nodes) > 0, "File node should be created")
            file_node = file_nodes[0]

            # Check if file node is connected to MSAO_Map
            self.assertTrue(
                pm.isConnected(file_node.outColor, shader.MSAO_Map),
                "File node should be connected to MSAO_Map",
            )

        finally:
            if os.path.exists(tex_path):
                os.remove(tex_path)
