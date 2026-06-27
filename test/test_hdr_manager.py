# !/usr/bin/python
# coding=utf-8
"""Regression tests for mayatk.light_utils.hdr_manager.

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
import os
import shutil
import logging
import tempfile
import unittest
from unittest import mock

import maya.cmds as cmds

from base_test import MayaTkTestCase
from mayatk.light_utils.hdr_manager import HdrManager, HdrManagerSlots


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
        file_nodes = cmds.listConnections(
            f"{skydome}.color", source=True, destination=False, type="file"
        ) or []
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
        with mock.patch(
            "mayatk.light_utils.hdr_manager.os.path.isfile", return_value=True
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.ptk.ImgUtils.validate_image_integrity",
            return_value=(False, "truncated: 1/16 scanlines"),
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
            self.mgr.hdr_env,
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
        self.assertEqual(self.mgr.hdr_env, "myCustomDome")

    def test_hdr_env_prefers_canonical_over_foreign(self):
        """With two domes present, the getter prefers the canonically-named one.

        (Created directly here — the setter would otherwise reuse the first dome
        rather than make a second, see the reuse test above.)
        """
        cmds.shadingNode("aiSkyDomeLight", asLight=True, name="myCustomDome")
        cmds.shadingNode(
            "aiSkyDomeLight", asLight=True, name=HdrManager.hdr_env_name
        )
        self.assertEqual(self.mgr.hdr_env, HdrManager.hdr_env_name)

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
        self.assertEqual(
            cmds.getAttr("defaultRenderGlobals.currentRenderer"), "arnold"
        )

    def test_refused_image_leaves_renderer_unchanged(self):
        """A refused (incomplete) HDR must not switch the renderer (no side effect)."""
        cmds.setAttr(
            "defaultRenderGlobals.currentRenderer", "mayaSoftware", type="string"
        )
        with mock.patch(
            "mayatk.light_utils.hdr_manager.os.path.isfile", return_value=True
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.ptk.ImgUtils.validate_image_integrity",
            return_value=(False, "truncated"),
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
            with mock.patch.object(
                type(mgr),
                "hdr_env_transform",
                new_callable=mock.PropertyMock,
                return_value=node,
            ), mock.patch(
                "mayatk.light_utils.hdr_manager.cmds.setAttr",
                side_effect=RuntimeError("boom"),
            ):
                mgr.rotation = 140.0  # try/except swallows → no raise
        finally:
            cmds.delete(node)


class _StubWidget:
    def __init__(self, data):
        self._data = data

    def currentData(self):
        return self._data


class _StubFooter:
    """Captures the last status + its severity level for assertions."""

    def __init__(self):
        self.text = None
        self.level = None

    def setText(self, text, level=None):
        self.text = text or ""
        self.level = level

    setStatusText = setText


class _StubCombo:
    """Minimal combobox stand-in for the folder-batch / select / refresh wiring."""

    def __init__(self, items=None):
        self.current_index = None
        self._items = list(items or [])  # userData per row
        self.last_header = None  # records the header passed to add()
        # Mirrors ComboBox.restore_state: add() resets it to ``not has_header``;
        # the HDR slot re-asserts False each populate (live mirror, no persist).
        self.restore_state = True

    def count(self):
        return len(self._items)

    def itemData(self, i):
        return self._items[i]

    def currentData(self):
        if self.current_index is not None and 0 <= self.current_index < len(self._items):
            return self._items[self.current_index]
        return None

    def findData(self, data):
        try:
            return self._items.index(data)
        except ValueError:
            return -1

    def blockSignals(self, blocked):
        pass

    def setCurrentIndex(self, index):
        self.current_index = index

    def clear(self):
        self._items = []

    def addItem(self, text, data=None):
        self._items.append(data)

    def insertItem(self, index, text, data=None):
        self._items.insert(index, data)
        if self.current_index is not None and index <= self.current_index:
            self.current_index += 1

    def add(self, pairs, ascending=True, clear=False, header=None, **kwargs):
        # Mirror ComboBox.add: restore_state is reset to ``not has_header`` on
        # every populate. The HDR combo passes no header and re-asserts False
        # afterward, so tests can confirm the persistence opt-out survives.
        self.last_header = header
        self.restore_state = not bool(header)
        if clear:
            self._items = []
        for _text, data in pairs:
            self._items.append(data)


class _StubUi:
    def __init__(self):
        self.footer = _StubFooter()
        self.cmb000 = _StubCombo()


class _StubManager:
    """Records ``hdr_env`` writes so a test can assert none happened.

    Mirrors :class:`HdrManager`'s getter contract: ``hdr_env`` resolves to
    ``None`` until a network is live (the real getter short-circuits to None
    while mtoa is unloaded), so the slot can never coax a plugin load out of
    the getter.
    """

    def __init__(self, env):
        self._env = env
        self.set_paths = []
        self.clear_calls = 0
        # The wired HDR file path the combo should mirror (None = no env);
        # tests set this to drive _select_active_in_combo.
        self.hdr_file_path = None
        # Viewport-preview state the rotation slider's toggle/skyRadius drive.
        self.sky_radius = 0
        self.preview = False

    @property
    def hdr_env(self):
        return self._env

    @hdr_env.setter
    def hdr_env(self, value):
        self.set_paths.append(value)
        self._env = "aiSkyDomeLight_"

    def clear(self):
        self.clear_calls += 1
        self._env = None


class _StubSb:
    """Records ``message_box`` calls (returns ``"Ok"`` like the real modal). The
    idle-deferral ``cmb000`` schedules is captured via the slot's stubbed
    ``_defer_to_idle`` (see ``_make_slots``), which appends the callback here."""

    def __init__(self):
        self.message_box_calls = []
        self.deferred = []  # callbacks scheduled via _defer_to_idle (Maya idle)

    def message_box(self, string, *buttons, **kwargs):
        self.message_box_calls.append((string, buttons))
        return "Ok"


def _make_slots(env=None):
    """A bare HdrManagerSlots with stub collaborators — real methods, no Qt.

    ``HdrManagerSlots.logger`` is a class-level property (no ``__init__``
    needed), so the real ``_notify`` / ``_validate_or_warn`` / ``cmb000`` run
    against the stubs. ``_sync_ui_to_scene`` is stubbed (it touches widgets the
    stub UI doesn't carry).
    """
    s = HdrManagerSlots.__new__(HdrManagerSlots)
    s.manager = _StubManager(env)
    s.ui = _StubUi()
    s.sb = _StubSb()
    # Rotation-slider option-box plugins are wired only in _setup_rotation_slider
    # (live panel); default them to None so hdr_map_visibility/preview read cleanly.
    s._viewport_toggle = None
    s._render_toggle = None
    s._rotation_value = None
    s._sync_calls = []
    s._sync_ui_to_scene = lambda: s._sync_calls.append(1)
    s._refresh_combo = lambda: None
    # Record idle-deferrals instead of touching Maya's idle queue (production
    # routes them through cmds.evalDeferred — see HdrManagerSlots._defer_to_idle).
    s._defer_to_idle = lambda cb: s.sb.deferred.append(cb)
    # Keep the shared class logger quiet during the suite (we assert on the
    # footer / dialog, not the captured log records).
    HdrManagerSlots.logger.setLevel(logging.CRITICAL)
    return s


def _write_truncated_hdr():
    """Write a Radiance HDR whose header declares 16x16 but carries no data."""
    import tempfile

    blob = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 16 +X 16\n\x02\x02\x00\x10"
    f = tempfile.NamedTemporaryFile(suffix=".hdr", delete=False)
    f.write(blob)
    f.close()
    return f.name


class TestHdrSelectionDoesNotEagerLoad(unittest.TestCase):
    """Regression: ``cmb000`` must NEVER mutate the scene synchronously — every
    apply (build / swap / clear) defers off the combo signal.

    Crash (2026-06-16): cold-start ``cmb000`` set ``manager.hdr_env``, whose
    setter calls ``arnold_available()`` → ``cmds.loadPlugin("mtoa")`` + creates
    render nodes; doing that inside a combobox ``currentIndexChanged`` callback
    (mid popup-teardown) crashed Maya. Black-render (2026-06-21): once selection
    actually applied (the headerless fix), the *in-place texture swap* and the
    *None-clear* also ran synchronously in that callback — and a live Arnold IPR
    re-translating re-entrantly left the RenderView stuck black. Fix: ``cmb000``
    only schedules :meth:`_apply_selection` (``_defer_to_idle`` →
    ``cmds.evalDeferred``); the single deferred apply does build / swap / clear
    after popup teardown. No Maya needed — the invariant is purely slot-level.
    """

    def test_selection_without_live_network_defers_apply(self):
        s = _make_slots(env=None)  # no skydome; mtoa not loaded
        HdrManagerSlots.cmb000(s, 0, _StubWidget("C:/img/sky.exr"))
        self.assertEqual(
            s.manager.set_paths,
            [],
            "Selecting an HDR must not write hdr_env synchronously when no "
            "network exists (would trigger loadPlugin + node creation inside "
            "the combo signal).",
        )
        self.assertEqual(s._sync_calls, [])
        # Exactly one deferred apply was scheduled (at Maya idle, off the signal).
        self.assertEqual(len(s.sb.deferred), 1)
        self.assertEqual(s.sb.deferred[0], s._apply_selection)
        # Footer reflects the in-progress apply, not a silent no-op.
        self.assertIn("sky.exr", s.ui.footer.text)
        self.assertIn("Applying", s.ui.footer.text)

    def test_selection_with_live_network_defers_then_swaps_in_place(self):
        # A live-network selection must also defer (a synchronous swap inside the
        # combo signal breaks a live Arnold render — RenderView goes black). The
        # deferred apply then swaps the texture in place.
        s = _make_slots(env="aiSkyDomeLight_")  # network already live
        s.ui.cmb000 = _StubCombo(["C:/img/dusk.exr"])
        s.ui.cmb000.current_index = 0
        HdrManagerSlots.cmb000(s, 0, s.ui.cmb000)
        # cmb000 must NOT mutate synchronously — only schedule the apply.
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(len(s.sb.deferred), 1)
        self.assertEqual(s.sb.deferred[0], s._apply_selection)
        self.assertIn("Applying", s.ui.footer.text)
        # Running the deferred apply swaps the texture in place + resyncs.
        s.sb.deferred[0]()
        self.assertEqual(s.manager.set_paths, ["C:/img/dusk.exr"])
        self.assertEqual(len(s._sync_calls), 1)
        self.assertIn("dusk.exr", s.ui.footer.text)
        self.assertEqual(s.ui.footer.level, "success")

    def test_empty_selection_is_a_noop(self):
        s = _make_slots(env="aiSkyDomeLight_")
        HdrManagerSlots.cmb000(s, 0, _StubWidget(None))
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s._sync_calls, [])
        self.assertEqual(s.sb.deferred, [])  # nothing scheduled

    def test_selection_of_incomplete_image_is_skipped(self):
        """A truncated/corrupt HDR must not be wired in (would crash VP2.0)."""
        path = _write_truncated_hdr()
        try:
            s = _make_slots(env="aiSkyDomeLight_")  # live network
            s.ui.cmb000 = _StubCombo([path])
            s.ui.cmb000.current_index = 0
            s._apply_selection()  # the deferred apply cmb000 schedules
            self.assertEqual(
                s.manager.set_paths, [], "incomplete image must not reach the skydome"
            )
            self.assertEqual(s._sync_calls, [])
            self.assertEqual(s.ui.footer.level, "error")
            self.assertIn("truncated", s.ui.footer.text)
            # Casual selection must NOT pop a modal dialog.
            self.assertEqual(s.sb.message_box_calls, [])
        finally:
            os.remove(path)


class TestVisibilityToggleSynchronous(unittest.TestCase):
    """``_on_render_visible`` applies the Visible flag SYNCHRONOUSLY (issue 1: gray IPR).

    The skydome ``camera`` flag must be set immediately on toggle — like the
    other live controls (intensity / exposure / rotation) — so an active Arnold
    IPR picks it up through its normal attribute-edit callback. It must NOT be
    deferred: the toggle has no combo-popup re-entrancy to escape (unlike the
    map swap), and deferring it to ``evalDeferred(lowestPriority=True)`` let an
    active IPR starve the ``setAttr`` so toggling Visible appeared to do nothing.
    Pure slot-level invariant — no Maya / Arnold needed. ('Visible' moved from
    the ``chk000`` checkbox to the rotation slider's render-visibility option-box
    toggle, which fires this via its ``toggled`` signal.)
    """

    def test_render_visible_applies_synchronously(self):
        s = _make_slots(env="aiSkyDomeLight_")
        calls = []
        s.manager.set_hdr_map_visibility = lambda v: calls.append(v)
        HdrManagerSlots._on_render_visible(s, True)
        # Applied immediately, in-band — not pushed onto the idle queue.
        self.assertEqual(calls, [True])
        self.assertEqual(s.sb.deferred, [])

    def test_render_visible_off_applies_false(self):
        s = _make_slots(env="aiSkyDomeLight_")
        calls = []
        s.manager.set_hdr_map_visibility = lambda v: calls.append(v)
        HdrManagerSlots._on_render_visible(s, False)
        self.assertEqual(calls, [False])


class TestViewToggleSlots(unittest.TestCase):
    """The rotation slider's two view toggles drive the engine synchronously.

    Viewport-visibility (``_on_viewport_visible`` → ``manager.preview``) and
    render-visibility (``_on_render_visible`` → ``manager.set_hdr_map_visibility``)
    apply live in-band, never deferred — like the other live controls. Pure
    slot-level — no Maya / Arnold needed.
    """

    def test_viewport_visible_sets_preview_in_band(self):
        s = _make_slots(env="aiSkyDomeLight_")
        HdrManagerSlots._on_viewport_visible(s, True)
        self.assertTrue(s.manager.preview)
        self.assertEqual(s.sb.deferred, [])  # live, never deferred

    def test_viewport_invisible_clears_preview(self):
        s = _make_slots(env="aiSkyDomeLight_")
        s.manager.preview = True
        HdrManagerSlots._on_viewport_visible(s, False)
        self.assertFalse(s.manager.preview)

    def test_hdr_map_flags_false_without_toggles(self):
        # Before the option-box toggles are wired, both flag reads fall back to
        # False (no AttributeError) so a build-path apply has sane defaults.
        s = _make_slots(env=None)
        self.assertFalse(HdrManagerSlots.hdr_map_visibility.fget(s))
        self.assertFalse(HdrManagerSlots.hdr_map_preview.fget(s))


class TestFailedApplyResyncsCombo(unittest.TestCase):
    """A bailed apply must re-mirror the dropdown to the live scene HDR (issue 2).

    Symptom: selecting a new map "doesn't change the map" and the popup's
    current-item marker still flags the *previous* map even though the field
    shows the new one. Root cause: when the deferred apply bails (e.g. an
    online-only cloud HDR refused by the integrity gate), the scene never
    changes — but the click already moved the combo's display + currentIndex to
    the rejected pick, so the dropdown lies until the next open re-syncs it. The
    fix re-points the combo at the active map on every bail, keeping field and
    marker honest. Pure slot-level — no Maya / Arnold needed.
    """

    def test_rejected_file_resyncs_to_active(self):
        path = _write_truncated_hdr()
        try:
            s = _make_slots(env="aiSkyDomeLight_")
            active = "C:/proj/sourceimages/active.hdr"
            s.manager.hdr_file_path = active
            s.ui.cmb000 = _StubCombo([active, path])
            s.ui.cmb000.current_index = 1  # user picked the (bad) second entry
            HdrManagerSlots._apply_selection(s)
            self.assertEqual(s.manager.set_paths, [])  # not wired
            # Re-mirrored to the active map (row 0), not left on the rejected pick
            # — so the field and the popup marker agree on what's truly live.
            self.assertEqual(s.ui.cmb000.current_index, 0)
            self.assertEqual(s.ui.footer.level, "error")
        finally:
            os.remove(path)

    def test_no_arnold_resyncs_to_active(self):
        # No live network + Arnold unavailable → build bails; combo must still
        # re-mirror (here: to the explicit None entry, since nothing is wired).
        s = _make_slots(env=None)
        s.manager.arnold_available = lambda: False
        s.ui.cmb000 = _StubCombo([HdrManagerSlots.NONE_TOKEN, "C:/x.exr"])
        s.ui.cmb000.current_index = 1
        HdrManagerSlots._apply_selection(s)
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s.ui.cmb000.current_index, 0)  # back to None (no env)
        self.assertEqual(s.ui.footer.level, "warning")


class TestDeferToIdle(unittest.TestCase):
    """``_defer_to_idle`` routes through Maya's idle queue at NORMAL priority.

    Black-render fix (2026-06-21): ``QTimer.singleShot(0)`` is serviced by
    whatever Qt loop is spinning — including the combo popup's *re-entrant*
    teardown loop — so the scene mutation still ran inside the re-entrancy and a
    live Arnold IPR re-translating left the RenderView stuck. ``cmds.evalDeferred``
    escapes to Maya idle, which the nested Qt loop never services.

    But it must be **normal** priority, not ``lowestPriority``: verified live in a
    fresh Maya, a ``lowestPriority`` callback is starved while Maya sits idle (does
    not run until a later event nudges the loop) while a normal one fires at the
    next idle — so a ``lowestPriority`` map-apply never ran when the user picked a
    map and waited ("the dropdown doesn't change the HDR"). Pin both: deferred (not
    a Qt tick) AND normal priority.
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
            s = HdrManagerSlots.__new__(HdrManagerSlots)
            marker = lambda: None
            s._defer_to_idle(marker)
        finally:
            hm.cmds = orig

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], marker)
        self.assertFalse(
            calls[0][1].get("lowestPriority"),
            "must NOT use lowestPriority — it is starved while Maya sits idle, so "
            "the deferred apply never runs and the dropdown appears not to change "
            "the HDR; normal priority still escapes the popup re-entrancy",
        )


