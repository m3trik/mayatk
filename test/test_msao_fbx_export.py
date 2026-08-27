import os
import shutil
import tempfile

try:
    import maya.cmds as cmds
except ImportError:
    pass

import pythontk as ptk
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

        # A REAL packed RGBA map (R=metallic, G=AO, A=smoothness). Splitting the
        # channels genuinely opens the file, so a stub PIL cannot identify makes
        # extraction correctly return None and nothing gets wired.
        #
        # In a TEMP dir, never the CWD: extraction writes the derived
        # <stem>_Metallic / _Ambient_Occlusion / _Roughness maps BESIDE the
        # source, and left in the repo root those strays are found by a later
        # suite's texture resolver (Windows lookups are case-insensitive).
        tmp_dir = tempfile.mkdtemp(prefix="msao_fbx_")
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        tex_path = os.path.join(tmp_dir, "test_msao.png")
        ptk.ImgUtils.save_image(
            ptk.ImgUtils.create_image("RGBA", (16, 16), (200, 128, 0, 64)), tex_path
        )

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
        mtl_inputs = (
            cmds.listConnections(
                f"{shader}.TEX_metallic_map", source=True, destination=False
            )
            or []
        )
        self.assertTrue(
            any(node in file_nodes for node in mtl_inputs),
            "TEX_metallic_map should be driven by the MSAO file node",
        )
