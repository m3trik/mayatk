# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.core_utils.plugins -- the one plug-in door, on a real Maya.

Standalone never shows GUI Maya's untrusted-plug-in prompt, so these check the
loads themselves: Maya's own, by name from its install. Which load the door
issues is pinned on a mocked Maya in ``mock_tests/test_plugins.py``.
"""

import os
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.core_utils.plugins._plugins import Plugins


def _folded(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class TestPlugins(MayaTkTestCase):
    def test_mayas_own_plugin_loads_by_name_from_its_install(self):
        if Plugins.is_loaded("objExport"):
            cmds.unloadPlugin("objExport")
        Plugins.load("objExport")
        self.assertTrue(Plugins.is_loaded("objExport"))
        path = _folded(cmds.pluginInfo("objExport", query=True, path=True))
        self.assertTrue(path.startswith(_folded(os.environ["MAYA_LOCATION"])), path)

    def test_an_unknown_plugin_raises_and_is_not_available(self):
        self.assertFalse(Plugins.available("not_a_real_plugin_xyz123"))
        with self.assertRaises(Plugins.LoadError):
            Plugins.load("not_a_real_plugin_xyz123")

    def test_an_installed_plugin_is_available_without_loading(self):
        """``available`` reads Maya's own search path, where every Maya-shipped
        plug-in folder sits from startup."""
        if Plugins.is_loaded("objExport"):
            cmds.unloadPlugin("objExport")
        self.assertTrue(Plugins.available("objExport"))
        self.assertFalse(Plugins.is_loaded("objExport"))


if __name__ == "__main__":
    unittest.main()
