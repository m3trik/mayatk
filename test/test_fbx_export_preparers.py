# !/usr/bin/python
# coding=utf-8
"""Tests for the shared FBX before-export preparer registry (FbxUtils).

The registry lets multiple subsystems (Shots, Audio, …) stamp their data onto
``data_export`` before *any* FBX export through one composable hook, then realize
declared takes.  Covers the registry mechanics (compose, ref-counted teardown,
fault isolation) and the real Audio + Shots composition reaching one ASCII FBX.
"""
import os
import tempfile
import unittest

from base_test import MayaTkTestCase

import maya.cmds as cmds
from maya import mel

from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.audio_utils._audio_utils import AudioUtils
from mayatk.audio_utils.audio_clips._audio_clips import AudioClips


def _export_selected_ascii(nodes, fname="mtk_preparers.fbx"):
    out = os.path.join(tempfile.gettempdir(), fname)
    try:
        mel.eval("FBXExportInAscii -v true")
        cmds.select(list(nodes), replace=True)
        cmds.file(
            out, force=True, options="v=0;", type="FBX export", exportSelected=True
        )
        with open(out, encoding="utf-8", errors="ignore") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.remove(out)


def _clear_export_state():
    for name in list(FbxUtils._export_preparers):
        FbxUtils.unregister_export_preparer(name)
    FbxUtils.disable_auto_takes()
    FbxUtils.reset_takes()


class TestExportPreparerRegistry(MayaTkTestCase):
    """Hook lifecycle + composition with lightweight stub preparers."""

    def setUp(self):
        super().setUp()
        _clear_export_state()

    def tearDown(self):
        _clear_export_state()
        super().tearDown()

    def test_register_installs_hook_and_runs_preparer(self):
        cube = self.create_test_cube("prepCube")
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=10, v=1)

        ran = []
        FbxUtils.register_export_preparer("stub", lambda: ran.append(True))
        self.assertTrue(FbxUtils.is_auto_takes_enabled())

        _export_selected_ascii([cube])
        self.assertEqual(len(ran), 1)  # hook fired the preparer exactly once

    def test_multiple_preparers_compose_in_registration_order(self):
        cube = self.create_test_cube("prepCube2")
        order = []
        FbxUtils.register_export_preparer("a", lambda: order.append("a"))
        FbxUtils.register_export_preparer("b", lambda: order.append("b"))

        _export_selected_ascii([cube])
        self.assertEqual(order, ["a", "b"])

    def test_known_producers_run_in_canonical_order(self):
        """shots must run before audio regardless of registration order —
        audio scopes its manifest against the fbx_takes shots republishes."""
        cube = self.create_test_cube("prepCube2b")
        order = []
        FbxUtils.register_export_preparer("audio", lambda: order.append("audio"))
        FbxUtils.register_export_preparer("shots", lambda: order.append("shots"))
        FbxUtils.register_export_preparer("zzz", lambda: order.append("zzz"))

        _export_selected_ascii([cube])
        self.assertEqual(order, ["shots", "audio", "zzz"])

    def test_unregister_refcounts_the_hook(self):
        FbxUtils.register_export_preparer("a", lambda: None)
        FbxUtils.register_export_preparer("b", lambda: None)
        self.assertTrue(FbxUtils.is_auto_takes_enabled())

        FbxUtils.unregister_export_preparer("a")
        self.assertTrue(FbxUtils.is_auto_takes_enabled())  # b still holds it

        FbxUtils.unregister_export_preparer("b")
        self.assertFalse(FbxUtils.is_auto_takes_enabled())  # last one gone → torn down

    def test_one_preparer_failure_does_not_abort_export(self):
        cube = self.create_test_cube("prepCube3")
        ran = []

        def boom():
            raise RuntimeError("preparer blew up")

        FbxUtils.register_export_preparer("bad", boom)
        FbxUtils.register_export_preparer("good", lambda: ran.append(True))

        text = _export_selected_ascii([cube])  # must still produce the FBX
        self.assertTrue(ran)  # the good preparer still ran
        self.assertIn("prepCube3", text)

    def test_explicit_enable_independent_of_preparers(self):
        self.assertFalse(FbxUtils.is_auto_takes_enabled())
        FbxUtils.enable_auto_takes()
        self.assertTrue(FbxUtils.is_auto_takes_enabled())
        FbxUtils.disable_auto_takes()
        self.assertFalse(FbxUtils.is_auto_takes_enabled())

    def test_explicit_enable_and_preparer_both_hold_the_hook(self):
        FbxUtils.enable_auto_takes()
        FbxUtils.register_export_preparer("a", lambda: None)
        FbxUtils.disable_auto_takes()  # explicit off, but a preparer remains
        self.assertTrue(FbxUtils.is_auto_takes_enabled())
        FbxUtils.unregister_export_preparer("a")
        self.assertFalse(FbxUtils.is_auto_takes_enabled())


class TestAudioShotsAutoExportCompose(MayaTkTestCase):
    """Audio + Shots auto-export hooks compose: one export, both channels fresh."""

    def setUp(self):
        super().setUp()
        _clear_export_state()
        ShotStore.clear_active()

    def tearDown(self):
        AudioClips.disable_auto_export()
        ShotStore.disable_auto_export()
        ShotStore.clear_active()
        _clear_export_state()
        super().tearDown()

    def test_both_systems_bake_on_one_export(self):
        cube = self.create_test_cube("anim_host")
        for t, v in ((1, 0.0), (50, 5.0), (100, 0.0)):
            cmds.setKeyframe(cube, attribute="translateX", t=t, value=v)
        cmds.playbackOptions(min=1, max=100)

        # Author audio + shots but DO NOT manually publish/prepare — the
        # registered preparers must do it inside the before-export hook.
        AudioUtils.write_key("footstep", frame=10, value=1)
        AudioUtils.write_key("footstep", frame=15, value=0)

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        store.define_shot("Outro", 51, 100)

        # Pre-create the carrier so it can be in the export selection; the hook
        # populates its channels during the export (mirrors File ▸ Export All,
        # where the carrier is included automatically).
        DataNodes.ensure_export()

        AudioClips.enable_auto_export()
        ShotStore.enable_auto_export()
        self.assertTrue(FbxUtils.is_auto_takes_enabled())

        text = _export_selected_ascii([cube, DataNodes.EXPORT], "mtk_both.fbx")

        # Shots: metadata + both named takes.
        self.assertIn("shot_metadata", text)
        self.assertIn("Intro", text)
        self.assertIn("Outro", text)
        # Audio: manifest channel + the track label.
        self.assertIn("audio_manifest", text)
        self.assertIn("footstep", text)

        # Both preparers wrote distinct attrs on the one carrier node.
        attrs = cmds.listAttr(DataNodes.EXPORT, userDefined=True) or []
        self.assertIn("shot_metadata", attrs)
        self.assertIn("fbx_takes", attrs)
        self.assertIn("audio_manifest", attrs)


if __name__ == "__main__":
    unittest.main()
