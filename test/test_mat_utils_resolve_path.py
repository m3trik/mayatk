# !/usr/bin/python
# coding=utf-8
"""
Unit tests for MatUtils.resolve_path and the node_type filter of
MatUtils.get_scene_mats.

These are the entry points that mayatk.MatUpdater relies on for every file
node and for material discovery. They had no direct coverage prior to this
suite.
"""
import os
import shutil
import tempfile
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.mat_utils._mat_utils import MatUtils


class ResolvePathTest(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="resolve_path_")
        self.real = os.path.join(self.tmp, "real.png").replace("\\", "/")
        with open(self.real, "w") as f:
            f.write("dummy")

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_none_returns_none(self):
        self.assertIsNone(MatUtils.resolve_path(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(MatUtils.resolve_path(""))

    def test_absolute_path_passthrough(self):
        out = MatUtils.resolve_path(self.real)
        self.assertEqual(os.path.normpath(out), os.path.normpath(self.real))

    def test_nonexistent_returns_none(self):
        out = MatUtils.resolve_path(os.path.join(self.tmp, "does_not_exist.png"))
        self.assertIsNone(out)

    def test_env_var_expansion(self):
        os.environ["MTK_TEST_TEX_DIR"] = self.tmp
        try:
            ref = "$MTK_TEST_TEX_DIR/real.png"
            out = MatUtils.resolve_path(ref)
            self.assertIsNotNone(out, f"Env-var path should resolve: {ref}")
            self.assertEqual(os.path.normpath(out), os.path.normpath(self.real))
        finally:
            del os.environ["MTK_TEST_TEX_DIR"]

    def test_udim_token_resolution(self):
        """A path with <UDIM> should resolve when 1001 tile exists."""
        udim_real = os.path.join(self.tmp, "tex_1001.png")
        with open(udim_real, "w") as f:
            f.write("dummy")
        ref = os.path.join(self.tmp, "tex_<UDIM>.png").replace("\\", "/")
        out = MatUtils.resolve_path(ref)
        self.assertIsNotNone(out, "UDIM-token path should resolve via 1001 tile")

    def test_resolves_from_workspace_sourceimages(self):
        """A bare basename should resolve when found under workspace sourceimages."""
        ws_root = tempfile.mkdtemp(prefix="resolve_ws_")
        si = os.path.join(ws_root, "sourceimages")
        os.makedirs(si, exist_ok=True)
        candidate = os.path.join(si, "ws_only.png")
        with open(candidate, "w") as f:
            f.write("dummy")
        original_ws = cmds.workspace(q=True, rd=True)
        try:
            cmds.workspace(ws_root, openWorkspace=True)
            out = MatUtils.resolve_path("ws_only.png")
            self.assertIsNotNone(out, "Bare basename should resolve via sourceimages")
            self.assertTrue(os.path.exists(out))
        finally:
            try:
                if original_ws and os.path.isdir(original_ws):
                    cmds.workspace(original_ws, openWorkspace=True)
            except Exception:
                pass
            shutil.rmtree(ws_root, ignore_errors=True)


class GetSceneMatsNodeTypeTest(MayaTkTestCase):
    """Lock down the node_type filter used by mat_updater to discover supported mats."""

    def setUp(self):
        super().setUp()
        self.lam = cmds.shadingNode("lambert", asShader=True, name="t_lambert")
        self.ss = cmds.shadingNode(
            "standardSurface", asShader=True, name="t_standard"
        )

    def test_node_type_filter_single(self):
        mats = MatUtils.get_scene_mats(node_type="standardSurface")
        self.assertIn(self.ss, mats)
        self.assertNotIn(self.lam, mats)

    def test_node_type_filter_list(self):
        mats = MatUtils.get_scene_mats(node_type=["standardSurface", "lambert"])
        self.assertIn(self.ss, mats)
        self.assertIn(self.lam, mats)

    def test_node_type_filter_empty_when_no_match(self):
        mats = MatUtils.get_scene_mats(node_type=["StingrayPBS"])
        # No StingrayPBS in this scene
        self.assertEqual(mats, [])

    def test_no_filter_returns_all(self):
        mats = MatUtils.get_scene_mats()
        self.assertIn(self.ss, mats)
        self.assertIn(self.lam, mats)


if __name__ == "__main__":
    unittest.main()