class TestValidateOrWarn(unittest.TestCase):
    """``HdrManagerSlots._validate_or_warn`` integrates the image-integrity gate.

    Regression (2026-06-16): wiring a truncated HDR (a partially-synced Dropbox
    file — header declared 8192x4096, only 82 scanlines present) into the
    skydome crashed Viewport 2.0 in ``AtilImageHandler::GetIBLIntensity``.
    """

    def test_missing_file_is_allowed(self):
        # Maya shows a checker for a missing file — not the IBL crash path.
        self.assertTrue(
            HdrManagerSlots._validate_or_warn(_make_slots(env="x"), "C:/no/such/x.hdr")
        )

    def test_truncated_hdr_is_refused_with_dialog(self):
        path = _write_truncated_hdr()
        try:
            s = _make_slots(env="aiSkyDomeLight_")
            # Explicit apply (default dialog=True) → footer + console + modal.
            self.assertFalse(HdrManagerSlots._validate_or_warn(s, path))
            self.assertEqual(s.ui.footer.level, "error")
            self.assertIn("truncated", s.ui.footer.text)
            self.assertEqual(len(s.sb.message_box_calls), 1)
            dialog_msg = s.sb.message_box_calls[0][0]
            # The dialog must enumerate the real causes (a cloud file still
            # syncing OR a full disk) with actionable fixes, not assert one --
            # "make available offline" is a misdiagnosis when the disk is full.
            low = dialog_msg.lower()
            self.assertIn("cloud", low)
            self.assertIn("sync", low)
            self.assertIn("free up space", low)
            # ...but stay digestible — the raw full path goes to the console,
            # not the popup.
            self.assertNotIn(path, dialog_msg)
        finally:
            os.remove(path)

    def test_truncated_hdr_no_dialog_when_suppressed(self):
        path = _write_truncated_hdr()
        try:
            s = _make_slots(env="aiSkyDomeLight_")
            self.assertFalse(HdrManagerSlots._validate_or_warn(s, path, dialog=False))
            self.assertEqual(s.sb.message_box_calls, [])
        finally:
            os.remove(path)


