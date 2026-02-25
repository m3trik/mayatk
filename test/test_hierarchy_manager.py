# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.hierarchy_manager

Tests for HierarchyManager class functionality including:
- Hierarchy analysis
- Missing/extra object detection
- Reparented object detection
- Fuzzy matching
- Optional real-world scene testing
"""
import os
import unittest
from pathlib import Path

import pymel.core as pm
import mayatk as mtk
from mayatk import HierarchyManager, NamespaceSandbox
from mayatk.env_utils.hierarchy_manager._hierarchy_manager import (
    HierarchyManager,
    get_clean_node_name,
    get_clean_node_name_from_string,
    clean_hierarchy_path,
    format_component,
    is_default_maya_camera,
    should_keep_node_by_type,
    filter_path_map_by_cameras,
    filter_path_map_by_types,
    select_objects_in_maya,
    _rename_node_removing_namespace,
    MayaObjectMatcher,
    ObjectSwapper,
    HierarchyMapBuilder,
)

from base_test import MayaTkTestCase, skipUnlessExtended


class TestHierarchyManager(MayaTkTestCase):
    """Comprehensive tests for HierarchyManager class."""

    def setUp(self):
        """Set up test environment."""
        super().setUp()
        self.test_dir = Path(__file__).parent / "temp_tests"
        self.test_dir.mkdir(exist_ok=True)

        # Real-world test scenes directory
        self.real_scenes_dir = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson\_tests\hierarchy_test"
        )

    def tearDown(self):
        """Restore test environment."""
        super().tearDown()

    # -------------------------------------------------------------------------
    # Basic Analysis Tests
    # -------------------------------------------------------------------------

    def test_analyze_hierarchies_basic(self):
        """Test basic hierarchy analysis with missing and extra objects."""
        # Create current scene objects
        root_current = pm.group(empty=True, name="root")
        child1_current = pm.group(empty=True, name="child1", parent=root_current)
        child2_current = pm.group(empty=True, name="child2", parent=root_current)

        # Create reference objects (simulating an imported reference)
        # Reference has child1, child3 (missing in current), but lacks child2 (extra in current)
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")
        root_ref = pm.group(empty=True, name="ref:root")
        child1_ref = pm.group(empty=True, name="ref:child1", parent=root_ref)
        child3_ref = pm.group(empty=True, name="ref:child3", parent=root_ref)

        reference_objects = [root_ref, child1_ref, child3_ref]

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)

        # Analyze
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=reference_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # Verify results
        self.assertIn("missing", diff_result)
        self.assertIn("extra", diff_result)

        # child3 is in reference but not in current -> missing
        self.assertIn("root|child3", diff_result["missing"])

        # child2 is in current but not in reference -> extra
        self.assertIn("root|child2", diff_result["extra"])

    def test_analyze_hierarchies_reparented(self):
        """Test detection of reparented objects.

        When a node has the same leaf name but different parent in current vs
        reference, it should appear in the ``reparented`` list with both paths,
        and be removed from ``missing``/``extra``.
        Updated: 2026-02-24 — matches new diff-categorization logic.
        """
        # Current scene: child1 is under root1
        root1_current = pm.group(empty=True, name="root1")
        root2_current = pm.group(empty=True, name="root2")
        child1_current = pm.group(empty=True, name="child1", parent=root1_current)

        # Reference scene: child1 is under root2
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")
        root1_ref = pm.group(empty=True, name="ref:root1")
        root2_ref = pm.group(empty=True, name="ref:root2")
        child1_ref = pm.group(empty=True, name="ref:child1", parent=root2_ref)

        reference_objects = [root1_ref, root2_ref, child1_ref]

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)

        # Analyze
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=reference_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # child1 should be categorized as reparented, NOT missing/extra
        self.assertNotIn(
            "root2|child1",
            diff_result["missing"],
            "Reparented item should not appear in 'missing'",
        )
        self.assertNotIn(
            "root1|child1",
            diff_result["extra"],
            "Reparented item should not appear in 'extra'",
        )

        reparented = diff_result.get("reparented", [])
        self.assertEqual(
            len(reparented),
            1,
            f"Expected exactly 1 reparented item, got {len(reparented)}",
        )
        self.assertEqual(reparented[0]["leaf"], "child1")
        self.assertEqual(reparented[0]["reference_path"], "root2|child1")
        self.assertEqual(reparented[0]["current_path"], "root1|child1")

    def test_analyze_hierarchies_fuzzy_rename(self):
        """Test that analyze_hierarchies detects renamed objects via fuzzy matching.

        When a node in the reference has a similar but not identical leaf name to
        one in the current scene (same parent), it should appear in ``fuzzy_matches``
        and be removed from ``missing``/``extra``.
        Added: 2026-02-24
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # Current scene: root > my_object_v1
        root_current = pm.group(empty=True, name="root")
        pm.group(empty=True, name="my_object_v1", parent=root_current)

        # Reference scene: root > my_object_v2  (fuzzy match for v1)
        root_ref = pm.group(empty=True, name="ref:root")
        pm.group(empty=True, name="ref:my_object_v2", parent=root_ref)

        reference_objects = [root_ref] + list(root_ref.getChildren())

        manager = HierarchyManager(fuzzy_matching=True, dry_run=True)

        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=reference_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        fuzzy = diff_result.get("fuzzy_matches", [])
        self.assertEqual(
            len(fuzzy), 1, f"Expected 1 fuzzy match, got {len(fuzzy)}: {fuzzy}"
        )
        self.assertEqual(fuzzy[0]["target_name"], "root|my_object_v2")
        self.assertEqual(fuzzy[0]["current_name"], "root|my_object_v1")
        self.assertGreater(fuzzy[0]["score"], 0.7)

        # Should NOT appear in missing/extra anymore
        self.assertNotIn("root|my_object_v2", diff_result["missing"])
        self.assertNotIn("root|my_object_v1", diff_result["extra"])

    def test_analyze_hierarchies_all_categories(self):
        """End-to-end test verifying all four diff categories are populated correctly.

        Scene layout:
        - ``shared`` — identical in both (no diff)
        - ``only_current`` — extra in current, truly missing from reference
        - ``only_ref`` — missing from current, truly only in reference
        - ``moved`` — reparented (under grp_a in current, grp_b in reference)
        - ``widget_v1`` / ``widget_v2`` — fuzzy renamed pair

        Added: 2026-02-24
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # --- Current scene ---
        grp_a = pm.group(empty=True, name="grp_a")
        grp_b = pm.group(empty=True, name="grp_b")
        pm.group(empty=True, name="shared", parent=grp_a)
        pm.group(empty=True, name="only_current", parent=grp_a)
        pm.group(empty=True, name="moved", parent=grp_a)  # reparented
        pm.group(empty=True, name="widget_v1", parent=grp_a)  # fuzzy rename

        # --- Reference scene ---
        grp_a_ref = pm.group(empty=True, name="ref:grp_a")
        grp_b_ref = pm.group(empty=True, name="ref:grp_b")
        pm.group(empty=True, name="ref:shared", parent=grp_a_ref)
        pm.group(empty=True, name="ref:only_ref", parent=grp_a_ref)
        pm.group(empty=True, name="ref:moved", parent=grp_b_ref)  # reparented
        pm.group(empty=True, name="ref:widget_v2", parent=grp_a_ref)  # fuzzy rename

        ref_objects = [grp_a_ref, grp_b_ref] + list(
            grp_a_ref.getChildren() + grp_b_ref.getChildren()
        )

        manager = HierarchyManager(fuzzy_matching=True, dry_run=True)
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # --- Verify all four keys exist ---
        for key in ("missing", "extra", "reparented", "fuzzy_matches"):
            self.assertIn(key, diff_result, f"Key '{key}' missing from diff_result")

        # Truly missing (only in reference, no match in current)
        self.assertIn("grp_a|only_ref", diff_result["missing"])

        # Truly extra (only in current, no match in reference)
        self.assertIn("grp_a|only_current", diff_result["extra"])

        # Reparented
        reparented = diff_result["reparented"]
        reparented_leaves = {r["leaf"] for r in reparented}
        self.assertIn(
            "moved",
            reparented_leaves,
            f"'moved' should be reparented. Got: {reparented}",
        )

        # Fuzzy renamed
        fuzzy = diff_result["fuzzy_matches"]
        fuzzy_pairs = {(f["target_name"], f["current_name"]) for f in fuzzy}
        self.assertIn(
            ("grp_a|widget_v2", "grp_a|widget_v1"),
            fuzzy_pairs,
            f"widget_v1↔v2 should be fuzzy matched. Got: {fuzzy}",
        )

        # Shared item should NOT appear anywhere
        for key in ("missing", "extra"):
            for path in diff_result[key]:
                self.assertNotIn(
                    "shared",
                    path,
                    f"'shared' should not be in '{key}': {diff_result[key]}",
                )

    def test_diff_result_counts(self):
        """Verify total_* count fields match actual list lengths.

        Added: 2026-02-24
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        root_cur = pm.group(empty=True, name="root")
        pm.group(empty=True, name="a", parent=root_cur)
        pm.group(empty=True, name="b", parent=root_cur)

        root_ref = pm.group(empty=True, name="ref:root")
        pm.group(empty=True, name="ref:a", parent=root_ref)
        pm.group(empty=True, name="ref:c", parent=root_ref)

        ref_objects = [root_ref] + list(root_ref.getChildren())

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        self.assertEqual(diff_result["total_missing"], len(diff_result["missing"]))
        self.assertEqual(diff_result["total_extra"], len(diff_result["extra"]))
        self.assertEqual(
            diff_result["total_reparented"], len(diff_result["reparented"])
        )
        self.assertEqual(diff_result["total_fuzzy"], len(diff_result["fuzzy_matches"]))

    def test_fuzzy_matching(self):
        """Test fuzzy matching logic."""
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # Current scene has "my_object_v1"
        root_current = pm.group(empty=True, name="root")
        obj_current = pm.group(empty=True, name="my_object_v1", parent=root_current)

        # Reference scene has "my_object_v2"
        root_ref = pm.group(empty=True, name="ref:root")
        obj_ref = pm.group(empty=True, name="ref:my_object_v2", parent=root_ref)

        reference_objects = [root_ref, obj_ref]

        # Test MayaObjectMatcher directly
        matcher = MayaObjectMatcher(import_manager=None, fuzzy_matching=True)
        found_objects, fuzzy_match_map = matcher.find_matches(
            target_objects=["my_object_v1"],
            imported_transforms=reference_objects,
            dry_run=True,
        )

        # Should find the fuzzy match
        self.assertEqual(len(found_objects), 1)
        self.assertEqual(found_objects[0], obj_ref)
        self.assertIn(obj_ref, fuzzy_match_map)
        self.assertEqual(fuzzy_match_map[obj_ref], "my_object_v1")

    def test_filtering(self):
        """Test filtering by type and name."""
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # Create objects with different types
        root_current = pm.group(empty=True, name="root")

        # Mesh
        mesh_current = pm.polyCube(name="my_mesh")[0]
        pm.parent(mesh_current, root_current)

        # Camera
        cam_current = pm.camera(name="my_camera")[0]
        pm.parent(cam_current, root_current)

        # Reference scene
        root_ref = pm.group(empty=True, name="ref:root")
        mesh_ref = pm.polyCube(name="ref:my_mesh")[0]
        pm.parent(mesh_ref, root_ref)
        cam_ref = pm.camera(name="ref:my_camera")[0]
        pm.parent(cam_ref, root_ref)

        # Add an extra object in reference to trigger diff
        extra_ref = pm.group(empty=True, name="ref:extra_obj", parent=root_ref)

        reference_objects = [root_ref, mesh_ref, cam_ref, extra_ref]

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)

        # Analyze with camera filtering ON
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=reference_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=False,
        )

        # Camera should be filtered out, so it shouldn't appear in diffs
        self.assertNotIn("root|my_camera", diff_result.get("missing", []))
        self.assertNotIn("root|my_camera", diff_result.get("extra", []))

        # Extra object should be missing in current
        self.assertIn("root|extra_obj", diff_result.get("missing", []))

    def test_path_cleaning_utilities(self):
        """Test module-level path cleaning utilities."""
        # get_clean_node_name_from_string
        self.assertEqual(get_clean_node_name_from_string("namespace:obj"), "obj")
        self.assertEqual(get_clean_node_name_from_string("root|namespace:obj"), "obj")
        self.assertEqual(get_clean_node_name_from_string("root|obj"), "obj")

        # clean_hierarchy_path
        self.assertEqual(clean_hierarchy_path("ns1:root|ns2:child"), "root|child")
        self.assertEqual(clean_hierarchy_path("root|child"), "root|child")
        self.assertEqual(clean_hierarchy_path("ns:obj"), "obj")

        # format_component
        self.assertEqual(format_component("ns:obj", strip_namespaces=True), "obj")
        self.assertEqual(format_component("ns:obj", strip_namespaces=False), "ns:obj")

    def test_filtering_utilities(self):
        """Test module-level filtering utilities."""
        # Create a camera and a mesh
        cam = pm.camera(name="persp")[0]
        mesh = pm.polyCube(name="my_mesh")[0]

        # is_default_maya_camera
        self.assertTrue(is_default_maya_camera("persp", cam))
        self.assertFalse(is_default_maya_camera("my_mesh", mesh))

        # should_keep_node_by_type
        self.assertTrue(should_keep_node_by_type(mesh, ["camera"], exclude=True))
        self.assertFalse(should_keep_node_by_type(cam, ["camera"], exclude=True))
        self.assertTrue(should_keep_node_by_type(cam, ["camera"], exclude=False))

        # filter_path_map_by_cameras
        path_map = {"persp": cam, "my_mesh": mesh}
        filtered_map = filter_path_map_by_cameras(path_map)
        self.assertIn("my_mesh", filtered_map)
        self.assertNotIn("persp", filtered_map)

        # filter_path_map_by_types
        filtered_map = filter_path_map_by_types(path_map, ["camera"], exclude=True)
        self.assertIn("my_mesh", filtered_map)
        self.assertNotIn("persp", filtered_map)

        # select_objects_in_maya
        pm.select(clear=True)
        count = select_objects_in_maya(["my_mesh"])
        self.assertEqual(count, 1)
        self.assertEqual(pm.ls(selection=True)[0], mesh)

        # _rename_node_removing_namespace
        if not pm.namespace(exists="test_ns"):
            pm.namespace(add="test_ns")
        ns_node = pm.group(empty=True, name="test_ns:my_node")
        _rename_node_removing_namespace(ns_node, allow_maya_auto_rename=True)
        self.assertEqual(ns_node.nodeName(), "my_node")

    def test_hierarchy_map_builder(self):
        """Test HierarchyMapBuilder methods."""
        root = pm.group(empty=True, name="root")
        child = pm.group(empty=True, name="child", parent=root)

        # build_path_map
        path_map = HierarchyMapBuilder.build_path_map(root)
        self.assertIn("root", path_map)
        self.assertIn("root|child", path_map)
        self.assertEqual(path_map["root"], root)
        self.assertEqual(path_map["root|child"], child)

        # build_path_map_from_nodes
        path_map_nodes = HierarchyMapBuilder.build_path_map_from_nodes([root, child])
        self.assertIn("root", path_map_nodes)
        self.assertIn("root|child", path_map_nodes)
        self.assertEqual(path_map_nodes["root"], root)
        self.assertEqual(path_map_nodes["root|child"], child)

    def test_object_swapper_methods(self):
        """Test ObjectSwapper internal methods."""
        swapper = ObjectSwapper(
            import_manager=None,
            fuzzy_matching=True,
            dry_run=True,
            pull_mode="Add to Scene",
            pull_children=False,
        )

        # Create some objects
        root = pm.group(empty=True, name="root")
        child = pm.group(empty=True, name="child", parent=root)

        # _filter_to_root_objects
        roots = swapper._filter_to_root_objects([root, child])
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0], root)

        # _expand_objects_with_children
        expanded = swapper._expand_objects_with_children([root])
        self.assertEqual(len(expanded), 2)
        self.assertIn(root, expanded)
        self.assertIn(child, expanded)

        # _collect_object_and_children
        result_list = []
        processed_set = set()
        swapper._collect_object_and_children(root, result_list, processed_set)
        self.assertEqual(len(result_list), 2)
        self.assertIn(root, result_list)
        self.assertIn(child, result_list)

        # _process_as_root_object
        # We can test this by creating a node with a namespace and seeing if it gets renamed
        if not pm.namespace(exists="test_ns"):
            pm.namespace(add="test_ns")
        ns_node = pm.group(empty=True, name="test_ns:my_node")
        swapper._process_as_root_object(ns_node, "my_node")
        self.assertEqual(ns_node.nodeName(), "my_node")

        # _process_with_hierarchy
        # Create a hierarchy with namespaces
        ns_root = pm.group(empty=True, name="test_ns:root2")
        ns_child = pm.group(empty=True, name="test_ns:child2", parent=ns_root)
        swapper._process_with_hierarchy(ns_child, "child2")
        self.assertEqual(ns_child.nodeName(), "child2")

        # _process_with_hierarchy_non_destructive
        ns_root3 = pm.group(empty=True, name="test_ns:root3")
        ns_child3 = pm.group(empty=True, name="test_ns:child3", parent=ns_root3)
        swapper._process_with_hierarchy_non_destructive(ns_child3, "child3")
        self.assertEqual(ns_child3.nodeName(), "child3")

        # _process_with_hierarchy_and_children
        ns_root4 = pm.group(empty=True, name="test_ns:root4")
        ns_child4 = pm.group(empty=True, name="test_ns:child4", parent=ns_root4)
        swapper._process_with_hierarchy_and_children(ns_root4, "root4")
        self.assertEqual(ns_root4.nodeName(), "root4")
        self.assertEqual(ns_child4.nodeName(), "child4")

        # _process_with_hierarchy_non_destructive_and_children
        ns_root5 = pm.group(empty=True, name="test_ns:root5")
        ns_child5 = pm.group(empty=True, name="test_ns:child5", parent=ns_root5)
        swapper._process_with_hierarchy_non_destructive_and_children(ns_root5, "root5")
        self.assertEqual(ns_root5.nodeName(), "root5")
        self.assertEqual(ns_child5.nodeName(), "child5")

        # _process_with_hierarchy_merge_root_only
        ns_root6 = pm.group(empty=True, name="test_ns:root6")
        ns_child6 = pm.group(empty=True, name="test_ns:child6", parent=ns_root6)
        swapper._process_with_hierarchy_merge_root_only(ns_root6, "root6")
        self.assertEqual(ns_root6.nodeName(), "root6")
        self.assertEqual(ns_child6.nodeName(), "child6")

    def test_logging_redirect_to_widget(self):
        """Verify that setup_logging_redirect actually pipes log output to a text widget.

        Bug: LoggingMixin subclasses (HierarchyManager, ObjectSwapper,
        MayaObjectMatcher) each have isolated loggers. If setup_logging_redirect
        is not called for each one, their output silently goes to the console
        instead of the UI textedit.
        Fixed: 2026-02-22
        """
        import time

        class FakeTextEdit:
            """Minimal mock that mimics QTextEdit.append()."""

            def __init__(self):
                self.messages = []

            def append(self, msg):
                self.messages.append(msg)

        # --- HierarchyManager ---
        widget_hm = FakeTextEdit()
        hm = HierarchyManager(fuzzy_matching=False, dry_run=True)
        hm.logger.setup_logging_redirect(widget_hm)
        hm.logger.info("HM_TEST_MSG")
        time.sleep(0.1)  # DefaultTextLogHandler uses threading.Timer(0, ...)
        self.assertTrue(
            any("HM_TEST_MSG" in m for m in widget_hm.messages),
            f"HierarchyManager log not redirected. Messages: {widget_hm.messages}",
        )

        # --- ObjectSwapper ---
        widget_os = FakeTextEdit()
        swapper = ObjectSwapper(dry_run=True, fuzzy_matching=False)
        swapper.logger.setup_logging_redirect(widget_os)
        swapper.logger.info("OS_TEST_MSG")
        time.sleep(0.1)
        self.assertTrue(
            any("OS_TEST_MSG" in m for m in widget_os.messages),
            f"ObjectSwapper log not redirected. Messages: {widget_os.messages}",
        )

        # --- MayaObjectMatcher ---
        widget_mm = FakeTextEdit()
        matcher = MayaObjectMatcher(import_manager=None, fuzzy_matching=False)
        matcher.logger.setup_logging_redirect(widget_mm)
        matcher.logger.info("MM_TEST_MSG")
        time.sleep(0.1)
        self.assertTrue(
            any("MM_TEST_MSG" in m for m in widget_mm.messages),
            f"MayaObjectMatcher log not redirected. Messages: {widget_mm.messages}",
        )

        # --- ObjectSwapper.import_manager (NamespaceSandbox) ---
        widget_ns = FakeTextEdit()
        swapper2 = ObjectSwapper(dry_run=True, fuzzy_matching=False)
        swapper2.import_manager.logger.setup_logging_redirect(widget_ns)
        swapper2.import_manager.logger.info("NS_TEST_MSG")
        time.sleep(0.1)
        self.assertTrue(
            any("NS_TEST_MSG" in m for m in widget_ns.messages),
            f"NamespaceSandbox log not redirected. Messages: {widget_ns.messages}",
        )

    # -------------------------------------------------------------------------
    # Real-World Scene Tests (Optional)
    # -------------------------------------------------------------------------

    @skipUnlessExtended
    def test_real_world_scenes(self):
        """Test hierarchy analysis using real-world scenes if available."""
        if not self.real_scenes_dir.exists():
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"

        if not current_scene.exists() or not reference_scene.exists():
            self.skipTest("Required real-world scene files not found.")

        # Load current scene
        pm.openFile(str(current_scene), force=True)

        # Import reference scene using NamespaceSandbox
        sandbox = NamespaceSandbox(dry_run=False)
        import_info = sandbox.import_with_namespace(
            str(reference_scene), force_complete_import=True
        )

        self.assertIsNotNone(import_info, "Failed to import reference scene")
        self.assertIn(
            "transforms", import_info, "No transforms found in reference scene"
        )

        reference_objects = import_info["transforms"]

        # Analyze
        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=True
        )
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=reference_objects,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        # Verify we got a valid result dictionary
        self.assertIsInstance(diff_result, dict)
        self.assertIn("missing", diff_result)
        self.assertIn("extra", diff_result)

        # Clean up
        sandbox.cleanup_all_namespaces()

    # -------------------------------------------------------------------------
    # Non-Destructive Scene Safety Tests
    # -------------------------------------------------------------------------

    @skipUnlessExtended
    def test_fbx_import_preserves_all_scene_objects(self):
        """Verify FBX reference import never deletes or renames original scene objects.

        Bug: _restore_renamed_objects was deleting _temp_import_conflict_ objects
        (the user's real geometry) when imported FBX objects occupied the original
        names. This caused massive geo loss in production scenes.
        Fixed: 2026-02-23
        """
        if not self.real_scenes_dir.exists():
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_fbx = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY.fbx"

        if not current_scene.exists() or not reference_fbx.exists():
            self.skipTest("Required scene files not found.")

        # --- Phase 1: Open scene and snapshot everything ---
        pm.openFile(str(current_scene), force=True)

        # Snapshot EVERY transform with its full DAG path (unique identifier)
        before_transforms = set()
        for t in pm.ls(type="transform"):
            try:
                before_transforms.add(t.longName())
            except Exception:
                pass  # Handle edge cases like deleted intermediates

        before_count = len(before_transforms)
        self.assertGreater(before_count, 0, "Scene has no transforms to protect")

        # Also snapshot assemblies (root-level objects — the ones that get renamed)
        before_assemblies = {}
        for a in pm.ls(assemblies=True, type="transform"):
            try:
                name = a.nodeName()
                if ":" not in name:  # Only root namespace objects
                    before_assemblies[a.longName()] = name
            except Exception:
                pass

        # --- Phase 2: Run the exact same import the Diff button uses ---
        sandbox = NamespaceSandbox(dry_run=False)
        import_info = sandbox.import_with_namespace(
            str(reference_fbx), force_complete_import=True
        )

        self.assertIsNotNone(import_info, "FBX import failed")
        imported = import_info.get("transforms", [])
        self.assertGreater(len(imported), 0, "FBX imported nothing")

        # --- Phase 3: Verify every original transform survived ---
        after_transforms = set()
        for t in pm.ls(type="transform"):
            try:
                after_transforms.add(t.longName())
            except Exception:
                pass

        # Find destroyed objects (were before, not after, excluding temp namespace)
        missing = set()
        for long_name in before_transforms:
            if long_name not in after_transforms:
                # Skip objects that were in a temp namespace (shouldn't happen)
                if "temp_import_" not in long_name:
                    missing.add(long_name)

        self.assertEqual(
            len(missing),
            0,
            f"DESTRUCTIVE: {len(missing)} original scene objects were destroyed! "
            f"First 20: {sorted(missing)[:20]}",
        )

        # Verify no _temp_import_conflict_ objects remain (all should be restored)
        leftover_temp = [
            t.nodeName()
            for t in pm.ls(type="transform")
            if t.nodeName().startswith("_temp_import_conflict_")
        ]
        self.assertEqual(
            len(leftover_temp),
            0,
            f"Restore incomplete: {len(leftover_temp)} objects still have temp names. "
            f"First 10: {leftover_temp[:10]}",
        )

        # Verify root assemblies were renamed back to original names
        for long_name, original_name in before_assemblies.items():
            # The long name may have changed if parent was temporarily renamed
            # So check by short name: original_name must exist somewhere
            matching = [
                t
                for t in pm.ls(type="transform")
                if t.nodeName() == original_name and ":" not in t.nodeName()
            ]
            self.assertGreater(
                len(matching),
                0,
                f"Assembly '{original_name}' was not restored after import",
            )

        # --- Phase 4: Verify total transform count hasn't decreased ---
        # After = before objects + namespaced imported objects
        # The count should be >= before_count (imported objects add to scene)
        non_ns_after = {p for p in after_transforms if "temp_import_" not in p}
        self.assertGreaterEqual(
            len(non_ns_after),
            before_count,
            f"Scene lost transforms: was {before_count}, now {len(non_ns_after)} "
            f"(excluding namespace objects)",
        )

        # --- Cleanup ---
        sandbox.cleanup_all_namespaces()

    @skipUnlessExtended
    def test_fbx_import_preserves_scene_c17(self):
        """Preservation test with C17 towing assembly — FBX vs FBX.

        Opens C17A_TOWING_ASSEMBLY_02.fbx as the current scene and imports
        C17A_TOWING_ASSEMBLY_01.fbx as the reference. This recreates the
        original bug scenario (630 objects renamed, dozens deleted).
        Fixed: 2026-02-23
        """
        if not self.real_scenes_dir.exists():
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

        current_scene = self.real_scenes_dir / "C17A_TOWING_ASSEMBLY_02.fbx"
        reference_fbx = self.real_scenes_dir / "C17A_TOWING_ASSEMBLY_01.fbx"

        if not current_scene.exists() or not reference_fbx.exists():
            self.skipTest("Required C17 FBX files not found.")

        # --- Open scene and snapshot ---
        pm.openFile(str(current_scene), force=True)

        before_long_names = set()
        for t in pm.ls(type="transform"):
            try:
                before_long_names.add(t.longName())
            except Exception:
                pass

        before_count = len(before_long_names)
        self.assertGreater(before_count, 0, "C17 scene has no transforms")

        # --- Run import ---
        sandbox = NamespaceSandbox(dry_run=False)
        import_info = sandbox.import_with_namespace(
            str(reference_fbx), force_complete_import=True
        )

        self.assertIsNotNone(import_info, "C17 FBX import failed")

        # --- Verify no original objects destroyed ---
        after_long_names = set()
        for t in pm.ls(type="transform"):
            try:
                after_long_names.add(t.longName())
            except Exception:
                pass

        missing = {
            n
            for n in before_long_names
            if n not in after_long_names and "temp_import_" not in n
        }

        self.assertEqual(
            len(missing),
            0,
            f"DESTRUCTIVE on C17: {len(missing)} objects destroyed! "
            f"First 20: {sorted(missing)[:20]}",
        )

        # Verify no leftover temp names
        leftover = [
            t.nodeName()
            for t in pm.ls(type="transform")
            if t.nodeName().startswith("_temp_import_conflict_")
        ]
        self.assertEqual(
            len(leftover),
            0,
            f"C17 restore incomplete: {len(leftover)} temp names remain. "
            f"First 10: {leftover[:10]}",
        )

        # --- Cleanup ---
        sandbox.cleanup_all_namespaces()


if __name__ == "__main__":
    unittest.main()
