# !/usr/bin/python
# coding=utf-8
"""Maya-side contract test for the shared workspace format
(``pythontk.file_utils.workspace``).

Blender (via blendertk's current-workspace resolver) and Maya share one project
folder through ``workspace.mel``; pythontk's codec is the no-DCC writer. This
suite proves the contract against the real Maya runtime, both directions:

- Maya **opens** a pythontk-created workspace and resolves its file rules —
  including a foreign (Blender-side) rule Maya doesn't recognize.
- Maya's own **rewrite** (``workspace -saveWorkspace``) preserves that foreign
  rule, and the codec parses Maya's output back — the round-trip that makes the
  marker a shared store rather than a one-way export.

The codec's pure-disk behavior (merge/preserve semantics, discovery, model) is
covered upstream in ``pythontk/test/test_workspace.py``; only the pieces that
need a live Maya live here.
"""
import os
import shutil
import unittest
import uuid

import pythontk as ptk

from base_test import MayaTkTestCase
import maya.cmds as cmds

TEMP_TESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_tests")


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


class WorkspaceMelContractTest(MayaTkTestCase):
    """pythontk workspace.mel codec ↔ live ``cmds.workspace``."""

    def setUp(self):
        super().setUp()
        self._orig_ws = cmds.workspace(q=True, rootDirectory=True)
        self._tmp = os.path.join(TEMP_TESTS, f"ws_contract_{uuid.uuid4().hex[:8]}")

    def tearDown(self):
        try:
            if self._orig_ws and os.path.isdir(self._orig_ws):
                try:
                    cmds.workspace(self._orig_ws, openWorkspace=True)
                except RuntimeError:
                    cmds.workspace(directory=self._orig_ws)
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def test_maya_opens_ptk_workspace(self):
        """Maya opens a codec-written project and resolves its rules (foreign one included)."""
        ws = ptk.Workspace.create(self._tmp)
        # a Blender-side rule Maya has no notion of, merged in by the codec
        ptk.write_workspace_mel(ws.marker_path, {"blenderScene": "scenes"})

        cmds.workspace(self._tmp, openWorkspace=True)

        self.assertEqual(_norm(cmds.workspace(q=True, rootDirectory=True)), _norm(self._tmp))
        self.assertEqual(cmds.workspace(fileRuleEntry="scene"), "scenes")
        self.assertEqual(cmds.workspace(fileRuleEntry="sourceImages"), "sourceimages")
        self.assertEqual(cmds.workspace(fileRuleEntry="blenderScene"), "scenes")
        # rule resolution goes through Maya's own expander
        self.assertEqual(
            _norm(cmds.workspace(expandName=cmds.workspace(fileRuleEntry="scene"))),
            _norm(os.path.join(self._tmp, "scenes")),
        )

    def test_maya_rewrite_preserves_foreign_rule(self):
        """Maya's saveWorkspace keeps the Blender-side rule; the codec parses Maya's output."""
        ws = ptk.Workspace.create(self._tmp, create_dirs=False)
        ptk.write_workspace_mel(ws.marker_path, {"blenderScene": "scenes"})

        cmds.workspace(self._tmp, openWorkspace=True)
        cmds.workspace(fileRule=("clips", "clips"))  # a Maya-side edit on top
        cmds.workspace(saveWorkspace=True)

        rules = ptk.parse_workspace_mel(ws.marker_path)
        self.assertEqual(rules.get("blenderScene"), "scenes")  # survived Maya's rewrite
        self.assertEqual(rules.get("clips"), "clips")  # Maya's addition visible to the codec
        self.assertEqual(rules.get("scene"), "scenes")  # template rules intact

    def test_find_containing_matches_maya_root(self):
        """The shared walk-up resolver agrees with Maya about which project owns a scene."""
        ptk.Workspace.create(self._tmp)
        scene = os.path.join(self._tmp, "scenes", "probe.ma")
        os.makedirs(os.path.dirname(scene), exist_ok=True)
        cmds.workspace(self._tmp, openWorkspace=True)
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii")

        found = ptk.Workspace.find_containing(scene)
        self.assertIsNotNone(found)
        self.assertEqual(_norm(found.root), _norm(cmds.workspace(q=True, rootDirectory=True)))


if __name__ == "__main__":
    unittest.main()