def _write_complete_hdr(path):
    """A 4x4 flat-RGBE Radiance HDR — validates as complete."""
    blob = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 4 +X 4\n" + b"\x10" * (4 * 4 * 4)
    with open(path, "wb") as f:
        f.write(blob)


class TestAddHdrsFromFolder(unittest.TestCase):
    """Batch folder import adds complete HDRs and skips incomplete ones."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def test_link_mode_adds_complete_skips_truncated(self):
        _write_complete_hdr(os.path.join(self.dir, "good.hdr"))
        with open(os.path.join(self.dir, "bad.hdr"), "wb") as f:
            f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 16 +X 16\n\x02\x02\x00\x10")

        s = _make_slots(env="aiSkyDomeLight_")
        s._add_mode = lambda: "link"
        HdrManagerSlots._add_hdrs_from_folder(s, self.dir)

        self.assertEqual(len(s.manager.set_paths), 1, "only the complete HDR is wired")
        self.assertEqual(os.path.basename(s.manager.set_paths[0]), "good.hdr")
        self.assertEqual(s.ui.footer.level, "success")
        self.assertIn("Added 1", s.ui.footer.text)
        self.assertIn("skipped", s.ui.footer.text)

    def test_empty_folder_warns(self):
        s = _make_slots()
        s._add_mode = lambda: "link"
        HdrManagerSlots._add_hdrs_from_folder(s, self.dir)
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s.ui.footer.level, "warning")

    def test_all_truncated_warns_none_added(self):
        with open(os.path.join(self.dir, "bad.hdr"), "wb") as f:
            f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 16 +X 16\n\x02\x02\x00\x10")
        s = _make_slots(env="aiSkyDomeLight_")
        s._add_mode = lambda: "link"
        HdrManagerSlots._add_hdrs_from_folder(s, self.dir)
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s.ui.footer.level, "warning")
        self.assertIn("incomplete", s.ui.footer.text)

    def test_copy_mode_imports_into_sourceimages(self):
        src_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(src_dir, ignore_errors=True))
        _write_complete_hdr(os.path.join(self.dir, "good.hdr"))
        s = _make_slots(env="aiSkyDomeLight_")
        s._add_mode = lambda: "copy"
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=src_dir,
        ):
            HdrManagerSlots._add_hdrs_from_folder(s, self.dir)
        self.assertTrue(
            os.path.exists(os.path.join(src_dir, "good.hdr")),
            "HDR must be copied into sourceimages",
        )
        self.assertEqual(len(s.manager.set_paths), 1)
        self.assertEqual(os.path.basename(s.manager.set_paths[0]), "good.hdr")
        self.assertEqual(s.ui.footer.level, "success")

    def test_copy_mode_skips_existing_truncated_in_sourceimages(self):
        """A same-named-but-truncated file already in sourceimages isn't reused."""
        src_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(src_dir, ignore_errors=True))
        _write_complete_hdr(os.path.join(self.dir, "x.hdr"))  # good source
        with open(os.path.join(src_dir, "x.hdr"), "wb") as f:  # truncated existing
            f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 16 +X 16\n\x02\x02\x00\x10")
        s = _make_slots(env="aiSkyDomeLight_")
        s._add_mode = lambda: "copy"
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=src_dir,
        ):
            HdrManagerSlots._add_hdrs_from_folder(s, self.dir)
        # Existing truncated file must not be counted/wired as a usable add.
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s.ui.footer.level, "warning")

    def test_copy_mode_file_in_sourceimages_subfolder_not_duplicated(self):
        """A file already in a sourceimages SUBFOLDER is used in place, not copied.

        Regression (2026-06-16): Copy/Move duplicated a ``sourceimages/hdr/x.hdr``
        into the sourceimages root instead of wiring it where it already lives.
        """
        src_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(src_dir, ignore_errors=True))
        sub = os.path.join(src_dir, "hdr")
        os.makedirs(sub)
        f = os.path.join(sub, "env.hdr")
        _write_complete_hdr(f)
        s = _make_slots(env="aiSkyDomeLight_")
        s._add_mode = lambda: "copy"
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=src_dir,
        ):
            HdrManagerSlots._add_hdrs(s, [f], where="hdr", careful=False)
        # Not duplicated into the root...
        self.assertFalse(os.path.exists(os.path.join(src_dir, "env.hdr")))
        # ...and wired in place (the subfolder path).
        self.assertEqual(len(s.manager.set_paths), 1)
        self.assertEqual(
            os.path.normcase(s.manager.set_paths[0]),
            os.path.normcase(os.path.normpath(f)),
        )


