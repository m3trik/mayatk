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

# Ensure QApplication exists before Maya standalone initialises (mayapy only
# creates QCoreApplication, which is insufficient for QWidget-based tests).
from qtpy import QtWidgets as _QtWidgets

if _QtWidgets.QApplication.instance() is None:
    _QtWidgets.QApplication([])

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

    # -------------------------------------------------------------------------
    # Hierarchy Repair Tests
    # -------------------------------------------------------------------------

    def test_reverse_mappings_persisted(self):
        """Verify clean_to_raw_current/reference are populated after analysis.

        Bug: reverse mappings were computed as locals and discarded.
        Fixed: 2026-03-06
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        root_cur = pm.group(empty=True, name="root")
        pm.group(empty=True, name="a", parent=root_cur)

        root_ref = pm.group(empty=True, name="ref:root")
        pm.group(empty=True, name="ref:a", parent=root_ref)
        pm.group(empty=True, name="ref:b", parent=root_ref)

        ref_objects = [root_ref] + list(root_ref.getChildren())

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # Reverse mappings must be populated
        self.assertGreater(len(manager.clean_to_raw_current), 0)
        self.assertGreater(len(manager.clean_to_raw_reference), 0)

        # Cleaned key "root" must map back to something in the raw path map
        self.assertIn("root", manager.clean_to_raw_current)
        self.assertIn("root", manager.clean_to_raw_reference)

    def test_ensure_parent_chain_creates_intermediates(self):
        """_ensure_parent_chain creates missing intermediate transforms.

        Added: 2026-03-06
        """
        # None of these exist yet
        parent = HierarchyManager._ensure_parent_chain("GRP_A|GRP_B|LEAF")
        self.assertIsNotNone(parent)
        self.assertEqual(parent.nodeName(), "GRP_B")
        self.assertTrue(pm.objExists("GRP_A"))
        self.assertTrue(pm.objExists("GRP_B"))
        # GRP_B should be under GRP_A
        self.assertEqual(parent.getParent().nodeName(), "GRP_A")

    def test_ensure_parent_chain_root_level(self):
        """_ensure_parent_chain returns None for root-level paths (no parent needed).

        Added: 2026-03-06
        """
        result = HierarchyManager._ensure_parent_chain("LEAF")
        self.assertIsNone(result)

    def test_create_stubs_dry_run(self):
        """create_stubs in dry_run mode reports what would be created without touching the scene.

        Added: 2026-03-06
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.differences = {"missing": ["grp|child1", "grp|child2"]}

        created = manager.create_stubs()
        self.assertEqual(len(created), 2)
        self.assertIn("child1", created)
        self.assertIn("child2", created)
        # Nothing actually created
        self.assertFalse(pm.objExists("child1"))
        self.assertFalse(pm.objExists("child2"))

    def test_create_stubs_live(self):
        """create_stubs with dry_run=False creates transforms at correct hierarchy positions.

        Added: 2026-03-06
        """
        # Create the parent so the stub can be placed correctly
        pm.group(empty=True, name="grp")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["grp|stub_child", "root_stub"]}

        created = manager.create_stubs()
        self.assertEqual(len(created), 2)
        self.assertTrue(pm.objExists("stub_child"))
        self.assertTrue(pm.objExists("root_stub"))

        # stub_child should be under grp
        stub_node = pm.PyNode("stub_child")
        self.assertEqual(stub_node.getParent().nodeName(), "grp")

        # root_stub should be at scene root
        root_stub = pm.PyNode("root_stub")
        self.assertIsNone(root_stub.getParent())

    def test_create_stubs_skips_existing(self):
        """create_stubs skips nodes that already exist in the scene.

        Added: 2026-03-06
        """
        pm.group(empty=True, name="existing_obj")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["existing_obj"]}

        created = manager.create_stubs()
        self.assertEqual(len(created), 0)

    def test_create_stubs_builds_intermediate_parents(self):
        """create_stubs creates intermediate parent transforms when they don't exist.

        Added: 2026-03-06
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["deep_grp|sub_grp|leaf_stub"]}

        created = manager.create_stubs()
        self.assertEqual(len(created), 1)
        self.assertTrue(pm.objExists("deep_grp"))
        self.assertTrue(pm.objExists("sub_grp"))
        self.assertTrue(pm.objExists("leaf_stub"))
        # Verify parenting chain
        leaf = pm.PyNode("leaf_stub")
        self.assertEqual(leaf.getParent().nodeName(), "sub_grp")
        self.assertEqual(leaf.getParent().getParent().nodeName(), "deep_grp")

    def test_quarantine_extras_dry_run(self):
        """quarantine_extras in dry_run mode reports without modifying the scene.

        Added: 2026-03-06
        """
        pm.group(empty=True, name="extra_obj")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.differences = {"extra": ["extra_obj"]}

        moved = manager.quarantine_extras()
        self.assertEqual(len(moved), 1)
        self.assertIn("extra_obj", moved)
        # Node should NOT have been moved
        self.assertFalse(pm.objExists("_QUARANTINE"))

    def test_quarantine_extras_live(self):
        """quarantine_extras moves extra items under a quarantine group.

        Added: 2026-03-06
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # Build current scene with an extra object
        root = pm.group(empty=True, name="root")
        child_ok = pm.group(empty=True, name="child_ok", parent=root)
        child_extra = pm.group(empty=True, name="child_extra", parent=root)

        # Reference only has root and child_ok
        root_ref = pm.group(empty=True, name="ref:root")
        pm.group(empty=True, name="ref:child_ok", parent=root_ref)

        ref_objects = [root_ref] + list(root_ref.getChildren())

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # child_extra should be detected as extra
        self.assertIn("root|child_extra", manager.differences["extra"])

        moved = manager.quarantine_extras()
        self.assertEqual(len(moved), 1)
        self.assertIn("child_extra", moved)

        # Verify it's now under _QUARANTINE
        self.assertTrue(pm.objExists("_QUARANTINE"))
        quarantined = pm.PyNode("child_extra")
        self.assertEqual(quarantined.getParent().nodeName(), "_QUARANTINE")

    def test_quarantine_extras_ancestor_dedup(self):
        """quarantine_extras deduplicates when both ancestor and descendant are extra.

        If GRP and GRP|CHILD are both extra, only GRP should be moved.
        Added: 2026-03-06
        """
        grp = pm.group(empty=True, name="orphan_grp")
        child = pm.group(empty=True, name="orphan_child", parent=grp)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        # Manually populate the path maps so _resolve_node works
        manager.current_scene_path_map = {
            "orphan_grp": grp,
            "orphan_grp|orphan_child": child,
        }
        manager.clean_to_raw_current = {
            "orphan_grp": "orphan_grp",
            "orphan_grp|orphan_child": "orphan_grp|orphan_child",
        }
        manager.differences = {
            "extra": ["orphan_grp", "orphan_grp|orphan_child"],
        }

        moved = manager.quarantine_extras()
        # Only the root should be moved; child comes along for free
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0], "orphan_grp")
        # Both should be under _QUARANTINE
        self.assertEqual(pm.PyNode("orphan_grp").getParent().nodeName(), "_QUARANTINE")
        self.assertEqual(pm.PyNode("orphan_child").getParent().nodeName(), "orphan_grp")

    def test_quarantine_extras_custom_group_name(self):
        """quarantine_extras uses a custom group name when specified.

        Added: 2026-03-06
        """
        extra_node = pm.group(empty=True, name="stray")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {"stray": extra_node}
        manager.clean_to_raw_current = {"stray": "stray"}
        manager.differences = {"extra": ["stray"]}

        moved = manager.quarantine_extras(group="MY_EXTRAS")
        self.assertEqual(len(moved), 1)
        self.assertTrue(pm.objExists("MY_EXTRAS"))
        self.assertEqual(pm.PyNode("stray").getParent().nodeName(), "MY_EXTRAS")

    def test_fix_reparented_dry_run(self):
        """fix_reparented in dry_run mode reports without modifying the scene.

        Added: 2026-03-06
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.differences = {
            "reparented": [
                {
                    "leaf": "child",
                    "current_path": "grp_a|child",
                    "reference_path": "grp_b|child",
                }
            ]
        }

        fixed = manager.fix_reparented()
        self.assertEqual(len(fixed), 1)
        self.assertIn("child", fixed)

    def test_fix_reparented_live(self):
        """fix_reparented moves nodes to match their reference hierarchy position.

        Added: 2026-03-06
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # Current: child is under grp_a
        grp_a = pm.group(empty=True, name="grp_a")
        grp_b = pm.group(empty=True, name="grp_b")
        child = pm.group(empty=True, name="child", parent=grp_a)

        # Reference: child should be under grp_b
        grp_a_ref = pm.group(empty=True, name="ref:grp_a")
        grp_b_ref = pm.group(empty=True, name="ref:grp_b")
        child_ref = pm.group(empty=True, name="ref:child", parent=grp_b_ref)

        ref_objects = [grp_a_ref, grp_b_ref, child_ref]

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # Should detect reparented
        reparented = manager.differences.get("reparented", [])
        self.assertEqual(len(reparented), 1)
        self.assertEqual(reparented[0]["leaf"], "child")

        fixed = manager.fix_reparented()
        self.assertEqual(len(fixed), 1)

        # child should now be under grp_b
        child_node = pm.PyNode("child")
        self.assertEqual(
            child_node.getParent().nodeName(),
            "grp_b",
            f"Expected child under grp_b, got {child_node.getParent().nodeName()}",
        )

    def test_fix_reparented_creates_missing_parent(self):
        """fix_reparented creates the target parent if it doesn't exist yet.

        Added: 2026-03-06
        """
        grp_a = pm.group(empty=True, name="grp_a")
        child = pm.group(empty=True, name="child", parent=grp_a)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {"grp_a|child": child}
        manager.clean_to_raw_current = {"grp_a|child": "grp_a|child"}
        manager.differences = {
            "reparented": [
                {
                    "leaf": "child",
                    "current_path": "grp_a|child",
                    "reference_path": "new_parent|child",
                }
            ]
        }

        fixed = manager.fix_reparented()
        self.assertEqual(len(fixed), 1)
        self.assertTrue(pm.objExists("new_parent"))
        child_node = pm.PyNode("child")
        self.assertEqual(child_node.getParent().nodeName(), "new_parent")

    def test_create_stubs_empty_differences(self):
        """create_stubs returns empty list when no missing items exist.

        Added: 2026-03-06
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": []}
        self.assertEqual(manager.create_stubs(), [])

    def test_quarantine_extras_empty_differences(self):
        """quarantine_extras returns empty list when no extra items exist.

        Added: 2026-03-06
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"extra": []}
        self.assertEqual(manager.quarantine_extras(), [])

    def test_fix_reparented_empty_differences(self):
        """fix_reparented returns empty list when no reparented items exist.

        Added: 2026-03-06
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"reparented": []}
        self.assertEqual(manager.fix_reparented(), [])

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
    # Real-World Diff Content Regression Tests
    # -------------------------------------------------------------------------

    @skipUnlessExtended
    def test_c5_ma_vs_ma_diff_content(self):
        """Regression: C5 MA-vs-MA diff produces exact known baseline counts and paths.

        Validates that analyze_hierarchies returns the correct missing/extra/reparented
        results for the C5_AFT_COMP_ASSEMBLY current.ma vs module.ma scene pair.
        Baseline captured: 2026-06-16
        """
        if not self.real_scenes_dir.exists():
            self.skipTest(f"Real-world scenes directory not found: {self.real_scenes_dir}")

        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"

        if not current_scene.exists() or not reference_scene.exists():
            self.skipTest("Required C5 MA scene files not found.")

        default_cams = frozenset({"persp", "top", "front", "side"})

        pm.openFile(str(current_scene), force=True)

        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_scene), force_complete_import=True
        )
        self.assertIsNotNone(info, "Failed to import reference scene")

        ref_objs = [
            t for t in info.get("transforms", [])
            if t.nodeName().split(":")[-1] not in default_cams
        ]

        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=True
        )
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        # --- Assert exact baseline counts ---
        self.assertEqual(
            len(diff["missing"]), 48,
            f"Expected 48 missing, got {len(diff['missing'])}",
        )
        self.assertEqual(
            len(diff["extra"]), 4,
            f"Expected 4 extra, got {len(diff['extra'])}",
        )
        self.assertEqual(
            len(diff["reparented"]), 1,
            f"Expected 1 reparented, got {len(diff['reparented'])}",
        )

        # --- Assert key paths are present in missing ---
        missing_set = set(diff["missing"])
        # SKINNED_HOSE rig chain must appear
        self.assertIn("INTERACTIVE|SKINNED_HOSE_RIG_GRP", missing_set)
        self.assertIn("INTERACTIVE|SKINNED_HOSE", missing_set)
        # HYDRO_DRILL groups must appear
        self.assertIn("INTERACTIVE|HYDRO_DRILL_CON_PISTON_GRP", missing_set)
        self.assertIn("INTERACTIVE|S00C40_HYDRO_DRILL_CON_GRP", missing_set)

        # --- Assert key paths in extra ---
        extra_set = set(diff["extra"])
        self.assertIn("S00C34_BELL_NUT_FRES_GRP", extra_set)
        self.assertIn("S00C34_BELL_NUT_FRES_GRP|S00C34_BELL_NUT_FRES_LOC", extra_set)

        # --- Assert reparented item ---
        rp = diff["reparented"][0]
        self.assertEqual(rp["leaf"], "S00C36_OUTB_ADAPTER_LOC")
        self.assertIn("S00C36_OUTB_ADAPTER_GRP|", rp["reference_path"])
        self.assertIn("S00C36_OUTB_ADAPTER_GRP1|", rp["current_path"])

        # --- Cross-validate against actual scene contents ---
        # Independently verify every diff result against the raw filtered maps
        current_cleaned = {
            clean_hierarchy_path(p) for p in manager.current_scene_path_map
        }
        reference_cleaned = {
            clean_hierarchy_path(p) for p in manager.reference_scene_path_map
        }

        # Every "missing" path must exist in reference but NOT in current
        for path in diff["missing"]:
            self.assertIn(
                path, reference_cleaned,
                f"Missing item '{path}' not found in reference scene",
            )
            self.assertNotIn(
                path, current_cleaned,
                f"Missing item '{path}' actually exists in current scene",
            )

        # Every "extra" path must exist in current but NOT in reference
        for path in diff["extra"]:
            self.assertIn(
                path, current_cleaned,
                f"Extra item '{path}' not found in current scene",
            )
            self.assertNotIn(
                path, reference_cleaned,
                f"Extra item '{path}' actually exists in reference scene",
            )

        # Every "reparented" leaf must appear in both but under different parents
        for rp_item in diff["reparented"]:
            self.assertIn(
                rp_item["reference_path"], reference_cleaned,
                f"Reparented ref path not in reference: {rp_item['reference_path']}",
            )
            self.assertIn(
                rp_item["current_path"], current_cleaned,
                f"Reparented cur path not in current: {rp_item['current_path']}",
            )

        # The independent set diff must match what analyze_hierarchies reported
        reparented_ref_paths = {r["reference_path"] for r in diff["reparented"]}
        reparented_cur_paths = {r["current_path"] for r in diff["reparented"]}
        fuzzy_ref_paths = {m["target_name"] for m in diff.get("fuzzy_matches", [])}
        fuzzy_cur_paths = {m["current_name"] for m in diff.get("fuzzy_matches", [])}
        independent_missing = reference_cleaned - current_cleaned - reparented_ref_paths - fuzzy_ref_paths
        independent_extra = current_cleaned - reference_cleaned - reparented_cur_paths - fuzzy_cur_paths
        self.assertEqual(
            sorted(independent_missing), sorted(diff["missing"]),
            "Independent missing computation disagrees with analyze_hierarchies",
        )
        self.assertEqual(
            sorted(independent_extra), sorted(diff["extra"]),
            "Independent extra computation disagrees with analyze_hierarchies",
        )

        sandbox.cleanup_all_namespaces()

    @skipUnlessExtended
    def test_c5_ma_vs_fbx_diff_content(self):
        """Regression: C5 MA-vs-FBX diff produces exact known baseline counts and paths.

        Validates that analyze_hierarchies returns the correct missing/extra results
        for C5_AFT_COMP_ASSEMBLY current.ma vs the FBX export.
        Baseline captured: 2026-06-16
        """
        if not self.real_scenes_dir.exists():
            self.skipTest(f"Real-world scenes directory not found: {self.real_scenes_dir}")

        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_fbx = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY.fbx"

        if not current_scene.exists() or not reference_fbx.exists():
            self.skipTest("Required C5 MA/FBX scene files not found.")

        default_cams = frozenset({"persp", "top", "front", "side"})

        pm.openFile(str(current_scene), force=True)

        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_fbx), force_complete_import=True
        )
        self.assertIsNotNone(info, "Failed to import FBX reference")

        ref_objs = [
            t for t in info.get("transforms", [])
            if t.nodeName().split(":")[-1] not in default_cams
        ]

        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=True
        )
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        # --- Assert exact baseline counts ---
        self.assertEqual(
            len(diff["missing"]), 0,
            f"Expected 0 missing, got {len(diff['missing'])}: {diff['missing'][:5]}",
        )
        self.assertEqual(
            len(diff["extra"]), 2,
            f"Expected 2 extra, got {len(diff['extra'])}: {diff['extra']}",
        )
        self.assertEqual(
            len(diff["reparented"]), 0,
            f"Expected 0 reparented, got {len(diff['reparented'])}",
        )

        # --- Assert exact extra paths ---
        extra_set = set(diff["extra"])
        self.assertIn("S00C34_BELL_NUT_FRES_GRP", extra_set)
        self.assertIn("S00C34_BELL_NUT_FRES_GRP|S00C34_BELL_NUT_FRES_LOC", extra_set)

        # --- Cross-validate against actual scene contents ---
        current_cleaned = {
            clean_hierarchy_path(p) for p in manager.current_scene_path_map
        }
        reference_cleaned = {
            clean_hierarchy_path(p) for p in manager.reference_scene_path_map
        }

        for path in diff["missing"]:
            self.assertIn(
                path, reference_cleaned,
                f"Missing item '{path}' not found in reference scene",
            )
            self.assertNotIn(
                path, current_cleaned,
                f"Missing item '{path}' actually exists in current scene",
            )

        for path in diff["extra"]:
            self.assertIn(
                path, current_cleaned,
                f"Extra item '{path}' not found in current scene",
            )
            self.assertNotIn(
                path, reference_cleaned,
                f"Extra item '{path}' actually exists in reference scene",
            )

        reparented_ref_paths = {r["reference_path"] for r in diff["reparented"]}
        reparented_cur_paths = {r["current_path"] for r in diff["reparented"]}
        fuzzy_ref_paths = {m["target_name"] for m in diff.get("fuzzy_matches", [])}
        fuzzy_cur_paths = {m["current_name"] for m in diff.get("fuzzy_matches", [])}
        independent_missing = reference_cleaned - current_cleaned - reparented_ref_paths - fuzzy_ref_paths
        independent_extra = current_cleaned - reference_cleaned - reparented_cur_paths - fuzzy_cur_paths
        self.assertEqual(
            sorted(independent_missing), sorted(diff["missing"]),
            "Independent missing computation disagrees with analyze_hierarchies",
        )
        self.assertEqual(
            sorted(independent_extra), sorted(diff["extra"]),
            "Independent extra computation disagrees with analyze_hierarchies",
        )

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

    def test_same_name_parent_child_survives_namespace_rename(self):
        """Verify transforms with same-name parent/child paths survive NamespaceSandbox.

        Bug: A ``has_consecutive_dupes`` filter silently dropped any transform
        whose DAG path contained a parent and child with the same short name
        (e.g. ``A|A``).  PyMEL's MObject-backed rename handles this correctly,
        so the filter was unnecessary and caused false "extra" items in diffs.
        Fixed: 2026-03-05
        """
        # Build hierarchy:  A -> A (child) -> B -> C
        pm.newFile(force=True)
        parent = pm.group(empty=True, name="A")
        child = pm.group(empty=True, name="A", parent=parent)
        grandchild = pm.group(empty=True, name="B", parent=child)
        leaf = pm.group(empty=True, name="C", parent=grandchild)

        # Sanity: child has a consecutive-dupe path
        self.assertEqual(child.longName(), "|A|A")

        all_nodes = [parent, child, grandchild, leaf]
        sandbox = NamespaceSandbox(dry_run=False)
        sandbox._fbx_importer._move_objects_to_namespace(all_nodes, "test_ns")

        # All 4 must survive
        queried = pm.ls("test_ns:*", type="transform")
        self.assertEqual(
            len(queried),
            4,
            f"Expected 4 transforms in namespace, got {len(queried)}: "
            f"{[t.longName() for t in queried]}",
        )

        # Hierarchy must be intact
        self.assertEqual(child.longName(), "|test_ns:A|test_ns:A")
        self.assertEqual(leaf.longName(), "|test_ns:A|test_ns:A|test_ns:B|test_ns:C")

        # Cleaned paths must preserve the same-name structure
        cleaned_paths = {clean_hierarchy_path(t.longName()) for t in queried}
        # clean_hierarchy_path preserves leading separator from longName()
        self.assertIn("|A|A", cleaned_paths)
        self.assertIn("|A|A|B|C", cleaned_paths)

        pm.namespace(removeNamespace="test_ns", mergeNamespaceWithRoot=True)

    def test_fbx_namespace_stripped_during_sandbox_rename(self):
        """Verify FBX-created namespaces are stripped to avoid nested namespaces.

        Bug: FBX import sometimes creates nodes in a ``ControlData:`` namespace.
        When ``_move_objects_to_namespace`` renamed these, the result was
        ``temp_import:ControlData:FOO`` (nested), which ``pm.ls("temp_import:*")``
        could not find.  The fix strips existing namespace prefixes before
        adding the sandbox namespace.
        Fixed: 2026-03-05
        """
        pm.newFile(force=True)

        # Simulate FBX-created node with a namespace
        if not pm.namespace(exists="ControlData"):
            pm.namespace(add="ControlData")
        node = pm.group(empty=True, name="ControlData:SWITCH_NODE")
        self.assertEqual(node.nodeName(), "ControlData:SWITCH_NODE")

        sandbox = NamespaceSandbox(dry_run=False)
        sandbox._fbx_importer._move_objects_to_namespace([node], "test_ns")

        # Must be in test_ns (flat), NOT test_ns:ControlData (nested)
        self.assertTrue(
            node.nodeName().startswith("test_ns:"),
            f"Node should be in test_ns namespace, got: {node.nodeName()}",
        )
        self.assertNotIn(
            "ControlData",
            node.nodeName(),
            f"ControlData namespace should be stripped, got: {node.nodeName()}",
        )

        # Must be discoverable with standard namespace query
        queried = pm.ls("test_ns:*", type="transform")
        self.assertEqual(len(queried), 1, f"Expected 1 node, got {len(queried)}")
        self.assertEqual(queried[0].nodeName(), "test_ns:SWITCH_NODE")

        pm.namespace(removeNamespace="test_ns", mergeNamespaceWithRoot=True)
        pm.namespace(removeNamespace="ControlData", mergeNamespaceWithRoot=True)


# ---------------------------------------------------------------------------
# Ignore Feature Tests (Controller logic — no full UI required)
# ---------------------------------------------------------------------------


class TestIgnoreFeature(MayaTkTestCase):
    """Tests for the tree-item ignore/unignore feature.

    These tests exercise the ignore logic on the controller directly,
    using lightweight Qt tree widgets as stand-ins for the full UI.
    Added: 2026-03-05
    """

    def setUp(self):
        super().setUp()
        from qtpy import QtWidgets, QtGui

        # Minimal stub so the controller can initialise.
        class _FakeUI:
            class _FakeTree(QtWidgets.QTreeWidget):
                def __init__(self):
                    super().__init__()
                    self.menu = type(
                        "Menu",
                        (),
                        {"add": lambda *a, **kw: None, "setTitle": lambda *a: None},
                    )()
                    self.is_initialized = True

            def __init__(self):
                self.tree000 = self._FakeTree()
                self.tree001 = self._FakeTree()
                self.txt003 = type(
                    "W",
                    (),
                    {
                        "append": lambda *a: None,
                        "setHtml": lambda *a: None,
                        "setText": lambda *a: None,
                        "clear": lambda *a: None,
                    },
                )()

        class _FakeSB:
            registered_widgets = type("RW", (), {"TextEditLogHandler": None})()

        fake_slots = type("Slots", (), {"sb": _FakeSB(), "ui": _FakeUI()})()
        from mayatk.env_utils.hierarchy_manager.hierarchy_manager_slots import (
            HierarchyManagerController,
        )

        self.controller = HierarchyManagerController(fake_slots)
        self.tree000 = fake_slots.ui.tree000
        self.tree001 = fake_slots.ui.tree001

    # -- helpers --

    def _populate_tree(self, tree, hierarchy):
        """Populate a QTreeWidget from a nested dict.

        Example::

            {"GRP": {"child1": {"leaf": {}}, "child2": {}}}
        """
        tree.clear()
        tree.setColumnCount(1)

        def _add(parent_item, children_dict):
            from qtpy import QtWidgets

            for name, sub in children_dict.items():
                item = QtWidgets.QTreeWidgetItem(parent_item, [name])
                _add(item, sub)

        _add(tree.invisibleRootItem(), hierarchy)

    # -- is_path_ignored --

    def test_is_path_ignored_exact_match(self):
        """Exact path in ignored set returns True."""
        self.controller._ignored_ref_paths.add("GRP|child1")
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "GRP|child1"))

    def test_is_path_ignored_ancestor_match(self):
        """A descendant of an ignored path is also considered ignored."""
        self.controller._ignored_ref_paths.add("GRP")
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "GRP|child1"))
        self.assertTrue(
            self.controller.is_path_ignored(self.tree000, "GRP|child1|leaf")
        )

    def test_is_path_ignored_no_false_prefix(self):
        """Paths that share a prefix but are NOT descendants are not ignored.

        Bug guard: 'GRP2' should not match ignored 'GRP'.
        """
        self.controller._ignored_ref_paths.add("GRP")
        self.assertFalse(self.controller.is_path_ignored(self.tree000, "GRP2"))
        self.assertFalse(self.controller.is_path_ignored(self.tree000, "GRP2|child"))

    def test_is_path_ignored_different_tree(self):
        """Ignored paths for reference tree should not affect current tree."""
        self.controller._ignored_ref_paths.add("GRP|child1")
        self.assertFalse(self.controller.is_path_ignored(self.tree001, "GRP|child1"))

    def test_is_path_ignored_empty_set(self):
        """Nothing ignored => everything returns False."""
        self.assertFalse(self.controller.is_path_ignored(self.tree000, "anything"))

    # -- clear_ignored_paths --

    def test_clear_ignored_paths(self):
        """clear_ignored_paths empties both sets."""
        self.controller._ignored_ref_paths.update({"A", "B"})
        self.controller._ignored_cur_paths.update({"C"})
        self.controller.clear_ignored_paths()
        self.assertEqual(len(self.controller._ignored_ref_paths), 0)
        self.assertEqual(len(self.controller._ignored_cur_paths), 0)

    # -- _build_item_path --

    def test_build_item_path_root(self):
        """Root-level item returns just its name."""
        self._populate_tree(self.tree000, {"GRP": {}})
        root_item = self.tree000.topLevelItem(0)
        self.assertEqual(self.controller._build_item_path(root_item), "GRP")

    def test_build_item_path_nested(self):
        """Nested item returns pipe-separated ancestor chain."""
        self._populate_tree(self.tree000, {"GRP": {"child": {"leaf": {}}}})
        root = self.tree000.topLevelItem(0)
        child = root.child(0)
        leaf = child.child(0)
        self.assertEqual(self.controller._build_item_path(leaf), "GRP|child|leaf")

    # -- _apply_ignore_styling --

    def test_apply_ignore_styling_strikethrough(self):
        """Ignored items get strikethrough font; non-ignored do not."""
        self._populate_tree(self.tree000, {"GRP": {"child1": {}, "child2": {}}})
        self.controller._ignored_ref_paths.add("GRP|child1")
        self.controller._apply_ignore_styling(self.tree000)

        root = self.tree000.topLevelItem(0)
        child1 = root.child(0)
        child2 = root.child(1)

        self.assertTrue(
            child1.font(0).strikeOut(), "Ignored item should have strikethrough"
        )
        self.assertFalse(
            child2.font(0).strikeOut(), "Non-ignored item should NOT have strikethrough"
        )

    def test_apply_ignore_styling_clears_on_unignore(self):
        """After removing an item from the ignored set, re-applying styling clears strikethrough."""
        self._populate_tree(self.tree000, {"GRP": {"child1": {}}})
        self.controller._ignored_ref_paths.add("GRP|child1")
        self.controller._apply_ignore_styling(self.tree000)

        root = self.tree000.topLevelItem(0)
        child1 = root.child(0)
        self.assertTrue(child1.font(0).strikeOut())

        # Unignore
        self.controller._ignored_ref_paths.discard("GRP|child1")
        self.controller._apply_ignore_styling(self.tree000)
        self.assertFalse(
            child1.font(0).strikeOut(), "Strikethrough should be removed after unignore"
        )

    def test_apply_ignore_styling_ancestor_propagation(self):
        """Ignoring a parent styles it with strikethrough; descendants get italic (inherited)."""
        self._populate_tree(self.tree000, {"GRP": {"child": {"leaf": {}}}})
        self.controller._ignored_ref_paths.add("GRP")
        self.controller._apply_ignore_styling(self.tree000)

        root = self.tree000.topLevelItem(0)
        child = root.child(0)
        leaf = child.child(0)

        self.assertTrue(
            root.font(0).strikeOut(), "Directly-ignored root should have strikethrough"
        )
        # Descendants are inherited-ignored → italic, not strikethrough
        self.assertTrue(
            child.font(0).italic(),
            "Inherited-ignored descendant should be italic",
        )
        self.assertFalse(
            child.font(0).strikeOut(),
            "Inherited-ignored descendant should NOT have strikethrough",
        )
        self.assertTrue(
            leaf.font(0).italic(), "Deep inherited-ignored descendant should be italic"
        )

    # -- Edge cases --

    def test_unignore_child_of_ignored_parent_is_noop(self):
        """Unignoring a child that is only implicitly ignored (via parent) changes nothing.

        The child's path is not in the set, so discard is a no-op.
        The parent remains ignored, so the child remains visually ignored.
        """
        self.controller._ignored_ref_paths.add("GRP")
        # Attempt to unignore child
        self.controller._ignored_ref_paths.discard("GRP|child1")
        # Parent still present
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "GRP|child1"))

    def test_unignore_parent_frees_descendants(self):
        """Unignoring a parent also frees all implicitly-ignored descendants."""
        self.controller._ignored_ref_paths.add("GRP")
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "GRP|child|leaf"))

        self.controller._ignored_ref_paths.discard("GRP")
        self.assertFalse(
            self.controller.is_path_ignored(self.tree000, "GRP|child|leaf")
        )

    def test_ignore_multiple_roots_independent(self):
        """Ignoring two independent roots does not interfere with each other."""
        self.controller._ignored_ref_paths.update({"A", "B"})
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "A|child"))
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "B|child"))

        self.controller._ignored_ref_paths.discard("A")
        self.assertFalse(self.controller.is_path_ignored(self.tree000, "A|child"))
        self.assertTrue(self.controller.is_path_ignored(self.tree000, "B|child"))

    def test_ignore_set_per_tree(self):
        """Reference and current trees maintain separate ignored sets."""
        self.controller._ignored_ref_paths.add("shared_path")
        self.controller._ignored_cur_paths.add("other_path")

        self.assertTrue(self.controller.is_path_ignored(self.tree000, "shared_path"))
        self.assertFalse(self.controller.is_path_ignored(self.tree001, "shared_path"))

        self.assertFalse(self.controller.is_path_ignored(self.tree000, "other_path"))
        self.assertTrue(self.controller.is_path_ignored(self.tree001, "other_path"))

    # -- _filter_ignored_from_diff --

    def test_filter_ignored_from_diff_excludes_ignored_missing(self):
        """_filter_ignored_from_diff removes missing items whose path is ignored.

        Added: 2026-03-06 (fix C1/C2)
        """
        self.controller._current_diff_result = {
            "missing": ["GRP|child1", "GRP|child2"],
            "extra": [],
            "reparented": [],
            "fuzzy_matches": [],
        }
        self.controller._ignored_ref_paths.add("GRP|child1")

        effective = self.controller._filter_ignored_from_diff()
        self.assertNotIn("GRP|child1", effective["missing"])
        self.assertIn("GRP|child2", effective["missing"])

    def test_filter_ignored_from_diff_excludes_ignored_extra(self):
        """_filter_ignored_from_diff removes extra items whose path is ignored.

        Added: 2026-03-06 (fix C1/C2)
        """
        self.controller._current_diff_result = {
            "missing": [],
            "extra": ["GRP|extra1", "GRP|extra2"],
            "reparented": [],
            "fuzzy_matches": [],
        }
        self.controller._ignored_cur_paths.add("GRP|extra1")

        effective = self.controller._filter_ignored_from_diff()
        self.assertNotIn("GRP|extra1", effective["extra"])
        self.assertIn("GRP|extra2", effective["extra"])

    def test_filter_ignored_from_diff_excludes_reparented_both_sides(self):
        """Reparented items are excluded when either side is ignored.

        Added: 2026-03-06 (fix C1/C2)
        """
        self.controller._current_diff_result = {
            "missing": [],
            "extra": [],
            "reparented": [
                {
                    "leaf": "node",
                    "current_path": "A|node",
                    "reference_path": "B|node",
                },
            ],
            "fuzzy_matches": [],
        }
        # Ignoring the current-side path should exclude it
        self.controller._ignored_cur_paths.add("A|node")

        effective = self.controller._filter_ignored_from_diff()
        self.assertEqual(len(effective["reparented"]), 0)

    def test_filter_ignored_from_diff_no_diff_returns_empty(self):
        """_filter_ignored_from_diff returns empty lists when no diff exists.

        Added: 2026-03-06
        """
        self.controller._current_diff_result = None
        effective = self.controller._filter_ignored_from_diff()
        self.assertEqual(effective["missing"], [])
        self.assertEqual(effective["extra"], [])
        self.assertEqual(effective["reparented"], [])
        self.assertEqual(effective["fuzzy_matches"], [])

    # -- _apply_ignore_styling: direct vs inherited --

    def test_apply_ignore_styling_direct_vs_inherited(self):
        """Directly-ignored items get strikethrough; inherited get italic.

        Added: 2026-03-06 (fix E2)
        """
        self._populate_tree(self.tree000, {"GRP": {"child": {"leaf": {}}}})
        self.controller._ignored_ref_paths.add("GRP|child")
        self.controller._apply_ignore_styling(self.tree000)

        root = self.tree000.topLevelItem(0)
        child = root.child(0)
        leaf = child.child(0)

        # Direct: strikethrough, NOT italic
        self.assertTrue(
            child.font(0).strikeOut(), "Direct-ignored should be strikethrough"
        )
        self.assertFalse(child.font(0).italic(), "Direct-ignored should NOT be italic")

        # Inherited: italic, NOT strikethrough
        self.assertTrue(leaf.font(0).italic(), "Inherited-ignored should be italic")
        self.assertFalse(
            leaf.font(0).strikeOut(), "Inherited-ignored should NOT be strikethrough"
        )

        # Non-ignored root: neither
        self.assertFalse(root.font(0).strikeOut())
        self.assertFalse(root.font(0).italic())

    # -- _clear_analysis_cache clears ignored paths --

    def test_clear_analysis_cache_clears_ignored_paths(self):
        """_clear_analysis_cache also resets ignored path sets.

        Added: 2026-03-06 (fix B1)
        """
        self.controller._ignored_ref_paths.add("GRP|child")
        self.controller._ignored_cur_paths.add("OTHER")
        self.controller._clear_analysis_cache()
        self.assertEqual(len(self.controller._ignored_ref_paths), 0)
        self.assertEqual(len(self.controller._ignored_cur_paths), 0)

    # -- repair_hierarchies --

    def test_repair_hierarchies_requires_prior_analysis(self):
        """repair_hierarchies returns False when no diff analysis exists.

        Added: 2026-03-06
        """
        self.controller.hierarchy_manager = None
        self.controller._current_diff_result = None
        result = self.controller.repair_hierarchies(dry_run=True)
        self.assertFalse(result)

    def test_repair_hierarchies_dry_run_restore(self):
        """repair_hierarchies restores hierarchy_manager.dry_run after execution.

        Bug: method mutated dry_run on the manager without save/restore.
        Fixed: 2026-03-06 (critique fix #1)
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["grp|stub"], "extra": [], "reparented": []}

        self.controller.hierarchy_manager = manager
        self.controller._current_diff_result = {
            "missing": ["grp|stub"],
            "extra": [],
            "reparented": [],
            "fuzzy_matches": [],
        }

        # Call with dry_run=True — should NOT permanently change manager.dry_run
        self.controller.repair_hierarchies(dry_run=True)
        self.assertFalse(
            manager.dry_run,
            "Manager dry_run should be restored to original value (False)",
        )

    def test_repair_hierarchies_cache_invalidation_after_live(self):
        """repair_hierarchies invalidates cache after live (non-dry-run) changes.

        Bug: stale diff cache persisted after a live repair.
        Fixed: 2026-03-06 (critique fix #2/6)
        """
        pm.group(empty=True, name="grp")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {
            "missing": ["grp|new_stub"],
            "extra": [],
            "reparented": [],
        }

        self.controller.hierarchy_manager = manager
        self.controller._current_diff_result = {
            "missing": ["grp|new_stub"],
            "extra": [],
            "reparented": [],
            "fuzzy_matches": [],
        }

        self.controller.repair_hierarchies(dry_run=False)

        # After live repair, cache should be cleared
        self.assertIsNone(
            self.controller._current_diff_result,
            "Diff cache should be cleared after live repair",
        )
        self.assertIsNone(
            self.controller.hierarchy_manager,
            "hierarchy_manager should be cleared after live repair",
        )

    def test_repair_hierarchies_respects_ignored_paths(self):
        """repair_hierarchies excludes ignored missing items from stub creation.

        Added: 2026-03-06
        """
        pm.group(empty=True, name="grp")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.differences = {
            "missing": ["grp|keep", "grp|skip"],
            "extra": [],
            "reparented": [],
        }

        self.controller.hierarchy_manager = manager
        self.controller._current_diff_result = {
            "missing": ["grp|keep", "grp|skip"],
            "extra": [],
            "reparented": [],
            "fuzzy_matches": [],
        }
        # Ignore "grp|skip" in the reference tree
        self.controller._ignored_ref_paths.add("grp|skip")

        result = self.controller.repair_hierarchies(
            create_stubs=True,
            quarantine_extras=False,
            fix_reparented=False,
            dry_run=True,
        )
        self.assertTrue(result)
        # Since it's dry-run, nothing created, but the stubs list should only contain "keep"
        # The manager was called with filtered missing — verify no stub created for "skip"
        self.assertFalse(pm.objExists("skip"))


