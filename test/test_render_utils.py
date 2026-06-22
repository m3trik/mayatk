# !/usr/bin/python
# coding=utf-8
"""Tests for RenderUtils — renderer enumeration / selection and Render-View control.

Enumeration, plugin detection, and renderer selection run against real Maya
standalone. The Render-View / IPR calls are interactive (the procs/commands are
absent or open windows headlessly), so those are exercised with the underlying
``mel.eval`` / ``cmds.arnoldRenderView`` patched to capture the call.

Run headless::

    & "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        o:/Cloud/Code/_scripts/mayatk/test/test_render_utils.py
"""
import os
import sys
import unittest
from unittest import mock

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
mayatk_dir = os.path.join(scripts_dir, "mayatk")
if mayatk_dir not in sys.path:
    sys.path.insert(0, mayatk_dir)

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    print(__file__, error)

import mayatk as mtk

RenderUtils = mtk.RenderUtils


class RenderUtilsTest(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def _require_mtoa(self):
        """Skip the calling test unless the Arnold plugin can be loaded."""
        try:
            if not cmds.pluginInfo("mtoa", query=True, loaded=True):
                cmds.loadPlugin("mtoa", quiet=True)
        except Exception:
            self.skipTest("mtoa (Arnold) plugin not available")
        if not cmds.pluginInfo("mtoa", query=True, loaded=True):
            self.skipTest("mtoa (Arnold) plugin not available")

    # ----------------------------------------------------------- enumeration
    def test_builtins_always_present(self):
        renderers = RenderUtils.get_available_renderers()
        names = [r["name"] for r in renderers]
        self.assertIn("mayaSoftware", names)
        self.assertIn("mayaHardware2", names)

    def test_entry_shape(self):
        for r in RenderUtils.get_available_renderers():
            self.assertEqual(set(r), {"name", "label", "loaded"})
            self.assertIsInstance(r["label"], str)
            self.assertIsInstance(r["loaded"], bool)

    def test_no_duplicate_names(self):
        names = [r["name"] for r in RenderUtils.get_available_renderers()]
        self.assertEqual(len(names), len(set(names)))

    def test_plugin_installed_false_for_unknown(self):
        self.assertFalse(RenderUtils._plugin_installed("not_a_real_plugin_xyz123"))

    # ------------------------------------------------------------- selection
    def test_current_renderer_is_str(self):
        self.assertIsInstance(RenderUtils.current_renderer(), str)

    def test_set_renderer_builtin(self):
        RenderUtils.set_renderer("mayaHardware2")
        self.assertEqual(
            cmds.getAttr("defaultRenderGlobals.currentRenderer"), "mayaHardware2"
        )

    def test_set_renderer_arnold_loads_plugin(self):
        self._require_mtoa()
        RenderUtils.set_renderer("arnold")
        self.assertTrue(cmds.pluginInfo("mtoa", query=True, loaded=True))
        self.assertEqual(
            cmds.getAttr("defaultRenderGlobals.currentRenderer"), "arnold"
        )

    # ----------------------------------------------------------- render view
    def test_render_camera_targets_render_view(self):
        captured = {}
        orig = mel.eval
        try:
            mel.eval = lambda cmd, *a, **k: captured.setdefault("cmd", cmd)
            RenderUtils.render_camera("persp")
        finally:
            mel.eval = orig
        self.assertIn("renderWindowRenderCamera", captured.get("cmd", ""))
        self.assertIn("persp", captured.get("cmd", ""))

    def test_redo_previous_render(self):
        captured = {}
        orig = mel.eval
        try:
            mel.eval = lambda cmd, *a, **k: captured.setdefault("cmd", cmd)
            RenderUtils.redo_previous_render()
        finally:
            mel.eval = orig
        self.assertIn("redoPreviousRender", captured.get("cmd", ""))

    def test_supports_ipr_false_for_unknown_renderer(self):
        # No registered procedure and not a known optional renderer → no IPR.
        self.assertFalse(RenderUtils.supports_ipr("no_such_renderer_xyz"))

    def test_supports_ipr_true_for_optional_renderer_even_when_unloaded(self):
        # V-Ray isn't installed here, but it's a known IPR-capable renderer, so
        # the gate reports True without forcing a plugin load (start_ipr is the
        # backstop). It only ever reaches the picker when actually installed.
        self.assertTrue(RenderUtils.supports_ipr("vray"))

    def test_supports_ipr_true_for_loaded_arnold(self):
        self._require_mtoa()
        self.assertTrue(RenderUtils.supports_ipr("arnold"))

    def test_supports_ipr_false_for_viewport2(self):
        # Hardware 2.0 (Viewport 2.0) registers no start-IPR procedure.
        self.assertFalse(RenderUtils.supports_ipr("mayaHardware2"))

    def test_start_ipr_returns_false_without_registered_procedure(self):
        # A renderer with no registered start-IPR procedure can't start IPR.
        self.assertFalse(RenderUtils.start_ipr("persp", "no_such_renderer_xyz"))

    def test_start_ipr_invokes_registered_procedure(self):
        # start_ipr delegates to the renderer's registered start-IPR MEL
        # procedure, on the chosen camera. That procedure registers only
        # interactively (it's absent in mayapy standalone, so a real Arnold
        # load still yields no proc here), so stub the lookup + plugin load to
        # test the delegation itself — deterministically, without needing mtoa.
        captured = {}
        orig = mel.eval
        try:
            mel.eval = lambda cmd, *a, **k: captured.setdefault("cmd", cmd)
            with mock.patch.object(
                RenderUtils, "_ipr_procedure", staticmethod(lambda r: "arnoldIprStart")
            ), mock.patch.object(
                RenderUtils, "_ensure_plugin", staticmethod(lambda r: None)
            ):
                result = RenderUtils.start_ipr("persp", "arnold")
        finally:
            mel.eval = orig
        self.assertTrue(result)
        cmd = captured.get("cmd", "")
        self.assertIn("arnoldIprStart", cmd)  # the registered procedure
        self.assertIn("persp", cmd)  # ...invoked on the chosen camera


if __name__ == "__main__":
    import maya.standalone

    try:
        cmds.about(version=True)
    except Exception:
        maya.standalone.initialize(name="python")

    unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2)

    try:
        maya.standalone.uninitialize()
    except Exception:
        pass