class TestAddHdr(unittest.TestCase):
    """The unified 'Add HDR(s)…' flow — one dialog picks files and/or a folder.

    The mixed files/folder dialog (`_pick_hdr_paths`) is stubbed; these assert
    the dispatch: a single loose file → careful (modal on a bad file), a folder
    or multiple → bulk (skip + count, no per-file modal).
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def _slots(self, mode, picked):
        s = _make_slots(env="aiSkyDomeLight_")
        s._add_mode = lambda: mode
        s._pick_hdr_paths = lambda start: picked
        return s

    def test_cancelled_dialog_is_noop(self):
        s = self._slots("link", [])
        HdrManagerSlots.add_hdr(s)
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s.sb.message_box_calls, [])

    def test_single_loose_file_is_careful(self):
        good = os.path.join(self.dir, "good.hdr")
        _write_complete_hdr(good)
        s = self._slots("link", [good])
        HdrManagerSlots.add_hdr(s)
        self.assertEqual(s.manager.set_paths, [good])
        self.assertEqual(s.ui.footer.level, "success")
        self.assertIn("Linked", s.ui.footer.text)

    def test_single_truncated_file_raises_modal(self):
        bad = os.path.join(self.dir, "bad.hdr")
        with open(bad, "wb") as f:
            f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 16 +X 16\n\x02\x02\x00\x10")
        s = self._slots("link", [bad])
        HdrManagerSlots.add_hdr(s)
        self.assertEqual(s.manager.set_paths, [])  # not wired
        self.assertEqual(s.ui.footer.level, "error")
        self.assertEqual(len(s.sb.message_box_calls), 1)  # careful → modal

    def test_folder_selection_is_bulk_no_per_file_modal(self):
        _write_complete_hdr(os.path.join(self.dir, "a.hdr"))
        with open(os.path.join(self.dir, "bad.hdr"), "wb") as f:
            f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 16 +X 16\n\x02\x02\x00\x10")
        s = self._slots("link", [self.dir])  # a directory
        HdrManagerSlots.add_hdr(s)
        self.assertEqual(len(s.manager.set_paths), 1)  # last good wired once
        self.assertIn("Added 1", s.ui.footer.text)  # bulk summary
        self.assertIn("skipped", s.ui.footer.text)
        self.assertEqual(s.sb.message_box_calls, [])  # bulk → no per-file modal

    def test_multiple_loose_files_is_bulk(self):
        a = os.path.join(self.dir, "a.hdr")
        b = os.path.join(self.dir, "b.hdr")
        _write_complete_hdr(a)
        _write_complete_hdr(b)
        s = self._slots("link", [a, b])  # two files → bulk
        HdrManagerSlots.add_hdr(s)
        self.assertIn("Added 2", s.ui.footer.text)
        self.assertEqual(s.sb.message_box_calls, [])


class TestRefreshComboRecursive(unittest.TestCase):
    """``_refresh_combo`` scans sourceimages recursively so subfolder HDRs list."""

    def test_scans_sourceimages_recursively(self):
        s = _make_slots(env=None)
        s.ui.cmb000 = _StubCombo()
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=r"C:\proj\sourceimages",
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.os.path.isdir", return_value=True
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.ptk.get_dir_contents",
            return_value={
                "filename": ["env"],
                "filepath": [r"C:\proj\sourceimages\hdr\env.hdr"],
            },
        ) as gdc:
            HdrManagerSlots._refresh_combo(s)
        # Subfolder HDRs (e.g. sourceimages/hdr/) must be reachable → recursive.
        self.assertTrue(gdc.call_args.kwargs.get("recursive"))
        # The subfolder HDR landed in the combo (alongside the prepended None).
        data = [s.ui.cmb000.itemData(i) for i in range(s.ui.cmb000.count())]
        self.assertIn(r"C:\proj\sourceimages\hdr\env.hdr", data)
        self.assertIn(HdrManagerSlots.NONE_TOKEN, data)

    def _refresh_with_disk(self, combo, disk_paths):
        """Run the real ``_refresh_combo`` against a stubbed disk listing."""
        s = _make_slots(env=None)
        s.ui.cmb000 = combo
        names = [os.path.splitext(os.path.basename(p))[0] for p in disk_paths]
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=r"C:\proj\sourceimages",
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.os.path.isdir", return_value=True
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.ptk.get_dir_contents",
            return_value={"filename": names, "filepath": list(disk_paths)},
        ), mock.patch.object(combo, "add", wraps=combo.add) as add_spy:
            HdrManagerSlots._refresh_combo(s)
        return s, add_spy

    def test_skips_rebuild_when_listing_unchanged(self):
        # Combo already lists exactly what's on disk (None + the two HDRs).
        paths = [r"C:\proj\sourceimages\a.hdr", r"C:\proj\sourceimages\b.hdr"]
        combo = _StubCombo([HdrManagerSlots.NONE_TOKEN, *paths])
        s, add_spy = self._refresh_with_disk(combo, paths)
        # Unchanged listing → no destructive repopulate (would desync the popup
        # view mid-open and drop the first click).
        add_spy.assert_not_called()
        self.assertIn("2 HDR", s.ui.footer.text)

    def test_rebuilds_when_listing_changed(self):
        # A new HDR appeared on disk → the combo must repopulate to show it.
        combo = _StubCombo([HdrManagerSlots.NONE_TOKEN, r"C:\proj\sourceimages\a.hdr"])
        paths = [r"C:\proj\sourceimages\a.hdr", r"C:\proj\sourceimages\b.hdr"]
        s, add_spy = self._refresh_with_disk(combo, paths)
        add_spy.assert_called()
        data = [combo.itemData(i) for i in range(combo.count())]
        self.assertIn(r"C:\proj\sourceimages\b.hdr", data)


class TestHdrSpinBoxesAreUitk(unittest.TestCase):
    """The HDR level/advanced spin boxes are uitk SpinBox/DoubleSpinBox so each
    carries the option-box reset-to-default button (2026-06-21). Static ``.ui``
    guard — no Qt; pairs with the ``add_reset_buttons`` call in ``_initialize_ui``.
    """

    def setUp(self):
        import xml.etree.ElementTree as ET
        import mayatk.light_utils.hdr_manager as hm

        ui_path = os.path.join(os.path.dirname(hm.__file__), "hdr_manager.ui")
        self.root = ET.parse(ui_path).getroot()

    def test_all_spinboxes_are_uitk_types(self):
        classes = {
            w.get("name"): w.get("class")
            for w in self.root.iter("widget")
            if (w.get("name") or "").startswith("spn_")
        }
        self.assertEqual(classes.get("spn_intensity"), "DoubleSpinBox")
        self.assertEqual(classes.get("spn_exposure"), "DoubleSpinBox")
        self.assertEqual(classes.get("spn_diffuse"), "DoubleSpinBox")
        self.assertEqual(classes.get("spn_specular"), "DoubleSpinBox")
        self.assertEqual(classes.get("spn_resolution"), "SpinBox")
        self.assertEqual(classes.get("spn_samples"), "SpinBox")
        # No plain Qt spin boxes remain (they'd lack the option-box reset).
        self.assertFalse(
            [n for n, c in classes.items() if c in ("QSpinBox", "QDoubleSpinBox")]
        )

    def test_uitk_spinbox_customwidgets_declared(self):
        declared = {cw.findtext("class") for cw in self.root.iter("customwidget")}
        self.assertIn("DoubleSpinBox", declared)
        self.assertIn("SpinBox", declared)


class TestRotationSliderUi(unittest.TestCase):
    """The rotation slider is a uitk Slider (carries the option box: value +
    viewport/render toggles). The old Visible checkbox (chk000), the tilt spin
    box (spn_tilt), and the separate preview slider (sld_preview) are all gone.
    Static ``.ui`` guard — no Qt; pairs with _setup_rotation_slider.
    """

    def setUp(self):
        import xml.etree.ElementTree as ET
        import mayatk.light_utils.hdr_manager as hm

        ui_path = os.path.join(os.path.dirname(hm.__file__), "hdr_manager.ui")
        self.root = ET.parse(ui_path).getroot()

    def test_rotation_slider_is_uitk_slider(self):
        classes = {w.get("name"): w.get("class") for w in self.root.iter("widget")}
        self.assertEqual(classes.get("slider000"), "Slider")
        # Removed controls: Visible checkbox, tilt spin box, preview slider.
        self.assertNotIn("chk000", classes)
        self.assertNotIn("spn_tilt", classes)
        self.assertNotIn("sld_preview", classes)

    def test_slider_customwidget_declared(self):
        declared = {cw.findtext("class") for cw in self.root.iter("customwidget")}
        self.assertIn("Slider", declared)


class TestSelectComboPath(unittest.TestCase):
    """``_select_combo_path`` matches despite slash/case path differences.

    Regression (2026-06-16): after Copy/Move add, the map showed green as set
    but the dropdown stayed blank — the combo stores ``get_dir_contents``
    filepaths (``os.path.join`` on Maya's forward-slash workspace →
    ``C:/proj/sourceimages\\x.hdr``) while the caller passed an
    ``os.path.normpath`` (all backslashes), so the exact ``findData`` missed.
    """

    def test_matches_mixed_slash_combo_data(self):
        s = _make_slots(env="aiSkyDomeLight_")
        combo_data = "C:/proj/sourceimages\\machine_shop.hdr"  # mixed slashes
        s.ui.cmb000 = _StubCombo([combo_data])
        target = os.path.normpath("C:/proj/sourceimages/machine_shop.hdr")
        self.assertNotEqual(combo_data, target)  # exact findData would miss
        self.assertTrue(HdrManagerSlots._select_combo_path(s, target))
        self.assertEqual(s.ui.cmb000.current_index, 0)

    def test_no_match_returns_false(self):
        s = _make_slots(env="aiSkyDomeLight_")
        s.ui.cmb000 = _StubCombo(["C:/proj/sourceimages/other.hdr"])
        self.assertFalse(
            HdrManagerSlots._select_combo_path(s, "C:/proj/sourceimages/missing.hdr")
        )
        self.assertIsNone(s.ui.cmb000.current_index)


class TestNotify(unittest.TestCase):
    """``HdrManagerSlots._notify`` — colour-coded footer + optional dialog."""

    def test_footer_level_set(self):
        s = _make_slots()
        HdrManagerSlots._notify(s, "Done", level="success")
        self.assertEqual(s.ui.footer.text, "Done")
        self.assertEqual(s.ui.footer.level, "success")
        self.assertEqual(s.sb.message_box_calls, [])

    def test_dialog_shows_digestible_message_not_full_detail(self):
        """The popup shows the short message; the full detail goes to console."""
        s = _make_slots()
        HdrManagerSlots._notify(
            s,
            "Boom",
            level="error",
            detail="full boom at C:/secret/raw/path.hdr",
            dialog=True,
        )
        self.assertEqual(len(s.sb.message_box_calls), 1)
        msg, _buttons = s.sb.message_box_calls[0]
        self.assertIn("Error:", msg)
        self.assertIn("Boom", msg)  # digestible footer message
        self.assertNotIn("C:/secret/raw/path.hdr", msg)  # NOT the raw detail

    def test_dialog_text_overrides_message_in_popup(self):
        """``dialog_text`` drives the popup; ``message``/``detail`` don't leak in."""
        s = _make_slots()
        HdrManagerSlots._notify(
            s,
            "short footer",
            level="error",
            detail="full detail",
            dialog=True,
            dialog_text="digestible dialog body",
        )
        msg, _buttons = s.sb.message_box_calls[0]
        self.assertIn("digestible dialog body", msg)
        self.assertNotIn("full detail", msg)
        # Footer still gets the short message; console gets the full detail.
        self.assertEqual(s.ui.footer.text, "short footer")


