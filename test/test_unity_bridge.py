# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.unity_bridge.

Maya-side coverage for the Unity hand-off: the engine resolves the selection, exports an FBX
(shared MayaExportMixin), and copies it into a Unity project's ``Assets/`` (shared
unitytk.CopyToAssetsDeliverer Strategy). No real Unity is needed -- the project is just a folder
with an ``Assets/`` directory, and ``LAUNCH_EDITOR`` defaults off.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py unity_bridge``).
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import maya.cmds as cmds

from pythontk.core_utils.app_handoff import HandoffRequest
from mayatk.env_utils.unity_bridge._unity_bridge import UnityBridge, list_delivery_modes

from base_test import MayaTkTestCase


def _copy_req():
    return HandoffRequest(template="copy_to_assets", mode="send_to")


class TestUnityBridgeUnit(unittest.TestCase):
    """Pure (no Maya geometry) -- composition + delivery modes."""

    def test_delivery_modes(self):
        self.assertEqual(list_delivery_modes(), [("copy_to_assets", "")])

    def test_params_defaults(self):
        d = UnityBridge().params_defaults()
        self.assertEqual(d["ASSETS_SUBDIR"], "Imported")
        self.assertFalse(d["LAUNCH_EDITOR"])
        self.assertTrue(d["INCLUDE_MATERIALS"])

    def test_preflight_requires_project(self):
        br = UnityBridge()
        self.assertFalse(br.deliverer.preflight(br, _copy_req()))  # no project set

    def test_preflight_rejects_non_project_dir(self):
        tmp = tempfile.mkdtemp()
        try:
            br = UnityBridge(project_path=tmp)  # no Assets/ subdir
            self.assertFalse(br.deliverer.preflight(br, _copy_req()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestUnityBridgeSend(MayaTkTestCase):
    """End-to-end: real Maya export -> copy into a temp Unity project's Assets/."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="unity_bridge_test_"))
        self.project = self.tmp / "UnityProj"
        (self.project / "Assets").mkdir(parents=True)
        self.bridge = UnityBridge(project_path=str(self.project))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_send_copies_named_fbx_into_assets_subdir(self):
        cube = cmds.polyCube(name="UnityHero")[0]
        result = self.bridge.send(
            [cube], template="copy_to_assets", mode="send_to",
            params={"ASSETS_SUBDIR": "Models", "ASSET_NAME": "", "LAUNCH_EDITOR": False},
        )
        self.assertIsNotNone(result, "send returned None (delivery failed)")
        dest = Path(result["asset"])
        # Asset named after the selected object, under Assets/Models.
        self.assertEqual(dest, self.project / "Assets" / "Models" / "UnityHero.fbx")
        self.assertTrue(dest.is_file(), f"FBX not copied to {dest}")
        self.assertGreater(dest.stat().st_size, 0, "copied FBX is empty")
        self.assertFalse(result["launched"])

    def test_send_default_subdir_and_explicit_name(self):
        cube = cmds.polyCube(name="UnityCube")[0]
        result = self.bridge.send(
            [cube], template="copy_to_assets", mode="send_to",
            params={"ASSET_NAME": "Custom/Name", "LAUNCH_EDITOR": False},
        )
        dest = Path(result["asset"])
        # Default subdir 'Imported'; name sanitized.
        self.assertEqual(dest, self.project / "Assets" / "Imported" / "Custom_Name.fbx")
        self.assertTrue(dest.is_file())

    def test_send_aborts_when_no_project(self):
        cube = cmds.polyCube(name="UnityNoProj")[0]
        self.bridge.project_path = str(self.tmp / "not_a_project")
        result = self.bridge.send([cube], template="copy_to_assets", mode="send_to")
        self.assertIsNone(result)

    def test_send_aborts_with_empty_selection(self):
        result = self.bridge.send([], template="copy_to_assets", mode="send_to")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
