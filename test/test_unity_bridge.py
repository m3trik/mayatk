# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.unity_bridge.

Maya-side coverage for the Unity hand-off: the engine resolves the selection, exports an FBX
(shared MayaExportMixin), and copies it into a Unity project's ``Assets/`` (shared
unitytk.CopyToAssetsDeliverer Strategy). No real Unity is needed -- the project is just a folder
with an ``Assets/`` directory, and ``LAUNCH_MODE`` defaults to no-launch.

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
        self.assertEqual(d["LAUNCH_MODE"], "")  # no-launch default
        self.assertTrue(d["INCLUDE_MATERIALS"])
        self.assertEqual(d["SCOPE"], "selected")
        self.assertEqual(d["UNITY_VERSION"], "")  # auto/newest

    def test_slot_single_delivery_mode_no_studio(self):
        """The slot exposes one copy-to-Assets target; the 'Unity Studio' mode is gone.

        Unity Studio is a separate paid, browser-based product (Unity Cloud Asset
        Manager), not this desktop FBX hand-off, so it was removed.
        """
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots as S

        # One mode, friendly label over the internal stem (matches the deliverer's stem).
        self.assertEqual(S.MODE_COPY, "copy_to_assets")
        self.assertEqual(S.MODE_LABELS, {S.MODE_COPY: "Copy to Project"})
        self.assertEqual(S.list_template_modes(S), [(S.MODE_COPY, "")])
        # No leftover Unity Studio surface.
        self.assertFalse(hasattr(S, "MODE_STUDIO"))
        self.assertFalse(hasattr(S, "MODE_EXISTING"))
        self.assertFalse(hasattr(S, "MODE_PARAM_KEYS"))

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
            params={"ASSETS_SUBDIR": "Models", "ASSET_NAME": ""},
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
            params={"ASSET_NAME": "Custom/Name"},
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


class TestUnityScopeResolution(MayaTkTestCase):
    """Scope resolution on the slot (``_resolve_scope_objects`` is self-free, so it
    runs without building the Qt panel)."""

    def _resolve(self, scope):
        from mayatk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots

        # The method doesn't touch ``self``; a bare instance is enough.
        return UnityBridgeSlots._resolve_scope_objects(object(), scope)

    def _mesh_count(self, objs):
        return len([o for o in objs if cmds.objectType(o) == "mesh"])

    def test_scope_all_gathers_every_scene_mesh(self):
        cmds.polyCube()
        cmds.polySphere()
        cmds.select(clear=True)  # 'all' ignores the selection
        self.assertGreaterEqual(self._mesh_count(self._resolve("all")), 2)

    def test_scope_selected_uses_selection_only(self):
        cube = cmds.polyCube()[0]
        cmds.polySphere()
        cmds.select(cube, replace=True)
        resolved = self._resolve("selected")
        self.assertTrue(any(cube in str(o) for o in resolved))
        # The unselected sphere transform must not be in the selected scope.
        self.assertFalse(any("Sphere" in str(o) for o in resolved))

    def test_scope_visible_excludes_hidden(self):
        visible = cmds.polyCube(name="VisibleCube")[0]
        hidden = cmds.polyCube(name="HiddenCube")[0]
        cmds.setAttr(f"{hidden}.visibility", 0)
        cmds.select(clear=True)
        resolved = [str(o) for o in self._resolve("visible")]
        self.assertTrue(any("VisibleCube" in o for o in resolved))
        self.assertFalse(any("HiddenCube" in o for o in resolved))


if __name__ == "__main__":
    unittest.main()