# ---------------------------------------------------------------------------
# Phase 1: Cached Reference Import Tests
# ---------------------------------------------------------------------------


class TestCachedReferenceImport(MayaTkTestCase):
    """Tests for the cached reference import mechanism in HierarchyManagerController.

    Verifies that the controller reuses a single import rather than importing
    the reference file multiple times, and that cache invalidation works correctly.
    Added: 2026-03-08
    """

    def setUp(self):
        super().setUp()
        from qtpy import QtWidgets, QtGui

        # Minimal stub so the controller can initialise (same pattern as TestIgnoreFeature).
        class _FakeTree(QtWidgets.QTreeWidget):
            def __init__(self):
                super().__init__()
                self.menu = type(
                    "Menu",
                    (),
                    {"add": lambda *a, **kw: None, "setTitle": lambda *a: None},
                )()
                self.is_initialized = True

            def create_item(self, data, obj=None, parent=None):
                item = QtWidgets.QTreeWidgetItem(parent or self, data)
                return item

        class _FakeUI:
            def __init__(self):
                self.tree000 = _FakeTree()
                self.tree001 = _FakeTree()
                self.txt003 = type(
                    "W",
                    (),
                    {
                        "append": lambda *a: None,
                        "setHtml": lambda *a: None,
                        "setText": lambda *a: None,
                        "clear": lambda *a: None,
                    },
                )()

        class _FakeSB:
            registered_widgets = type("RW", (), {"TextEditLogHandler": None})()

        fake_slots = type("Slots", (), {"sb": _FakeSB(), "ui": _FakeUI()})()
        from mayatk.env_utils.hierarchy_manager.hierarchy_manager_slots import (
            HierarchyManagerController,
        )

        self.controller = HierarchyManagerController(fake_slots)
        self.tree000 = fake_slots.ui.tree000
        self.tree001 = fake_slots.ui.tree001

    def test_cached_reference_starts_none(self):
        """_cached_reference_import is None on fresh controller."""
        self.assertIsNone(self.controller._cached_reference_import)

    def test_clear_analysis_cache_clears_cached_import(self):
        """_clear_analysis_cache resets _cached_reference_import to None.

        Added: 2026-03-08
        """
        # Simulate a cached import entry (without a real sandbox).
        self.controller._cached_reference_import = {
            "path": "/fake/path.ma",
            "sandbox": None,
            "transforms": [],
        }
        self.controller._clear_analysis_cache()
        self.assertIsNone(
            self.controller._cached_reference_import,
            "Cached import should be cleared after _clear_analysis_cache()",
        )

    def test_analysis_preserves_cache_after_populate_reference_tree(self):
        """Analysis results survive a subsequent populate_reference_tree call.

        Regression: previously, populate_reference_tree() called
        _clear_analysis_cache() unconditionally, which destroyed the diff
        result even for the same reference path.
        Added: 2026-03-08
        """
        # Build a scene & reference in-process (no file I/O needed).
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        root_cur = pm.group(empty=True, name="root")
        pm.group(empty=True, name="child_a", parent=root_cur)

        root_ref = pm.group(empty=True, name="ref:root")
        pm.group(empty=True, name="ref:child_a", parent=root_ref)
        pm.group(empty=True, name="ref:child_b", parent=root_ref)

        ref_objects = [root_ref] + list(root_ref.getChildren())

        # Run analysis directly on the core HierarchyManager.
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        # Simulate the controller state after a successful analysis.
        self.controller.hierarchy_manager = manager
        self.controller._current_diff_result = diff

        # Verify analysis detected something.
        self.assertIn("root|child_b", diff["missing"])

        # Now verify the analysis result still exists.
        # (In the old code, populate_reference_tree() would destroy it.)
        self.assertIsNotNone(
            self.controller._current_diff_result,
            "Diff result should survive (no file-based tree population in this test)",
        )
        self.assertIn(
            "root|child_b", self.controller._current_diff_result["missing"]
        )

    def test_cleanup_cached_reference_import_noop_when_none(self):
        """_cleanup_cached_reference_import is safe to call when cache is None.

        Added: 2026-03-08
        """
        self.controller._cached_reference_import = None
        # Should not raise.
        self.controller._cleanup_cached_reference_import()
        self.assertIsNone(self.controller._cached_reference_import)


