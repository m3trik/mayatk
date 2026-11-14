# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk's operations: the ``maya`` host (MayaSceneFiles) and its declarations.

The round trip launches a FRESH mayapy per call, never this session: that is
the batch runner's contract, so it is what the test exercises (an MCP session
keeps one instance instead: pythontk's ``test_session_operation_host.py``).
"""

import os
import unittest

import maya.cmds as cmds
import pythontk as ptk

import mayatk.operations as declarations
from mayatk.env_utils.maya_scene_files import MayaSceneFiles

from base_test import MayaTkTestCase


class TestMayaOperations(MayaTkTestCase):
    def test_the_committed_catalog_is_current(self):
        self.assertEqual(ptk.OperationProvider.problems(declarations.__name__), [])

    def test_find_mayapy_finds_an_interpreter(self):
        path = MayaSceneFiles.find_mayapy()
        self.assertTrue(path and os.path.isfile(path), path)
        self.assertTrue(ptk.AppLauncher.looks_like_python(path), path)

    def test_code_runs_in_a_fresh_mayapy_saves_what_it_saves_and_is_never_planned(self):
        store = ptk.TempArtifacts.scoped("mtk_ops_test")
        scene = os.path.join(store.dir_path(), "ops_scene.ma")
        cmds.file(new=True, force=True)
        cmds.shadingNode("lambert", asShader=True, name="M_old")
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii")
        catalog = ptk.OperationCatalog.discover(
            ["mayatk.operations"], hosts={"maya": MayaSceneFiles.operation_host()}
        )
        args = {"code": 'import maya.cmds as cmds\ncmds.rename("M_old", "M_new")'}

        plan = catalog.run("maya.python.exec", args, scenes=[scene])
        self.assertEqual(plan.status, "failed")
        self.assertIn("no dry run", plan.error)  # code has no plan: apply runs it
        cmds.file(scene, open=True, force=True)
        self.assertTrue(cmds.objExists("M_old"), "a refused plan touched the scene")

        left = catalog.run("maya.python.exec", args, apply=True, scenes=[scene])
        self.assertTrue(left.ok, left.error)
        cmds.file(scene, open=True, force=True)
        self.assertTrue(cmds.objExists("M_old"), "code that saved nothing changed the file")
        saved = {"code": args["code"] + "\ncmds.file(save=True)"}
        done = catalog.run("maya.python.exec", saved, apply=True, scenes=[scene])
        self.assertTrue(done.ok, done.error)
        cmds.file(scene, open=True, force=True)
        self.assertEqual((cmds.objExists("M_new"), cmds.objExists("M_old")), (True, False))
        cmds.file(new=True, force=True)
        store.cleanup()


if __name__ == "__main__":
    unittest.main()
