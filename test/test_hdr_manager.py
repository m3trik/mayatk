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

    def test_create_network_applies_quality_knobs(self):
        """create_network forwards the new quality/contribution knobs."""
        node = self.mgr.create_network(
            hdrMap="C:/tmp/x.exr",
            resolution=4096,
            samples=2,
            diffuse=0.25,
            specular=0.75,
        )
        self.assertIsNotNone(node)
        self.assertEqual(cmds.getAttr(f"{node}.resolution"), 4096)
        self.assertEqual(cmds.getAttr(f"{node}.aiSamples"), 2)
        self.assertAlmostEqual(cmds.getAttr(f"{node}.aiDiffuse"), 0.25)
        self.assertAlmostEqual(cmds.getAttr(f"{node}.aiSpecular"), 0.75)


class TestRotationSetterRobust(unittest.TestCase):
    """The rotation setter no-ops cleanly on a bad network (never throws).

    Regression (2026-06-16): a slider drag raised ``TypeError: Object 140.0 is
    invalid`` from ``cmds.rotate`` instead of returning cleanly. Needs ``cmds``
    (real node existence) but not Arnold.
    """

    def test_stale_transform_does_not_raise(self):
        mgr = HdrManager.__new__(HdrManager)
        with mock.patch.object(
            type(mgr),
            "hdr_env_transform",
            new_callable=mock.PropertyMock,
            return_value="ghostTransform_does_not_exist",
        ):
            mgr.rotation = 140.0  # objExists False → clean early return

    def test_rotate_runtime_error_is_swallowed(self):
        node = cmds.createNode("transform")
        try:
            mgr = HdrManager.__new__(HdrManager)
            with mock.patch.object(
                type(mgr),
                "hdr_env_transform",
                new_callable=mock.PropertyMock,
                return_value=node,
            ), mock.patch(
                "mayatk.light_utils.hdr_manager.cmds.rotate",
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
        self.last_header = None  # records the header passed to add() (persistence opt-out)

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
        # A header makes the real ComboBox set restore_state = not has_header
        # → False (no cross-session persistence); record it so tests can assert
        # the HDR combo opts out.
        self.last_header = header
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


class _StubQTimer:
    """Records ``singleShot`` deferrals instead of touching a real event loop."""

    def __init__(self, sb):
        self._sb = sb

    def singleShot(self, ms, fn):
        self._sb.deferred.append((ms, fn))


class _StubSb:
    """Records ``message_box`` calls (returns ``"Ok"`` like the real modal) and
    the cold-start ``QtCore.QTimer.singleShot`` deferrals ``cmb000`` schedules."""

    def __init__(self):
        self.message_box_calls = []
        self.deferred = []  # (ms, callback) scheduled via QtCore.QTimer.singleShot
        # cmb000 reaches the timer as self.sb.QtCore.QTimer.singleShot(...).
        self.QtCore = type("_QtCore", (), {"QTimer": _StubQTimer(self)})()

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
    s._sync_calls = []
    s._sync_ui_to_scene = lambda: s._sync_calls.append(1)
    s._refresh_combo = lambda: None
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
    """Regression: a cold-start selection must not create the network or load
    Arnold *synchronously* — it must defer the build off the combo signal.

    Crash (2026-06-16): ``cmb000`` set ``manager.hdr_env`` on every selection,
    whose setter calls ``arnold_available()`` → ``cmds.loadPlugin("mtoa")`` and
    creates render nodes. Booting mtoa synchronously from a combobox
    ``currentIndexChanged`` callback (mid popup-teardown) crashed Maya. The
    separate "Set HDR" button was removed (2026-06-19): selecting is now the
    sole apply action, and cold-start creation is deferred to the next event
    loop tick (``QTimer.singleShot`` → :meth:`_apply_selection`) so it runs
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
        # Exactly one deferred apply was scheduled (next-tick, off the signal).
        self.assertEqual(len(s.sb.deferred), 1)
        self.assertEqual(s.sb.deferred[0][1], s._apply_selection)
        # Footer reflects the in-progress apply, not a silent no-op.
        self.assertIn("sky.exr", s.ui.footer.text)
        self.assertIn("Applying", s.ui.footer.text)

    def test_selection_with_live_network_swaps_texture_in_place(self):
        s = _make_slots(env="aiSkyDomeLight_")  # network already live
        HdrManagerSlots.cmb000(s, 0, _StubWidget("C:/img/dusk.exr"))
        self.assertEqual(s.manager.set_paths, ["C:/img/dusk.exr"])
        self.assertEqual(len(s._sync_calls), 1)
        self.assertIn("dusk.exr", s.ui.footer.text)
        self.assertEqual(s.ui.footer.level, "success")

    def test_empty_selection_is_a_noop(self):
        s = _make_slots(env="aiSkyDomeLight_")
        HdrManagerSlots.cmb000(s, 0, _StubWidget(None))
        self.assertEqual(s.manager.set_paths, [])
        self.assertEqual(s._sync_calls, [])

    def test_selection_of_incomplete_image_is_skipped(self):
        """A truncated/corrupt HDR must not be wired in (would crash VP2.0)."""
        path = _write_truncated_hdr()
        try:
            s = _make_slots(env="aiSkyDomeLight_")  # live network
            HdrManagerSlots.cmb000(s, 0, _StubWidget(path))
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
            # The dialog must carry the actionable cloud-sync guidance...
            self.assertIn("Make available offline", dialog_msg)
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
        # The combo is populated with a header → non-persistent across sessions
        # (restore_state = not has_header), so it never restores a stale pick.
        self.assertEqual(s.ui.cmb000.last_header, "HDR Map:")

    def test_refresh_combo_prepends_none_when_no_sourceimages(self):
        s = _make_slots(env=None)
        s.ui.cmb000 = _StubCombo()
        with mock.patch(
            "mayatk.light_utils.hdr_manager.EnvUtils.get_env_info",
            return_value=None,
        ):
            HdrManagerSlots._refresh_combo(s)
        self.assertEqual(s.ui.cmb000.itemData(0), HdrManagerSlots.NONE_TOKEN)
        # Even the no-sourceimages path keeps the combo non-persistent.
        self.assertEqual(s.ui.cmb000.last_header, "HDR Map:")

    def test_select_none_clears_live_network(self):
        s = _make_slots(env="aiSkyDomeLight_")
        HdrManagerSlots.cmb000(s, 0, _StubWidget(HdrManagerSlots.NONE_TOKEN))
        self.assertEqual(s.manager.clear_calls, 1)
        self.assertIsNone(s.manager.hdr_env)
        self.assertEqual(len(s._sync_calls), 1)
        self.assertEqual(s.ui.footer.level, "success")
        # Selecting None must never wire a path into the skydome.
        self.assertEqual(s.manager.set_paths, [])

    def test_select_none_without_network_is_informational(self):
        s = _make_slots(env=None)
        HdrManagerSlots.cmb000(s, 0, _StubWidget(HdrManagerSlots.NONE_TOKEN))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