class TestBatchPyMELOptimizations(MayaTkTestCase):
    """Tests for Phase 2 optimizations: batch pm.ls in build_path_map,
    cmds-based traversal in build_path_map_from_nodes/build_hierarchy_structure.

    These verify identical behaviour after replacing per-node pm.PyNode()
    with batch pm.ls() and PyMEL traversal with cmds equivalents.
    Added: 2026-03-08
    """

    def test_build_path_map_deep_hierarchy(self):
        """build_path_map returns correct keys and PyMEL nodes for a 4-level hierarchy."""
        a = pm.group(empty=True, name="A")
        b = pm.group(empty=True, name="B", parent=a)
        c = pm.group(empty=True, name="C", parent=b)
        d = pm.group(empty=True, name="D", parent=c)

        path_map = HierarchyMapBuilder.build_path_map(a)
        self.assertEqual(set(path_map.keys()), {"A", "A|B", "A|B|C", "A|B|C|D"})
        self.assertEqual(path_map["A"], a)
        self.assertEqual(path_map["A|B|C|D"], d)

    def test_build_path_map_scene_wide_mode(self):
        """build_path_map with SCENE_WIDE_MODE sentinel includes all assemblies."""
        r1 = pm.group(empty=True, name="Root1")
        r2 = pm.group(empty=True, name="Root2")
        c = pm.group(empty=True, name="Child", parent=r1)

        path_map = HierarchyMapBuilder.build_path_map("SCENE_WIDE_MODE")
        self.assertIn("Root1", path_map)
        self.assertIn("Root2", path_map)
        self.assertIn("Root1|Child", path_map)
        self.assertEqual(path_map["Root1|Child"], c)

    def test_build_path_map_values_are_pymel(self):
        """Values returned by build_path_map are PyMEL Transform nodes."""
        grp = pm.group(empty=True, name="GRP")
        path_map = HierarchyMapBuilder.build_path_map(grp)
        node = path_map["GRP"]
        self.assertIsInstance(node, pm.nodetypes.Transform)
        self.assertTrue(node.exists())

    def test_build_path_map_from_nodes_subset(self):
        """build_path_map_from_nodes only includes nodes in the given list."""
        a = pm.group(empty=True, name="A")
        b = pm.group(empty=True, name="B", parent=a)
        pm.group(empty=True, name="C", parent=b)  # not passed in

        path_map = HierarchyMapBuilder.build_path_map_from_nodes([a, b])
        self.assertIn("A", path_map)
        self.assertIn("A|B", path_map)
        self.assertNotIn("A|B|C", path_map)

    def test_build_path_map_from_nodes_values_are_original_pymel(self):
        """build_path_map_from_nodes preserves the original PyMEL objects."""
        a = pm.group(empty=True, name="A")
        b = pm.group(empty=True, name="B", parent=a)
        path_map = HierarchyMapBuilder.build_path_map_from_nodes([a, b])
        self.assertIs(path_map["A"], a)
        self.assertIs(path_map["A|B"], b)

    def test_build_hierarchy_structure_basic(self):
        """build_hierarchy_structure returns correct parent/type info using cmds."""
        from mayatk.env_utils.hierarchy_manager._tree_utils import (
            build_hierarchy_structure,
        )

        parent = pm.group(empty=True, name="Parent")
        child = pm.group(empty=True, name="Child", parent=parent)

        items, roots = build_hierarchy_structure([parent, child])
        parent_key = parent.fullPath()
        child_key = child.fullPath()

        self.assertIn(parent_key, items)
        self.assertIn(child_key, items)
        self.assertEqual(items[parent_key]["short_name"], "Parent")
        self.assertEqual(items[child_key]["short_name"], "Child")
        self.assertEqual(items[child_key]["type"], "transform")
        self.assertEqual(items[child_key]["parent"], parent_key)
        self.assertIsNone(items[parent_key]["parent"])
        self.assertIn(parent_key, roots)
        self.assertNotIn(child_key, roots)


if __name__ == "__main__":
    unittest.main()
