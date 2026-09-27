# !/usr/bin/python
# coding=utf-8
"""``MayaBridgeSlotsBase.resolve_scope_objects`` precedence, with no Maya.

The scene reads are mocked (``maya.cmds`` from this dir's conftest, the export
mixin's static visible read patched); what is pinned is WHICH read answers each
Scope word and what happens when a read cannot answer -- the precedence every
Maya bridge shares. The reads themselves are covered against a real scene by
``test_unity_bridge.TestUnityScopeResolution`` / ``test_blender_bridge``.
"""

import types
import unittest
from unittest import mock


class TestScopePrecedence(unittest.TestCase):
    def setUp(self):
        import maya.cmds as cmds

        self.cmds = cmds
        self._saved_ls = cmds.ls
        cmds.ls = mock.Mock(side_effect=self._ls)
        self.addCleanup(setattr, cmds, "ls", self._saved_ls)

        from mayatk.env_utils import handoff_export

        patcher = mock.patch.object(
            handoff_export.MayaExportMixin,
            "_visible_objects",
            staticmethod(lambda: ["|visible"]),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _ls(*args, **kwargs):
        if kwargs.get("selection"):
            return ["|sel"]
        if kwargs.get("type") == "mesh":
            return ["|meshShape"]
        return []

    @staticmethod
    def _resolve(scope, owner):
        from mayatk.ui_utils.maya_bridge_slots_base import MayaBridgeSlotsBase

        return MayaBridgeSlotsBase.resolve_scope_objects(owner, scope)

    def test_selected_and_unknown_read_the_selection(self):
        owner = types.SimpleNamespace()  # no .bridge: must not be needed
        self.assertEqual(self._resolve("selected", owner), ["|sel"])
        self.assertEqual(self._resolve("bogus", owner), ["|sel"])

    def test_all_prefers_the_bridges_whole_scene_hook(self):
        bridge = types.SimpleNamespace(_scene_objects=lambda: ["|root"])
        owner = types.SimpleNamespace(bridge=bridge)
        self.assertEqual(self._resolve("all", owner), ["|root"])

    def test_all_without_a_hook_answer_falls_back_to_the_meshes(self):
        no_answer = types.SimpleNamespace(
            bridge=types.SimpleNamespace(_scene_objects=lambda: None)
        )
        self.assertEqual(self._resolve("all", no_answer), ["|meshShape"])
        no_bridge = types.SimpleNamespace()
        self.assertEqual(self._resolve("all", no_bridge), ["|meshShape"])

    def test_an_empty_scene_is_not_widened_or_narrowed(self):
        owner = types.SimpleNamespace(
            bridge=types.SimpleNamespace(_scene_objects=lambda: [])
        )
        self.assertEqual(self._resolve("all", owner), [])

    def test_visible_reads_the_export_mixin_without_a_bridge(self):
        owner = types.SimpleNamespace()
        self.assertEqual(self._resolve("visible", owner), ["|visible"])


if __name__ == "__main__":
    unittest.main()
