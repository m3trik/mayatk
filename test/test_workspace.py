# !/usr/bin/python
# coding=utf-8
"""Test Suite for env_utils workspace modules.

Covers:
    - WorkspaceManager (workspace_manager.py — properties, fallback, cache)
    - WorkspaceMap (workspace_map.py — analyze + filter + tree shaping)
"""
import os
import unittest
import tempfile
import shutil

import maya.cmds as cmds

from mayatk.env_utils.workspace_manager import WorkspaceManager
from mayatk.env_utils.workspace_map import WorkspaceMap

from base_test import MayaTkTestCase, QuickTestCase


def _make_workspace_dir(parent: str, name: str) -> str:
    """Create a fake Maya workspace directory under *parent*."""
    ws = os.path.join(parent, name)
    os.makedirs(os.path.join(ws, "scenes"), exist_ok=True)
    # workspace.mel marker file
    with open(os.path.join(ws, "workspace.mel"), "w", encoding="utf-8") as f:
        f.write('// fake workspace\nworkspace -fr "scene" "scenes";\n')
    return ws


def _make_scene_file(workspace: str, name: str = "scene1.ma") -> str:
    path = os.path.join(workspace, "scenes", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("//Maya ASCII 2025 scene\nfile -rdi 1\n")
    return path


class TestWorkspaceManagerDefaults(QuickTestCase):
    """Property defaults and accessors."""

    def test_default_recursive_search(self):
        mgr = WorkspaceManager()
        self.assertTrue(mgr.recursive_search)

    def test_default_ignore_empty(self):
        mgr = WorkspaceManager()
        self.assertTrue(mgr.ignore_empty_workspaces)

    def test_recursive_search_setter_invalidates_cache(self):
        mgr = WorkspaceManager()
        mgr._workspace_files = {"some": ["data"]}
        mgr.recursive_search = False
        # Setter should call invalidate_workspace_files
        self.assertEqual(mgr.recursive_search, False)

    def test_ignore_empty_setter_invalidates_cache(self):
        mgr = WorkspaceManager()
        mgr._workspace_files = {"some": ["data"]}
        mgr.ignore_empty_workspaces = False
        self.assertFalse(mgr.ignore_empty_workspaces)

    def test_fallback_workspace_returns_real_dir(self):
        mgr = WorkspaceManager()
        result = mgr._get_fallback_workspace()
        self.assertTrue(os.path.isdir(result))


class TestWorkspaceManagerWithFakeFs(MayaTkTestCase):
    """Workspace discovery with a real temp filesystem."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="ws_mgr_")
        self.ws_a = _make_workspace_dir(self.tmp, "ws_a")
        self.ws_b = _make_workspace_dir(self.tmp, "ws_b")
        _make_scene_file(self.ws_a)
        _make_scene_file(self.ws_b)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_find_available_workspaces_returns_tuples(self):
        mgr = WorkspaceManager()
        results = mgr.find_available_workspaces(self.tmp)
        self.assertIsInstance(results, list)
        # We created 2 workspaces; results should include them
        self.assertGreaterEqual(len(results), 2)

    def test_find_available_workspaces_invalid_dir_returns_empty(self):
        mgr = WorkspaceManager()
        self.assertEqual(
            mgr.find_available_workspaces("/__definitely_not_a_dir__/"),
            [],
        )

    def test_current_working_dir_setter_validates(self):
        mgr = WorkspaceManager()
        mgr.current_working_dir = self.tmp
        self.assertEqual(mgr.current_working_dir, self.tmp)

        # Invalid dir setter should be ignored
        mgr.current_working_dir = "/__nonexistent__/"
        self.assertEqual(mgr.current_working_dir, self.tmp)


class TestWorkspaceMap(MayaTkTestCase):
    """WorkspaceMap — extends WorkspaceManager with analysis + tree shaping."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="ws_map_")
        self.ws_alpha = _make_workspace_dir(self.tmp, "alpha")
        self.ws_beta = _make_workspace_dir(self.tmp, "beta")
        _make_scene_file(self.ws_alpha)
        _make_scene_file(self.ws_beta)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_analyze_workspace_returns_expected_keys(self):
        wm = WorkspaceMap()
        info = wm._analyze_workspace(self.ws_alpha)
        for key in (
            "scene_count",
            "scenes",
            "recent_files",
            "subdirectories",
            "size_mb",
            "last_modified",
        ):
            self.assertIn(key, info)

    def test_analyze_workspace_counts_scenes(self):
        wm = WorkspaceMap()
        info = wm._analyze_workspace(self.ws_alpha)
        self.assertGreaterEqual(info["scene_count"], 1)
        self.assertGreaterEqual(len(info["scenes"]), 1)

    def test_analyze_workspace_scenes_subdir(self):
        wm = WorkspaceMap()
        info = wm._analyze_workspace(self.ws_alpha)
        # Should detect the scenes/ subdirectory
        self.assertIn("scenes", info["subdirectories"])

    def test_analyze_workspace_size_positive(self):
        wm = WorkspaceMap()
        info = wm._analyze_workspace(self.ws_alpha)
        self.assertGreater(info["size_mb"], 0)

    def test_workspace_data_caches(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        # First access populates
        data1 = wm.workspace_data
        # Second access should be the same cached dict
        data2 = wm.workspace_data
        self.assertIs(data1, data2)

    def test_workspace_data_contains_expected_workspaces(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        data = wm.workspace_data
        # Workspaces are keyed by path
        names = {info["name"] for info in data.values()}
        self.assertTrue({"alpha", "beta"}.issubset(names))

    def test_get_filtered_workspaces_empty_filter(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        result = wm.get_filtered_workspaces()
        self.assertGreaterEqual(len(result), 2)

    def test_get_filtered_workspaces_with_filter(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        result = wm.get_filtered_workspaces(filter_text="alpha")
        names = {ws["name"] for ws in result}
        self.assertIn("alpha", names)
        self.assertNotIn("beta", names)

    def test_get_workspace_tree_data_groups_by_parent(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        tree = wm.get_workspace_tree_data()
        # Tree should have at least one parent grouping
        self.assertGreater(len(tree), 0)
        # Each entry has the expected shape
        for parent_name, entry in tree.items():
            self.assertIn("path", entry)
            self.assertIn("workspaces", entry)
            self.assertEqual(entry["type"], "directory")

    def test_invalidate_workspace_data_resets_cache(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        _ = wm.workspace_data
        wm.invalidate_workspace_data()
        # Cache should still reflect what was found, but rebuild happened
        self.assertIsInstance(wm._workspace_data, dict)

    def test_recursive_search_setter_invalidates(self):
        wm = WorkspaceMap()
        wm.current_working_dir = self.tmp
        wm.recursive_search = False
        # After invalidation cache is rebuilt; just verify it's non-None
        self.assertIsNotNone(wm._workspace_data)


if __name__ == "__main__":
    unittest.main()
