# !/usr/bin/python
# coding=utf-8
"""Regression tests for mayatk.light_utils.hdr_manager.

The panel's checks moved with it to tentacle's ``test/test_hdr_manager_panel.py``.

Bug fixed 2026-05-07: PyMEL-style attribute proxies
(``self.hdr_env_transform.hiddenInOutliner.set(1)``,
``cmds.connectAttr(file_node.outColor, node.color)``,
``file_node.fileTextureName.set(...)``, ``node.camera.set(state)``,
``node.rotateY.get()``) were converted to ``cmds.setAttr/connectAttr/getAttr``
with f-string plug paths.

Also added: auto-load of the mtoa (Arnold) plugin via
``HdrManager.ensure_plugin_loaded`` — needed because all paths through
the class touch ``aiSkyDomeLight``.
"""

import unittest
from unittest import mock

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.light_utils.hdr_manager import HdrManager


def _arnold_available() -> bool:
    """Return True if mtoa can be loaded (plugin installed and loadable)."""
    try:
        if cmds.pluginInfo("mtoa", query=True, loaded=True):
            return True
        cmds.loadPlugin("mtoa")
        return True
    except Exception:
        return False


@unittest.skipUnless(_arnold_available(), "Arnold (mtoa) plugin not available")
class TestHdrManager(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.mgr = HdrManager()

    def tearDown(self):
        for n in cmds.ls(HdrManager.hdr_env_name, exactType="aiSkyDomeLight") or []:
            transforms = cmds.listRelatives(n, parent=True, fullPath=True) or []
            cmds.delete(transforms or n)
        super().tearDown()

    def test_ensure_plugin_loaded(self):
        """ensure_plugin_loaded should return True when Arnold is available."""
        self.assertTrue(HdrManager.ensure_plugin_loaded())
        self.assertTrue(cmds.pluginInfo("mtoa", query=True, loaded=True))

    def test_hdr_env_setter_creates_skydome(self):
        """Setting hdr_env on an empty scene must create the aiSkyDomeLight."""
        self.assertIsNone(self.mgr.hdr_env)

        self.mgr.hdr_env = "C:/tmp/dummy.exr"

        self.assertIsNotNone(self.mgr.hdr_env, "hdr_env should now resolve")
        self.assertTrue(cmds.objectType(self.mgr.hdr_env) == "aiSkyDomeLight")

    def test_hdr_env_setter_sets_file_texture(self):
        """The file node's fileTextureName must hold the assigned path."""
        path = "C:/tmp/test_hdr.exr"
        self.mgr.hdr_env = path

        skydome = self.mgr.hdr_env
        file_nodes = (
            cmds.listConnections(
                f"{skydome}.color", source=True, destination=False, type="file"
            )
            or []
        )
        self.assertTrue(file_nodes, "Expected a file node connected to skydome.color")
        actual = cmds.getAttr(f"{file_nodes[0]}.fileTextureName")
        self.assertEqual(actual, path)

    def test_set_hdr_map_visibility_toggles_camera_attr(self):
        """set_hdr_map_visibility must drive the skydome's .camera attribute."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        skydome = self.mgr.hdr_env

        self.mgr.set_hdr_map_visibility(True)
        self.assertEqual(cmds.getAttr(f"{skydome}.camera"), 1)

        self.mgr.set_hdr_map_visibility(False)
        self.assertEqual(cmds.getAttr(f"{skydome}.camera"), 0)

    def test_hdr_env_transform_returns_string(self):
        """The transform property must return a cmds-style string path."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        transform = self.mgr.hdr_env_transform
        self.assertIsInstance(transform, str)
        self.assertTrue(cmds.objExists(transform))

    def test_quality_defaults_without_skydome(self):
        """Quality/contribution getters return Arnold defaults with no skydome."""
        self.assertIsNone(self.mgr.hdr_env)
        self.assertEqual(self.mgr.resolution, 1000)
        self.assertEqual(self.mgr.samples, 1)
        self.assertEqual(self.mgr.diffuse, 1.0)
        self.assertEqual(self.mgr.specular, 1.0)

    def test_quality_and_contribution_attrs_roundtrip(self):
        """resolution/samples/diffuse/specular must drive the matching attrs."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        skydome = self.mgr.hdr_env

        self.mgr.resolution = 2048
        self.assertEqual(cmds.getAttr(f"{skydome}.resolution"), 2048)
        self.assertEqual(self.mgr.resolution, 2048)

        self.mgr.samples = 3
        self.assertEqual(cmds.getAttr(f"{skydome}.aiSamples"), 3)
        self.assertEqual(self.mgr.samples, 3)

        self.mgr.diffuse = 0.5
        self.assertAlmostEqual(cmds.getAttr(f"{skydome}.aiDiffuse"), 0.5)
        self.assertAlmostEqual(self.mgr.diffuse, 0.5)

        self.mgr.specular = 0.0
        self.assertAlmostEqual(cmds.getAttr(f"{skydome}.aiSpecular"), 0.0)
        self.assertAlmostEqual(self.mgr.specular, 0.0)

    def test_sky_radius_roundtrip(self):
        """sky_radius drives the skydome's skyRadius (viewport-preview size)."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        skydome = self.mgr.hdr_env
        # Created hidden (skyRadius=0) so the dome sphere doesn't clutter the VP.
        self.assertEqual(cmds.getAttr(f"{skydome}.skyRadius"), 0)

        self.mgr.sky_radius = 750
        self.assertAlmostEqual(cmds.getAttr(f"{skydome}.skyRadius"), 750)
        self.assertAlmostEqual(self.mgr.sky_radius, 750)

    def test_sky_radius_clamps_negative_to_zero(self):
        """A negative radius is meaningless — clamped to 0 (preview off)."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        self.mgr.sky_radius = -50
        self.assertEqual(self.mgr.sky_radius, 0)

    def test_preview_toggles_sky_radius(self):
        """preview is the on/off boolean over skyRadius (on → PREVIEW_SKY_RADIUS)."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        skydome = self.mgr.hdr_env
        self.assertFalse(self.mgr.preview)  # created hidden (skyRadius 0)

        self.mgr.preview = True
        self.assertEqual(
            cmds.getAttr(f"{skydome}.skyRadius"), HdrManager.PREVIEW_SKY_RADIUS
        )
        self.assertTrue(self.mgr.preview)

        self.mgr.preview = False
        self.assertEqual(cmds.getAttr(f"{skydome}.skyRadius"), 0)
        self.assertFalse(self.mgr.preview)

    def test_preview_true_with_manual_radius(self):
        """preview reads True for any non-zero skyRadius (incl. a manual size)."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        self.mgr.sky_radius = 5000  # user sized it manually in the viewport
        self.assertTrue(self.mgr.preview)

    def test_setter_refuses_incomplete_image(self):
        """The hdr_env setter must not build a network for a truncated/corrupt HDR.

        Regression (2026-06-16): wiring an incomplete HDR (a partially-synced
        Dropbox file) into the skydome crashed Viewport 2.0 in
        ``AtilImageHandler::GetIBLIntensity`` (null image) on the next refresh.
        """
        self.assertIsNone(self.mgr.hdr_env)
        with (
            mock.patch(
                "mayatk.light_utils.hdr_manager.os.path.isfile", return_value=True
            ),
            mock.patch(
                "mayatk.light_utils.hdr_manager.ptk.ImgUtils.validate_image_integrity",
                return_value=(False, "truncated: 1/16 scanlines"),
            ),
        ):
            self.mgr.hdr_env = "C:/tmp/incomplete.hdr"
        self.assertIsNone(
            self.mgr.hdr_env, "incomplete image must not create an aiSkyDomeLight"
        )

    def test_hdr_env_adopts_foreign_skydome(self):
        """hdr_env must surface a skydome made *outside* the manager (issue 3).

        Bug (2026-06-21): the getter matched only the canonically-named
        ``aiSkyDomeLight_``, so a dome created via Arnold's own *Lights* menu, an
        import/reference, or a rename resolved to ``None`` — the panel showed
        "None" despite a live HDR (and would build a *second* dome on the next
        pick). It must adopt any aiSkyDomeLight.
        """
        self.assertIsNone(self.mgr.hdr_env)
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="myCustomDome")
        self.assertEqual(
            (self.mgr.hdr_env or "").rsplit("|", 1)[-1],
            "myCustomDome",
            "manager must adopt a foreign-named skydome, not return None",
        )

    def test_setter_reuses_foreign_skydome_no_duplicate(self):
        """Applying an HDR with a foreign dome present reuses it, not a 2nd dome.

        Because the getter now adopts the foreign dome, the setter's
        ``self.hdr_env or _create_skydome()`` resolves to it and swaps the texture
        in place — so the scene keeps exactly one aiSkyDomeLight.
        """
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="myCustomDome")
        self.mgr.hdr_env = "C:/tmp/x.exr"
        self.assertEqual(len(cmds.ls(exactType="aiSkyDomeLight") or []), 1)
        self.assertEqual((self.mgr.hdr_env or "").rsplit("|", 1)[-1], "myCustomDome")

    def test_hdr_env_prefers_canonical_over_foreign(self):
        """With two domes present, the getter prefers the canonically-named one.

        (Created directly here — the setter would otherwise reuse the first dome
        rather than make a second, see the reuse test above.)
        """
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="myCustomDome")
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name=HdrManager.hdr_env_name)
        self.assertEqual(
            (self.mgr.hdr_env or "").rsplit("|", 1)[-1], HdrManager.hdr_env_name
        )

    def test_apply_sets_arnold_renderer(self):
        """Applying an HDR flips the active renderer to Arnold (issue 2).

        Bug (2026-06-21): ``aiSkyDomeLight`` renders in no other renderer, and the
        fresh-scene default is ``mayaSoftware`` — under which the skydome renders
        **black regardless of the Visible flag**. The setter must make Arnold
        active so the just-applied HDR actually shows up.
        """
        cmds.setAttr(
            "defaultRenderGlobals.currentRenderer", "mayaSoftware", type="string"
        )
        self.mgr.hdr_env = "C:/tmp/x.exr"
        self.assertEqual(cmds.getAttr("defaultRenderGlobals.currentRenderer"), "arnold")

    def test_refused_image_leaves_renderer_unchanged(self):
        """A refused (incomplete) HDR must not switch the renderer (no side effect)."""
        cmds.setAttr(
            "defaultRenderGlobals.currentRenderer", "mayaSoftware", type="string"
        )
        with (
            mock.patch(
                "mayatk.light_utils.hdr_manager.os.path.isfile", return_value=True
            ),
            mock.patch(
                "mayatk.light_utils.hdr_manager.ptk.ImgUtils.validate_image_integrity",
                return_value=(False, "truncated"),
            ),
        ):
            self.mgr.hdr_env = "C:/tmp/incomplete.hdr"
        self.assertEqual(
            cmds.getAttr("defaultRenderGlobals.currentRenderer"), "mayaSoftware"
        )

    def test_create_network_applies_quality_knobs(self):
        """create_network forwards the new quality/contribution knobs."""
        node = self.mgr.create_network(
            hdrMap="C:/tmp/x.exr",
            resolution=4096,
            samples=2,
            diffuse=0.25,
            specular=0.75,
            preview=True,
        )
        self.assertIsNotNone(node)
        self.assertEqual(cmds.getAttr(f"{node}.resolution"), 4096)
        self.assertEqual(cmds.getAttr(f"{node}.aiSamples"), 2)
        self.assertAlmostEqual(cmds.getAttr(f"{node}.aiDiffuse"), 0.25)
        self.assertAlmostEqual(cmds.getAttr(f"{node}.aiSpecular"), 0.75)
        # preview=True sizes the dome to PREVIEW_SKY_RADIUS (viewport backdrop).
        self.assertEqual(
            cmds.getAttr(f"{node}.skyRadius"), HdrManager.PREVIEW_SKY_RADIUS
        )

    def test_hdr_env_setter_is_one_undo_step(self):
        """Building the network from the setter (skydome + file + place2d) is ONE
        undo step: a dropdown pick is one user action. Added: 2026-10-28"""
        cmds.undoInfo(state=True)
        self.mgr.hdr_env = "C:/tmp/x.exr"
        self.assertIsNotNone(self.mgr.hdr_env)
        cmds.undo()
        self.assertIsNone(self.mgr.hdr_env)

    def test_select_selects_each_part(self):
        """``select`` (the panel's right-click Select items) replaces the
        selection with the skydome, its transform or its file node."""
        self.mgr.hdr_env = "C:/tmp/x.exr"
        for part, node in (
            ("skydome", self.mgr.hdr_env),
            ("transform", self.mgr.hdr_env_transform),
            ("file_node", self.mgr.hdr_file_node),
        ):
            with self.subTest(part=part):
                cmds.select(clear=True)
                self.assertEqual(self.mgr.select(part), node)
                self.assertEqual(
                    cmds.ls(selection=True, long=True), cmds.ls(node, long=True)
                )

    def test_select_without_an_environment_keeps_the_selection(self):
        cube = cmds.polyCube(name="hdrSelectKeep")[0]
        try:
            cmds.select(cube)
            self.assertIsNone(self.mgr.select("skydome"))
            self.assertEqual(cmds.ls(selection=True), [cube])
        finally:
            cmds.delete(cube)


class TestRotationSetterRobust(unittest.TestCase):
    """The rotation setter actually rotates the dome, and no-ops on a bad network.

    Regression (2026-06-21): the dome never rotated. The setter called
    ``cmds.rotate(transform, angle, …)`` — object first — so Maya parsed the
    *angle* as an object (``Object N is invalid``) and raised on **every** set
    (valid transform included); the try/except swallowed it. Fixed by setting
    ``rotateY`` directly. (Supersedes the 2026-06-16 no-throw-only guard.) Needs
    ``cmds`` (real node existence) but not Arnold.
    """

    def test_rotation_applies_to_transform(self):
        node = cmds.createNode("transform")
        try:
            mgr = HdrManager.__new__(HdrManager)
            with mock.patch.object(
                type(mgr),
                "hdr_env_transform",
                new_callable=mock.PropertyMock,
                return_value=node,
            ):
                mgr.rotation = 137.0
            self.assertAlmostEqual(cmds.getAttr(f"{node}.rotateY"), 137.0, places=3)
        finally:
            cmds.delete(node)

    def test_stale_transform_does_not_raise(self):
        mgr = HdrManager.__new__(HdrManager)
        with mock.patch.object(
            type(mgr),
            "hdr_env_transform",
            new_callable=mock.PropertyMock,
            return_value="ghostTransform_does_not_exist",
        ):
            mgr.rotation = 140.0  # objExists False → clean early return

    def test_setattr_runtime_error_is_swallowed(self):
        node = cmds.createNode("transform")
        try:
            mgr = HdrManager.__new__(HdrManager)
            with (
                mock.patch.object(
                    type(mgr),
                    "hdr_env_transform",
                    new_callable=mock.PropertyMock,
                    return_value=node,
                ),
                mock.patch(
                    "mayatk.light_utils.hdr_manager.cmds.setAttr",
                    side_effect=RuntimeError("boom"),
                ),
            ):
                mgr.rotation = 140.0  # try/except swallows → no raise
        finally:
            cmds.delete(node)


class TestDeferApply(unittest.TestCase):
    """``HdrManager.defer_apply`` runs a dropdown-driven edit on Maya's idle queue
    at NORMAL priority.

    Black-render fix (2026-06-21): ``QTimer.singleShot(0)`` is serviced by
    whatever Qt loop is spinning -- including the combo popup's *re-entrant*
    teardown loop -- so the scene mutation still ran inside the re-entrancy and a
    live Arnold IPR re-translating left the RenderView stuck. ``cmds.evalDeferred``
    escapes to Maya idle, which the nested Qt loop never services. But it must be
    **normal** priority: verified live in a fresh Maya, a ``lowestPriority``
    callback is starved while Maya sits idle, so a map pick never ran. (The
    panel's ``_defer_to_idle``, now the manager's.)
    """

    def test_defers_via_evaldeferred_normal_priority(self):
        import mayatk.light_utils.hdr_manager as hm

        calls = []

        class _StubCmds:
            def evalDeferred(self, cb, **kw):
                calls.append((cb, kw))

        orig = hm.cmds
        hm.cmds = _StubCmds()
        try:
            marker = lambda: None  # noqa: E731
            HdrManager.defer_apply(marker)
        finally:
            hm.cmds = orig

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], marker)
        self.assertFalse(
            calls[0][1].get("lowestPriority"),
            "must NOT use lowestPriority -- it is starved while Maya sits idle, so "
            "the deferred apply never runs and the dropdown appears not to change "
            "the HDR; normal priority still escapes the popup re-entrancy",
        )


class TestKnobs(unittest.TestCase):
    """``HdrManager.KNOBS`` names what tentacle's HDR Manager drives: each a
    readable, writable property that answers a default with no skydome.
    Added: 2026-10-28"""

    def test_every_knob_is_a_settable_property(self):
        for knob in HdrManager.KNOBS:
            with self.subTest(knob=knob):
                prop = getattr(HdrManager, knob, None)
                self.assertIsInstance(prop, property)
                self.assertIsNotNone(prop.fset)

    def test_the_knobs_read_their_defaults_without_a_skydome(self):
        mgr = HdrManager.__new__(HdrManager)
        with mock.patch.object(
            HdrManager, "hdr_env", new_callable=mock.PropertyMock, return_value=None
        ):
            readings = {knob: getattr(mgr, knob) for knob in HdrManager.KNOBS}
        self.assertEqual(
            readings,
            {
                "rotation": 0.0,
                "visibility": False,
                "preview": False,
                "intensity": 1.0,
                "exposure": 0.0,
                "resolution": 1000,
                "samples": 1,
                "diffuse": 1.0,
                "specular": 1.0,
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