class TestHdrNoneOption(unittest.TestCase):
    """The dropdown's explicit 'None' entry removes the HDR environment.

    Feature (2026-06-18): a 'None' item is prepended to the HDR dropdown so the
    user can clear the skydome environment from the same combobox that sets it
    (carries ``HdrManagerSlots.NONE_TOKEN`` as userData). Pure slot-level
    behavior — no Maya / Arnold needed.
    """

    def _slots_with_none_selected(self, env):
        s = _make_slots(env=env)
        s.ui.cmb000 = _StubCombo([HdrManagerSlots.NONE_TOKEN])
        s.ui.cmb000.current_index = 0
        return s

    def test_refresh_combo_prepends_none_entry(self):
        s = _make_slots(env=None)
        s.ui.cmb000 = _StubCombo()
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=r"C:\proj\sourceimages",
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.os.path.isdir", return_value=True
        ), mock.patch(
            "mayatk.light_utils.hdr_manager.ptk.get_dir_contents",
            return_value={
                "filename": ["env"],
                "filepath": [r"C:\proj\sourceimages\env.hdr"],
            },
        ):
            HdrManagerSlots._refresh_combo(s)
        # None sits at the top (index 0), ahead of the listed HDR file.
        self.assertEqual(s.ui.cmb000.itemData(0), HdrManagerSlots.NONE_TOKEN)
        self.assertEqual(s.ui.cmb000.count(), 2)
        # No header (it would hide the active map + break selection); the combo
        # is still made non-persistent so it never restores a stale pick.
        self.assertIsNone(s.ui.cmb000.last_header)
        self.assertFalse(s.ui.cmb000.restore_state)

    def test_refresh_combo_prepends_none_when_no_sourceimages(self):
        s = _make_slots(env=None)
        s.ui.cmb000 = _StubCombo()
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=None,
        ):
            HdrManagerSlots._refresh_combo(s)
        self.assertEqual(s.ui.cmb000.itemData(0), HdrManagerSlots.NONE_TOKEN)
        # Even the no-sourceimages path keeps the combo non-persistent + headerless.
        self.assertIsNone(s.ui.cmb000.last_header)
        self.assertFalse(s.ui.cmb000.restore_state)

    def test_select_none_defers_then_clears_live_network(self):
        s = _make_slots(env="aiSkyDomeLight_")
        s.ui.cmb000 = _StubCombo([HdrManagerSlots.NONE_TOKEN])
        s.ui.cmb000.current_index = 0
        HdrManagerSlots.cmb000(s, 0, s.ui.cmb000)
        # Deferred, never a synchronous delete inside the combo signal.
        self.assertEqual(s.manager.clear_calls, 0)
        self.assertEqual(len(s.sb.deferred), 1)
        self.assertIn("Removing", s.ui.footer.text)
        # The deferred apply removes the network.
        s.sb.deferred[0]()
        self.assertEqual(s.manager.clear_calls, 1)
        self.assertIsNone(s.manager.hdr_env)
        self.assertEqual(len(s._sync_calls), 1)
        self.assertEqual(s.ui.footer.level, "success")
        # Selecting None must never wire a path into the skydome.
        self.assertEqual(s.manager.set_paths, [])

    def test_select_none_without_network_defers_then_informs(self):
        s = _make_slots(env=None)
        s.ui.cmb000 = _StubCombo([HdrManagerSlots.NONE_TOKEN])
        s.ui.cmb000.current_index = 0
        HdrManagerSlots.cmb000(s, 0, s.ui.cmb000)
        self.assertEqual(len(s.sb.deferred), 1)
        self.assertIn("Removing", s.ui.footer.text)
        s.sb.deferred[0]()
        self.assertEqual(s.manager.clear_calls, 0)
        self.assertEqual(s._sync_calls, [])
        self.assertEqual(s.ui.footer.level, "info")
        self.assertIn("None", s.ui.footer.text)

    def test_set_hdr_with_none_clears_network(self):
        s = self._slots_with_none_selected(env="aiSkyDomeLight_")
        HdrManagerSlots._apply_selection(s)
        self.assertEqual(s.manager.clear_calls, 1)
        self.assertEqual(len(s._sync_calls), 1)
        self.assertEqual(s.ui.footer.level, "success")
        self.assertEqual(s.manager.set_paths, [])

    def test_set_hdr_with_none_and_no_network_is_noop(self):
        s = self._slots_with_none_selected(env=None)
        HdrManagerSlots._apply_selection(s)
        self.assertEqual(s.manager.clear_calls, 0)
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s.ui.footer.level, "info")


