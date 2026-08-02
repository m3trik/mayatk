# !/usr/bin/python
# coding=utf-8
"""Test Suite for env_utils workspace modules.

Covers:
    - EnvUtils workspace API (discovery over the shared pythontk.Workspace model,
      rule-fed folder resolution, create/promote, the shared template store)
    - WorkspaceManager (workspace_manager.py — properties, fallback, cache)
    - WorkspaceMap (workspace_map.py — analyze + filter + tree shaping)
"""
import os
import unittest
import tempfile
import shutil

import maya.cmds as cmds
import pythontk as ptk

from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager
from mayatk.env_utils.workspace_map import WorkspaceMap

from base_test import MayaTkTestCase, QuickTestCase


def _make_workspace_dir(parent: str, name: str, scene_rule: str = "scenes") -> str:
    """Create a fake Maya workspace directory under *parent*."""
    ws = os.path.join(parent, name)
    os.makedirs(os.path.join(ws, scene_rule), exist_ok=True)
    # workspace.mel marker file
    with open(os.path.join(ws, "workspace.mel"), "w", encoding="utf-8") as f:
        f.write(f'// fake workspace\nworkspace -fr "scene" "{scene_rule}";\n')
    return ws


def _make_scene_file(
    workspace: str, name: str = "scene1.ma", scene_rule: str = "scenes"
) -> str:
    path = os.path.join(workspace, scene_rule, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("//Maya ASCII 2025 scene\nfile -rdi 1\n")
    return path


class _SandboxedTemplates:
    """Mixin: point the shared workspace-template store at a temp dir so a test
    never reads or writes the user's live templates."""

    def _sandbox_templates(self, root):
        self._prev_presets_root = os.environ.get("UITK_PRESETS_ROOT")
        os.environ["UITK_PRESETS_ROOT"] = os.path.join(root, "presets")

    def _restore_templates(self):
        prev = getattr(self, "_prev_presets_root", None)
        if prev is None:
            os.environ.pop("UITK_PRESETS_ROOT", None)
        else:
            os.environ["UITK_PRESETS_ROOT"] = prev


class TestEnvUtilsWorkspaceDiscovery(MayaTkTestCase):
    """find_workspaces / find_workspace_using_path over the shared model."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="ws_find_")
        self.child = _make_workspace_dir(self.tmp, "child")
        _make_scene_file(self.child)
        # A project whose scene rule is NOT "scenes" — the shared format allows any
        # mapping, and blendertk promotes flat folders to scene rule ".".
        self.shots = _make_workspace_dir(self.tmp, "shots_proj", scene_rule="shots")
        _make_scene_file(self.shots, scene_rule="shots")
        # A marked project holding no scenes at all.
        self.empty = _make_workspace_dir(self.tmp, "empty")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _names(self, **kwargs):
        return sorted(
            os.path.basename(os.path.normpath(p))
            for p in EnvUtils.find_workspaces(self.tmp, **kwargs)
        )

    def test_non_recursive_includes_immediate_children(self):
        """recursive=False means "root + its children", the twin of
        btk.find_workspaces — an earlier version stopped at root_dir itself, so
        unchecking Recursive Search emptied the workspace tree."""
        self.assertEqual(
            self._names(recursive=False, ignore_empty=False),
            ["child", "empty", "shots_proj"],
        )

    def test_recursive_matches_non_recursive_for_a_flat_root(self):
        self.assertEqual(
            self._names(recursive=True, ignore_empty=False),
            self._names(recursive=False, ignore_empty=False),
        )

    def test_ignore_empty_honors_the_projects_scene_rule(self):
        """A project mapping scenes to "shots" is a real project. Scanning a
        hardcoded scenes/ folder dropped it as empty."""
        found = self._names(ignore_empty=True)
        self.assertIn("shots_proj", found)
        self.assertIn("child", found)
        self.assertNotIn("empty", found)

    def test_ignore_empty_false_keeps_sceneless_projects(self):
        self.assertIn("empty", self._names(ignore_empty=False))

    def test_return_type_pairs(self):
        pairs = EnvUtils.find_workspaces(self.tmp, return_type="dirname|dir")
        self.assertTrue(all(len(p) == 2 for p in pairs))
        self.assertIn("child", [p[0] for p in pairs])

    def test_file_types_widens_the_emptiness_scan(self):
        fbx_only = _make_workspace_dir(self.tmp, "fbx_proj")
        with open(os.path.join(fbx_only, "scenes", "a.fbx"), "w") as f:
            f.write("")
        self.assertNotIn("fbx_proj", self._names(ignore_empty=True))
        widened = sorted(
            os.path.basename(os.path.normpath(p))
            for p in EnvUtils.find_workspaces(
                self.tmp, ignore_empty=True, file_types=("*.ma", "*.mb", "*.fbx")
            )
        )
        self.assertIn("fbx_proj", widened)

    def test_find_workspace_using_path_walks_up(self):
        scene = os.path.join(self.child, "scenes", "scene1.ma")
        found = EnvUtils.find_workspace_using_path(scene)
        self.assertEqual(os.path.normcase(found), os.path.normcase(self.child))

    def test_find_workspace_using_path_outside_any_project(self):
        loose = os.path.join(self.tmp, "loose.ma")
        with open(loose, "w") as f:
            f.write("")
        self.assertIsNone(EnvUtils.find_workspace_using_path(loose))

    def test_get_workspace_scenes_missing_dir_is_empty(self):
        self.assertEqual(
            EnvUtils.get_workspace_scenes(root_dir=os.path.join(self.tmp, "nope")), []
        )

    def test_get_workspace_scenes_default_file_types_are_not_shared(self):
        """A mutable default arg would leak a caller's widened list into the next
        call; the default is the immutable class constant."""
        EnvUtils.get_workspace_scenes(
            root_dir=self.child, recursive=True, file_types=["*.fbx"]
        )
        self.assertEqual(EnvUtils.SCENE_FILE_TYPES, ("*.ma", "*.mb"))


class TestEnvUtilsWorkspaceModel(_SandboxedTemplates, MayaTkTestCase):
    """current_workspace / rule-fed dirs / create / promote / templates."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="ws_model_")
        self._sandbox_templates(self.tmp)

    def tearDown(self):
        self._restore_templates()
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_current_workspace_for_an_explicit_path(self):
        proj = _make_workspace_dir(self.tmp, "proj")
        scene = _make_scene_file(proj)
        ws = EnvUtils.current_workspace(scene)
        self.assertIsNotNone(ws)
        self.assertEqual(os.path.normcase(ws.root), os.path.normcase(proj))
        self.assertTrue(ws.is_marked)

    def test_current_workspace_for_maya_active_project(self):
        ws = EnvUtils.current_workspace()
        self.assertIsInstance(ws, ptk.Workspace)
        self.assertTrue(os.path.isdir(ws.root))

    def test_source_images_dir_follows_the_rule(self):
        proj = os.path.join(self.tmp, "textured")
        ptk.Workspace.create(proj, rules={"sourceImages": "tex"}, create_dirs=True)
        self.assertEqual(
            os.path.normcase(EnvUtils.source_images_dir(proj)),
            os.path.normcase(os.path.join(proj, "tex")),
        )

    def test_source_images_dir_falls_back_to_convention(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(os.path.join(plain, "textures"))
        self.assertEqual(
            os.path.normcase(EnvUtils.source_images_dir(plain)),
            os.path.normcase(os.path.join(plain, "textures")),
        )

    def test_scenes_dir_follows_the_rule(self):
        proj = os.path.join(self.tmp, "shots")
        ptk.Workspace.create(proj, rules={"scene": "shots"}, create_dirs=True)
        self.assertEqual(
            os.path.normcase(EnvUtils.scenes_dir(proj)),
            os.path.normcase(os.path.join(proj, "shots")),
        )

    def test_get_env_info_sourceimages_is_rule_fed(self):
        """The env key joined a hardcoded sourceimages/ folder, so a project that
        maps the rule elsewhere got the wrong directory back. Driven through
        Maya's REAL active project — comparing against source_images_dir() would
        only restate the delegation."""
        proj = os.path.join(self.tmp, "rule_fed")
        ptk.Workspace.create(proj, rules={"sourceImages": "tex"}, create_dirs=True)
        previous = cmds.workspace(q=True, rd=True)
        try:
            cmds.workspace(proj, openWorkspace=True)
            self.assertEqual(
                os.path.normcase(EnvUtils.get_env_info("sourceimages")),
                os.path.normcase(os.path.join(proj, "tex")),
            )
        finally:
            cmds.workspace(previous, openWorkspace=True)

    def test_create_workspace_writes_marker_and_folders(self):
        root = os.path.join(self.tmp, "fresh")
        ws = EnvUtils.create_workspace(root)
        self.assertTrue(ws.is_marked)
        self.assertTrue(os.path.isdir(os.path.join(root, "scenes")))
        self.assertEqual(
            ptk.Workspace.parse_workspace_mel(ws.marker_path), ptk.DEFAULT_FILE_RULES
        )

    def test_create_workspace_builds_from_the_active_template(self):
        EnvUtils.save_workspace_template("studio", {"scene": "shots"})
        ws = EnvUtils.create_workspace(os.path.join(self.tmp, "templated"))
        self.assertEqual(ws.rules, {"scene": "shots"})
        self.assertTrue(os.path.isdir(os.path.join(ws.root, "shots")))

    def test_template_store_is_shared_with_blendertk(self):
        """Same unnamespaced store both DCCs read — that is what makes a template
        saved in Blender build a Maya project."""
        EnvUtils.save_workspace_template("shared", {"scene": "s"})
        self.assertIn("shared", EnvUtils.list_workspace_templates())
        self.assertEqual(ptk.WorkspaceTemplates.rules("shared"), {"scene": "s"})
        self.assertTrue(EnvUtils.delete_workspace_template("shared"))
        self.assertEqual(EnvUtils.workspace_template_rules(), ptk.DEFAULT_FILE_RULES)

    def test_save_template_captures_the_active_project_rules(self):
        EnvUtils.save_workspace_template("from_active")
        self.assertIn("from_active", EnvUtils.list_workspace_templates())
        self.assertEqual(
            EnvUtils.workspace_template_rules("from_active"),
            EnvUtils.current_workspace().rules,
        )

    def test_create_workspace_is_idempotent(self):
        root = os.path.join(self.tmp, "twice")
        EnvUtils.create_workspace(root, rules={"scene": "mine"})
        again = EnvUtils.create_workspace(root, rules={"scene": "other"})
        self.assertEqual(again.rules["scene"], "mine")  # existing rules win

    def test_promote_describes_the_existing_layout(self):
        flat = os.path.join(self.tmp, "flat")
        os.makedirs(os.path.join(flat, "textures"))
        with open(os.path.join(flat, "a.ma"), "w") as f:
            f.write("")
        ws = EnvUtils.promote_workspace(flat)
        self.assertTrue(ws.is_marked)
        self.assertEqual(ws.rules["scene"], ".")
        self.assertEqual(ws.rules["sourceImages"], "textures")
        self.assertFalse(os.path.isdir(os.path.join(flat, "scenes")))

    def test_promote_is_non_destructive(self):
        flat = os.path.join(self.tmp, "keepme")
        os.makedirs(flat)
        ws = EnvUtils.promote_workspace(flat)
        with open(ws.marker_path, "a", encoding="utf-8") as f:
            f.write('//note\nworkspace -fr "customRule" "custom";\n')
        again = EnvUtils.promote_workspace(flat)
        self.assertEqual(again.rules.get("customRule"), "custom")
        with open(again.marker_path, encoding="utf-8") as f:
            self.assertIn("//note", f.read())

    def test_promoted_project_is_discoverable(self):
        """The round trip the shared format exists for: promote a flat folder,
        then find it with the same emptiness rule Maya's browser uses."""
        flat = os.path.join(self.tmp, "promoted")
        os.makedirs(flat)
        with open(os.path.join(flat, "a.ma"), "w") as f:
            f.write("")
        EnvUtils.promote_workspace(flat)
        found = EnvUtils.find_workspaces(self.tmp, ignore_empty=True)
        self.assertIn(
            os.path.normcase(flat),
            [os.path.normcase(os.path.normpath(p)) for p in found],
        )

    def test_set_current_workspace_rejects_a_bad_path(self):
        self.assertEqual(EnvUtils.set_current_workspace("/__nope__/"), "")

    def test_create_workspace_without_a_root(self):
        self.assertIsNone(EnvUtils.create_workspace(""))


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

    def test_analyze_reports_scene_bytes_not_the_whole_tree(self):
        """size_mb totals the SCENE files only. Walking the entire workspace tree
        (the old behavior) stalls the UI for seconds on a real project root, for a
        number a scene browser does not need."""
        bulk = os.path.join(self.ws_alpha, "sourceimages")
        os.makedirs(bulk, exist_ok=True)
        with open(os.path.join(bulk, "big.tga"), "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))  # 2 MB of non-scene payload

        wm = WorkspaceMap()
        info = wm._analyze_workspace(self.ws_alpha)
        scene_bytes = sum(os.path.getsize(s) for s in info["scenes"])
        self.assertAlmostEqual(info["size_mb"], scene_bytes / (1024 * 1024), places=6)
        self.assertLess(info["size_mb"], 1.0)

    def test_mark_root_no_ops_on_an_already_marked_root(self):
        """Discovery is marker-only, so every tree row is a project already —
        promotion targets the ROOT, and answers None when that is redundant."""
        wm = WorkspaceMap()
        wm.current_working_dir = self.ws_alpha  # a marked project
        self.assertIsNone(wm.mark_root_as_project())

    def test_mark_root_promotes_an_unmarked_root(self):
        plain = os.path.join(self.tmp, "loose_scenes")
        os.makedirs(plain, exist_ok=True)
        with open(os.path.join(plain, "a.ma"), "w") as f:
            f.write("")
        wm = WorkspaceMap()
        wm.current_working_dir = plain
        ws = wm.mark_root_as_project()
        self.assertIsNotNone(ws)
        self.assertTrue(ws.is_marked)
        self.assertEqual(ws.rules["scene"], ".")  # describes the flat layout


class _FakeItem:
    """Minimal QTreeWidgetItem stand-in (no Qt needed to pin tree shaping)."""

    def __init__(self, parent=None):
        self.texts = {}
        self.values = {}
        self.children = []
        if parent is not None:
            parent.children.append(self)

    def setText(self, column, text):
        self.texts[column] = text

    def setData(self, column, role, value):
        self.values[column] = value


class _FakeTree(_FakeItem):
    def __init__(self):
        super().__init__()
        self.clear_calls = 0

    def clear(self):
        self.clear_calls += 1
        self.children = []

    def expandAll(self):
        pass


class TestWorkspaceMapTreePopulation(MayaTkTestCase):
    """The tree widget write path — no Qt, stubbed item factory."""

    def setUp(self):
        super().setUp()
        import types

        self.tmp = tempfile.mkdtemp(prefix="ws_tree_")
        for name in ("alpha", "beta"):
            ws = _make_workspace_dir(self.tmp, name)
            _make_scene_file(ws)

        self.tree = _FakeTree()
        sb = types.SimpleNamespace(
            QtWidgets=types.SimpleNamespace(QTreeWidgetItem=_FakeItem),
            QtCore=types.SimpleNamespace(Qt=types.SimpleNamespace(UserRole=0)),
        )
        slot = types.SimpleNamespace(
            sb=sb, ui=types.SimpleNamespace(tree000=self.tree)
        )
        from mayatk.env_utils.workspace_map import WorkspaceMapController

        self.controller = WorkspaceMapController(slot)
        self.controller.current_working_dir = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _workspace_rows(self):
        return [ws for parent in self.tree.children for ws in parent.children]

    def test_repopulating_does_not_duplicate_rows(self):
        """Filtering repopulates on every keystroke; without the clear that is
        one extra copy of every row per character typed."""
        self.controller._update_workspace_tree()
        first = len(self._workspace_rows())
        self.assertGreaterEqual(first, 2)

        self.controller._update_workspace_tree()
        self.assertEqual(len(self._workspace_rows()), first)
        self.assertEqual(self.tree.clear_calls, 2)

    def test_filtered_repopulate_narrows_instead_of_appending(self):
        self.controller._update_workspace_tree()
        self.controller._update_workspace_tree(filter_text="alpha")
        names = [row.texts.get(0) for row in self._workspace_rows()]
        self.assertEqual(names, ["alpha"])


if __name__ == "__main__":
    unittest.main()
