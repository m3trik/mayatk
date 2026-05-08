# !/usr/bin/python
# coding=utf-8
"""Regression tests for mayatk.light_utils.hdr_manager.

Bug fixed 2026-05-07: PyMEL-style attribute proxies
(``self.hdr_env_transform.hiddenInOutliner.set(1)``,
``cmds.connectAttr(file_node.outColor, node.color)``,
``file_node.fileTextureName.set(...)``, ``node.camera.set(state)``,
``node.rotateY.get()``) were converted to ``cmds.setAttr/connectAttr/getAttr``
with f-string plug paths.

Also added: auto-load of the mtoa (Arnold) plugin via
``HdrManager.ensure_plugin_loaded`` — needed because all paths through
the class touch ``aiSkyDomeLight``.
"""
import os
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.light_utils.hdr_manager import HdrManager


def _arnold_available() -> bool:
    """Return True if mtoa can be loaded (plugin installed and loadable)."""
    try:
        if cmds.pluginInfo("mtoa", query=True, loaded=True):
            return True
        cmds.loadPlugin("mtoa")
        return True
    except Exception:
        return False


@unittest.skipUnless(_arnold_available(), "Arnold (mtoa) plugin not available")
class TestHdrManager(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.mgr = HdrManager()

    def tearDown(self):
        for n in cmds.ls(HdrManager.hdr_env_name, exactType="aiSkyDomeLight") or []:
            transforms = cmds.listRelatives(n, parent=True, fullPath=True) or []
            cmds.delete(transforms or n)
        super().tearDown()

    def test_ensure_plugin_loaded(self):
        """ensure_plugin_loaded should return True when Arnold is available."""
        self.assertTrue(HdrManager.ensure_plugin_loaded())
        self.assertTrue(cmds.pluginInfo("mtoa", query=True, loaded=True))

    def test_hdr_env_setter_creates_skydome(self):
        """Setting hdr_env on an empty scene must create the aiSkyDomeLight."""
        self.assertIsNone(self.mgr.hdr_env)

        self.mgr.hdr_env = "C:/tmp/dummy.exr"

        self.assertIsNotNone(self.mgr.hdr_env, "hdr_env should now resolve")
        self.assertTrue(cmds.objectType(self.mgr.hdr_env) == "aiSkyDomeLight")

    def test_hdr_env_setter_sets_file_texture(self):
        """The file node's fileTextureName must hold the assigned path."""
        path = "C:/tmp/test_hdr.exr"
        self.mgr.hdr_env = path

        skydome = self.mgr.hdr_env
        file_nodes = cmds.listConnections(
            f"{skydome}.color", source=True, destination=False, type="file"
        ) or []
        self.assertTrue(file_nodes, "Expected a file node connected to skydome.color")
        actual = cmds.getAttr(f"{file_nodes[0]}.fileTextureName")
        self.assertEqual(actual, path)

    def test_set_hdr_map_visibility_toggles_camera_attr(self):
        """set_hdr_map_visibility must drive the skydome's .camera attribute."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        skydome = self.mgr.hdr_env

        self.mgr.set_hdr_map_visibility(True)
        self.assertEqual(cmds.getAttr(f"{skydome}.camera"), 1)

        self.mgr.set_hdr_map_visibility(False)
        self.assertEqual(cmds.getAttr(f"{skydome}.camera"), 0)

    def test_hdr_env_transform_returns_string(self):
        """The transform property must return a cmds-style string path."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        transform = self.mgr.hdr_env_transform
        self.assertIsInstance(transform, str)
        self.assertTrue(cmds.objExists(transform))


if __name__ == "__main__":
    unittest.main(verbosity=2)