class TestSelectActiveInCombo(unittest.TestCase):
    """``_select_active_in_combo`` keeps the dropdown a live mirror of the scene.

    Feature (2026-06-21): the combo must always *display* the active HDR (or the
    explicit ``None`` entry when none is wired) so the user can tell at a glance
    what's lighting the scene — replacing the old fixed ``HDR Map:`` header that
    hid it. Pure slot-level behavior — no Maya / Arnold needed.
    """

    def test_selects_matching_active_path(self):
        s = _make_slots(env="aiSkyDomeLight_")
        s.ui.cmb000 = _StubCombo(
            [HdrManagerSlots.NONE_TOKEN, "C:/proj/sourceimages/env.hdr"]
        )
        s.manager.hdr_file_path = "C:/proj/sourceimages/env.hdr"
        s._select_active_in_combo()
        self.assertEqual(s.ui.cmb000.current_index, 1)  # the active map's row

    def test_selects_none_entry_when_no_env(self):
        s = _make_slots(env=None)
        s.ui.cmb000 = _StubCombo(
            [HdrManagerSlots.NONE_TOKEN, "C:/proj/sourceimages/env.hdr"]
        )
        s._select_active_in_combo()
        # No env → land on the explicit None row, not a blank box.
        self.assertEqual(s.ui.cmb000.current_index, 0)

    def test_surfaces_active_path_when_not_listed(self):
        # An active HDR outside sourceimages (e.g. a Link-mode file) isn't in the
        # list — it must still be shown, not reported as a misleading "None".
        s = _make_slots(env="aiSkyDomeLight_")
        s.ui.cmb000 = _StubCombo(
            [HdrManagerSlots.NONE_TOKEN, "C:/proj/sourceimages/other.hdr"]
        )
        s.manager.hdr_file_path = "C:/ext/linked.hdr"  # active, outside the list
        s._select_active_in_combo()
        self.assertEqual(s.ui.cmb000.currentData(), "C:/ext/linked.hdr")
        self.assertNotEqual(s.ui.cmb000.currentData(), HdrManagerSlots.NONE_TOKEN)


