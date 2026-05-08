# !/usr/bin/python
# coding=utf-8
"""Regression tests for mayatk.mat_utils.texture_path_editor.

Bug fixed 2026-05-07: ``_resolve_absolute_texture_path`` called
``file_node.fileTextureName.get()`` — a PyMEL idiom — against the
cmds-style string node names that the rest of the file uses.
"""
import os
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.mat_utils.texture_path_editor import TexturePathEditorSlots


class TestResolveAbsoluteTexturePath(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.file_node = cmds.shadingNode("file", asTexture=True, name="tpe_file")
        # Bypass __init__ — the method only needs `self`.
        self.slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)

    def test_returns_absolute_path_for_absolute_input(self):
        abs_path = os.path.abspath(__file__)
        cmds.setAttr(f"{self.file_node}.fileTextureName", abs_path, type="string")

        result = self.slot._resolve_absolute_texture_path(self.file_node)

        self.assertEqual(os.path.normcase(result), os.path.normcase(abs_path))

    def test_returns_empty_when_unset(self):
        # fileTextureName starts empty
        result = self.slot._resolve_absolute_texture_path(self.file_node)
        self.assertEqual(result, "")

    def test_does_not_crash_on_string_node(self):
        """Regression: must not raise AttributeError on a string file_node."""
        cmds.setAttr(
            f"{self.file_node}.fileTextureName", "C:/tmp/x.png", type="string"
        )
        # Bug pre-fix: AttributeError: 'str' object has no attribute 'fileTextureName'
        self.slot._resolve_absolute_texture_path(self.file_node)


if __name__ == "__main__":
    unittest.main(verbosity=2)
