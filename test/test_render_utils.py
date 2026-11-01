# !/usr/bin/python
# coding=utf-8
"""Tests for RenderUtils — renderer enumeration / selection and Render-View control.

Enumeration, plugin detection, and renderer selection run against real Maya
standalone. The Render-View / IPR calls are interactive (the procs/commands are
absent or open windows headlessly), so those are exercised with the underlying
``mel.eval`` / ``cmds.arnoldRenderView`` patched to capture the call.

Run headless (from the workspace root)::

    & "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" \
        mayatk/test/test_render_utils.py
"""

import os
import sys
import unittest
from unittest import mock

import base_test  # noqa: F401 — sys.path bootstrap for the sibling repos

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

    # --------------------------------------------------------------- cameras
    def test_render_cameras_put_persp_first(self):
        cameras = RenderUtils.render_cameras()
        self.assertEqual(cameras[0], "persp")
        self.assertIn("top", cameras)
        self.assertNotIn("perspShape", cameras)
        self.assertEqual(cameras[1:], sorted(cameras[1:]))
        made = cmds.rename(cmds.camera()[0], "shotCam")
        self.assertIn(made, RenderUtils.render_cameras())

    def test_render_camera_takes_a_shape_for_its_transform(self):
        module = sys.modules[RenderUtils.__module__]
        with mock.patch.object(module, "mel") as fake_mel:
            RenderUtils.render_camera("perspShape")
        command = fake_mel.eval.call_args[0][0]
        self.assertIn('"persp"', command)
        self.assertNotIn("perspShape", command)

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
        self.assertEqual(cmds.getAttr("defaultRenderGlobals.currentRenderer"), "arnold")

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
            with (
                mock.patch.object(
                    RenderUtils,
                    "_ipr_procedure",
                    staticmethod(lambda r: "arnoldIprStart"),
                ),
                mock.patch.object(
                    RenderUtils, "_ensure_plugin", staticmethod(lambda r: None)
                ),
            ):
                result = RenderUtils.start_ipr("persp", "arnold")
        finally:
            mel.eval = orig
        self.assertTrue(result)
        cmd = captured.get("cmd", "")
        self.assertIn("arnoldIprStart", cmd)  # the registered procedure
        self.assertIn("persp", cmd)  # ...invoked on the chosen camera

    # ------------------------------------------------------ render settings
    def test_render_settings_round_trip(self):
        self._require_mtoa()
        RenderUtils.ensure_arnold_options()
        applied = RenderUtils.set_render_settings(
            {"camera_samples": 5, "diffuse_depth": 3, "width": 640, "bogus": 1}
        )
        self.assertEqual(applied, 3)  # an unknown key is skipped, not raised
        settings = RenderUtils.render_settings()
        self.assertEqual(settings["camera_samples"], 5)
        self.assertEqual(settings["diffuse_depth"], 3)
        self.assertEqual(settings["width"], 640)
        self.assertEqual(cmds.getAttr("defaultArnoldRenderOptions.AASamples"), 5)
        self.assertEqual(set(settings), set(RenderUtils.RENDER_SETTINGS))

    def test_render_settings_without_arnold_options_reads_resolution_only(self):
        # A fresh scene has no defaultArnoldRenderOptions until Arnold is
        # picked: the Arnold keys are absent rather than raising.
        if cmds.objExists("defaultArnoldRenderOptions"):
            self.skipTest("scene template already carries Arnold options")
        settings = RenderUtils.render_settings()
        self.assertIn("width", settings)
        self.assertNotIn("camera_samples", settings)

    def test_applying_an_arnold_setting_creates_the_options(self):
        self._require_mtoa()
        if cmds.objExists("defaultArnoldRenderOptions"):
            self.skipTest("scene template already carries Arnold options")
        self.assertEqual(RenderUtils.set_render_settings({"camera_samples": 2}), 1)
        self.assertEqual(cmds.getAttr("defaultArnoldRenderOptions.AASamples"), 2)

    def test_builtin_render_presets(self):
        """The shipped Arnold tiers load, use only known keys, rise in quality,
        and apply to the scene."""
        self._require_mtoa()
        store = RenderUtils.render_preset_store()
        tiers = ["Draft", "Preview", "Production", "Final"]
        for name in tiers:
            self.assertEqual(store.source(name), "builtin", name)
            self.assertTrue(store.description(name), name)
        loaded = [store.load(name) for name in tiers]
        for data in loaded:
            data.pop("_meta", None)
            self.assertLessEqual(set(data), set(RenderUtils.RENDER_SETTINGS))
        aa = [data["camera_samples"] for data in loaded]
        self.assertEqual(aa, sorted(aa))
        self.assertEqual(len(set(aa)), len(aa))

        RenderUtils.set_render_settings(loaded[0])
        self.assertEqual(
            RenderUtils.render_settings()["camera_samples"], loaded[0]["camera_samples"]
        )

    # -------------------------------------------------------- arnold preview
    def _cube(self, name, culling=1, double_sided=True):
        shape = cmds.listRelatives(cmds.polyCube(name=name)[0], shapes=True)[0]
        cmds.setAttr(f"{shape}.backfaceCulling", culling)
        cmds.setAttr(f"{shape}.doubleSided", double_sided)
        return shape

    @staticmethod
    def _options(shape):
        return cmds.getAttr(f"{shape}.aiUserOptions") or ""

    def test_backface_culling_reaches_arnold_camera_rays(self):
        """Regression (a production assembly, 2026-10-13): a cutaway wall culled in
        the viewport rendered its unlit back in Arnold, so an outside camera
        saw a black box -- VP2 culling is display-only."""
        self._require_mtoa()
        culled = self._cube("culled")
        plain = self._cube("plain", culling=0)
        # Single-sided already culls for every ray; adding the token would
        # make it double-sided for shadows instead.
        single = self._cube("single", double_sided=False)

        result = RenderUtils.sync_arnold_backface_culling()

        self.assertEqual(result, cmds.ls(culled, long=True))
        self.assertEqual(self._options(culled), RenderUtils.ARNOLD_CAMERA_CULL)
        self.assertEqual(self._options(plain), "")
        self.assertEqual(self._options(single), "")

        RenderUtils.sync_arnold_backface_culling()  # idempotent
        self.assertEqual(self._options(culled), RenderUtils.ARNOLD_CAMERA_CULL)

    def test_backface_culling_sync_composes_with_authored_options(self):
        self._require_mtoa()
        shape = self._cube("authored")
        cmds.setAttr(f"{shape}.aiUserOptions", "subdiv_iterations 2", type="string")
        RenderUtils.sync_arnold_backface_culling()
        self.assertEqual(
            self._options(shape),
            f"subdiv_iterations 2 {RenderUtils.ARNOLD_CAMERA_CULL}",
        )

        # Culling turned off: the token goes, the authored options stay.
        cmds.setAttr(f"{shape}.backfaceCulling", 0)
        self.assertEqual(RenderUtils.sync_arnold_backface_culling(), [])
        self.assertEqual(self._options(shape), "subdiv_iterations 2")

        # An authored sidedness is the artist's call; never overridden.
        cmds.setAttr(f"{shape}.backfaceCulling", 1)
        cmds.setAttr(f"{shape}.aiUserOptions", "sidedness 0", type="string")
        RenderUtils.sync_arnold_backface_culling()
        self.assertEqual(self._options(shape), "sidedness 0")

    def test_prepare_arnold_preview_bridges_and_culls(self):
        self._require_mtoa()
        culled = self._cube("prepCulled")
        bridge = mock.MagicMock()
        bridge.unrenderable_materials.return_value = ["game_mat"]
        bridge.return_value.add.return_value = ["game_mat_ai"]
        with mock.patch("mayatk.render_utils._render_utils.ArnoldBridge", bridge):
            result = RenderUtils.prepare_arnold_preview()
        bridge.return_value.add.assert_called_once_with(materials=["game_mat"])
        self.assertEqual(result["bridged"], ["game_mat_ai"])
        self.assertEqual(result["culled"], cmds.ls(culled, long=True))

        # Nothing untranslatable: no bridge is built.
        bridge.reset_mock()
        bridge.unrenderable_materials.return_value = []
        with mock.patch("mayatk.render_utils._render_utils.ArnoldBridge", bridge):
            self.assertEqual(RenderUtils.prepare_arnold_preview()["bridged"], [])
        bridge.return_value.add.assert_not_called()

    def test_an_authored_camera_cull_is_never_stripped(self):
        """Pre-release review 2026-10-13: an artist who wrote the very token
        the sync adds (``sidedness 254``) lost it the moment the mesh's
        viewport culling went off -- the sync took every copy for its own."""
        self._require_mtoa()
        shape = self._cube("artistCulled")
        cmds.setAttr(
            f"{shape}.aiUserOptions", RenderUtils.ARNOLD_CAMERA_CULL, type="string"
        )
        self.assertEqual(
            RenderUtils.sync_arnold_backface_culling(), cmds.ls(shape, long=True)
        )

        cmds.setAttr(f"{shape}.backfaceCulling", 0)
        RenderUtils.sync_arnold_backface_culling()

        self.assertEqual(self._options(shape), RenderUtils.ARNOLD_CAMERA_CULL)

    def test_the_token_the_sync_added_leaves_after_a_reopen(self):
        """What the sync added is recorded with the scene, so it still knows
        its own token in the next session."""
        import shutil
        import tempfile

        self._require_mtoa()
        shape = self._cube("reopened")
        RenderUtils.sync_arnold_backface_culling()
        folder = tempfile.mkdtemp(prefix="mtk_cull_record_")
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "culled.ma")
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(path, open=True, force=True)
        shape = cmds.ls("reopenedShape", long=True)[0]

        cmds.setAttr(f"{shape}.backfaceCulling", 0)
        RenderUtils.sync_arnold_backface_culling()

        self.assertEqual(self._options(shape), "")


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
