# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.blender_bridge.

Maya-side regression coverage for the template-driven send to Blender. The actual Blender launch
and the FBX export are stubbed (launching Blender would open a GUI; export is covered by the
``FbxUtils`` suite), so these tests pin the bridge's own logic:

- executable discovery never raises and honors ``$BLENDER_EXE``,
- template discovery + mode parsing,
- the rendered Blender script substitutes the FBX path + parameter values,
- ``send`` derives FBX options from params, writes the script, and launches ``--python``,
- the strip-materials path exports shader-less duplicates and leaves the originals untouched.

Run inside a live Maya session via ``run_tests.py`` (``run_tests.py blender_bridge``).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import maya.cmds as cmds

from mayatk.env_utils.blender_bridge import _blender_bridge as bb
from mayatk.env_utils.blender_bridge._blender_bridge import (
    BlenderBridge,
    list_template_modes,
    template_modes,
    _TEMPLATE_DIR,
)
from mayatk.env_utils.blender_bridge import parameters as params

# Shared engine internals moved upstream: the Maya FBX export lives on
# MayaExportMixin (handoff_export); the fresh-app launch lives on the pythontk
# ScriptLaunchDeliverer (app_handoff). Patch them where they're actually looked up.
from mayatk.env_utils import handoff_export
from pythontk.core_utils import app_handoff as bridge_base

from base_test import MayaTkTestCase


class TestBlenderBridgeDiscovery(unittest.TestCase):
    """Executable discovery -- pure."""

    def test_blender_path_no_raise(self):
        self.assertTrue(BlenderBridge().blender_path is None or isinstance(BlenderBridge().blender_path, str))

    def test_env_override(self):
        fd, path = tempfile.mkstemp(suffix=".exe", prefix="fake_blender_")
        os.close(fd)
        try:
            with mock.patch.dict(os.environ, {"BLENDER_EXE": path}):
                self.assertEqual(BlenderBridge().blender_path, path)
        finally:
            Path(path).unlink(missing_ok=True)


class TestBlenderBridgeTemplates(unittest.TestCase):
    """Template discovery + rendering -- pure (no Maya geometry)."""

    def test_list_template_modes(self):
        pairs = list_template_modes()
        stems = {t for t, _ in pairs}
        # The three near-identical recipes collapsed into one options-driven template.
        self.assertEqual(stems, {"import"})
        self.assertTrue(all(mode == "send_to" for _, mode in pairs))

    def test_template_modes_parsed(self):
        self.assertEqual(template_modes(_TEMPLATE_DIR / "import.py"), ("send_to",))

    def test_render_substitutes_path_and_params(self):
        merged = params.defaults()
        rendered = BlenderBridge().render_template("import", r"C:\t\x.fbx", merged)
        self.assertIn("bpy.ops.import_scene.fbx", rendered)
        self.assertIn('FBX_PATH = r"C:/t/x.fbx"', rendered)  # forward-slashed, no __KEY__ left
        self.assertNotIn("__", rendered)  # every placeholder substituted
        self.assertIn("APPLY_UNIT_SCALE = True", rendered)
        self.assertIn("INCLUDE_ANIMATION = False", rendered)
        # The export-options comment was substituted too (panel-visibility echo).
        self.assertIn("materials=True", rendered)

    def test_import_exposes_scene_and_frame_options(self):
        # The unified template exposes both scene-behavior knobs so the panel shows them.
        used = params.referenced_keys((_TEMPLATE_DIR / "import.py").read_text())
        self.assertIn("FRAME_VIEW", used)
        self.assertIn("CLEAR_SCENE", used)


class TestBlenderBridgeSend(MayaTkTestCase):
    """Send flow -- Blender launch + FBX export stubbed; strip path runs for real."""

    def setUp(self):
        super().setUp()
        self.bridge = BlenderBridge(blender_path="C:/fake/blender.exe")

    def _patches(self, export_side_effect=None):
        return (
            mock.patch.object(handoff_export.FbxUtils, "export", side_effect=export_side_effect, return_value="x.fbx"),
            mock.patch.object(handoff_export.FbxUtils, "load_plugin"),
            mock.patch.object(bridge_base.AppLauncher, "launch", return_value=object()),
        )

    def test_send_export_and_launch_args(self):
        cube = cmds.polyCube(name="bb_send")[0]
        export, load, launch = self._patches()
        with export as m_export, load, launch as m_launch:
            result = self.bridge.send(
                [cube], template="import",
                params={"EMBED_TEXTURES": True, "TRIANGULATE": True, "INCLUDE_ANIMATION": False},
            )
        opts = m_export.call_args.kwargs["options"]
        self.assertTrue(opts["FBXExportEmbeddedTextures"])
        self.assertTrue(opts["FBXExportTriangulate"])
        self.assertFalse(opts["FBXExportBakeComplexAnimation"])
        args = m_launch.call_args.kwargs.get("args") or m_launch.call_args.args[1]
        self.assertEqual(args[0], "--python")
        script = result["script"]
        self.assertEqual(args[1], script)
        self.assertTrue(m_launch.call_args.kwargs.get("detached"))
        self.assertIn("import_scene.fbx", Path(script).read_text(encoding="utf-8"))
        Path(script).unlink(missing_ok=True)

    def test_send_bad_template_returns_none(self):
        cube = cmds.polyCube(name="bb_badtpl")[0]
        export, load, launch = self._patches()
        with export, load, launch:
            self.assertIsNone(self.bridge.send([cube], template="does_not_exist"))

    def test_strip_materials_exports_shaderless_copies(self):
        cube = cmds.polyCube(name="bb_strip")[0]
        shader = cmds.shadingNode("lambert", asShader=True, name="bb_lam")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="bb_lamSG")
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        captured = {}

        def capture(**kwargs):
            objs = kwargs["objects"]
            captured["objects"] = list(objs)
            sgs = set()
            for o in objs:
                shapes = cmds.listRelatives(o, shapes=True, fullPath=True) or []
                sgs.update(cmds.listConnections(shapes, type="shadingEngine") or [])
            captured["shading_engines"] = sgs
            return "x.fbx"

        export, load, launch = self._patches(export_side_effect=capture)
        with export, load, launch:
            self.bridge.send([cube], template="import", params={"INCLUDE_MATERIALS": False})

        self.assertTrue(captured["objects"])
        self.assertNotIn(cube, captured["objects"])
        self.assertIn("initialShadingGroup", captured["shading_engines"])
        self.assertNotIn(sg, captured["shading_engines"])
        for dup in captured["objects"]:
            self.assertFalse(cmds.objExists(dup), f"temp duplicate {dup} not deleted")
        orig_shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        self.assertIn(sg, cmds.listConnections(orig_shape, type="shadingEngine") or [])


if __name__ == "__main__":
    unittest.main()