class TestComboHeaderBreaksSelection(unittest.TestCase):
    """Root-cause lock for "selecting an HDR does nothing" (2026-06-21).

    A header on the dropdown silently broke selection: ``ComboBox.check_index``
    snaps ``currentIndex`` back to ``-1`` after every pick, so the slot's
    ``widget.currentData()`` read ``None`` and the apply no-opped (and the combo
    kept painting the fixed header instead of the chosen map). Verified against
    the REAL uitk ``ComboBox`` — the ``_StubWidget`` tests above couldn't catch
    it because they returned ``currentData()`` unconditionally. The fix is to
    populate the HDR combo *without* a header (see ``_refresh_combo``).
    """

    @classmethod
    def setUpClass(cls):
        try:
            from qtpy import QtWidgets
        except Exception as e:  # pragma: no cover - environment without Qt
            raise unittest.SkipTest(f"qtpy unavailable: {e}")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def _combo():
        from uitk.widgets.comboBox import ComboBox

        return ComboBox()

    def test_header_combo_loses_currentdata_on_select(self):
        """Reproduce the bug: with a header, a pick leaves currentData() None."""
        cmb = self._combo()
        cmb.add(
            [("envA", "C:/a.exr"), ("envB", "C:/b.exr")], header="HDR Map:", clear=True
        )
        cmb.setCurrentIndex(1)  # user picks envB
        self.assertEqual(cmb.currentIndex(), -1)  # snapped back to the header
        self.assertIsNone(cmb.currentData())  # ← the slot would read None

    def test_headerless_combo_delivers_currentdata_to_slot(self):
        """The fix: no header → the pick survives and the slot sees the path."""
        cmb = self._combo()
        cmb.add([("envA", "C:/a.exr"), ("envB", "C:/b.exr")], clear=True)
        cmb.restore_state = False
        seen = []
        cmb.currentIndexChanged.connect(lambda i: seen.append(cmb.currentData()))
        cmb.setCurrentIndex(1)  # user picks envB
        self.assertEqual(cmb.currentIndex(), 1)  # selection sticks
        self.assertEqual(cmb.currentData(), "C:/b.exr")
        self.assertEqual(seen[-1], "C:/b.exr")  # the slot received the real path


if __name__ == "__main__":
    unittest.main(verbosity=2)
