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
from base_test import MayaTkTestCase


class TestMsaoFbxExport(MayaTkTestCase):
    def setUp(self):
        super(TestMsaoFbxExport, self).setUp()
        self.gs = GameShader()

    def test_msao_connection_wires_metallic_ao_roughness(self):
        """MSAO map (Unity HDRP mask) is split into metallic/AO/roughness channels.

        Updated post-refactor: MSAO no longer creates a single ``MSAO_Map``
        attribute. The texture is split per Unity HDRP convention:
        R → TEX_metallic_map, G → TEX_ao_map, A → invert → TEX_roughness_map.
        """
        if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
            cmds.loadPlugin("shaderFXPlugin")

        # Create shader
        shader = self.gs.setup_stringray_node("TestShader", opacity=False)

        # Create dummy texture file
        tex_path = os.path.abspath("test_msao.png")
        with open(tex_path, "w") as f:
            f.write("dummy")

        try:
            self.gs.connect_stingray_nodes(tex_path, "MSAO", shader)

            file_nodes = cmds.ls(type="file") or []
            self.assertTrue(file_nodes, "File node should be created")

            # use_*_map toggles must be enabled by the MSAO branch.
            for flag in ("use_metallic_map", "use_ao_map", "use_roughness_map"):
                self.assertTrue(
                    cmds.attributeQuery(flag, node=str(shader), exists=True),
                    f"{flag} should exist on Stingray shader",
                )
                self.assertEqual(cmds.getAttr(f"{shader}.{flag}"), 1)

            # Metallic channel must come from one of the file nodes.
            mtl_inputs = cmds.listConnections(
                f"{shader}.TEX_metallic_map", source=True, destination=False
            ) or []
            self.assertTrue(
                any(node in file_nodes for node in mtl_inputs),
                "TEX_metallic_map should be driven by the MSAO file node",
            )

        finally:
            if os.path.exists(tex_path):
                os.remove(tex_path)
