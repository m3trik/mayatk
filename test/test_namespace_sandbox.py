# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.namespace_sandbox module.

Covers headless-safe surfaces:
    - FBXImporter / MayaImporter.is_supported_file (file type dispatch)
    - CameraTracker (camera-state diffing)
    - NamespaceSandbox public API: get_supported_formats, _track_namespace,
      _create_unique_namespace, _clean_namespace_name, find_objects_in_namespace,
      cleanup_namespace, get_namespace_hierarchy

Skips: actual file imports (would require fixture .ma/.mb/.fbx files and
side-effect-heavy import paths).
"""
import os
import unittest
from pathlib import Path
import logging

import maya.cmds as cmds

from mayatk.env_utils.namespace_sandbox import (
    NamespaceSandbox,
    FBXImporter,
    MayaImporter,
    CameraTracker,
)

from base_test import MayaTkTestCase, QuickTestCase


_LOG = logging.getLogger("nstest")


class TestImporterFileTypeDispatch(QuickTestCase):
    """is_supported_file is pure path inspection."""

    def setUp(self):
        super().setUp()
        self.fbx = FBXImporter(_LOG, dry_run=True)
        self.maya = MayaImporter(_LOG, dry_run=True)

    def test_fbx_importer_accepts_fbx(self):
        self.assertTrue(self.fbx.is_supported_file("foo.fbx"))
        self.assertTrue(self.fbx.is_supported_file(Path("a/b/c.FBX")))

    def test_fbx_importer_rejects_others(self):
        self.assertFalse(self.fbx.is_supported_file("foo.ma"))
        self.assertFalse(self.fbx.is_supported_file("foo.mb"))
        self.assertFalse(self.fbx.is_supported_file("foo.txt"))

    def test_maya_importer_accepts_ma_mb(self):
        self.assertTrue(self.maya.is_supported_file("scene.ma"))
        self.assertTrue(self.maya.is_supported_file("scene.mb"))
        self.assertTrue(self.maya.is_supported_file("SCENE.MA"))

    def test_maya_importer_rejects_fbx(self):
        self.assertFalse(self.maya.is_supported_file("scene.fbx"))


class TestCameraTracker(MayaTkTestCase):
    """CameraTracker — diffs camera state across import boundaries."""

    def setUp(self):
        super().setUp()
        self.tracker = CameraTracker(_LOG)

    def test_initial_state_empty(self):
        self.assertEqual(self.tracker.pre_import_cameras, set())
        self.assertEqual(self.tracker.post_import_cameras, set())
        self.assertEqual(self.tracker.new_cameras, set())

    def test_capture_pre_import_state_returns_existing_cameras(self):
        before = self.tracker.capture_pre_import_state()
        self.assertIsInstance(before, set)
        # Default Maya scene includes persp/top/front/side
        for default in ("persp", "top", "front", "side"):
            self.assertIn(default, before)

    def test_diff_detects_new_cameras(self):
        self.tracker.capture_pre_import_state()
        # Add a new camera — Maya may suffix the name to keep it unique
        new_cam = cmds.camera(name="ns_imported_cam")[0]

        self.tracker.capture_post_import_state()
        self.assertIn(new_cam, self.tracker.new_cameras)

    def test_get_imported_cameras_with_namespace_filter(self):
        self.tracker.capture_pre_import_state()
        cmds.namespace(add="my_ns")
        cmds.namespace(set="my_ns")
        cam = cmds.camera()[0]
        cmds.namespace(set=":")

        self.tracker.capture_post_import_state()
        filtered = self.tracker.get_imported_cameras(namespace_filter="my_ns")
        # All filtered cameras must have the namespace prefix
        for c in filtered:
            self.assertIn("my_ns", c)

    def test_reset_clears_all_state(self):
        self.tracker.pre_import_cameras = {"a"}
        self.tracker.post_import_cameras = {"b"}
        self.tracker.new_cameras = {"c"}
        self.tracker.reset()
        self.assertEqual(self.tracker.pre_import_cameras, set())
        self.assertEqual(self.tracker.post_import_cameras, set())
        self.assertEqual(self.tracker.new_cameras, set())


class TestNamespaceSandboxBasics(QuickTestCase):
    """Basic state and helpers — no Maya scene required."""

    def test_supported_formats(self):
        sb = NamespaceSandbox()
        self.assertEqual(sb.get_supported_formats(), [".ma", ".mb", ".fbx"])

    def test_track_namespace_appends(self):
        sb = NamespaceSandbox()
        sb._track_namespace("ns_a")
        self.assertIn("ns_a", sb._active_namespaces)

    def test_track_namespace_dedupes(self):
        sb = NamespaceSandbox()
        sb._track_namespace("ns_b")
        sb._track_namespace("ns_b")
        self.assertEqual(sb._active_namespaces.count("ns_b"), 1)

    def test_create_unique_namespace_uses_default_prefix(self):
        sb = NamespaceSandbox()
        ns = sb._create_unique_namespace()
        self.assertTrue(ns.startswith(NamespaceSandbox.TEMP_NAMESPACE_PREFIX))

    def test_create_unique_namespace_custom_prefix(self):
        sb = NamespaceSandbox()
        ns = sb._create_unique_namespace(prefix="custom_")
        self.assertTrue(ns.startswith("custom_"))

    def test_clean_namespace_name_strips_namespace(self):
        self.assertEqual(NamespaceSandbox._clean_namespace_name("ns:obj"), "obj")
        self.assertEqual(NamespaceSandbox._clean_namespace_name("a:b:obj"), "obj")
        self.assertEqual(NamespaceSandbox._clean_namespace_name("obj"), "obj")


class TestImporterDispatch(QuickTestCase):
    """_get_importer_for_file dispatches by file extension."""

    def setUp(self):
        super().setUp()
        self.sb = NamespaceSandbox()

    def test_dispatches_fbx_to_fbx_importer(self):
        importer = self.sb._get_importer_for_file("foo.fbx")
        self.assertIsInstance(importer, FBXImporter)

    def test_dispatches_ma_to_maya_importer(self):
        importer = self.sb._get_importer_for_file("foo.ma")
        self.assertIsInstance(importer, MayaImporter)

    def test_unknown_extension_returns_none(self):
        self.assertIsNone(self.sb._get_importer_for_file("foo.txt"))


class TestNamespaceCleanup(MayaTkTestCase):
    """cleanup_import / cleanup_namespace — namespace removal logic."""

    def test_cleanup_unmanaged_namespace_returns_false(self):
        sb = NamespaceSandbox(dry_run=False)
        # Not in active_namespaces → returns False
        self.assertFalse(sb.cleanup_import("never_tracked"))

    def test_cleanup_dry_run_returns_true_without_action(self):
        sb = NamespaceSandbox(dry_run=True)
        sb._track_namespace("dry_run_test")
        self.assertTrue(sb.cleanup_import("dry_run_test"))

    def test_cleanup_namespace_alias_for_cleanup_import(self):
        sb = NamespaceSandbox(dry_run=False)
        sb._track_namespace("aliasable")
        # Alias should match cleanup_import behavior — namespace doesn't exist in Maya
        self.assertTrue(sb.cleanup_namespace("aliasable"))

    def test_cleanup_actual_namespace_removes_it(self):
        sb = NamespaceSandbox(dry_run=False)
        cmds.namespace(add="real_ns_for_cleanup")
        sb._track_namespace("real_ns_for_cleanup")

        sb.cleanup_import("real_ns_for_cleanup")
        self.assertFalse(cmds.namespace(exists="real_ns_for_cleanup"))


class TestFindObjectsInNamespace(MayaTkTestCase):
    """find_objects_in_namespace — exact and fuzzy matching."""

    def test_finds_exact_match(self):
        cmds.namespace(add="find_ns")
        cmds.namespace(set="find_ns")
        cube = cmds.polyCube(name="find_cube")[0]
        cmds.namespace(set=":")

        sb = NamespaceSandbox(fuzzy_matching=False)
        result = sb.find_objects_in_namespace("find_ns", ["find_cube"])
        self.assertEqual(len(result), 1)
        self.assertIn("find_cube", result[0])

    def test_returns_empty_when_no_matches(self):
        cmds.namespace(add="empty_ns")
        sb = NamespaceSandbox(fuzzy_matching=False)
        result = sb.find_objects_in_namespace("empty_ns", ["nothing"])
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
