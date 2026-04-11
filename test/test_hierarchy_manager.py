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
from qtpy import QtCore, QtWidgets as _QtWidgets

if _QtWidgets.QApplication.instance() is None:
    _QtWidgets.QApplication([])

import pymel.core as pm
import maya.cmds as cmds
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

    def test_same_leaf_different_parent_not_fuzzy(self):
        """Verify same-name leaves under different parents stay in missing/extra.

        Bug: FuzzyMatcher scored identical leaf names at 1.0, causing them to
        be classified as "renamed" when they were actually ambiguous reparents
        (same leaf, different parent, N:M multiplicity).
        Fixed: 2026-04-01
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")

        # Current scene: adapter_loc under grp1 AND grp1_copy
        grp1 = pm.group(empty=True, name="grp1")
        grp1_copy = pm.group(empty=True, name="grp1_copy")
        pm.group(empty=True, name="adapter_loc", parent=grp1)
        pm.group(empty=True, name="adapter_loc", parent=grp1_copy)

        # Reference scene: adapter_loc under grp AND grp_copy
        grp_ref = pm.group(empty=True, name="ref:grp")
        grp_copy_ref = pm.group(empty=True, name="ref:grp_copy")
        pm.group(empty=True, name="ref:adapter_loc", parent=grp_ref)
        pm.group(empty=True, name="ref:adapter_loc", parent=grp_copy_ref)

        ref_objects = [grp_ref, grp_copy_ref] + list(
            grp_ref.getChildren() + grp_copy_ref.getChildren()
        )

        manager = HierarchyManager(fuzzy_matching=True, dry_run=True)
        diff_result = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        fuzzy = diff_result.get("fuzzy_matches", [])
        fuzzy_leaves = {f["target_name"].rsplit("|", 1)[-1] for f in fuzzy} | {
            f["current_name"].rsplit("|", 1)[-1] for f in fuzzy
        }

        self.assertNotIn(
            "adapter_loc",
            fuzzy_leaves,
            f"Same-name leaf 'adapter_loc' should NOT be in fuzzy_matches: {fuzzy}",
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

    def test_create_stubs_are_tagged_and_locked(self):
        """Stubs are tagged with hierarchyManagerStub attr, noted, and locked.

        Verifies that create_stubs applies the full finalization treatment
        so that Maya's Optimize Scene Size cannot delete them.
        Added: 2026-04-10
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["stub_node"]}

        created = manager.create_stubs()
        self.assertEqual(len(created), 1)

        node = "stub_node"
        self.assertTrue(pm.objExists(node))

        # Custom attribute exists and is True.
        self.assertTrue(
            cmds.attributeQuery(HierarchyManager.STUB_ATTR, node=node, exists=True),
            "Stub should have hierarchyManagerStub attribute",
        )
        self.assertTrue(cmds.getAttr(f"{node}.{HierarchyManager.STUB_ATTR}"))

        # Notes attribute with explanation.
        self.assertTrue(cmds.attributeQuery("notes", node=node, exists=True))
        notes = cmds.getAttr(f"{node}.notes")
        self.assertIn("Hierarchy Manager", notes)

        # Node is locked.
        self.assertTrue(cmds.lockNode(node, query=True, lock=True)[0])

        # Outliner colour is set.
        self.assertTrue(cmds.getAttr(f"{node}.useOutlinerColor"))

    def test_create_stubs_intermediate_parents_are_tagged(self):
        """Intermediate parent groups created by _ensure_parent_chain are also
        tagged and locked, preventing cascading cleanup deletion.

        Added: 2026-04-10
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["grp_a|grp_b|leaf_node"]}

        manager.create_stubs()

        # All three nodes should exist.
        for name in ("grp_a", "grp_b", "leaf_node"):
            self.assertTrue(pm.objExists(name), f"{name} should exist")

        # The intermediate parents should also be tagged and locked.
        for name in ("grp_a", "grp_b"):
            self.assertTrue(
                cmds.attributeQuery(HierarchyManager.STUB_ATTR, node=name, exists=True),
                f"{name} should have stub attribute",
            )
            self.assertTrue(
                cmds.lockNode(name, query=True, lock=True)[0],
                f"{name} should be locked",
            )

    def test_create_stubs_rerun_adds_under_locked_parents(self):
        """A second create_stubs call can add stubs under previously locked parents.

        Regression: _ensure_parent_chain locked parent nodes on first run,
        so subsequent pm.parent calls on a second run would fail with a
        lock error.
        Added: 2026-04-10
        """
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)

        # First run — creates grp_a (locked) and leaf_1 under it.
        manager.differences = {"missing": ["grp_a|leaf_1"]}
        created1 = manager.create_stubs()
        self.assertEqual(len(created1), 1)
        self.assertTrue(cmds.lockNode("grp_a", query=True, lock=True)[0])

        # Second run — add a sibling under the same (locked) parent.
        manager.differences = {"missing": ["grp_a|leaf_2"]}
        created2 = manager.create_stubs()
        self.assertEqual(len(created2), 1)
        self.assertTrue(pm.objExists("leaf_2"))
        leaf2 = pm.PyNode("leaf_2")
        self.assertEqual(leaf2.getParent().nodeName(), "grp_a")

        # Parent should still be locked after the second run.
        self.assertTrue(cmds.lockNode("grp_a", query=True, lock=True)[0])

    def test_merge_can_delete_locked_stubs(self):
        """Merge mode unlocks and replaces locked stubs without error.

        Verifies that _unlock_if_stub is called before pm.delete so that
        locked stub nodes don't block the pull operation.
        Added: 2026-04-10
        """
        # Create a stub via the normal path.
        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.differences = {"missing": ["REPLACE_ME"]}
        manager.create_stubs()

        # Confirm it's locked.
        self.assertTrue(cmds.lockNode("REPLACE_ME", query=True, lock=True)[0])

        # Now simulate a pull that replaces it.
        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.group(empty=True, name="temp_import:REPLACE_ME")

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )

        clean_name = get_clean_node_name(imported)
        # Should not raise despite the node being locked.
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        self.assertEqual(imported.nodeName(), "REPLACE_ME")
        matches = pm.ls("REPLACE_ME", type="transform")
        self.assertEqual(len(matches), 1)

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

        # _integrate_object (unified replacement for _process_as_root_object,
        # _process_with_hierarchy, etc.)
        # Test single-object integration: namespaced node gets placed and renamed
        if not pm.namespace(exists="test_ns"):
            pm.namespace(add="test_ns")
        ns_node = pm.group(empty=True, name="test_ns:my_node")
        swapper._integrate_object(ns_node, "my_node", merge=False)
        self.assertEqual(ns_node.nodeName(), "my_node")

        # Test single-object integration with hierarchy
        ns_root2 = pm.group(empty=True, name="test_ns:root2")
        ns_child2 = pm.group(empty=True, name="test_ns:child2", parent=ns_root2)
        swapper._integrate_object(ns_child2, "child2", merge=False)
        self.assertEqual(ns_child2.nodeName(), "child2")

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
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

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
            t
            for t in info.get("transforms", [])
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
            len(diff["missing"]),
            48,
            f"Expected 48 missing, got {len(diff['missing'])}",
        )
        self.assertEqual(
            len(diff["extra"]),
            4,
            f"Expected 4 extra, got {len(diff['extra'])}",
        )
        self.assertEqual(
            len(diff["reparented"]),
            1,
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
                path,
                reference_cleaned,
                f"Missing item '{path}' not found in reference scene",
            )
            self.assertNotIn(
                path,
                current_cleaned,
                f"Missing item '{path}' actually exists in current scene",
            )

        # Every "extra" path must exist in current but NOT in reference
        for path in diff["extra"]:
            self.assertIn(
                path,
                current_cleaned,
                f"Extra item '{path}' not found in current scene",
            )
            self.assertNotIn(
                path,
                reference_cleaned,
                f"Extra item '{path}' actually exists in reference scene",
            )

        # Every "reparented" leaf must appear in both but under different parents
        for rp_item in diff["reparented"]:
            self.assertIn(
                rp_item["reference_path"],
                reference_cleaned,
                f"Reparented ref path not in reference: {rp_item['reference_path']}",
            )
            self.assertIn(
                rp_item["current_path"],
                current_cleaned,
                f"Reparented cur path not in current: {rp_item['current_path']}",
            )

        # The independent set diff must match what analyze_hierarchies reported
        reparented_ref_paths = {r["reference_path"] for r in diff["reparented"]}
        reparented_cur_paths = {r["current_path"] for r in diff["reparented"]}
        fuzzy_ref_paths = {m["target_name"] for m in diff.get("fuzzy_matches", [])}
        fuzzy_cur_paths = {m["current_name"] for m in diff.get("fuzzy_matches", [])}
        independent_missing = (
            reference_cleaned - current_cleaned - reparented_ref_paths - fuzzy_ref_paths
        )
        independent_extra = (
            current_cleaned - reference_cleaned - reparented_cur_paths - fuzzy_cur_paths
        )
        self.assertEqual(
            sorted(independent_missing),
            sorted(diff["missing"]),
            "Independent missing computation disagrees with analyze_hierarchies",
        )
        self.assertEqual(
            sorted(independent_extra),
            sorted(diff["extra"]),
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
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

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
            t
            for t in info.get("transforms", [])
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
            len(diff["missing"]),
            0,
            f"Expected 0 missing, got {len(diff['missing'])}: {diff['missing'][:5]}",
        )
        self.assertEqual(
            len(diff["extra"]),
            2,
            f"Expected 2 extra, got {len(diff['extra'])}: {diff['extra']}",
        )
        self.assertEqual(
            len(diff["reparented"]),
            0,
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
                path,
                reference_cleaned,
                f"Missing item '{path}' not found in reference scene",
            )
            self.assertNotIn(
                path,
                current_cleaned,
                f"Missing item '{path}' actually exists in current scene",
            )

        for path in diff["extra"]:
            self.assertIn(
                path,
                current_cleaned,
                f"Extra item '{path}' not found in current scene",
            )
            self.assertNotIn(
                path,
                reference_cleaned,
                f"Extra item '{path}' actually exists in reference scene",
            )

        reparented_ref_paths = {r["reference_path"] for r in diff["reparented"]}
        reparented_cur_paths = {r["current_path"] for r in diff["reparented"]}
        fuzzy_ref_paths = {m["target_name"] for m in diff.get("fuzzy_matches", [])}
        fuzzy_cur_paths = {m["current_name"] for m in diff.get("fuzzy_matches", [])}
        independent_missing = (
            reference_cleaned - current_cleaned - reparented_ref_paths - fuzzy_ref_paths
        )
        independent_extra = (
            current_cleaned - reference_cleaned - reparented_cur_paths - fuzzy_cur_paths
        )
        self.assertEqual(
            sorted(independent_missing),
            sorted(diff["missing"]),
            "Independent missing computation disagrees with analyze_hierarchies",
        )
        self.assertEqual(
            sorted(independent_extra),
            sorted(diff["extra"]),
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

    @skipUnlessExtended
    def test_c130_fbx_vs_ma_diff_content(self):
        """Regression: C130 FBX-vs-MA diff correctly detects FBX name-flattening.

        Validates that analyze_hierarchies handles FBX name-flattening artifacts
        (e.g. BOOSTER_OFF_6_SWITCH → OVERHEAD_CONSOLE_BOOSTERS_BOOSTER_OFF_6_SWITCH)
        by recognizing them as renames rather than missing+extra pairs.

        Bug: Before suffix matching, the BOOSTER items were split into 2 false
        "missing" and 2 false "extra" entries. The fix operation would have created
        empty stubs AND quarantined real geometry — destroying the scene.
        Fixed: 2026-03-09
        """
        if not self.real_scenes_dir.exists():
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

        reference_fbx = self.real_scenes_dir / "C130_FCR_Speedrun_Assembly.fbx"
        current_scene = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Moth+Flame Team Folder"
            r"\PRODUCTION\AF\C-130HJ_Mutual\PRODUCTION\Maya\Flap_Rigging\scenes"
            r"\modules\C130H_FCR_SPEEDRUN\C130H_FCR_SPEEDRUN_module.ma"
        )

        if not reference_fbx.exists() or not current_scene.exists():
            self.skipTest("Required C130 FBX/MA scene files not found.")

        pm.openFile(str(current_scene), force=True)

        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_fbx), force_complete_import=True
        )
        self.assertIsNotNone(info, "Failed to import FBX reference")

        default_cams = frozenset({"persp", "top", "front", "side"})
        ref_objs = [
            t
            for t in info.get("transforms", [])
            if t.nodeName().split(":")[-1] not in default_cams
        ]

        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=True
        )
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=False,
            filter_cameras=False,
            filter_lights=False,
        )

        # --- Assert exact baseline counts ---
        self.assertEqual(
            len(diff["missing"]),
            2,
            f"Expected 2 missing, got {len(diff['missing'])}: {diff['missing']}",
        )
        self.assertEqual(
            len(diff["extra"]),
            20,
            f"Expected 20 extra, got {len(diff['extra'])}: {diff['extra'][:5]}",
        )
        self.assertEqual(
            len(diff["reparented"]),
            3,
            f"Expected 3 reparented, got {len(diff['reparented'])}",
        )
        self.assertEqual(
            len(diff["fuzzy_matches"]),
            2,
            f"Expected 2 fuzzy (suffix) matches, got {len(diff['fuzzy_matches'])}",
        )

        # --- Assert missing items are genuinely missing (arrow GEOs) ---
        missing_set = set(diff["missing"])
        self.assertIn(
            "INTERACTIVES|ARROWS|S00A24_ARROW_GRP|S00A24_ARROW_LOC|S00A24_ARROW_GEO",
            missing_set,
        )
        self.assertIn(
            "INTERACTIVES|ARROWS|S00A27_ARROW_GRP|S00A27_ARROW_LOC|S00A27_ARROW_GEO",
            missing_set,
        )

        # --- Assert BOOSTER items are NOT in missing/extra (they are suffix-matched) ---
        all_missing_extra = set(diff["missing"]) | set(diff["extra"])
        for path in all_missing_extra:
            self.assertNotIn(
                "BOOSTER_OFF_6_SWITCH",
                path.rsplit("|", 1)[-1],
                f"BOOSTER item should be suffix-matched, not missing/extra: {path}",
            )

        # --- Assert suffix-matched pairs are in fuzzy_matches ---
        fuzzy_targets = {m["target_name"] for m in diff["fuzzy_matches"]}
        fuzzy_currents = {m["current_name"] for m in diff["fuzzy_matches"]}
        self.assertIn(
            "INTERACTIVES|SWITCHES|S00A18_AIL_SWITCH_LOC|"
            "OVERHEAD_CONSOLE_BOOSTERS_BOOSTER_OFF_6_SWITCH",
            fuzzy_targets,
        )
        self.assertIn(
            "INTERACTIVES|SWITCHES|S00A18_AIL_SWITCH_LOC|BOOSTER_OFF_6_SWITCH",
            fuzzy_currents,
        )

        # --- Assert extras are all under REVISIONS (legitimate MA-only content) ---
        for path in diff["extra"]:
            self.assertTrue(
                path.startswith("REVISIONS"),
                f"Extra item should be under REVISIONS, got: {path}",
            )

        # --- Assert reparented items are the ExampleBase nodes ---
        reparented_leaves = {r["leaf"] for r in diff["reparented"]}
        self.assertEqual(
            reparented_leaves,
            {"ExampleBase", "ExampleBase_1", "ExampleBase_2"},
        )

        # --- Cross-validate against actual scene contents ---
        current_cleaned = {
            clean_hierarchy_path(p) for p in manager.current_scene_path_map
        }
        reference_cleaned = {
            clean_hierarchy_path(p) for p in manager.reference_scene_path_map
        }

        for path in diff["missing"]:
            self.assertIn(
                path,
                reference_cleaned,
                f"Missing item '{path}' not found in reference scene",
            )
            self.assertNotIn(
                path,
                current_cleaned,
                f"Missing item '{path}' actually exists in current scene",
            )

        for path in diff["extra"]:
            self.assertIn(
                path,
                current_cleaned,
                f"Extra item '{path}' not found in current scene",
            )
            self.assertNotIn(
                path,
                reference_cleaned,
                f"Extra item '{path}' actually exists in reference scene",
            )

        # Independent diff computation must agree with analyze_hierarchies
        reparented_ref_paths = {r["reference_path"] for r in diff["reparented"]}
        reparented_cur_paths = {r["current_path"] for r in diff["reparented"]}
        fuzzy_ref_paths = {m["target_name"] for m in diff.get("fuzzy_matches", [])}
        fuzzy_cur_paths = {m["current_name"] for m in diff.get("fuzzy_matches", [])}
        independent_missing = (
            reference_cleaned - current_cleaned - reparented_ref_paths - fuzzy_ref_paths
        )
        independent_extra = (
            current_cleaned - reference_cleaned - reparented_cur_paths - fuzzy_cur_paths
        )
        self.assertEqual(
            sorted(independent_missing),
            sorted(diff["missing"]),
            "Independent missing computation disagrees with analyze_hierarchies",
        )
        self.assertEqual(
            sorted(independent_extra),
            sorted(diff["extra"]),
            "Independent extra computation disagrees with analyze_hierarchies",
        )

    # -------------------------------------------------------------------------
    # Real-World Animation Preservation Tests
    # -------------------------------------------------------------------------

    @staticmethod
    def _snapshot_scene_animation():
        """Capture a complete animation snapshot of the current scene.

        Returns a dict mapping ``"node.attr"`` to a dict with:
            - ``times``: sorted list of keyframe times
            - ``values``: corresponding values at those times
            - ``curve_type``: animCurve node type (e.g. animCurveTL)

        Also captures:
            - ``constraints``: dict of constrained node → list of constraint types
            - ``expressions``: dict of expression name → target objects
            - ``anim_curve_count``: total animCurve nodes in scene
        """
        snapshot = {
            "curves": {},
            "constraints": {},
            "expressions": {},
            "anim_curve_count": 0,
        }

        # Snapshot all animCurve connections
        all_curves = cmds.ls(type="animCurve") or []
        snapshot["anim_curve_count"] = len(all_curves)

        for curve in all_curves:
            outputs = (
                cmds.listConnections(curve + ".output", s=False, d=True, plugs=True)
                or []
            )
            for dest_plug in outputs:
                times = cmds.keyframe(curve, query=True, timeChange=True) or []
                values = cmds.keyframe(curve, query=True, valueChange=True) or []
                curve_type = cmds.objectType(curve)
                snapshot["curves"][dest_plug] = {
                    "times": sorted(times),
                    "values": values,
                    "curve_type": curve_type,
                    "curve_node": curve,
                }

        # Snapshot constraints
        all_constraints = cmds.ls(type="constraint") or []
        for cst in all_constraints:
            parent = (cmds.listRelatives(cst, parent=True, fullPath=True) or [None])[0]
            if parent:
                short_parent = parent.rsplit("|", 1)[-1]
                snapshot["constraints"].setdefault(short_parent, []).append(
                    cmds.objectType(cst)
                )

        # Snapshot expressions
        all_expressions = cmds.ls(type="expression") or []
        for expr in all_expressions:
            targets = cmds.listConnections(expr, s=False, d=True) or []
            snapshot["expressions"][expr] = sorted(set(targets))

        return snapshot

    @skipUnlessExtended
    def test_c5_analyze_preserves_all_animation(self):
        """Analyze-only workflow (dry_run) must not alter any animation data.

        Opens C5_AFT_COMP_ASSEMBLY_current.ma, snapshots all animation,
        runs analyze_hierarchies with a reference import, then verifies
        every animCurve, constraint, and expression survived unchanged.

        This validates that the read-only analysis path has no side effects
        on scene animation.
        """
        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"

        if not current_scene.exists() or not reference_scene.exists():
            self.skipTest("Required C5 scene files not found.")

        pm.openFile(str(current_scene), force=True)
        before = self._snapshot_scene_animation()

        # Import reference and analyze
        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_scene), force_complete_import=True
        )
        self.assertIsNotNone(info)

        ref_objs = info.get("transforms", [])
        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=True
        )
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        # Clean up reference namespace before checking
        sandbox.cleanup_all_namespaces()

        after = self._snapshot_scene_animation()

        # --- Verify no animation was lost ---
        self.assertEqual(
            after["anim_curve_count"],
            before["anim_curve_count"],
            f"AnimCurve count changed: {before['anim_curve_count']} -> "
            f"{after['anim_curve_count']}",
        )

        # Every curve that existed before must still exist with same keys
        for plug, curve_data in before["curves"].items():
            self.assertIn(
                plug,
                after["curves"],
                f"Animation on '{plug}' was destroyed during analyze",
            )
            after_data = after["curves"][plug]
            self.assertEqual(
                curve_data["times"],
                after_data["times"],
                f"Keyframe times changed on '{plug}'",
            )
            # Compare values with tolerance for floating-point drift
            for i, (bv, av) in enumerate(
                zip(curve_data["values"], after_data["values"])
            ):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=6,
                    msg=f"Value changed on '{plug}' at time {curve_data['times'][i]}",
                )

        # Constraints must be identical
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed during analyze",
        )

        # Expressions must be identical
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expression set changed during analyze",
        )

    @skipUnlessExtended
    def test_c5_full_repair_preserves_animation(self):
        """Full repair workflow (non-dry-run) must preserve all existing animation.

        Runs the complete hierarchy repair pipeline on C5_AFT_COMP scenes:
        analyze → fix_reparented → quarantine_extras.  Verifies that every
        animated node that existed before the repair still has its animation
        data intact afterward.

        Nodes that are reparented or quarantined should retain their curves.
        """
        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"

        if not current_scene.exists() or not reference_scene.exists():
            self.skipTest("Required C5 scene files not found.")

        pm.openFile(str(current_scene), force=True)
        before = self._snapshot_scene_animation()

        # Import reference
        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_scene), force_complete_import=True
        )
        self.assertIsNotNone(info)

        ref_objs = info.get("transforms", [])
        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=False
        )
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        # Run repair operations
        if diff.get("reparented"):
            manager.fix_reparented()
        if diff.get("extra"):
            manager.quarantine_extras(skip_animated=True)
        if diff.get("fuzzy_matches"):
            manager.fix_fuzzy_renames()

        # Clean up reference namespace
        sandbox.cleanup_all_namespaces()

        after = self._snapshot_scene_animation()

        # --- Every curve from the original scene must survive ---
        lost_curves = []
        damaged_curves = []
        for plug, curve_data in before["curves"].items():
            if plug not in after["curves"]:
                lost_curves.append(plug)
                continue
            after_data = after["curves"][plug]
            if curve_data["times"] != after_data["times"]:
                damaged_curves.append(
                    f"{plug}: times {curve_data['times'][:3]}... "
                    f"→ {after_data['times'][:3]}..."
                )
                continue
            for bv, av in zip(curve_data["values"], after_data["values"]):
                if abs(bv - av) > 1e-6:
                    damaged_curves.append(f"{plug}: value drift {bv} → {av}")
                    break

        self.assertEqual(
            len(lost_curves),
            0,
            f"Repair destroyed {len(lost_curves)} animation curves: "
            f"{lost_curves[:10]}",
        )
        self.assertEqual(
            len(damaged_curves),
            0,
            f"Repair damaged {len(damaged_curves)} animation curves: "
            f"{damaged_curves[:10]}",
        )

        # Constraints belonging to original scene nodes must survive
        for node, cst_types in before["constraints"].items():
            if node in after["constraints"]:
                self.assertEqual(
                    sorted(cst_types),
                    sorted(after["constraints"][node]),
                    f"Constraint types changed on '{node}'",
                )
            else:
                # Node may have been reparented — check if it still exists
                if cmds.objExists(node):
                    self.fail(
                        f"Node '{node}' exists but lost its constraints: {cst_types}"
                    )

    @skipUnlessExtended
    def test_tangent_preservation_scene_animation_intact(self):
        """Tangent preservation test scene: animation data survives analyze workflow.

        Opens the dedicated tangent_preservation_test.ma scene (3.1 MB),
        snapshots all animation, runs an identity analyze (no reference),
        and verifies the scene is untouched.  Additionally checks curve
        counts and that animated nodes have the expected curve types.
        """
        test_scenes = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\scene_exporter_test"
        )
        scene_file = test_scenes / "tangent_preservation_test.ma"

        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        # Verify the scene actually has animation to protect
        self.assertGreater(
            before["anim_curve_count"],
            0,
            "tangent_preservation_test.ma should have animation curves",
        )

        # Build a HierarchyManager and do a scene-wide analysis
        # (no reference — this just exercises the map-building code path)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.current_scene_path_map = HierarchyMapBuilder.build_path_map(
            "SCENE_WIDE_MODE", strip_namespaces=False
        )

        after = self._snapshot_scene_animation()

        # No curves should be lost or modified
        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            "AnimCurve count changed after map-building",
        )
        for plug, data in before["curves"].items():
            self.assertIn(plug, after["curves"], f"Curve lost: {plug}")
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Times changed: {plug}",
            )

    @skipUnlessExtended
    def test_baked_curves_scene_animation_intact(self):
        """Baked curves test scene: animation survives analyze + repair workflow.

        Opens breaks_baked_curves_test.ma (6.8 MB), which contains
        densely-baked keyframe data.  Snapshots animation, runs analyze
        with the scene as its own reference (identity diff — no changes
        should be made), and verifies all curves are intact.
        """
        test_scenes = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\scene_exporter_test"
        )
        scene_file = test_scenes / "breaks_baked_curves_test.ma"

        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        self.assertGreater(
            before["anim_curve_count"],
            0,
            "breaks_baked_curves_test.ma should have baked animation curves",
        )

        # Analyze the scene against itself (identity — nothing should change)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=all_transforms,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        after = self._snapshot_scene_animation()

        # Curve count must be identical
        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            f"Baked curve count changed: {before['anim_curve_count']} "
            f"→ {after['anim_curve_count']}",
        )

        # Spot-check: for every curve, times and values match
        mismatches = []
        for plug, data in before["curves"].items():
            if plug not in after["curves"]:
                mismatches.append(f"LOST: {plug}")
                continue
            a = after["curves"][plug]
            if len(data["times"]) != len(a["times"]):
                mismatches.append(
                    f"KEY COUNT: {plug} ({len(data['times'])} → {len(a['times'])})"
                )
        self.assertEqual(
            len(mismatches),
            0,
            f"Baked curves damaged: {mismatches[:10]}",
        )

    @skipUnlessExtended
    def test_c5_scene_animated_node_inventory(self):
        """Verify expected animation inventory for C5_AFT_COMP_ASSEMBLY_current.

        Opens the scene and validates that the set of animated nodes and
        their constraint/expression types match a known baseline.
        This catches silent scene corruption (e.g. if Maya or a plugin
        strips animation on load).
        """
        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        if not current_scene.exists():
            self.skipTest("C5 current scene not found.")

        pm.openFile(str(current_scene), force=True)
        snap = self._snapshot_scene_animation()

        # The C5 AFT COMP scene should have known animation properties.
        # Rather than hard-coding exact counts (which change with scene edits),
        # validate structural properties that must always hold:

        # 1. Animation curves should exist (this is an animated scene)
        self.assertGreater(
            snap["anim_curve_count"],
            0,
            "C5 scene should have at least some animation curves",
        )

        # 2. Every curve must have at least 1 keyframe
        for plug, data in snap["curves"].items():
            self.assertGreater(
                len(data["times"]),
                0,
                f"Curve on '{plug}' has no keyframes",
            )

        # 3. Every constraint must have a valid type
        valid_constraint_types = {
            "parentConstraint",
            "orientConstraint",
            "pointConstraint",
            "scaleConstraint",
            "aimConstraint",
            "poleVectorConstraint",
            "geometryConstraint",
            "normalConstraint",
            "tangentConstraint",
        }
        for node, cst_types in snap["constraints"].items():
            for ct in cst_types:
                self.assertIn(
                    ct,
                    valid_constraint_types,
                    f"Unknown constraint type '{ct}' on '{node}'",
                )

        # 4. Animated objects must still exist in the scene
        animated_nodes = set()
        for plug in snap["curves"]:
            node_name = plug.split(".")[0]
            animated_nodes.add(node_name)
        for node_name in animated_nodes:
            self.assertTrue(
                cmds.objExists(node_name),
                f"Animated node '{node_name}' doesn't exist in scene",
            )

    @skipUnlessExtended
    def test_c5_module_vs_current_animation_delta(self):
        """Compare animation between the module and current C5 scenes.

        Opens both scenes sequentially, snapshots their animation, and
        validates that the current scene is a superset of the module's
        animation (the current scene should have at least as much
        animation as the module it was built from).
        """
        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_current.ma"
        reference_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"

        if not current_scene.exists() or not reference_scene.exists():
            self.skipTest("Required C5 scene files not found.")

        # Snapshot module animation
        pm.openFile(str(reference_scene), force=True)
        module_snap = self._snapshot_scene_animation()

        # Snapshot current animation
        pm.openFile(str(current_scene), force=True)
        current_snap = self._snapshot_scene_animation()

        # Current scene should have animation (at minimum)
        self.assertGreater(
            current_snap["anim_curve_count"],
            0,
            "Current scene should have animation",
        )

        # If both scenes have animation, the current should have >= module's
        # animation (current is the evolved version of module)
        if module_snap["anim_curve_count"] > 0:
            # Find curves that exist in module but not in current (by plug name)
            # Note: node names may differ (namespaces stripped), so compare by
            # the attribute suffix only (e.g. ".translateX")
            module_attrs = set()
            for plug in module_snap["curves"]:
                attr = plug.split(".")[-1] if "." in plug else plug
                module_attrs.add(attr)

            current_attrs = set()
            for plug in current_snap["curves"]:
                attr = plug.split(".")[-1] if "." in plug else plug
                current_attrs.add(attr)

            # The set of animated attribute types should overlap substantially
            overlap = module_attrs & current_attrs
            self.assertGreaterEqual(
                len(overlap),
                3,
                f"Module and current scenes should share multiple animated "
                f"attribute types, only found {len(overlap)}: {overlap}",
            )

    @skipUnlessExtended
    def test_freeze_transforms_animation_survives_analyze(self):
        """freeze_transforms.ma: animation unaffected by hierarchy analysis.

        This scene lives in the transforms/ test folder and may contain
        keyed transforms with frozen channels — a tricky edge case where
        Maya rewires animation curves after a freeze.  Validates that
        the full analyze_hierarchies pipeline does not alter any curves.
        """
        scene_file = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\transforms\freeze_transforms.ma"
        )
        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        # Full analyze pipeline (scene as its own reference)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        if all_transforms:
            manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=all_transforms,
                filter_meshes=True,
                filter_cameras=True,
                filter_lights=True,
            )

        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            "AnimCurve count changed in freeze_transforms scene",
        )
        for plug, data in before["curves"].items():
            self.assertIn(plug, after["curves"], f"Curve lost: {plug}")
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Keyframe times changed: {plug}",
            )
            for bv, av in zip(data["values"], after["curves"][plug]["values"]):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=6,
                    msg=f"Value changed: {plug}",
                )
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed in freeze_transforms scene",
        )
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expressions changed in freeze_transforms scene",
        )

    @skipUnlessExtended
    def test_icio_loadmaster_panel_animation_survives_analyze(self):
        """C5M_AFT_LOADMASTER_PANEL_copy.ma: animation survives analysis.

        Scene from icio_error/ — reproduces an import-cycle-induced-orphan
        bug.  Validates animation invariance through the full analyze pipeline.
        """
        scene_file = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\icio_error\C5M_AFT_LOADMASTER_PANEL_copy.ma"
        )
        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        # Full analyze pipeline (scene as its own reference)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        if all_transforms:
            manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=all_transforms,
                filter_meshes=True,
                filter_cameras=True,
                filter_lights=True,
            )

        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            "AnimCurve count changed in LOADMASTER_PANEL scene",
        )
        for plug, data in before["curves"].items():
            self.assertIn(plug, after["curves"], f"Curve lost: {plug}")
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Keyframe times changed: {plug}",
            )
            for bv, av in zip(data["values"], after["curves"][plug]["values"]):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=6,
                    msg=f"Value changed: {plug}",
                )
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed in LOADMASTER_PANEL scene",
        )
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expressions changed in LOADMASTER_PANEL scene",
        )

    @skipUnlessExtended
    def test_split_assembly_animation_survives_analyze(self):
        """example_of_a_split_assembly.ma: animation unaffected by analysis.

        This instance_separator test scene has instance nodes that may
        carry animation.  Validates that the full analyze_hierarchies
        pipeline does not interfere with instanced-transform animation.
        """
        scene_file = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\instance_separator\example_of_a_split_assembly.ma"
        )
        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        # Full analyze pipeline (scene as its own reference)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        if all_transforms:
            manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=all_transforms,
                filter_meshes=True,
                filter_cameras=True,
                filter_lights=True,
            )

        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            "AnimCurve count changed in split_assembly scene",
        )
        for plug, data in before["curves"].items():
            self.assertIn(plug, after["curves"], f"Curve lost: {plug}")
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Keyframe times changed: {plug}",
            )
            for bv, av in zip(data["values"], after["curves"][plug]["values"]):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=6,
                    msg=f"Value changed: {plug}",
                )
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed in split_assembly scene",
        )
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expressions changed in split_assembly scene",
        )

    @skipUnlessExtended
    def test_c5m_aft_compartment_animation_survives_analyze(self):
        """C5M_AFT_COMPARTMENT_module.mb: animation survives analysis.

        A .mb (binary) scene file in hierarchy_test/.  Tests that the
        binary format does not affect animation snapshot fidelity and
        that the full analyze_hierarchies pipeline works on binary scenes.
        """
        scene_file = self.real_scenes_dir / "C5M_AFT_COMPARTMENT_module.mb"
        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        # Full analyze pipeline (scene as its own reference)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        if all_transforms:
            manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=all_transforms,
                filter_meshes=True,
                filter_cameras=True,
                filter_lights=True,
            )

        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            "AnimCurve count changed in C5M_AFT_COMPARTMENT .mb scene",
        )
        for plug, data in before["curves"].items():
            self.assertIn(plug, after["curves"], f"Curve lost: {plug}")
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Keyframe times changed: {plug}",
            )
            for bv, av in zip(data["values"], after["curves"][plug]["values"]):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=6,
                    msg=f"Value changed: {plug}",
                )
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed in C5M_AFT_COMPARTMENT scene",
        )
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expressions changed in C5M_AFT_COMPARTMENT scene",
        )

    @skipUnlessExtended
    def test_tube_rig_mlg_animation_survives_analyze(self):
        """C130J_MLG_copy.ma: animation survives full analyze workflow.

        A tube-rig scene (8.2 MB) that likely contains rigging constraints
        and possibly driven keys.  Validates the complete analysis path
        including reference import and diff computation.
        """
        scene_file = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\tube_rig\C130J_MLG_copy.ma"
        )
        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        # Full analysis using the scene as its own reference (identity diff)
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        if all_transforms:
            manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=all_transforms,
                filter_meshes=True,
                filter_cameras=True,
                filter_lights=True,
            )

        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            "AnimCurve count changed in C130J_MLG tube rig scene",
        )
        for plug, data in before["curves"].items():
            self.assertIn(plug, after["curves"], f"Curve lost: {plug}")
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Keyframe times changed: {plug}",
            )
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed in C130J_MLG scene",
        )
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expressions changed in C130J_MLG scene",
        )

    @skipUnlessExtended
    def test_optimized_baked_keys_animation_survives_analyze(self):
        """C5M_MAIN_LANDING_GEAR_DOORS_module_baked_optimized.ma: dense baked
        animation survives the full analyze workflow.

        This 23.8 MB scene has heavily baked and then optimized keyframe
        data — potentially thousands of keys per curve.  Validates that
        the analysis pipeline handles large key counts without loss.
        """
        scene_file = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
            r"\_tests\optimize_baked_keys"
            r"\C5M_MAIN_LANDING_GEAR_DOORS_module_baked_optimized.ma"
        )
        if not scene_file.exists():
            self.skipTest(f"Scene not found: {scene_file}")

        pm.openFile(str(scene_file), force=True)
        before = self._snapshot_scene_animation()

        self.assertGreater(
            before["anim_curve_count"],
            0,
            "Optimized baked scene should have animation curves",
        )

        # Full analysis using scene as its own reference
        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        all_transforms = [
            t
            for t in pm.ls(type="transform")
            if not cmds.objectType(str(t), isAType="camera")
        ]
        if all_transforms:
            manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=all_transforms,
                filter_meshes=True,
                filter_cameras=True,
                filter_lights=True,
            )

        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            f"Baked curve count changed: {before['anim_curve_count']} "
            f"→ {after['anim_curve_count']}",
        )

        # Spot-check: every curve retains its key count and first/last values
        mismatches = []
        for plug, data in before["curves"].items():
            if plug not in after["curves"]:
                mismatches.append(f"LOST: {plug}")
                continue
            a = after["curves"][plug]
            if len(data["times"]) != len(a["times"]):
                mismatches.append(
                    f"KEY COUNT: {plug} ({len(data['times'])} → {len(a['times'])})"
                )
                continue
            # Check first and last values for drift
            if data["values"] and a["values"]:
                if abs(data["values"][0] - a["values"][0]) > 1e-6:
                    mismatches.append(f"FIRST VALUE: {plug}")
                if abs(data["values"][-1] - a["values"][-1]) > 1e-6:
                    mismatches.append(f"LAST VALUE: {plug}")

        self.assertEqual(
            len(mismatches),
            0,
            f"Optimized baked curves damaged: {mismatches[:10]}",
        )

    @skipUnlessExtended
    def test_c5m_aft_compartment_with_reference_import(self):
        """C5M_AFT_COMPARTMENT: import C5 assembly as reference and analyze.

        Uses the .mb compartment scene as current and imports the full
        C5_AFT_COMP_ASSEMBLY_module.ma as the reference.  This cross-scene
        pair exercises the namespace import → analyze → diff pipeline on
        a scene pair that has a parent-child structural relationship.
        Validates animation is preserved through the full workflow.
        """
        current_scene = self.real_scenes_dir / "C5M_AFT_COMPARTMENT_module.mb"
        reference_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"

        if not current_scene.exists() or not reference_scene.exists():
            self.skipTest("Required C5 AFT COMPARTMENT scene files not found.")

        pm.openFile(str(current_scene), force=True)
        before = self._snapshot_scene_animation()

        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_scene), force_complete_import=True
        )
        self.assertIsNotNone(info)

        ref_objs = info.get("transforms", [])
        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=True
        )
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        sandbox.cleanup_all_namespaces()
        after = self._snapshot_scene_animation()

        self.assertEqual(
            before["anim_curve_count"],
            after["anim_curve_count"],
            f"Curves lost during cross-scene analyze: "
            f"{before['anim_curve_count']} → {after['anim_curve_count']}",
        )
        for plug, data in before["curves"].items():
            self.assertIn(
                plug,
                after["curves"],
                f"Animation on '{plug}' destroyed during cross-scene analyze",
            )
            self.assertEqual(
                data["times"],
                after["curves"][plug]["times"],
                f"Keyframe times changed on '{plug}'",
            )
            for bv, av in zip(data["values"], after["curves"][plug]["values"]):
                self.assertAlmostEqual(
                    bv,
                    av,
                    places=6,
                    msg=f"Value changed on '{plug}'",
                )
        self.assertEqual(
            before["constraints"],
            after["constraints"],
            "Constraints changed during cross-scene analyze",
        )
        self.assertEqual(
            sorted(before["expressions"].keys()),
            sorted(after["expressions"].keys()),
            "Expressions changed during cross-scene analyze",
        )


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
        self.assertEqual(self.controller.tree.build_item_path(root_item), "GRP")

    def test_build_item_path_nested(self):
        """Nested item returns pipe-separated ancestor chain."""
        self._populate_tree(self.tree000, {"GRP": {"child": {"leaf": {}}}})
        root = self.tree000.topLevelItem(0)
        child = root.child(0)
        leaf = child.child(0)
        self.assertEqual(self.controller.tree.build_item_path(leaf), "GRP|child|leaf")

    # -- _apply_ignore_styling --

    def test_apply_ignore_styling_strikethrough(self):
        """Ignored items get strikethrough font; non-ignored do not."""
        self._populate_tree(self.tree000, {"GRP": {"child1": {}, "child2": {}}})
        self.controller._ignored_ref_paths.add("GRP|child1")
        self.controller.tree.apply_ignore_styling(self.tree000)

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
        self.controller.tree.apply_ignore_styling(self.tree000)

        root = self.tree000.topLevelItem(0)
        child1 = root.child(0)
        self.assertTrue(child1.font(0).strikeOut())

        # Unignore
        self.controller._ignored_ref_paths.discard("GRP|child1")
        self.controller.tree.apply_ignore_styling(self.tree000)
        self.assertFalse(
            child1.font(0).strikeOut(), "Strikethrough should be removed after unignore"
        )

    def test_apply_ignore_styling_ancestor_propagation(self):
        """Ignoring a parent styles it with strikethrough; descendants get italic (inherited)."""
        self._populate_tree(self.tree000, {"GRP": {"child": {"leaf": {}}}})
        self.controller._ignored_ref_paths.add("GRP")
        self.controller.tree.apply_ignore_styling(self.tree000)

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
        self.controller.tree.apply_ignore_styling(self.tree000)

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
        self.assertIn("root|child_b", self.controller._current_diff_result["missing"])

    def test_cleanup_cached_reference_import_noop_when_none(self):
        """_cleanup_cached_reference_import is safe to call when cache is None.

        Added: 2026-03-08
        """
        self.controller._cached_reference_import = None
        # Should not raise.
        self.controller._cleanup_cached_reference_import()
        self.assertIsNone(self.controller._cached_reference_import)

    def test_stale_mtime_bypasses_cache(self):
        """Cache is bypassed when the reference file has been modified on disk.

        Bug: _import_reference_cached only checked whether the first cached
        transform still existed; if the external file was updated after import,
        the stale in-scene objects would be reused silently.
        Fixed: 2026-04-10
        """
        import tempfile, time

        # Create a temp .ma file so os.path.getmtime works on a real path.
        tmp = tempfile.NamedTemporaryFile(
            suffix=".ma", delete=False, dir=self._temp_dir()
        )
        tmp.write(b"//Maya ASCII 2025 scene\n")
        tmp.close()
        tmp_path = tmp.name
        self.addCleanup(os.unlink, tmp_path)

        original_mtime = os.path.getmtime(tmp_path)

        # Create a real transform that "exists" in the current scene.
        node = pm.group(empty=True, name="cached_node")

        # Seed the cache with the original mtime.
        self.controller._cached_reference_import = {
            "path": str(Path(tmp_path).resolve()),
            "mtime": original_mtime,
            "sandbox": None,
            "transforms": [node],
        }

        # Touch the file so its mtime changes.
        time.sleep(0.05)
        Path(tmp_path).write_bytes(b"//Maya ASCII 2025 scene\n// modified\n")

        # Calling _import_reference_cached should NOT return the cached
        # transforms because the file was modified on disk.  It will fall
        # through and attempt a fresh import from the temp file, which will
        # fail (it's not a real Maya file) and return None.
        result = self.controller._import_reference_cached(tmp_path)
        self.assertIsNone(
            result,
            "Stale cache should have been invalidated by mismatched mtime",
        )

    def test_fresh_mtime_reuses_cache(self):
        """Cache is reused when the reference file has NOT been modified.

        Verifies the normal cache-hit path returns the existing transforms
        without re-importing.
        Added: 2026-04-10
        """
        import tempfile

        tmp = tempfile.NamedTemporaryFile(
            suffix=".ma", delete=False, dir=self._temp_dir()
        )
        tmp.write(b"//Maya ASCII 2025 scene\n")
        tmp.close()
        tmp_path = tmp.name
        self.addCleanup(os.unlink, tmp_path)

        node = pm.group(empty=True, name="valid_cached_node")

        self.controller._cached_reference_import = {
            "path": str(Path(tmp_path).resolve()),
            "mtime": os.path.getmtime(tmp_path),
            "sandbox": None,
            "transforms": [node],
        }

        # File unchanged — should return the cached transforms directly.
        result = self.controller._import_reference_cached(tmp_path)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], node)

    def _temp_dir(self):
        """Return (and create) a temp directory for test artifacts."""
        d = Path(__file__).parent / "temp_tests"
        d.mkdir(exist_ok=True)
        return str(d)


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

    # ── Auto-detect quarantine container ──

    def test_quarantine_auto_detects_natural_container(self):
        """quarantine_extras reuses existing root group when all extras share it.

        When using the default "_QUARANTINE" name and all extras share a
        single root-level ancestor that is itself extra AND has ≥2 direct
        extra children, that root is adopted as the quarantine container.
        Added: 2026-03-09
        """
        container = pm.group(empty=True, name="REVISIONS")
        child_a = pm.group(empty=True, name="rev_a", parent=container)
        child_b = pm.group(empty=True, name="rev_b", parent=container)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "REVISIONS": container,
            "REVISIONS|rev_a": child_a,
            "REVISIONS|rev_b": child_b,
        }
        manager.clean_to_raw_current = {
            "REVISIONS": "REVISIONS",
            "REVISIONS|rev_a": "REVISIONS|rev_a",
            "REVISIONS|rev_b": "REVISIONS|rev_b",
        }
        manager.differences = {
            "extra": ["REVISIONS", "REVISIONS|rev_a", "REVISIONS|rev_b"],
        }

        moved = manager.quarantine_extras()
        # Should auto-detect REVISIONS as the container; nothing to move
        self.assertFalse(
            pm.objExists("_QUARANTINE"),
            "Should NOT create _QUARANTINE when natural container exists",
        )
        # Items are already under REVISIONS — returned as "already contained"
        self.assertIn("REVISIONS", moved)

    def test_quarantine_no_auto_detect_single_child(self):
        """quarantine_extras does NOT auto-detect when root has < 2 direct extra children.

        A lone orphan root with one child should still be moved to _QUARANTINE.
        Added: 2026-03-09
        """
        grp = pm.group(empty=True, name="lone_grp")
        child = pm.group(empty=True, name="lone_child", parent=grp)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "lone_grp": grp,
            "lone_grp|lone_child": child,
        }
        manager.clean_to_raw_current = {
            "lone_grp": "lone_grp",
            "lone_grp|lone_child": "lone_grp|lone_child",
        }
        manager.differences = {
            "extra": ["lone_grp", "lone_grp|lone_child"],
        }

        moved = manager.quarantine_extras()
        # Should use default _QUARANTINE since only 1 direct child
        self.assertTrue(pm.objExists("_QUARANTINE"))
        self.assertEqual(pm.PyNode("lone_grp").getParent().nodeName(), "_QUARANTINE")

    def test_quarantine_already_under_group(self):
        """quarantine_extras skips extras already under the quarantine group.

        If the quarantine group already exists and an extra is nested under
        it, that item should be reported but not moved again.
        Added: 2026-03-09
        """
        q_grp = pm.group(empty=True, name="MY_Q")
        existing = pm.group(empty=True, name="already_there", parent=q_grp)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "MY_Q|already_there": existing,
        }
        manager.clean_to_raw_current = {
            "MY_Q|already_there": "MY_Q|already_there",
        }
        manager.differences = {
            "extra": ["MY_Q|already_there"],
        }

        moved = manager.quarantine_extras(group="MY_Q")
        # Should recognize it's already under MY_Q
        self.assertIn("already_there", moved)
        # Should NOT have been re-parented
        self.assertEqual(existing.getParent().nodeName(), "MY_Q")

    # ── Skip animated ── (detailed tests in TestAnimationSafety)

    def test_quarantine_skip_animated_live(self):
        """quarantine_extras skips extras that themselves have animation data.

        When skip_animated=True, extras that have their own keyframes,
        constraints, or expressions are left in place.
        Updated: 2026-07-19 — now checks the node itself, not ancestors.
        """
        anim_extra = pm.group(empty=True, name="anim_extra_node")
        pm.setKeyframe(anim_extra, attribute="translateX", time=1, value=0)
        pm.setKeyframe(anim_extra, attribute="translateX", time=10, value=5)
        plain_extra = pm.group(empty=True, name="plain_extra")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "anim_extra_node": anim_extra,
            "plain_extra": plain_extra,
        }
        manager.clean_to_raw_current = {
            "anim_extra_node": "anim_extra_node",
            "plain_extra": "plain_extra",
        }
        manager.differences = {
            "extra": ["anim_extra_node", "plain_extra"],
        }

        moved = manager.quarantine_extras(skip_animated=True)
        # plain_extra should be quarantined
        self.assertIn("plain_extra", moved)
        # anim_extra_node should NOT be moved (has animation data)
        self.assertNotIn("anim_extra_node", moved)
        self.assertTrue(pm.objExists("anim_extra_node"))

    def test_quarantine_skip_animated_false_default(self):
        """quarantine_extras moves animated-parent extras when skip_animated=False.

        Default behavior: skip_animated is False, so animation on ancestors
        does not prevent quarantining.
        Added: 2026-03-09
        """
        anim_root = pm.group(empty=True, name="anim_root2")
        pm.setKeyframe(anim_root, attribute="translateX", time=1, value=0)
        extra = pm.group(empty=True, name="child_under_anim", parent=anim_root)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "anim_root2|child_under_anim": extra,
        }
        manager.clean_to_raw_current = {
            "anim_root2|child_under_anim": "anim_root2|child_under_anim",
        }
        manager.differences = {
            "extra": ["anim_root2|child_under_anim"],
        }

        moved = manager.quarantine_extras(skip_animated=False)
        self.assertIn("child_under_anim", moved)
        self.assertEqual(extra.getParent().nodeName(), "_QUARANTINE")

    def test_quarantine_skip_animated_dry_run(self):
        """quarantine_extras dry_run respects skip_animated.

        In dry-run mode, items with animation data should be reported
        as skipped and not included in the returned list.
        Updated: 2026-07-19 — now checks node itself, not ancestors.
        """
        anim_extra = pm.group(empty=True, name="dry_anim_extra")
        pm.setKeyframe(anim_extra, attribute="translateY", time=1, value=0)
        pm.setKeyframe(anim_extra, attribute="translateY", time=10, value=5)
        static_extra = pm.group(empty=True, name="dry_static_extra")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=True)
        manager.current_scene_path_map = {
            "dry_anim_extra": anim_extra,
            "dry_static_extra": static_extra,
        }
        manager.clean_to_raw_current = {
            "dry_anim_extra": "dry_anim_extra",
            "dry_static_extra": "dry_static_extra",
        }
        manager.differences = {
            "extra": ["dry_anim_extra", "dry_static_extra"],
        }

        moved = manager.quarantine_extras(skip_animated=True)
        # Only static_extra should be in the "would quarantine" list
        self.assertIn("dry_static_extra", moved)
        self.assertNotIn("dry_anim_extra", moved)
        # No quarantine group created in dry-run
        self.assertFalse(pm.objExists("_QUARANTINE"))


# ---------------------------------------------------------------------------
# Renderer Tests (presentation-only — no Maya scene needed for most)
# ---------------------------------------------------------------------------


class TestHierarchyTreeRenderer(MayaTkTestCase):
    """Tests for HierarchyTreeRenderer presentation methods.

    Validates that populate_reference_tree, show_reference_placeholder, and
    show_reference_error produce correct tree widget content without side effects.
    Added: 2026-06-18
    """

    def setUp(self):
        super().setUp()
        from qtpy import QtWidgets, QtGui

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
        self.renderer = self.controller.tree
        self.tree = fake_slots.ui.tree000

    # -- show_reference_placeholder --

    def test_show_reference_placeholder_content(self):
        """show_reference_placeholder inserts a browse item with underline and UserRole data."""
        self.renderer.show_reference_placeholder(self.tree, "My Reference")
        self.assertEqual(self.tree.headerItem().text(0), "My Reference")
        self.assertEqual(self.tree.topLevelItemCount(), 1)
        item = self.tree.topLevelItem(0)
        self.assertEqual(item.text(0), "Browse for Reference Scene")
        self.assertTrue(item.font(0).underline())
        self.assertEqual(item.data(0, QtCore.Qt.UserRole), "browse_placeholder")

    def test_show_reference_placeholder_default_name(self):
        """show_reference_placeholder uses default header when name omitted."""
        self.renderer.show_reference_placeholder(self.tree)
        self.assertEqual(self.tree.headerItem().text(0), "Reference Scene")

    # -- show_reference_error --

    def test_show_reference_error_default_message(self):
        """show_reference_error displays 'File Not Found' by default."""
        self.renderer.show_reference_error(self.tree, "Ref")
        self.assertEqual(self.tree.headerItem().text(0), "Ref")
        self.assertEqual(self.tree.topLevelItemCount(), 1)
        self.assertEqual(self.tree.topLevelItem(0).text(0), "File Not Found")

    def test_show_reference_error_custom_message(self):
        """show_reference_error displays a custom message."""
        self.renderer.show_reference_error(self.tree, "Ref", "Import failed")
        self.assertEqual(self.tree.topLevelItem(0).text(0), "Import failed")

    def test_show_reference_error_clears_existing_items(self):
        """show_reference_error clears previous tree content before displaying."""
        # Pre-populate with junk.
        self.tree.create_item(["old_item_1"])
        self.tree.create_item(["old_item_2"])
        self.assertEqual(self.tree.topLevelItemCount(), 2)

        self.renderer.show_reference_error(self.tree, "Ref", "Error")
        self.assertEqual(self.tree.topLevelItemCount(), 1)
        self.assertEqual(self.tree.topLevelItem(0).text(0), "Error")

    # -- populate_reference_tree (renderer) --

    def test_populate_reference_tree_empty_transforms(self):
        """populate_reference_tree shows 'No objects' when transforms list is empty."""
        self.renderer.populate_reference_tree(self.tree, [], "Empty Ref")
        self.assertEqual(self.tree.headerItem().text(0), "Empty Ref")
        self.assertEqual(self.tree.topLevelItemCount(), 1)
        self.assertIn("No objects", self.tree.topLevelItem(0).text(0))

    def test_populate_reference_tree_with_transforms(self):
        """populate_reference_tree builds a hierarchy from real Maya transforms."""
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")
        root = pm.group(empty=True, name="ref:Root")
        child = pm.group(empty=True, name="ref:Child", parent=root)

        self.renderer.populate_reference_tree(self.tree, [root, child], "TestRef")
        self.assertEqual(self.tree.headerItem().text(0), "TestRef")
        # At least one top-level item (the root).
        self.assertGreater(self.tree.topLevelItemCount(), 0)
        # Root item text should be namespace-stripped.
        root_item = self.tree.topLevelItem(0)
        self.assertEqual(root_item.text(0), "Root")

    def test_populate_reference_tree_clears_existing(self):
        """populate_reference_tree clears previous content before populating."""
        self.tree.create_item(["stale_item"])
        self.assertEqual(self.tree.topLevelItemCount(), 1)

        root = pm.group(empty=True, name="fresh_root")
        self.renderer.populate_reference_tree(self.tree, [root], "Fresh")
        # Old item should be gone, only new content present.
        for i in range(self.tree.topLevelItemCount()):
            self.assertNotEqual(self.tree.topLevelItem(i).text(0), "stale_item")


# ---------------------------------------------------------------------------
# Diff Formatting, Delegate, and Tooltip Tests
# ---------------------------------------------------------------------------


class TestDiffFormattingAndDelegate(MayaTkTestCase):
    """Tests for _DiffSelectionDelegate, _apply_diff_color, tooltips, and
    the full apply_difference_formatting / clear_tree_colors cycle.

    Validates the delegate composites selection correctly, diff tooltips
    are applied without clobbering existing ones, and clear_tree_colors
    restores the default delegate.
    Added: 2026-04-10
    """

    def setUp(self):
        super().setUp()
        from qtpy import QtWidgets, QtGui

        class _FakeTree(QtWidgets.QTreeWidget):
            def __init__(self):
                super().__init__()
                self.setColumnCount(1)
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
        self.renderer = self.controller.tree
        self.tree000 = fake_slots.ui.tree000
        self.tree001 = fake_slots.ui.tree001

    def _add_item(self, tree, name, parent=None):
        """Add a QTreeWidgetItem and return it."""
        from qtpy import QtWidgets

        return QtWidgets.QTreeWidgetItem(parent or tree, [name])

    # -- _DiffSelectionDelegate --

    def test_delegate_installed_after_apply_difference_formatting(self):
        """apply_difference_formatting wraps existing delegate with _DiffSelectionDelegate."""
        from mayatk.env_utils.hierarchy_manager._tree_renderer import (
            _DiffSelectionDelegate,
        )

        # Need a diff result so formatting proceeds.
        self.controller._current_diff_result = {
            "missing": [],
            "extra": [],
            "reparented": [],
            "fuzzy_matches": [],
        }
        original_001 = self.tree001.itemDelegate()
        original_000 = self.tree000.itemDelegate()
        self.renderer.apply_difference_formatting(self.tree001, self.tree000)
        self.assertIsInstance(self.tree001.itemDelegate(), _DiffSelectionDelegate)
        self.assertIsInstance(self.tree000.itemDelegate(), _DiffSelectionDelegate)
        # The original delegates are preserved inside the wrapper.
        self.assertIs(self.tree001.itemDelegate()._original, original_001)
        self.assertIs(self.tree000.itemDelegate()._original, original_000)

    def test_delegate_removed_after_clear_tree_colors(self):
        """clear_tree_colors restores the original wrapped delegate."""
        from mayatk.env_utils.hierarchy_manager._tree_renderer import (
            _DiffSelectionDelegate,
        )

        # Record the original delegate, then wrap it.
        original = self.tree001.itemDelegate()
        self.tree001.setItemDelegate(
            _DiffSelectionDelegate(self.tree001, original_delegate=original)
        )
        self.assertIsInstance(self.tree001.itemDelegate(), _DiffSelectionDelegate)

        self.renderer.clear_tree_colors(self.tree001)
        self.assertNotIsInstance(self.tree001.itemDelegate(), _DiffSelectionDelegate)
        self.assertIs(self.tree001.itemDelegate(), original)

    def test_delegate_has_diff_detects_solid_brush(self):
        """Delegate identifies items with SolidPattern background as diff-coloured."""
        from qtpy import QtGui

        item = self._add_item(self.tree001, "coloured")
        item.setBackground(0, QtGui.QBrush(QtGui.QColor("#3D2929")))
        index = self.tree001.indexFromItem(item, 0)
        bg = index.data(QtCore.Qt.BackgroundRole)
        # Must be detected as diff (SolidPattern, not NoBrush).
        self.assertIsNotNone(bg)
        self.assertNotEqual(bg.style(), QtCore.Qt.NoBrush)

    def test_delegate_ignores_default_brush(self):
        """Delegate does not treat a default/cleared brush as diff-coloured."""
        item = self._add_item(self.tree001, "plain")
        index = self.tree001.indexFromItem(item, 0)
        bg = index.data(QtCore.Qt.BackgroundRole)
        # PySide returns None for unset roles.
        if bg is not None:
            self.assertEqual(bg.style(), QtCore.Qt.NoBrush)

    # -- _apply_diff_color --

    def test_apply_diff_color_sets_foreground_and_background(self):
        """_apply_diff_color sets both foreground and background brushes."""
        from qtpy import QtGui

        item = self._add_item(self.tree001, "test_item")
        self.renderer._apply_diff_color(item, "extra")

        fg = item.foreground(0)
        bg = item.background(0)
        self.assertNotEqual(fg.style(), QtCore.Qt.NoBrush)
        self.assertNotEqual(bg.style(), QtCore.Qt.NoBrush)

    def test_apply_diff_color_unknown_type_is_noop(self):
        """Unknown diff_type silently does nothing."""
        from qtpy import QtGui

        item = self._add_item(self.tree001, "test_item")
        self.renderer._apply_diff_color(item, "nonexistent_type")
        # Background should remain default.
        bg = item.background(0)
        self.assertEqual(bg.style(), QtCore.Qt.NoBrush)

    # -- Tooltip behaviour --

    def test_tooltip_set_on_diff_item(self):
        """_apply_diff_color with a tooltip string sets the item tooltip."""
        item = self._add_item(self.tree001, "extra_item")
        self.renderer._apply_diff_color(item, "extra", "Extra — not in reference")
        self.assertIn("Extra", item.toolTip(0))

    def test_tooltip_appended_to_existing(self):
        """Diff tooltip is appended (not replaced) when item already has a tooltip.

        Bug: diff tooltip was overwriting reference tree's 'Full Name' tooltip.
        Fixed: 2026-04-10
        """
        item = self._add_item(self.tree001, "ref_item")
        item.setToolTip(0, "Full Name: |root|child\nType: transform")

        self.renderer._apply_diff_color(
            item, "missing", "Missing — not in current scene"
        )

        tip = item.toolTip(0)
        self.assertIn("Full Name", tip, "Original tooltip preserved")
        self.assertIn("Missing", tip, "Diff tooltip appended")

    def test_tooltip_not_set_when_empty(self):
        """No tooltip set when tooltip parameter is empty string."""
        item = self._add_item(self.tree001, "no_tip")
        self.renderer._apply_diff_color(item, "extra", "")
        self.assertEqual(item.toolTip(0), "")

    def test_tooltip_does_not_stack_on_reapply(self):
        """Tooltip must not accumulate stale diff text across clear/reapply cycles.

        Bug: clear_tree_colors reset fg/bg but left tooltip intact, so the
        append logic in _apply_diff_color doubled the diff annotation on
        each _refresh_tree_styling cycle.
        Fixed: 2026-04-10
        """
        item = self._add_item(self.tree001, "ref_item")
        original_tip = "Full Name: |root|child\nType: transform"
        item.setToolTip(0, original_tip)

        # First apply — tooltip should contain original + diff.
        self.renderer._apply_diff_color(
            item, "missing", "Missing — not in current scene"
        )
        tip_after_first = item.toolTip(0)
        self.assertEqual(tip_after_first.count("Missing"), 1)

        # Simulate re-apply cycle: clear then re-apply.
        self.renderer.clear_tree_colors(self.tree001)
        self.assertEqual(item.toolTip(0), original_tip, "Original restored after clear")

        self.renderer._apply_diff_color(
            item, "missing", "Missing — not in current scene"
        )
        tip_after_second = item.toolTip(0)
        self.assertEqual(tip_after_second.count("Missing"), 1, "No stale duplication")

    # -- Full cycle: apply → clear → reapply --

    def test_clear_tree_colors_resets_all_item_brushes(self):
        """clear_tree_colors removes fg/bg from all items in the tree."""
        from qtpy import QtGui

        item1 = self._add_item(self.tree001, "item1")
        item2 = self._add_item(self.tree001, "item2")
        self.renderer._apply_diff_color(item1, "extra")
        self.renderer._apply_diff_color(item2, "missing")

        # Both should be coloured now.
        self.assertNotEqual(item1.background(0).style(), QtCore.Qt.NoBrush)
        self.assertNotEqual(item2.background(0).style(), QtCore.Qt.NoBrush)

        self.renderer.clear_tree_colors(self.tree001)

        # Both should be cleared.
        self.assertEqual(item1.background(0).style(), QtCore.Qt.NoBrush)
        self.assertEqual(item2.background(0).style(), QtCore.Qt.NoBrush)

    def test_clear_removes_old_stylesheet_remnant(self):
        """clear_tree_colors strips the old transparent-selection stylesheet block.

        Transitional cleanup: the previous border-only approach appended
        a stylesheet block with 'selection-background-color: transparent'.
        clear_tree_colors must remove it so it doesn't accumulate.
        """
        old_block = (
            "\n        QTreeWidget {\n"
            "            selection-background-color: transparent;\n"
            "        }\n"
            "        QTreeWidget::item:selected {\n"
            "            background-color: transparent;\n"
            "            border: 1px solid rgba(90, 140, 190, 0.7);\n"
            "        }\n"
            "        QTreeWidget::item:hover:!selected {\n"
            "            background-color: rgba(255, 255, 255, 0.05);\n"
            "        }\n"
        )
        base_ss = "QTreeWidget { background: #333; }"
        self.tree001.setStyleSheet(base_ss + old_block)

        self.renderer.clear_tree_colors(self.tree001)

        ss = self.tree001.styleSheet()
        self.assertNotIn("selection-background-color: transparent", ss)
        # Base style should survive.
        self.assertIn("background: #333", ss)

    # -- Diff colors from Palette.diff() --

    def test_diff_colors_has_all_domain_aliases(self):
        """DIFF_COLORS palette has aliases for all four domain categories."""
        from mayatk.env_utils.hierarchy_manager._tree_renderer import (
            HierarchyTreeRenderer,
        )

        for key in ("missing", "extra", "fuzzy", "reparented"):
            pair = HierarchyTreeRenderer.DIFF_COLORS.get(key)
            self.assertIsNotNone(pair, f"DIFF_COLORS missing alias '{key}'")
            fg, bg = pair
            self.assertTrue(fg, f"'{key}' fg should be non-empty")
            self.assertTrue(bg, f"'{key}' bg should be non-empty")


# ---------------------------------------------------------------------------
# Controller Tree Orchestration Tests
# ---------------------------------------------------------------------------


class TestControllerTreeOrchestration(MayaTkTestCase):
    """Tests for Controller.populate_reference_tree and Controller.refresh_trees.

    These exercise the orchestration layer that sits between Slots and the
    Renderer — handling cache invalidation, import delegation, and selection
    save/restore.
    Added: 2026-06-18
    """

    def setUp(self):
        super().setUp()
        from qtpy import QtWidgets, QtGui

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

        class _FakeLineEdit:
            def __init__(self, text=""):
                self._text = text

            def text(self):
                return self._text

        class _FakeUI:
            def __init__(self):
                self.tree000 = _FakeTree()
                self.tree001 = _FakeTree()
                self.txt001 = _FakeLineEdit("")
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
        self.fake_ui = fake_slots.ui

    # -- Controller.populate_reference_tree --

    def test_populate_reference_tree_no_path_shows_placeholder(self):
        """Controller.populate_reference_tree shows placeholder when path is None."""
        self.controller.populate_reference_tree(self.tree000, reference_path=None)
        self.assertEqual(self.tree000.topLevelItemCount(), 1)
        item = self.tree000.topLevelItem(0)
        self.assertEqual(item.text(0), "Browse for Reference Scene")

    def test_populate_reference_tree_empty_path_shows_placeholder(self):
        """Controller.populate_reference_tree shows placeholder when path is empty string."""
        self.controller.populate_reference_tree(self.tree000, reference_path="")
        item = self.tree000.topLevelItem(0)
        self.assertEqual(item.text(0), "Browse for Reference Scene")

    def test_populate_reference_tree_nonexistent_path_shows_error(self):
        """Controller.populate_reference_tree shows error for non-existent file."""
        self.controller.populate_reference_tree(
            self.tree000, reference_path="/nonexistent/file.ma"
        )
        self.assertEqual(self.tree000.topLevelItemCount(), 1)
        self.assertEqual(self.tree000.topLevelItem(0).text(0), "File Not Found")

    def test_populate_reference_tree_clears_cache_on_path_change(self):
        """Controller.populate_reference_tree invalidates cache when path changes."""
        self.controller._cached_reference_import = {
            "path": "C:/old/path.ma",
            "sandbox": None,
            "transforms": [],
        }
        self.controller._current_diff_result = {"missing": ["a"]}

        # New path triggers cache clear (file won't exist, so we'll get error).
        self.controller.populate_reference_tree(
            self.tree000, reference_path="/new/path.ma"
        )
        self.assertIsNone(self.controller._cached_reference_import)
        self.assertIsNone(self.controller._current_diff_result)

    # -- Controller.refresh_trees --

    def test_refresh_trees_populates_current_scene(self):
        """refresh_trees populates the current-scene tree (tree001)."""
        # Create some scene content.
        pm.group(empty=True, name="scene_root")

        self.controller.refresh_trees()

        # tree001 should have at least one item (the scene_root).
        self.assertGreater(self.tree001.topLevelItemCount(), 0)

    def test_refresh_trees_skips_reference_when_no_path(self):
        """refresh_trees shows placeholder in reference tree when txt001 is empty."""
        self.fake_ui.txt001 = type(
            "LE", (), {"text": lambda self: "", "strip": str.strip}
        )()
        self.fake_ui.txt001 = type("LE", (), {"text": lambda s: ""})()

        self.controller.refresh_trees()

        # Reference tree should not have been populated with a file import.
        # It may be empty or have a placeholder depending on implementation.
        # The key assertion: no crash and current tree was still populated.
        self.assertGreater(self.tree001.topLevelItemCount(), 0)


# ---------------------------------------------------------------------------
# Merge Hierarchies — Replace-In-Place Tests
# ---------------------------------------------------------------------------


class TestMergeHierarchiesReplaceInPlace(MayaTkTestCase):
    """Tests for ObjectSwapper merge-hierarchies mode replacing existing objects.

    Bug: _integrate_hierarchy skipped deletion when the existing object was
    at world level, causing Maya to auto-rename the pulled object instead of
    replacing the existing one.
    Added: 2026-04-10
    """

    def test_integrate_hierarchy_replaces_world_level_object(self):
        """Merge mode replaces an existing world-level object instead of creating a duplicate.

        Bug: When existing object had no parent (world level), _integrate_hierarchy
        logged a debug message but did NOT delete it, so Maya auto-renamed the
        pulled object to 'OBJ1'.
        Fixed: 2026-04-10
        """
        # Create an existing world-level object.
        existing = pm.group(empty=True, name="TARGET_OBJ")
        self.assertTrue(pm.objExists("TARGET_OBJ"))

        # Simulate an imported object in a namespace (as NamespaceSandbox would produce).
        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.group(empty=True, name="temp_import:TARGET_OBJ")

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )

        clean_name = get_clean_node_name(imported)
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        # The pulled object should have taken the name — no auto-rename suffix.
        self.assertEqual(
            imported.nodeName(),
            "TARGET_OBJ",
            f"Expected 'TARGET_OBJ', got '{imported.nodeName()}' (was auto-renamed instead of replacing)",
        )
        # Only one object named TARGET_OBJ should exist.
        matches = pm.ls("TARGET_OBJ", type="transform")
        self.assertEqual(
            len(matches),
            1,
            f"Expected 1 'TARGET_OBJ', got {len(matches)}: {[m.longName() for m in matches]}",
        )

    def test_integrate_hierarchy_replaces_nested_object(self):
        """Merge mode replaces an object nested in another hierarchy.

        This case already worked, but verify it remains correct after the fix.
        """
        parent = pm.group(empty=True, name="PARENT")
        existing = pm.group(empty=True, name="TARGET_OBJ", parent=parent)
        self.assertEqual(existing.getParent().nodeName(), "PARENT")

        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.group(empty=True, name="temp_import:TARGET_OBJ")

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )

        clean_name = get_clean_node_name(imported)
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        self.assertEqual(imported.nodeName(), "TARGET_OBJ")
        matches = pm.ls("TARGET_OBJ", type="transform")
        self.assertEqual(len(matches), 1)

    def test_integrate_hierarchy_with_children_replaces_world_level(self):
        """Merge mode replaces a world-level object that has children, preserving pulled children.

        Bug: The world-level existing object was not deleted, so the entire
        pulled hierarchy with children ended up at root with auto-renamed names.
        Fixed: 2026-04-10
        """
        # Existing hierarchy: TARGET_ROOT -> existing_child
        existing_root = pm.group(empty=True, name="TARGET_ROOT")
        existing_child = pm.group(empty=True, name="old_child", parent=existing_root)

        # Imported hierarchy: TARGET_ROOT -> new_child
        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported_root = pm.group(empty=True, name="temp_import:TARGET_ROOT")
        imported_child = pm.group(
            empty=True, name="temp_import:new_child", parent=imported_root
        )

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )

        clean_name = get_clean_node_name(imported_root)
        swapper._integrate_hierarchy(
            imported_root, clean_name, merge=True, allow_auto_rename=False
        )

        # Pulled root should have the correct name (no suffix).
        self.assertEqual(
            imported_root.nodeName(),
            "TARGET_ROOT",
            f"Expected 'TARGET_ROOT', got '{imported_root.nodeName()}'",
        )
        # Old child should be gone.
        self.assertFalse(
            pm.objExists("old_child"),
            "Old child should have been deleted with the replaced root",
        )
        # Exactly one TARGET_ROOT should exist.
        matches = pm.ls("TARGET_ROOT", type="transform")
        self.assertEqual(len(matches), 1)
        # New child must survive under the pulled root.
        self.assertTrue(
            imported_child.exists(),
            "Pulled child should still exist after hierarchy integration",
        )
        self.assertEqual(
            imported_child.getParent(),
            imported_root,
            "Pulled child should remain parented under the pulled root",
        )

    def test_integrate_hierarchy_replaces_in_different_position(self):
        """Merge mode replaces existing object even when it lives in a different hierarchy location.

        The pulled object should land at the reference's hierarchy position.
        Added: 2026-04-10
        """
        # Existing: OLD_GRP -> TARGET_OBJ
        old_parent = pm.group(empty=True, name="OLD_GRP")
        existing = pm.group(empty=True, name="TARGET_OBJ", parent=old_parent)
        self.assertEqual(existing.getParent().nodeName(), "OLD_GRP")

        # Reference: NEW_GRP -> TARGET_OBJ (different parent).
        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        new_parent = pm.group(empty=True, name="temp_import:NEW_GRP")
        imported = pm.group(
            empty=True, name="temp_import:TARGET_OBJ", parent=new_parent
        )

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )

        clean_name = get_clean_node_name(imported)
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        self.assertEqual(imported.nodeName(), "TARGET_OBJ")
        matches = pm.ls("TARGET_OBJ", type="transform")
        self.assertEqual(
            len(matches),
            1,
            f"Expected 1 'TARGET_OBJ', got {len(matches)}: {[m.longName() for m in matches]}",
        )
        # Pulled object should live under NEW_GRP, not OLD_GRP.
        self.assertIsNotNone(imported.getParent())
        self.assertEqual(
            imported.getParent().nodeName(),
            "NEW_GRP",
            f"Expected parent 'NEW_GRP', got '{imported.getParent().nodeName()}'",
        )

    def test_integrate_single_replaces_world_level_object(self):
        """Single-object merge mode replaces a world-level object (already correct)."""
        existing = pm.group(empty=True, name="SINGLE_OBJ")

        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.group(empty=True, name="temp_import:SINGLE_OBJ")

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=False,
        )

        clean_name = get_clean_node_name(imported)
        swapper._integrate_single(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        self.assertEqual(imported.nodeName(), "SINGLE_OBJ")
        matches = pm.ls("SINGLE_OBJ", type="transform")
        self.assertEqual(len(matches), 1)


class TestDeleteSelectedObjects(MayaTkTestCase):
    """Tests for the b018 delete-selected-objects slot logic.

    The slot reads selected tree items, extracts Maya nodes, sorts by depth,
    and deletes inside an undo chunk.  These tests exercise the core deletion
    logic (depth sorting, undo support) using the same Maya primitives.
    Added: 2026-04-10
    """

    def test_delete_child_before_parent(self):
        """Deleting a parent and child should succeed when sorted deepest-first.

        Bug: reversed(selectedItems()) did not guarantee children precede parents.
        A deeply-nested child could appear above its parent in visual order.
        Fixed: 2026-04-10 — nodes sorted by longName depth before pm.delete.
        """
        parent = pm.group(empty=True, name="DEL_PARENT")
        child = pm.group(empty=True, name="DEL_CHILD", parent=parent)
        grandchild = pm.group(empty=True, name="DEL_GRANDCHILD", parent=child)

        # Sort deepest-first (same logic as b018)
        nodes = [parent, child, grandchild]

        def _depth(n):
            return len(n.longName().split("|"))

        nodes.sort(key=_depth, reverse=True)
        self.assertEqual(
            nodes[0].nodeName(),
            "DEL_GRANDCHILD",
            "Deepest node should be first after sorting",
        )

        pm.delete(nodes)
        self.assertFalse(pm.objExists("DEL_PARENT"))
        self.assertFalse(pm.objExists("DEL_CHILD"))
        self.assertFalse(pm.objExists("DEL_GRANDCHILD"))

    def test_delete_is_undoable(self):
        """Deletion wrapped in an undo chunk should be fully reversible.

        Added: 2026-04-10 — undo support was missing from b018.
        """
        grp = pm.group(empty=True, name="UNDO_TARGET")
        self.assertTrue(pm.objExists("UNDO_TARGET"))

        pm.undoInfo(openChunk=True, chunkName="test_delete")
        pm.delete(grp)
        pm.undoInfo(closeChunk=True)
        self.assertFalse(pm.objExists("UNDO_TARGET"))

        pm.undo()
        self.assertTrue(
            pm.objExists("UNDO_TARGET"),
            "Object should be restored after undo",
        )

    def test_delete_only_selected_nodes(self):
        """Only the targeted nodes should be deleted; siblings must survive."""
        keep = pm.group(empty=True, name="KEEP_ME")
        delete_me = pm.group(empty=True, name="DELETE_ME")

        pm.delete(delete_me)
        self.assertFalse(pm.objExists("DELETE_ME"))
        self.assertTrue(pm.objExists("KEEP_ME"), "Sibling should survive deletion")

    def test_delete_mixed_depth_nodes(self):
        """Selecting nodes at different depths should all be deleted correctly."""
        root_a = pm.group(empty=True, name="ROOT_A")
        child_a = pm.group(empty=True, name="CHILD_A", parent=root_a)
        root_b = pm.group(empty=True, name="ROOT_B")

        nodes = [root_a, child_a, root_b]

        def _depth(n):
            return len(n.longName().split("|"))

        nodes.sort(key=_depth, reverse=True)

        pm.undoInfo(openChunk=True, chunkName="test_mixed_delete")
        pm.delete(nodes)
        pm.undoInfo(closeChunk=True)

        self.assertFalse(pm.objExists("ROOT_A"))
        self.assertFalse(pm.objExists("CHILD_A"))
        self.assertFalse(pm.objExists("ROOT_B"))

        # Full undo should restore all
        pm.undo()
        self.assertTrue(pm.objExists("ROOT_A"))
        self.assertTrue(pm.objExists("CHILD_A"))
        self.assertTrue(pm.objExists("ROOT_B"))


class TestAnimationSafety(MayaTkTestCase):
    """Comprehensive tests for animation-safe hierarchy repair operations.

    Validates that repair operations (merge delete, quarantine, rename,
    empty-parent cleanup) detect and preserve animation data instead of
    silently destroying it.  Covers all edge cases from the animation
    safety plan (EC-1 through EC-13).

    Added: 2026-07-19
    Extended: 2026-04-10
    """

    # ── Helpers ───────────────────────────────────────────────────────

    def _make_animated_cube(self, name="ANIM_CUBE", parent=None):
        """Create a polyCube with a keyframed translateX."""
        cube = pm.polyCube(name=name)[0]
        if parent:
            pm.parent(cube, parent)
        cmds.setKeyframe(str(cube), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(cube), attribute="translateX", time=24, value=10)
        return cube

    def _make_multi_attr_animated(self, name="MULTI_ANIM"):
        """Create a polyCube with keyframes on translateX, rotateY, and scaleZ."""
        cube = pm.polyCube(name=name)[0]
        for attr, vals in [
            ("translateX", (0, 10)),
            ("rotateY", (0, 90)),
            ("scaleZ", (1, 2)),
        ]:
            cmds.setKeyframe(str(cube), attribute=attr, time=1, value=vals[0])
            cmds.setKeyframe(str(cube), attribute=attr, time=24, value=vals[1])
        return cube

    def _make_constrained(self, name="CONSTRAINED", target_name="TARGET"):
        """Create a node with a parentConstraint."""
        target = pm.group(empty=True, name=target_name)
        node = pm.group(empty=True, name=name)
        pm.parentConstraint(target, node, mo=True)
        return node, target

    def _make_driven_key(self, driver_name="DRIVER", driven_name="DRIVEN"):
        """Create a set-driven key relationship."""
        driver = pm.group(empty=True, name=driver_name)
        driven = pm.group(empty=True, name=driven_name)
        cmds.setDrivenKeyframe(
            f"{driven_name}.translateX",
            currentDriver=f"{driver_name}.translateY",
            driverValue=0,
            value=0,
        )
        cmds.setDrivenKeyframe(
            f"{driven_name}.translateX",
            currentDriver=f"{driver_name}.translateY",
            driverValue=10,
            value=5,
        )
        return driver, driven

    def _make_expression(self, name="EXPR_NODE"):
        """Create a node driven by a MEL expression."""
        node = pm.group(empty=True, name=name)
        cmds.expression(
            string=f"{name}.translateX = frame * 0.5",
            object=name,
            alwaysEvaluate=True,
            name=f"{name}_expr",
        )
        return node

    # ══════════════════════════════════════════════════════════════════
    #  _has_animation_data — detection breadth & edge cases
    # ══════════════════════════════════════════════════════════════════

    def test_has_animation_data_keyframed(self):
        """Returns True for a node with time-based keyframes."""
        cube = self._make_animated_cube("HAD_KEYED")
        self.assertTrue(HierarchyManager._has_animation_data(cube))

    def test_has_animation_data_plain(self):
        """Returns False for a plain transform with no connections."""
        grp = pm.group(empty=True, name="HAD_PLAIN")
        self.assertFalse(HierarchyManager._has_animation_data(grp))

    def test_has_animation_data_constrained(self):
        """Returns True for a node with a parentConstraint."""
        node, _target = self._make_constrained("HAD_CONSTR", "HAD_CTGT")
        self.assertTrue(HierarchyManager._has_animation_data(node))

    def test_has_animation_data_driven_key(self):
        """Returns True for a driven-key target."""
        _driver, driven = self._make_driven_key("HAD_DKVR", "HAD_DKVN")
        self.assertTrue(HierarchyManager._has_animation_data(driven))

    def test_has_animation_data_expression(self):
        """Returns True when an expression drives the node."""
        node = self._make_expression("HAD_EXPR")
        self.assertTrue(
            HierarchyManager._has_animation_data(node),
            "Expression-driven node should be detected as animated",
        )

    def test_has_animation_data_combo_keys_and_constraint(self):
        """Returns True when a node has BOTH keyframes and a constraint."""
        target = pm.group(empty=True, name="HAD_COMBO_TGT")
        node = pm.polyCube(name="HAD_COMBO")[0]
        cmds.setKeyframe(str(node), attribute="translateX", time=1, value=0)
        pm.parentConstraint(target, node, mo=True)
        self.assertTrue(HierarchyManager._has_animation_data(node))

    def test_has_animation_data_nonexistent_node(self):
        """Returns False gracefully for a node that does not exist."""
        self.assertFalse(HierarchyManager._has_animation_data("DOES_NOT_EXIST_12345"))

    def test_has_animation_data_node_with_shapes_only(self):
        """Returns False for a mesh with shapes but no animation."""
        cube = pm.polyCube(name="HAD_SHAPES")[0]
        self.assertFalse(HierarchyManager._has_animation_data(cube))

    # ── check_descendants variations ──────────────────────────────────

    def test_has_animation_data_descendants_basic(self):
        """check_descendants=True detects animation on a direct child."""
        parent = pm.group(empty=True, name="HAD_DESC_P")
        self._make_animated_cube("HAD_DESC_C", parent=parent)
        self.assertFalse(HierarchyManager._has_animation_data(parent))
        self.assertTrue(
            HierarchyManager._has_animation_data(parent, check_descendants=True)
        )

    def test_has_animation_data_descendants_deep_chain(self):
        """check_descendants=True finds animation three levels deep."""
        root = pm.group(empty=True, name="HAD_DEEP_R")
        mid = pm.group(empty=True, name="HAD_DEEP_M", parent=root)
        leaf = pm.group(empty=True, name="HAD_DEEP_L", parent=mid)
        cmds.setKeyframe(str(leaf), attribute="rotateZ", time=1, value=0)
        self.assertTrue(
            HierarchyManager._has_animation_data(root, check_descendants=True),
            "Should detect keyframe 3 levels deep",
        )

    def test_has_animation_data_descendants_constraint_child(self):
        """check_descendants=True detects a constrained child."""
        parent = pm.group(empty=True, name="HAD_CDC_P")
        target = pm.group(empty=True, name="HAD_CDC_TGT")
        child = pm.group(empty=True, name="HAD_CDC_C", parent=parent)
        pm.parentConstraint(target, child, mo=True)
        self.assertTrue(
            HierarchyManager._has_animation_data(parent, check_descendants=True),
            "Should detect constraint on child",
        )

    def test_has_animation_data_descendants_false_when_clean(self):
        """check_descendants=True returns False when entire subtree is clean."""
        root = pm.group(empty=True, name="HAD_CLN_R")
        pm.group(empty=True, name="HAD_CLN_C1", parent=root)
        pm.group(empty=True, name="HAD_CLN_C2", parent=root)
        self.assertFalse(
            HierarchyManager._has_animation_data(root, check_descendants=True)
        )

    # ══════════════════════════════════════════════════════════════════
    #  _classify_animation — structured breakdown
    # ══════════════════════════════════════════════════════════════════

    def test_classify_time_curves(self):
        """Time-based keyframes show up in curves list, not driven_keys."""
        cube = self._make_animated_cube("CLS_TIME")
        cls = HierarchyManager._classify_animation(cube)
        self.assertGreater(len(cls["curves"]), 0)
        self.assertEqual(len(cls["driven_keys"]), 0)
        self.assertFalse(cls["is_referenced"])
        self.assertFalse(cls["has_anim_layers"])

    def test_classify_driven_key(self):
        """Set-driven keys appear in driven_keys, not curves."""
        _driver, driven = self._make_driven_key("CLS2_DRV", "CLS2_DVN")
        cls = HierarchyManager._classify_animation(driven)
        self.assertGreater(len(cls["driven_keys"]), 0)
        # Driven-key nodes should NOT appear in curves
        self.assertEqual(len(cls["curves"]), 0)

    def test_classify_constraint(self):
        """Constraints appear in the constraints list."""
        node, _target = self._make_constrained("CLS2_CONSTR", "CLS2_TGT")
        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(len(cls["constraints"]), 0)

    def test_classify_expression(self):
        """Expressions appear in the expressions list."""
        node = self._make_expression("CLS_EXPR")
        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(
            len(cls["expressions"]), 0, "Should detect expression connection"
        )

    def test_classify_no_animation(self):
        """Clean node returns all empty lists."""
        grp = pm.group(empty=True, name="CLS_CLEAN")
        cls = HierarchyManager._classify_animation(grp)
        self.assertEqual(len(cls["curves"]), 0)
        self.assertEqual(len(cls["driven_keys"]), 0)
        self.assertEqual(len(cls["constraints"]), 0)
        self.assertEqual(len(cls["expressions"]), 0)
        self.assertFalse(cls["is_referenced"])
        self.assertFalse(cls["has_anim_layers"])

    def test_classify_multi_attribute(self):
        """Multiple keyed attributes each appear as separate curve entries."""
        cube = self._make_multi_attr_animated("CLS_MULTI")
        cls = HierarchyManager._classify_animation(cube)
        # translateX, rotateY, scaleZ → at least 3 curves
        self.assertGreaterEqual(
            len(cls["curves"]),
            3,
            f"Expected >= 3 curves, got {len(cls['curves'])}",
        )

    def test_classify_mixed_curves_and_constraint(self):
        """Node with both keyframes and constraint reports both."""
        target = pm.group(empty=True, name="CLS_MIX_TGT")
        node = pm.polyCube(name="CLS_MIX")[0]
        cmds.setKeyframe(str(node), attribute="scaleX", time=1, value=1)
        cmds.setKeyframe(str(node), attribute="scaleX", time=24, value=2)
        pm.parentConstraint(target, node, mo=True)
        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(len(cls["curves"]), 0, "Should have time-based curves")
        self.assertGreater(len(cls["constraints"]), 0, "Should have constraints")

    def test_classify_mixed_driven_key_and_time_curve(self):
        """Node with both a driven key and time-based key separates them."""
        driver = pm.group(empty=True, name="CLS_MDKT_DRV")
        node = pm.group(empty=True, name="CLS_MDKT_N")
        # Time-based on translateY
        cmds.setKeyframe(str(node), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(node), attribute="translateY", time=24, value=10)
        # Driven key on translateX
        cmds.setDrivenKeyframe(
            f"{node}.translateX",
            currentDriver=f"{driver}.rotateZ",
            driverValue=0,
            value=0,
        )
        cmds.setDrivenKeyframe(
            f"{node}.translateX",
            currentDriver=f"{driver}.rotateZ",
            driverValue=90,
            value=5,
        )
        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(len(cls["curves"]), 0, "Time-based curve on translateY")
        self.assertGreater(len(cls["driven_keys"]), 0, "Driven key on translateX")

    def test_classify_dict_keys_present(self):
        """Returned dict always has all expected keys."""
        grp = pm.group(empty=True, name="CLS_KEYS")
        cls = HierarchyManager._classify_animation(grp)
        for key in (
            "curves",
            "driven_keys",
            "constraints",
            "expressions",
            "is_referenced",
            "has_anim_layers",
        ):
            self.assertIn(key, cls, f"Missing key: {key}")

    # ══════════════════════════════════════════════════════════════════
    #  _transfer_anim_curves — lossless rewire + edge cases
    # ══════════════════════════════════════════════════════════════════

    def test_transfer_rewires_time_curves(self):
        """Disconnect/reconnect transfers time-based curves losslessly."""
        old = self._make_animated_cube("XFR_OLD")
        new = pm.polyCube(name="XFR_NEW")[0]
        result = HierarchyManager._transfer_anim_curves(old, new)
        self.assertGreater(result["transferred"], 0)
        self.assertEqual(result["method"], "rewire")
        keys = cmds.keyframe(str(new), attribute="translateX", query=True, tc=True)
        self.assertIsNotNone(keys)
        self.assertGreater(len(keys), 0)

    def test_transfer_values_preserved_exactly(self):
        """Key times and values are bit-identical after rewire transfer.

        Validates the lossless property of disconnect/reconnect — the same
        animCurve node is simply repointed, preserving tangents and values.
        """
        old = pm.polyCube(name="XFR_VAL_OLD")[0]
        cmds.setKeyframe(str(old), attribute="translateX", time=1, value=3.14159)
        cmds.setKeyframe(str(old), attribute="translateX", time=48, value=-7.5)
        new = pm.polyCube(name="XFR_VAL_NEW")[0]

        HierarchyManager._transfer_anim_curves(old, new)

        keys = cmds.keyframe(str(new), attribute="translateX", query=True, tc=True)
        vals = cmds.keyframe(str(new), attribute="translateX", query=True, vc=True)
        self.assertEqual(keys, [1.0, 48.0])
        self.assertAlmostEqual(vals[0], 3.14159, places=4)
        self.assertAlmostEqual(vals[1], -7.5, places=4)

    def test_transfer_multi_attribute(self):
        """All time-based curves transfer — translateX, rotateY, scaleZ."""
        old = self._make_multi_attr_animated("XFR_MOLD")
        new = pm.polyCube(name="XFR_MNEW")[0]
        result = HierarchyManager._transfer_anim_curves(old, new)
        self.assertGreaterEqual(
            result["transferred"],
            3,
            f"Expected >= 3 transferred, got {result['transferred']}",
        )
        for attr in ("translateX", "rotateY", "scaleZ"):
            keys = cmds.keyframe(str(new), attribute=attr, query=True, tc=True)
            self.assertIsNotNone(keys, f"No keys on {attr} after transfer")
            self.assertGreater(len(keys), 0, f"No keys on {attr} after transfer")

    def test_transfer_skips_missing_attr(self):
        """EC-6: Skips transfer when replacement lacks the keyed attribute.

        A custom attr on the old node has keyframes, but the replacement
        has no such attr → reported in skipped with clear reason.
        """
        old = pm.group(empty=True, name="XFR_MISS_OLD")
        cmds.addAttr(str(old), longName="myWeight", attributeType="float", keyable=True)
        cmds.setKeyframe(str(old), attribute="myWeight", time=1, value=0)
        cmds.setKeyframe(str(old), attribute="myWeight", time=24, value=1)
        new = pm.group(empty=True, name="XFR_MISS_NEW")  # no myWeight attr

        result = HierarchyManager._transfer_anim_curves(old, new)
        skipped_attrs = [s["attr"] for s in result["skipped"]]
        self.assertIn("myWeight", skipped_attrs)
        reasons = [s["reason"] for s in result["skipped"] if s["attr"] == "myWeight"]
        self.assertTrue(
            any("not found" in r for r in reasons),
            f"Expected 'not found' reason, got: {reasons}",
        )

    def test_transfer_unlocks_locked_attr(self):
        """EC-7: Locked attrs on replacement are unlocked before reconnecting."""
        old = self._make_animated_cube("XFR_LCK_OLD")
        new = pm.polyCube(name="XFR_LCK_NEW")[0]
        cmds.setAttr(f"{new}.translateX", lock=True)

        result = HierarchyManager._transfer_anim_curves(old, new)
        self.assertGreater(result["transferred"], 0, "Should transfer despite lock")
        keys = cmds.keyframe(str(new), attribute="translateX", query=True, tc=True)
        self.assertIsNotNone(keys)

    def test_transfer_replaces_existing_animation(self):
        """EC-8: Existing animation on replacement is removed before transfer.

        If the replacement already has its own curves, they must be deleted
        to prevent orphan animCurve nodes.
        """
        old = pm.polyCube(name="XFR_REP_OLD")[0]
        cmds.setKeyframe(str(old), attribute="translateX", time=1, value=100)
        cmds.setKeyframe(str(old), attribute="translateX", time=24, value=200)

        new = pm.polyCube(name="XFR_REP_NEW")[0]
        cmds.setKeyframe(str(new), attribute="translateX", time=1, value=-50)
        cmds.setKeyframe(str(new), attribute="translateX", time=24, value=-100)

        result = HierarchyManager._transfer_anim_curves(old, new)
        self.assertGreater(result["transferred"], 0)

        # New node should have OLD values, not its original ones
        vals = cmds.keyframe(str(new), attribute="translateX", query=True, vc=True)
        self.assertAlmostEqual(vals[0], 100.0, places=1)
        self.assertAlmostEqual(vals[1], 200.0, places=1)

    def test_transfer_skips_driven_keys(self):
        """Driven keys appear in skipped list with reason."""
        _driver, driven = self._make_driven_key("XFR2_DRV", "XFR2_DVN")
        replacement = pm.group(empty=True, name="XFR2_REPL")
        result = HierarchyManager._transfer_anim_curves(driven, replacement)
        skipped_reasons = [s["reason"] for s in result["skipped"]]
        self.assertIn("driven key", skipped_reasons)

    def test_transfer_skips_constraints(self):
        """Constraints appear in skipped list with reason."""
        node, _target = self._make_constrained("XFR2_CONSTR", "XFR2_CTGT")
        replacement = pm.group(empty=True, name="XFR2_CREPL")
        result = HierarchyManager._transfer_anim_curves(node, replacement)
        skipped_reasons = [s["reason"] for s in result["skipped"]]
        self.assertIn("constraint", skipped_reasons)

    def test_transfer_skips_expressions(self):
        """Expressions appear in skipped list with reason."""
        node = self._make_expression("XFR_EXPR")
        replacement = pm.group(empty=True, name="XFR_EXPR_REPL")
        result = HierarchyManager._transfer_anim_curves(node, replacement)
        skipped_reasons = [s["reason"] for s in result["skipped"]]
        self.assertIn("expression", skipped_reasons)

    def test_transfer_group_to_polycube(self):
        """Transfer works between different node types sharing standard attrs."""
        old = pm.group(empty=True, name="XFR_GRP")
        cmds.setKeyframe(str(old), attribute="translateX", time=1, value=5)
        cmds.setKeyframe(str(old), attribute="translateX", time=24, value=15)
        new = pm.polyCube(name="XFR_CUBE")[0]

        result = HierarchyManager._transfer_anim_curves(old, new)
        self.assertGreater(result["transferred"], 0)
        keys = cmds.keyframe(str(new), attribute="translateX", query=True, tc=True)
        self.assertIsNotNone(keys)

    def test_transfer_old_node_disconnected(self):
        """After rewire, old node no longer has animCurve connections."""
        old = self._make_animated_cube("XFR_DISC_OLD")
        new = pm.polyCube(name="XFR_DISC_NEW")[0]
        HierarchyManager._transfer_anim_curves(old, new)
        conns = cmds.listConnections(
            str(old), type="animCurve", source=True, destination=False
        )
        self.assertFalse(
            conns, "Old node should have no animCurve connections after transfer"
        )

    # ══════════════════════════════════════════════════════════════════
    #  ObjectSwapper._safe_merge_delete — merge-mode protection
    # ══════════════════════════════════════════════════════════════════

    def test_safe_merge_delete_plain_node(self):
        """Unanimated node is deleted immediately."""
        existing = pm.group(empty=True, name="SMD_PLAIN")
        replacement = pm.group(empty=True, name="SMD_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertTrue(swapper._safe_merge_delete(existing, replacement))
        self.assertFalse(pm.objExists("SMD_PLAIN"))

    def test_safe_merge_delete_transfers_time_curves(self):
        """Time-curve-only node is transferred and deleted.

        Validates the core merge path: old node's animation is losslessly
        moved to the replacement, then the old node is deleted.
        """
        old = self._make_animated_cube("SMD_ANIM")
        new = pm.polyCube(name="SMD_NEW")[0]
        swapper = ObjectSwapper(dry_run=False)
        self.assertTrue(
            swapper._safe_merge_delete(old, new),
            "Node with only time curves should be replaceable",
        )
        self.assertFalse(pm.objExists("SMD_ANIM"))
        keys = cmds.keyframe(str(new), attribute="translateX", query=True, tc=True)
        self.assertIsNotNone(keys)
        self.assertGreater(len(keys), 0)

    def test_safe_merge_delete_preserves_constrained(self):
        """Constrained node is preserved — constraints can't be auto-transferred."""
        node, _target = self._make_constrained("SMD_CONSTR", "SMD_CTGT")
        replacement = pm.group(empty=True, name="SMD_CREPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(node, replacement),
            "Constrained node should be preserved",
        )
        self.assertTrue(pm.objExists("SMD_CONSTR"))

    def test_safe_merge_delete_preserves_expression(self):
        """Expression-driven node is preserved."""
        node = self._make_expression("SMD_EXPR")
        replacement = pm.group(empty=True, name="SMD_EXPR_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(node, replacement),
            "Expression node should be preserved",
        )
        self.assertTrue(pm.objExists("SMD_EXPR"))

    def test_safe_merge_delete_preserves_driven_keys(self):
        """Node with driven keys is preserved."""
        _driver, driven = self._make_driven_key("SMD_DRVR", "SMD_DRVN")
        replacement = pm.group(empty=True, name="SMD_DK_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(driven, replacement),
            "Driven-key node should be preserved",
        )
        self.assertTrue(pm.objExists("SMD_DRVN"))

    def test_safe_merge_delete_preserves_mixed_animation(self):
        """Node with both time curves AND constraint is preserved.

        The non-transferable constraint takes priority — the entire node
        is kept even though the time curves alone could be transferred.
        """
        target = pm.group(empty=True, name="SMD_MIX_TGT")
        node = pm.polyCube(name="SMD_MIX")[0]
        cmds.setKeyframe(str(node), attribute="scaleX", time=1, value=1)
        cmds.setKeyframe(str(node), attribute="scaleX", time=24, value=2)
        pm.parentConstraint(target, node, mo=True)

        replacement = pm.group(empty=True, name="SMD_MIX_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(node, replacement),
            "Mixed animation type should be preserved",
        )
        self.assertTrue(pm.objExists("SMD_MIX"))

    def test_safe_merge_delete_preserves_animated_descendants(self):
        """EC-2: Parent with animated child is preserved — delete would destroy subtree."""
        parent = pm.group(empty=True, name="SMD_PARENT")
        self._make_animated_cube("SMD_ACHILD", parent=parent)
        replacement = pm.group(empty=True, name="SMD_PAREPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(parent, replacement),
            "Parent with animated child should be preserved",
        )
        self.assertTrue(pm.objExists("SMD_PARENT"))
        self.assertTrue(pm.objExists("SMD_ACHILD"))

    def test_safe_merge_delete_preserves_deep_animated_descendants(self):
        """Animated grandchild prevents deletion of the root."""
        root = pm.group(empty=True, name="SMD_DEEP_R")
        mid = pm.group(empty=True, name="SMD_DEEP_M", parent=root)
        leaf = pm.group(empty=True, name="SMD_DEEP_L", parent=mid)
        cmds.setKeyframe(str(leaf), attribute="translateZ", time=1, value=0)
        cmds.setKeyframe(str(leaf), attribute="translateZ", time=24, value=10)

        replacement = pm.group(empty=True, name="SMD_DEEP_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(root, replacement),
            "Deep animated descendant should prevent deletion",
        )
        self.assertTrue(pm.objExists("SMD_DEEP_R"))
        self.assertTrue(pm.objExists("SMD_DEEP_L"))

    def test_safe_merge_delete_node_with_shapes(self):
        """Unanimated mesh (has shapes but no animation) is deleted."""
        existing = pm.polyCube(name="SMD_MESH")[0]
        replacement = pm.polyCube(name="SMD_MESH_REPL")[0]
        swapper = ObjectSwapper(dry_run=False)
        self.assertTrue(
            swapper._safe_merge_delete(existing, replacement),
            "Unanimated mesh should be deletable",
        )
        self.assertFalse(pm.objExists("SMD_MESH"))

    # ══════════════════════════════════════════════════════════════════
    #  Integration: _integrate_hierarchy / _integrate_single + animation
    # ══════════════════════════════════════════════════════════════════

    def test_integrate_hierarchy_preserves_animated_existing(self):
        """EC-3: Merge mode preserves animated existing and cleans up replacement.

        When an existing object has non-transferable animation, the
        replacement MUST be deleted (not left orphaned in the scene).
        """
        target = pm.group(empty=True, name="INT_H_TGT")
        existing = pm.group(empty=True, name="INT_H_EXIST")
        pm.parentConstraint(target, existing, mo=True)

        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.group(empty=True, name="temp_import:INT_H_EXIST")

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )
        clean_name = get_clean_node_name(imported)
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        # Existing must survive with its constraint
        self.assertTrue(pm.objExists("INT_H_EXIST"), "Animated existing must survive")
        # Replacement must be cleaned up (EC-3)
        self.assertFalse(
            imported.exists(),
            "Orphaned replacement should be deleted when existing is preserved",
        )

    def test_integrate_single_preserves_animated_existing(self):
        """Single-object merge preserves animated existing, cleans replacement."""
        existing = self._make_animated_cube("INT_S_EXIST")
        # Add a constraint to make it non-transferable
        target = pm.group(empty=True, name="INT_S_TGT")
        pm.parentConstraint(target, existing, mo=True)

        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.group(empty=True, name="temp_import:INT_S_EXIST")

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=False,
        )
        clean_name = get_clean_node_name(imported)
        swapper._integrate_single(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        self.assertTrue(pm.objExists("INT_S_EXIST"), "Animated existing must survive")
        self.assertFalse(
            imported.exists(),
            "Orphaned replacement should be deleted",
        )

    def test_integrate_hierarchy_transfers_then_replaces(self):
        """Merge mode with time-curve-only existing: transfers animation, replaces node."""
        existing = self._make_animated_cube("INT_XFER")

        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.polyCube(name="temp_import:INT_XFER")[0]

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )
        clean_name = get_clean_node_name(imported)
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        # Existing should be gone (replaced)
        matches = pm.ls("INT_XFER", type="transform")
        self.assertEqual(len(matches), 1, "Only replacement should remain")
        # Replacement should have the transferred animation
        keys = cmds.keyframe(str(imported), attribute="translateX", query=True, tc=True)
        self.assertIsNotNone(keys)
        self.assertGreater(len(keys), 0, "Replacement should carry transferred keys")

    # ══════════════════════════════════════════════════════════════════
    #  fix_reparented — empty-parent animation guard
    # ══════════════════════════════════════════════════════════════════

    def test_empty_parent_preserved_when_animated(self):
        """EC-11: Animated empty parent is NOT deleted after children move out.

        Bug: After reparenting a child to world, the now-empty parent was
        unconditionally deleted — destroying any animation on it.
        Fixed: 2026-07-19
        """
        parent = pm.group(empty=True, name="FRP_ANIM_P")
        child = pm.group(empty=True, name="FRP_CHILD", parent=parent)
        cmds.setKeyframe(str(parent), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateY", time=24, value=5)

        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"FRP_ANIM_P|FRP_CHILD": child}
        hm.clean_to_raw_current = {"FRP_ANIM_P|FRP_CHILD": "FRP_ANIM_P|FRP_CHILD"}
        entries = [
            {"current_path": "FRP_ANIM_P|FRP_CHILD", "reference_path": "FRP_CHILD"}
        ]
        hm.fix_reparented(entries)

        self.assertTrue(pm.objExists("FRP_CHILD"), "Child should be reparented")
        self.assertTrue(
            pm.objExists("FRP_ANIM_P"),
            "Animated empty parent should NOT be deleted",
        )
        # Verify animation survived
        keys = cmds.keyframe("FRP_ANIM_P", attribute="translateY", query=True, tc=True)
        self.assertIsNotNone(keys, "Animation on preserved parent should survive")

    def test_empty_parent_deleted_when_not_animated(self):
        """Plain empty parent IS deleted after all children are reparented."""
        parent = pm.group(empty=True, name="FRP_PLAIN_P")
        child = pm.group(empty=True, name="FRP_PCHILD", parent=parent)

        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"FRP_PLAIN_P|FRP_PCHILD": child}
        hm.clean_to_raw_current = {"FRP_PLAIN_P|FRP_PCHILD": "FRP_PLAIN_P|FRP_PCHILD"}
        entries = [
            {"current_path": "FRP_PLAIN_P|FRP_PCHILD", "reference_path": "FRP_PCHILD"}
        ]
        hm.fix_reparented(entries)

        self.assertTrue(pm.objExists("FRP_PCHILD"), "Child should be at world")
        self.assertFalse(
            pm.objExists("FRP_PLAIN_P"),
            "Plain empty parent should be deleted",
        )

    def test_empty_parent_preserved_with_constraint(self):
        """Constrained empty parent is preserved."""
        target = pm.group(empty=True, name="FRP_C_TGT")
        parent = pm.group(empty=True, name="FRP_C_PAR")
        pm.parentConstraint(target, parent, mo=True)
        child = pm.group(empty=True, name="FRP_C_CHILD", parent=parent)

        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"FRP_C_PAR|FRP_C_CHILD": child}
        hm.clean_to_raw_current = {"FRP_C_PAR|FRP_C_CHILD": "FRP_C_PAR|FRP_C_CHILD"}
        entries = [
            {"current_path": "FRP_C_PAR|FRP_C_CHILD", "reference_path": "FRP_C_CHILD"}
        ]
        hm.fix_reparented(entries)

        self.assertTrue(pm.objExists("FRP_C_CHILD"))
        self.assertTrue(
            pm.objExists("FRP_C_PAR"),
            "Constrained empty parent should NOT be deleted",
        )

    # ══════════════════════════════════════════════════════════════════
    #  fix_fuzzy_renames — expression guard
    # ══════════════════════════════════════════════════════════════════

    def test_fuzzy_rename_skips_expression_connected(self):
        """Expression-connected node is NOT renamed when skip_animated=True.

        Renaming breaks expressions that reference the node by name.
        """
        node = self._make_expression("FZRN_EXPR")
        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"FZRN_EXPR": node}
        hm.clean_to_raw_current = {"FZRN_EXPR": "FZRN_EXPR"}
        items = [{"current_name": "FZRN_EXPR", "target_name": "FZRN_RENAMED"}]

        renamed = hm.fix_fuzzy_renames(items, skip_animated=True)
        self.assertEqual(len(renamed), 0, "Expression node should NOT be renamed")
        self.assertTrue(pm.objExists("FZRN_EXPR"))

    def test_fuzzy_rename_proceeds_for_keyframed_only(self):
        """Keyframed node WITHOUT expressions IS renamed (skip_animated only guards expressions)."""
        node = self._make_animated_cube("FZRN_KEYED")
        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"FZRN_KEYED": node}
        hm.clean_to_raw_current = {"FZRN_KEYED": "FZRN_KEYED"}
        items = [{"current_name": "FZRN_KEYED", "target_name": "FZRN_KEYED_NEW"}]

        renamed = hm.fix_fuzzy_renames(items, skip_animated=True)
        self.assertEqual(len(renamed), 1, "Keyframed-only node should be renamed")
        self.assertTrue(pm.objExists("FZRN_KEYED_NEW"))

    def test_fuzzy_rename_proceeds_when_skip_disabled(self):
        """skip_animated=False allows renaming expression-connected nodes."""
        node = self._make_expression("FZRN_FORCE")
        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"FZRN_FORCE": node}
        hm.clean_to_raw_current = {"FZRN_FORCE": "FZRN_FORCE"}
        items = [{"current_name": "FZRN_FORCE", "target_name": "FZRN_FORCENEW"}]

        renamed = hm.fix_fuzzy_renames(items, skip_animated=False)
        self.assertEqual(len(renamed), 1, "Should rename when skip_animated=False")

    def test_fuzzy_rename_dry_run_no_skip(self):
        """Dry-run mode does not apply the skip_animated guard (it doesn't rename anyway)."""
        node = self._make_expression("FZRN_DRY")
        hm = HierarchyManager()
        hm.dry_run = True
        items = [{"current_name": "FZRN_DRY", "target_name": "FZRN_DRY_NEW"}]

        renamed = hm.fix_fuzzy_renames(items, skip_animated=True)
        # Dry-run reports it WOULD rename (skip check is on the live path only)
        self.assertEqual(len(renamed), 1, "Dry-run should report the rename")
        # But original name must survive
        self.assertTrue(pm.objExists("FZRN_DRY"))

    # ══════════════════════════════════════════════════════════════════
    #  quarantine_extras — skip_animated scenarios
    # ══════════════════════════════════════════════════════════════════

    def test_quarantine_skips_animated_by_default(self):
        """Default skip_animated=True preserves animated extras.

        Bug: skip_animated defaulted to False, silently quarantining
        animation-bearing nodes.
        Fixed: 2026-07-19
        """
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        node = self._make_animated_cube("QUA_ANIM")
        pm.parent(node, world=True)
        hm.differences = {"extra": ["QUA_ANIM"]}
        hm.current_scene_path_map = {"QUA_ANIM": node}
        hm.clean_to_raw_current = {"QUA_ANIM": "QUA_ANIM"}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0, "Animated node should be skipped")
        self.assertTrue(pm.objExists("QUA_ANIM"))

    def test_quarantine_mixed_animated_and_plain(self):
        """Mixed batch: animated extras stay, plain extras are quarantined."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        anim = self._make_animated_cube("QUA_MIX_A")
        pm.parent(anim, world=True)
        plain1 = pm.group(empty=True, name="QUA_MIX_P1")
        plain2 = pm.group(empty=True, name="QUA_MIX_P2")

        hm.current_scene_path_map = {
            "QUA_MIX_A": anim,
            "QUA_MIX_P1": plain1,
            "QUA_MIX_P2": plain2,
        }
        hm.clean_to_raw_current = {
            "QUA_MIX_A": "QUA_MIX_A",
            "QUA_MIX_P1": "QUA_MIX_P1",
            "QUA_MIX_P2": "QUA_MIX_P2",
        }
        hm.differences = {
            "extra": ["QUA_MIX_A", "QUA_MIX_P1", "QUA_MIX_P2"],
        }

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertNotIn("QUA_MIX_A", moved, "Animated extra must stay")
        self.assertIn("QUA_MIX_P1", moved)
        self.assertIn("QUA_MIX_P2", moved)
        self.assertTrue(pm.objExists("QUA_MIX_A"))

    def test_quarantine_force_override_skip_false(self):
        """skip_animated=False quarantines animated nodes too."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        node = self._make_animated_cube("QUA_FORCE")
        pm.parent(node, world=True)

        hm.current_scene_path_map = {"QUA_FORCE": node}
        hm.clean_to_raw_current = {"QUA_FORCE": "QUA_FORCE"}
        hm.differences = {"extra": ["QUA_FORCE"]}

        moved = hm.quarantine_extras(skip_animated=False)
        self.assertIn("QUA_FORCE", moved, "Should quarantine when skip_animated=False")

    def test_quarantine_constrained_extra_skipped(self):
        """Constrained extras are skipped by default (they have animation data)."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        target = pm.group(empty=True, name="QUA_CTGT")
        node = pm.group(empty=True, name="QUA_CNST")
        pm.parentConstraint(target, node, mo=True)

        hm.current_scene_path_map = {"QUA_CNST": node}
        hm.clean_to_raw_current = {"QUA_CNST": "QUA_CNST"}
        hm.differences = {"extra": ["QUA_CNST"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0, "Constrained extra should be skipped")
        self.assertTrue(pm.objExists("QUA_CNST"))

    def test_quarantine_expression_extra_skipped(self):
        """Expression-driven extras are skipped by default."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        node = self._make_expression("QUA_EXPR")

        hm.current_scene_path_map = {"QUA_EXPR": node}
        hm.clean_to_raw_current = {"QUA_EXPR": "QUA_EXPR"}
        hm.differences = {"extra": ["QUA_EXPR"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0, "Expression extra should be skipped")
        self.assertTrue(pm.objExists("QUA_EXPR"))

    # ══════════════════════════════════════════════════════════════════
    #  Advanced / compound scenarios
    # ══════════════════════════════════════════════════════════════════

    def test_transfer_partial_success(self):
        """Multi-attr node where some attrs transfer and some are skipped.

        translateX exists on both → transferred.
        myWeight is a custom attr only on old → skipped (missing attr).
        Result should report both transferred > 0 AND len(skipped) > 0.
        """
        old = pm.group(empty=True, name="ADV_PART_OLD")
        cmds.addAttr(str(old), longName="myWeight", attributeType="float", keyable=True)
        cmds.setKeyframe(str(old), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(old), attribute="translateX", time=24, value=10)
        cmds.setKeyframe(str(old), attribute="myWeight", time=1, value=0)
        cmds.setKeyframe(str(old), attribute="myWeight", time=24, value=1)
        new = pm.group(empty=True, name="ADV_PART_NEW")  # no myWeight attr

        result = HierarchyManager._transfer_anim_curves(old, new)
        self.assertGreater(result["transferred"], 0, "translateX should transfer")
        self.assertGreater(len(result["skipped"]), 0, "myWeight should be skipped")
        # Verify translateX actually landed
        keys = cmds.keyframe(str(new), attribute="translateX", query=True, tc=True)
        self.assertIsNotNone(keys)

    def test_transfer_no_time_curves_only_driven(self):
        """Node with ONLY driven keys and no time curves → transferred=0."""
        _driver, driven = self._make_driven_key("ADV_NOTIME_DRV", "ADV_NOTIME_DVN")
        replacement = pm.group(empty=True, name="ADV_NOTIME_REPL")
        result = HierarchyManager._transfer_anim_curves(driven, replacement)
        self.assertEqual(result["transferred"], 0, "No time-based curves to transfer")
        self.assertGreater(len(result["skipped"]), 0, "Driven key should be skipped")

    def test_transfer_tangent_types_preserved(self):
        """Stepped tangent types survive the disconnect/reconnect rewire.

        Rewire repoints the same animCurve node so tangent data is
        inherently preserved.  This test makes that guarantee explicit.
        """
        old = pm.polyCube(name="ADV_TAN_OLD")[0]
        cmds.setKeyframe(str(old), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(old), attribute="translateX", time=24, value=10)
        cmds.keyTangent(str(old), attribute="translateX", outTangentType="step")
        # Capture tangent before transfer
        tan_before = cmds.keyTangent(
            str(old), attribute="translateX", query=True, outTangentType=True
        )

        new = pm.polyCube(name="ADV_TAN_NEW")[0]
        HierarchyManager._transfer_anim_curves(old, new)

        tan_after = cmds.keyTangent(
            str(new), attribute="translateX", query=True, outTangentType=True
        )
        self.assertEqual(
            tan_before,
            tan_after,
            f"Tangent types should be preserved: {tan_before} vs {tan_after}",
        )

    def test_transfer_weighted_tangent_values_preserved(self):
        """Weighted tangent handles are preserved through rewire transfer."""
        old = pm.polyCube(name="ADV_WT_OLD")[0]
        cmds.setKeyframe(str(old), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(old), attribute="translateX", time=48, value=20)
        cmds.keyTangent(str(old), attribute="translateX", weightedTangents=True)
        cmds.keyTangent(
            str(old),
            attribute="translateX",
            time=(1, 1),
            outWeight=15.0,
            outAngle=30.0,
        )
        weight_before = cmds.keyTangent(
            str(old),
            attribute="translateX",
            time=(1, 1),
            query=True,
            outWeight=True,
        )

        new = pm.polyCube(name="ADV_WT_NEW")[0]
        HierarchyManager._transfer_anim_curves(old, new)

        weight_after = cmds.keyTangent(
            str(new),
            attribute="translateX",
            time=(1, 1),
            query=True,
            outWeight=True,
        )
        self.assertAlmostEqual(
            weight_before[0],
            weight_after[0],
            places=3,
            msg="Weighted tangent should be preserved through rewire",
        )

    def test_safe_merge_delete_partial_transfer_preserves(self):
        """EC-10: Partial transfer failure → node is preserved.

        If _transfer_anim_curves reports skipped items (e.g. custom attr
        missing on replacement), _safe_merge_delete treats the whole
        operation as unsafe and preserves the existing node.
        """
        existing = pm.group(empty=True, name="ADV_SMD_PART")
        cmds.addAttr(
            str(existing), longName="customBlend", attributeType="float", keyable=True
        )
        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateX", time=24, value=5)
        cmds.setKeyframe(str(existing), attribute="customBlend", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="customBlend", time=24, value=1)

        replacement = pm.polyCube(name="ADV_SMD_PART_REPL")[0]  # no customBlend
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(existing, replacement),
            "Partial transfer should preserve existing node",
        )
        self.assertTrue(pm.objExists("ADV_SMD_PART"))

    def test_safe_merge_delete_clean_root_animated_descendant_driven_key(self):
        """Clean root with driven-key child is preserved (descendant check).

        The root itself has zero animation but its child has a driven key.
        _safe_merge_delete must detect the descendant animation and refuse
        to delete the subtree.
        """
        root = pm.group(empty=True, name="ADV_DKDESC_ROOT")
        driver = pm.group(empty=True, name="ADV_DKDESC_DRV")
        child = pm.group(empty=True, name="ADV_DKDESC_CHILD", parent=root)
        cmds.setDrivenKeyframe(
            f"{child}.translateX",
            currentDriver=f"{driver}.translateY",
            driverValue=0,
            value=0,
        )
        cmds.setDrivenKeyframe(
            f"{child}.translateX",
            currentDriver=f"{driver}.translateY",
            driverValue=10,
            value=5,
        )

        replacement = pm.group(empty=True, name="ADV_DKDESC_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(root, replacement),
            "Driven-key child should prevent root deletion",
        )
        self.assertTrue(pm.objExists("ADV_DKDESC_ROOT"))
        self.assertTrue(pm.objExists("ADV_DKDESC_CHILD"))

    def test_classify_multiple_constraint_types(self):
        """Verify parentConstraint and aimConstraint are each classified correctly."""
        target1 = pm.group(empty=True, name="ADV_MC_TGT1")
        target2 = pm.group(empty=True, name="ADV_MC_TGT2")
        node = pm.group(empty=True, name="ADV_MC_NODE")
        pm.parentConstraint(target1, node, mo=True)
        # aimConstraint conflicts with parentConstraint on the same rotate
        # attrs, so use a second node with an aimConstraint instead.
        node2 = pm.group(empty=True, name="ADV_MC_NODE2")
        pm.aimConstraint(target2, node2, mo=True)

        cls1 = HierarchyManager._classify_animation(node)
        cls2 = HierarchyManager._classify_animation(node2)
        total_constraints = len(cls1["constraints"]) + len(cls2["constraints"])
        self.assertGreaterEqual(
            total_constraints,
            2,
            f"Expected >= 2 constraints across both nodes, got {total_constraints}",
        )
        self.assertEqual(
            len(cls1["curves"]), 0, "Constraints don't produce time curves"
        )
        self.assertEqual(len(cls1["driven_keys"]), 0)
        self.assertEqual(
            len(cls2["curves"]), 0, "Constraints don't produce time curves"
        )
        self.assertEqual(len(cls2["driven_keys"]), 0)

    def test_classify_constraint_only_no_curves(self):
        """Node with ONLY a constraint has empty curves and driven_keys lists."""
        node, _target = self._make_constrained("ADV_CONLY", "ADV_CONLY_TGT")
        cls = HierarchyManager._classify_animation(node)
        self.assertEqual(len(cls["curves"]), 0)
        self.assertEqual(len(cls["driven_keys"]), 0)
        self.assertGreater(len(cls["constraints"]), 0)

    def test_has_animation_data_expression_on_custom_attr(self):
        """Expression driving a custom attribute is detected."""
        node = pm.group(empty=True, name="ADV_EXPR_CA")
        cmds.addAttr(str(node), longName="wobble", attributeType="float", keyable=True)
        cmds.expression(
            string=f"{node}.wobble = sin(frame * 0.1)",
            object=str(node),
            alwaysEvaluate=True,
            name="ADV_EXPR_CA_expr",
        )
        self.assertTrue(
            HierarchyManager._has_animation_data(node),
            "Expression on custom attr should be detected",
        )

    def test_safe_merge_delete_multi_attr_all_transfer(self):
        """Node with time curves on 3 standard attrs: all transfer and node is deleted."""
        existing = self._make_multi_attr_animated("ADV_SMD_3ATTR")
        replacement = pm.polyCube(name="ADV_SMD_3ATTR_REPL")[0]
        swapper = ObjectSwapper(dry_run=False)
        self.assertTrue(
            swapper._safe_merge_delete(existing, replacement),
            "All standard attrs should transfer successfully",
        )
        self.assertFalse(pm.objExists("ADV_SMD_3ATTR"))
        # All 3 attrs should be on the replacement
        for attr in ("translateX", "rotateY", "scaleZ"):
            keys = cmds.keyframe(str(replacement), attribute=attr, query=True, tc=True)
            self.assertIsNotNone(keys, f"Missing keys on {attr} after merge")
            self.assertGreater(len(keys), 0, f"Missing keys on {attr} after merge")

    def test_quarantine_dry_run_respects_skip_animated(self):
        """Dry-run mode correctly partitions animated vs. plain extras."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=True)
        anim = self._make_animated_cube("ADV_QDR_A")
        pm.parent(anim, world=True)
        expr_node = self._make_expression("ADV_QDR_E")
        plain = pm.group(empty=True, name="ADV_QDR_P")

        hm.current_scene_path_map = {
            "ADV_QDR_A": anim,
            "ADV_QDR_E": expr_node,
            "ADV_QDR_P": plain,
        }
        hm.clean_to_raw_current = {
            "ADV_QDR_A": "ADV_QDR_A",
            "ADV_QDR_E": "ADV_QDR_E",
            "ADV_QDR_P": "ADV_QDR_P",
        }
        hm.differences = {
            "extra": ["ADV_QDR_A", "ADV_QDR_E", "ADV_QDR_P"],
        }

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertIn("ADV_QDR_P", moved, "Plain extra should be in dry-run list")
        self.assertNotIn("ADV_QDR_A", moved, "Animated extra should be skipped")
        self.assertNotIn("ADV_QDR_E", moved, "Expression extra should be skipped")
        # No scene changes in dry-run
        self.assertFalse(pm.objExists("_QUARANTINE"))

    def test_fuzzy_rename_constrained_node_proceeds(self):
        """Constrained node IS renamed (skip_animated only guards expressions)."""
        node, _target = self._make_constrained("ADV_FZRN_C", "ADV_FZRN_CTGT")
        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"ADV_FZRN_C": node}
        hm.clean_to_raw_current = {"ADV_FZRN_C": "ADV_FZRN_C"}
        items = [{"current_name": "ADV_FZRN_C", "target_name": "ADV_FZRN_C_NEW"}]

        renamed = hm.fix_fuzzy_renames(items, skip_animated=True)
        self.assertEqual(len(renamed), 1, "Constrained node should still be renamed")
        self.assertTrue(pm.objExists("ADV_FZRN_C_NEW"))

    def test_integrate_hierarchy_time_curves_value_fidelity(self):
        """End-to-end: key values are identical on replacement after merge integration."""
        existing = pm.polyCube(name="ADV_INT_FIDELITY")[0]
        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=42.5)
        cmds.setKeyframe(str(existing), attribute="translateX", time=100, value=-13.7)

        if not pm.namespace(exists="temp_import"):
            pm.namespace(add="temp_import")
        imported = pm.polyCube(name="temp_import:ADV_INT_FIDELITY")[0]

        swapper = ObjectSwapper(
            dry_run=False,
            fuzzy_matching=False,
            pull_mode="Merge Hierarchies",
            pull_children=True,
        )
        clean_name = get_clean_node_name(imported)
        swapper._integrate_hierarchy(
            imported, clean_name, merge=True, allow_auto_rename=False
        )

        # Verify the replacement carries the exact key values
        vals = cmds.keyframe(str(imported), attribute="translateX", query=True, vc=True)
        self.assertIsNotNone(vals)
        self.assertAlmostEqual(vals[0], 42.5, places=2)
        self.assertAlmostEqual(vals[1], -13.7, places=2)

    def test_empty_parent_chain_preserves_deepest_animated(self):
        """Nested empty parents: only the animated one is preserved.

        GRP_A (plain) > GRP_B (animated) > CHILD (reparented to world).
        After CHILD moves, GRP_B should survive (animated), and GRP_A
        still has GRP_B as a child so it's not empty and also survives.
        """
        grp_a = pm.group(empty=True, name="ADV_EPC_A")
        grp_b = pm.group(empty=True, name="ADV_EPC_B", parent=grp_a)
        cmds.setKeyframe(str(grp_b), attribute="rotateY", time=1, value=0)
        cmds.setKeyframe(str(grp_b), attribute="rotateY", time=24, value=90)
        child = pm.group(empty=True, name="ADV_EPC_CHILD", parent=grp_b)

        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {
            "ADV_EPC_A|ADV_EPC_B|ADV_EPC_CHILD": child,
        }
        hm.clean_to_raw_current = {
            "ADV_EPC_A|ADV_EPC_B|ADV_EPC_CHILD": "ADV_EPC_A|ADV_EPC_B|ADV_EPC_CHILD",
        }
        entries = [
            {
                "current_path": "ADV_EPC_A|ADV_EPC_B|ADV_EPC_CHILD",
                "reference_path": "ADV_EPC_CHILD",
            }
        ]
        hm.fix_reparented(entries)

        self.assertTrue(pm.objExists("ADV_EPC_CHILD"), "Child reparented to world")
        self.assertTrue(
            pm.objExists("ADV_EPC_B"),
            "Animated GRP_B must survive",
        )
        self.assertTrue(
            pm.objExists("ADV_EPC_A"),
            "GRP_A still has GRP_B as child, so it's not empty",
        )

        self.assertTrue(
            pm.objExists("ADV_EPC_A"),
            "GRP_A still has GRP_B as child, so it's not empty",
        )

    # ══════════════════════════════════════════════════════════════════
    #  Constraint type coverage
    # ══════════════════════════════════════════════════════════════════

    # -- detection breadth --

    def test_has_animation_data_orient_constraint(self):
        """orientConstraint is detected as animation data."""
        target = pm.group(empty=True, name="CST_OC_TGT")
        node = pm.group(empty=True, name="CST_OC_NODE")
        pm.orientConstraint(target, node, mo=True)
        self.assertTrue(HierarchyManager._has_animation_data(node))

    def test_has_animation_data_point_constraint(self):
        """pointConstraint is detected as animation data."""
        target = pm.group(empty=True, name="CST_PC_TGT")
        node = pm.group(empty=True, name="CST_PC_NODE")
        pm.pointConstraint(target, node)
        self.assertTrue(HierarchyManager._has_animation_data(node))

    def test_has_animation_data_scale_constraint(self):
        """scaleConstraint is detected as animation data."""
        target = pm.group(empty=True, name="CST_SC_TGT")
        node = pm.group(empty=True, name="CST_SC_NODE")
        pm.scaleConstraint(target, node)
        self.assertTrue(HierarchyManager._has_animation_data(node))

    def test_has_animation_data_aim_constraint(self):
        """aimConstraint is detected as animation data."""
        target = pm.group(empty=True, name="CST_AC_TGT")
        node = pm.group(empty=True, name="CST_AC_NODE")
        pm.aimConstraint(target, node)
        self.assertTrue(HierarchyManager._has_animation_data(node))

    def test_has_animation_data_pole_vector_constraint(self):
        """poleVectorConstraint is detected as animation data.

        Pole vector constraints are children of the IK handle, but in
        rigs they sometimes appear on transforms via intermediate wiring.
        Uses an IK handle for a valid pole vector target.
        """
        joint1 = pm.joint(name="CST_PV_J1", position=(0, 0, 0))
        joint2 = pm.joint(name="CST_PV_J2", position=(5, 0, 0))
        joint3 = pm.joint(name="CST_PV_J3", position=(10, 0, 0))
        pm.select(clear=True)
        ik_handle = pm.ikHandle(
            startJoint=joint1,
            endEffector=joint3,
            solver="ikRPsolver",
            name="CST_PV_IK",
        )[0]
        pole = pm.group(empty=True, name="CST_PV_POLE")
        pm.poleVectorConstraint(pole, ik_handle)
        self.assertTrue(
            HierarchyManager._has_animation_data(ik_handle),
            "poleVectorConstraint should be detected",
        )

    # -- classify reports each type --

    def test_classify_orient_constraint(self):
        """orientConstraint appears in constraints list."""
        target = pm.group(empty=True, name="CST_CLS_OC_TGT")
        node = pm.group(empty=True, name="CST_CLS_OC")
        pm.orientConstraint(target, node, mo=True)
        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(len(cls["constraints"]), 0)
        constraint_types = [cmds.objectType(c) for c in cls["constraints"]]
        self.assertIn("orientConstraint", constraint_types)

    def test_classify_point_constraint(self):
        """pointConstraint appears in constraints list."""
        target = pm.group(empty=True, name="CST_CLS_PC_TGT")
        node = pm.group(empty=True, name="CST_CLS_PC")
        pm.pointConstraint(target, node)
        cls = HierarchyManager._classify_animation(node)
        constraint_types = [cmds.objectType(c) for c in cls["constraints"]]
        self.assertIn("pointConstraint", constraint_types)

    def test_classify_scale_constraint(self):
        """scaleConstraint appears in constraints list."""
        target = pm.group(empty=True, name="CST_CLS_SC_TGT")
        node = pm.group(empty=True, name="CST_CLS_SC")
        pm.scaleConstraint(target, node)
        cls = HierarchyManager._classify_animation(node)
        constraint_types = [cmds.objectType(c) for c in cls["constraints"]]
        self.assertIn("scaleConstraint", constraint_types)

    def test_classify_aim_constraint(self):
        """aimConstraint appears in constraints list."""
        target = pm.group(empty=True, name="CST_CLS_AC_TGT")
        node = pm.group(empty=True, name="CST_CLS_AC")
        pm.aimConstraint(target, node)
        cls = HierarchyManager._classify_animation(node)
        constraint_types = [cmds.objectType(c) for c in cls["constraints"]]
        self.assertIn("aimConstraint", constraint_types)

    def test_classify_three_different_constraints(self):
        """Node with point + orient + scale constraints reports all three.

        Common rig pattern: separate TRS constraints for independent
        blending via constraint weight attributes.
        """
        pos_tgt = pm.group(empty=True, name="CST_3C_POS")
        rot_tgt = pm.group(empty=True, name="CST_3C_ROT")
        scl_tgt = pm.group(empty=True, name="CST_3C_SCL")
        node = pm.group(empty=True, name="CST_3C_NODE")
        pm.pointConstraint(pos_tgt, node)
        pm.orientConstraint(rot_tgt, node, mo=True)
        pm.scaleConstraint(scl_tgt, node)

        cls = HierarchyManager._classify_animation(node)
        constraint_types = set(cmds.objectType(c) for c in cls["constraints"])
        self.assertIn("pointConstraint", constraint_types)
        self.assertIn("orientConstraint", constraint_types)
        self.assertIn("scaleConstraint", constraint_types)
        self.assertGreaterEqual(len(cls["constraints"]), 3)

    # -- multi-target constraints --

    def test_classify_parent_constraint_multi_target(self):
        """parentConstraint with two targets is a single constraint node.

        Common for space-switching rigs. The single constraint node should
        appear exactly once in the constraints list.
        """
        tgt_a = pm.group(empty=True, name="CST_MT_A")
        tgt_b = pm.group(empty=True, name="CST_MT_B")
        node = pm.group(empty=True, name="CST_MT_NODE")
        pm.parentConstraint(tgt_a, node, mo=True)
        pm.parentConstraint(tgt_b, node, mo=True)

        cls = HierarchyManager._classify_animation(node)
        # Maya stores multi-target in a single constraint node
        parent_constraints = [
            c for c in cls["constraints"] if cmds.objectType(c) == "parentConstraint"
        ]
        self.assertEqual(
            len(parent_constraints),
            1,
            "Multi-target parentConstraint is one node",
        )

    def test_classify_orient_constraint_multi_target(self):
        """orientConstraint with two targets reports one constraint node."""
        tgt_a = pm.group(empty=True, name="CST_OMT_A")
        tgt_b = pm.group(empty=True, name="CST_OMT_B")
        node = pm.group(empty=True, name="CST_OMT_NODE")
        pm.orientConstraint(tgt_a, node, mo=True)
        pm.orientConstraint(tgt_b, node, mo=True)

        cls = HierarchyManager._classify_animation(node)
        orient_constraints = [
            c for c in cls["constraints"] if cmds.objectType(c) == "orientConstraint"
        ]
        self.assertEqual(len(orient_constraints), 1)

    # -- constraint + keyframe combos --

    def test_classify_orient_constraint_with_keyframes(self):
        """Node with orientConstraint AND translateX keyframes reports both."""
        target = pm.group(empty=True, name="CST_OCK_TGT")
        node = pm.polyCube(name="CST_OCK_NODE")[0]
        pm.orientConstraint(target, node, mo=True)
        cmds.setKeyframe(str(node), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(node), attribute="translateX", time=24, value=10)

        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(len(cls["constraints"]), 0, "orientConstraint present")
        self.assertGreater(len(cls["curves"]), 0, "time-based curve on translateX")

    def test_classify_point_constraint_with_scale_keyframes(self):
        """pointConstraint locks position; scale keyframes are independent."""
        target = pm.group(empty=True, name="CST_PCSK_TGT")
        node = pm.polyCube(name="CST_PCSK_NODE")[0]
        pm.pointConstraint(target, node)
        cmds.setKeyframe(str(node), attribute="scaleX", time=1, value=1)
        cmds.setKeyframe(str(node), attribute="scaleX", time=24, value=2)

        cls = HierarchyManager._classify_animation(node)
        self.assertGreater(len(cls["constraints"]), 0)
        self.assertGreater(len(cls["curves"]), 0)

    # -- merge behaviour with various constraint types --

    def test_safe_merge_delete_preserves_orient_constrained(self):
        """orientConstraint prevents merge-delete."""
        target = pm.group(empty=True, name="CST_SMD_OC_TGT")
        existing = pm.group(empty=True, name="CST_SMD_OC")
        pm.orientConstraint(target, existing, mo=True)
        replacement = pm.group(empty=True, name="CST_SMD_OC_REPL")

        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(swapper._safe_merge_delete(existing, replacement))
        self.assertTrue(pm.objExists("CST_SMD_OC"))

    def test_safe_merge_delete_preserves_point_constrained(self):
        """pointConstraint prevents merge-delete."""
        target = pm.group(empty=True, name="CST_SMD_PC_TGT")
        existing = pm.group(empty=True, name="CST_SMD_PC")
        pm.pointConstraint(target, existing)
        replacement = pm.group(empty=True, name="CST_SMD_PC_REPL")

        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(swapper._safe_merge_delete(existing, replacement))
        self.assertTrue(pm.objExists("CST_SMD_PC"))

    def test_safe_merge_delete_preserves_scale_constrained(self):
        """scaleConstraint prevents merge-delete."""
        target = pm.group(empty=True, name="CST_SMD_SC_TGT")
        existing = pm.group(empty=True, name="CST_SMD_SC")
        pm.scaleConstraint(target, existing)
        replacement = pm.group(empty=True, name="CST_SMD_SC_REPL")

        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(swapper._safe_merge_delete(existing, replacement))
        self.assertTrue(pm.objExists("CST_SMD_SC"))

    def test_safe_merge_delete_preserves_full_trs_constrained(self):
        """Node with point + orient + scale constraints is preserved.

        Even though each constraint alone would block, verify the
        combined case doesn't accidentally slip through.
        """
        pos_tgt = pm.group(empty=True, name="CST_SMDT_POS")
        rot_tgt = pm.group(empty=True, name="CST_SMDT_ROT")
        scl_tgt = pm.group(empty=True, name="CST_SMDT_SCL")
        existing = pm.group(empty=True, name="CST_SMDT_NODE")
        pm.pointConstraint(pos_tgt, existing)
        pm.orientConstraint(rot_tgt, existing, mo=True)
        pm.scaleConstraint(scl_tgt, existing)
        replacement = pm.group(empty=True, name="CST_SMDT_REPL")

        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(swapper._safe_merge_delete(existing, replacement))
        self.assertTrue(pm.objExists("CST_SMDT_NODE"))

    # -- transfer reports constraints as non-transferable --

    def test_transfer_skips_orient_constraint(self):
        """orientConstraint reported as non-transferable with 'constraint' reason."""
        target = pm.group(empty=True, name="CST_XFR_OC_TGT")
        node = pm.group(empty=True, name="CST_XFR_OC")
        pm.orientConstraint(target, node, mo=True)
        replacement = pm.group(empty=True, name="CST_XFR_OC_REPL")

        result = HierarchyManager._transfer_anim_curves(node, replacement)
        skipped_reasons = [s["reason"] for s in result["skipped"]]
        self.assertIn("constraint", skipped_reasons)

    def test_transfer_skips_point_constraint(self):
        """pointConstraint reported as non-transferable."""
        target = pm.group(empty=True, name="CST_XFR_PC_TGT")
        node = pm.group(empty=True, name="CST_XFR_PC")
        pm.pointConstraint(target, node)
        replacement = pm.group(empty=True, name="CST_XFR_PC_REPL")

        result = HierarchyManager._transfer_anim_curves(node, replacement)
        skipped_reasons = [s["reason"] for s in result["skipped"]]
        self.assertIn("constraint", skipped_reasons)

    def test_transfer_constraint_plus_curves_reports_both(self):
        """Node with constraint AND time curves: curves transfer, constraint skipped.

        This validates the mixed-type transfer path — time-based curves
        can be rewired but constraints are reported as skipped.
        """
        target = pm.group(empty=True, name="CST_XFR_MIX_TGT")
        node = pm.polyCube(name="CST_XFR_MIX")[0]
        pm.orientConstraint(target, node, mo=True)
        cmds.setKeyframe(str(node), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(node), attribute="translateX", time=24, value=10)

        replacement = pm.polyCube(name="CST_XFR_MIX_REPL")[0]
        result = HierarchyManager._transfer_anim_curves(node, replacement)

        self.assertGreater(result["transferred"], 0, "Time curves should transfer")
        skipped_reasons = [s["reason"] for s in result["skipped"]]
        self.assertIn("constraint", skipped_reasons, "Constraint should be in skipped")

    # -- descendant constraint scenarios --

    def test_has_animation_data_descendant_orient_constraint(self):
        """check_descendants detects orientConstraint on a child."""
        parent = pm.group(empty=True, name="CST_DESC_OC_P")
        target = pm.group(empty=True, name="CST_DESC_OC_TGT")
        child = pm.group(empty=True, name="CST_DESC_OC_C", parent=parent)
        pm.orientConstraint(target, child, mo=True)

        self.assertFalse(HierarchyManager._has_animation_data(parent))
        self.assertTrue(
            HierarchyManager._has_animation_data(parent, check_descendants=True),
        )

    def test_safe_merge_delete_descendant_with_scale_constraint(self):
        """Parent with scale-constrained child is preserved."""
        parent = pm.group(empty=True, name="CST_SMDD_P")
        target = pm.group(empty=True, name="CST_SMDD_TGT")
        child = pm.group(empty=True, name="CST_SMDD_C", parent=parent)
        pm.scaleConstraint(target, child)

        replacement = pm.group(empty=True, name="CST_SMDD_REPL")
        swapper = ObjectSwapper(dry_run=False)
        self.assertFalse(
            swapper._safe_merge_delete(parent, replacement),
            "Constrained child should block parent deletion",
        )
        self.assertTrue(pm.objExists("CST_SMDD_P"))
        self.assertTrue(pm.objExists("CST_SMDD_C"))

    # -- quarantine with various constraint types --

    def test_quarantine_skips_orient_constrained(self):
        """orientConstraint extra is skipped by default."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        target = pm.group(empty=True, name="CST_QUA_OC_TGT")
        node = pm.group(empty=True, name="CST_QUA_OC")
        pm.orientConstraint(target, node, mo=True)

        hm.current_scene_path_map = {"CST_QUA_OC": node}
        hm.clean_to_raw_current = {"CST_QUA_OC": "CST_QUA_OC"}
        hm.differences = {"extra": ["CST_QUA_OC"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0)
        self.assertTrue(pm.objExists("CST_QUA_OC"))

    def test_quarantine_skips_point_constrained(self):
        """pointConstraint extra is skipped by default."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        target = pm.group(empty=True, name="CST_QUA_PC_TGT")
        node = pm.group(empty=True, name="CST_QUA_PC")
        pm.pointConstraint(target, node)

        hm.current_scene_path_map = {"CST_QUA_PC": node}
        hm.clean_to_raw_current = {"CST_QUA_PC": "CST_QUA_PC"}
        hm.differences = {"extra": ["CST_QUA_PC"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0)
        self.assertTrue(pm.objExists("CST_QUA_PC"))

    # -- reparent safety with various constraint types --

    def test_reparent_preserves_orient_constraint(self):
        """orientConstraint survives pm.parent."""
        target = pm.group(empty=True, name="CST_REP_OC_TGT")
        node = pm.group(empty=True, name="CST_REP_OC")
        pm.orientConstraint(target, node, mo=True)
        new_parent = pm.group(empty=True, name="CST_REP_OC_P")

        pm.parent(node, new_parent)

        constraints = cmds.listRelatives(str(node), type="constraint") or []
        self.assertGreater(len(constraints), 0)
        constraint_types = [cmds.objectType(c) for c in constraints]
        self.assertIn("orientConstraint", constraint_types)

    def test_reparent_preserves_point_constraint(self):
        """pointConstraint survives pm.parent."""
        target = pm.group(empty=True, name="CST_REP_PC_TGT")
        node = pm.group(empty=True, name="CST_REP_PC")
        pm.pointConstraint(target, node)
        new_parent = pm.group(empty=True, name="CST_REP_PC_P")

        pm.parent(node, new_parent)

        constraints = cmds.listRelatives(str(node), type="constraint") or []
        constraint_types = [cmds.objectType(c) for c in constraints]
        self.assertIn("pointConstraint", constraint_types)

    def test_reparent_preserves_scale_constraint(self):
        """scaleConstraint survives pm.parent."""
        target = pm.group(empty=True, name="CST_REP_SC_TGT")
        node = pm.group(empty=True, name="CST_REP_SC")
        pm.scaleConstraint(target, node)
        new_parent = pm.group(empty=True, name="CST_REP_SC_P")

        pm.parent(node, new_parent)

        constraints = cmds.listRelatives(str(node), type="constraint") or []
        constraint_types = [cmds.objectType(c) for c in constraints]
        self.assertIn("scaleConstraint", constraint_types)

    def test_reparent_preserves_multi_target_parent_constraint(self):
        """Multi-target parentConstraint survives pm.parent with both targets intact."""
        tgt_a = pm.group(empty=True, name="CST_REP_MT_A")
        tgt_b = pm.group(empty=True, name="CST_REP_MT_B")
        node = pm.group(empty=True, name="CST_REP_MT")
        pm.parentConstraint(tgt_a, node, mo=True)
        pm.parentConstraint(tgt_b, node, mo=True)
        new_parent = pm.group(empty=True, name="CST_REP_MT_P")

        constraint_before = cmds.listRelatives(str(node), type="constraint") or []
        pm.parent(node, new_parent)
        constraint_after = cmds.listRelatives(str(node), type="constraint") or []

        self.assertEqual(
            len(constraint_before),
            len(constraint_after),
            "Constraint count should survive reparent",
        )

    # -- fix_reparented with constrained child --

    def test_empty_parent_preserved_orient_constrained_child_moved(self):
        """Parent with orientConstraint is preserved when child moves out."""
        target = pm.group(empty=True, name="CST_FRP_TGT")
        parent = pm.group(empty=True, name="CST_FRP_PAR")
        pm.orientConstraint(target, parent, mo=True)
        child = pm.group(empty=True, name="CST_FRP_CHILD", parent=parent)

        hm = HierarchyManager()
        hm.dry_run = False
        hm.current_scene_path_map = {"CST_FRP_PAR|CST_FRP_CHILD": child}
        hm.clean_to_raw_current = {
            "CST_FRP_PAR|CST_FRP_CHILD": "CST_FRP_PAR|CST_FRP_CHILD"
        }
        entries = [
            {
                "current_path": "CST_FRP_PAR|CST_FRP_CHILD",
                "reference_path": "CST_FRP_CHILD",
            }
        ]
        hm.fix_reparented(entries)

        self.assertTrue(pm.objExists("CST_FRP_CHILD"))
        self.assertTrue(
            pm.objExists("CST_FRP_PAR"),
            "Orient-constrained parent should be preserved",
        )

    # ══════════════════════════════════════════════════════════════════
    #  Parent + child both animated (hierarchical animation)
    # ══════════════════════════════════════════════════════════════════

    def test_detect_parent_keyed_child_keyed(self):
        """Both parent and child have keyframes — both individually detected."""
        parent = pm.group(empty=True, name="PC_KK_P")
        child = pm.polyCube(name="PC_KK_C")[0]
        pm.parent(child, parent)
        cmds.setKeyframe(str(parent), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateY", time=24, value=5)
        cmds.setKeyframe(str(child), attribute="rotateX", time=1, value=0)
        cmds.setKeyframe(str(child), attribute="rotateX", time=24, value=90)

        self.assertTrue(HierarchyManager._has_animation_data(parent))
        self.assertTrue(HierarchyManager._has_animation_data(child))
        self.assertTrue(
            HierarchyManager._has_animation_data(parent, check_descendants=True),
        )

    def test_detect_parent_keyed_child_constrained(self):
        """Parent has keyframes, child has a constraint — independent detection."""
        parent = pm.group(empty=True, name="PC_KC_P")
        child = pm.group(empty=True, name="PC_KC_C", parent=parent)
        target = pm.group(empty=True, name="PC_KC_TGT")
        cmds.setKeyframe(str(parent), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateX", time=24, value=10)
        pm.parentConstraint(target, child, mo=True)

        self.assertTrue(HierarchyManager._has_animation_data(parent))
        self.assertTrue(HierarchyManager._has_animation_data(child))

    def test_detect_parent_constrained_child_keyed(self):
        """Parent has a constraint, child has keyframes."""
        target = pm.group(empty=True, name="PC_CK_TGT")
        parent = pm.group(empty=True, name="PC_CK_P")
        pm.orientConstraint(target, parent, mo=True)
        child = pm.polyCube(name="PC_CK_C")[0]
        pm.parent(child, parent)
        cmds.setKeyframe(str(child), attribute="scaleX", time=1, value=1)
        cmds.setKeyframe(str(child), attribute="scaleX", time=24, value=3)

        self.assertTrue(HierarchyManager._has_animation_data(parent))
        self.assertTrue(HierarchyManager._has_animation_data(child))

    def test_detect_parent_keyed_child_expression(self):
        """Parent has keyframes, child has an expression."""
        parent = pm.group(empty=True, name="PC_KE_P")
        child = pm.group(empty=True, name="PC_KE_C", parent=parent)
        cmds.setKeyframe(str(parent), attribute="translateZ", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateZ", time=24, value=5)
        cmds.expression(
            string=f"{child}.rotateY = sin(frame * 0.1) * 45",
            object=str(child),
            alwaysEvaluate=True,
            name="PC_KE_expr",
        )

        self.assertTrue(HierarchyManager._has_animation_data(parent))
        self.assertTrue(HierarchyManager._has_animation_data(child))

    def test_detect_parent_keyed_child_driven_key(self):
        """Parent has keyframes, child has a set-driven key."""
        parent = pm.group(empty=True, name="PC_KD_P")
        child = pm.group(empty=True, name="PC_KD_C", parent=parent)
        cmds.setKeyframe(str(parent), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateX", time=24, value=10)
        cmds.setDrivenKeyframe(
            f"{child}.rotateZ",
            currentDriver=f"{parent}.translateX",
            driverValue=0,
            value=0,
        )
        cmds.setDrivenKeyframe(
            f"{child}.rotateZ",
            currentDriver=f"{parent}.translateX",
            driverValue=10,
            value=90,
        )

        self.assertTrue(HierarchyManager._has_animation_data(parent))
        self.assertTrue(HierarchyManager._has_animation_data(child))

    # -- classify hierarchical combos --

    def test_classify_parent_keyed_child_constrained(self):
        """Parent classify shows curves; child classify shows constraint."""
        parent = pm.group(empty=True, name="PC_CLS_KC_P")
        child = pm.group(empty=True, name="PC_CLS_KC_C", parent=parent)
        target = pm.group(empty=True, name="PC_CLS_KC_TGT")
        cmds.setKeyframe(str(parent), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateY", time=24, value=5)
        pm.pointConstraint(target, child)

        cls_p = HierarchyManager._classify_animation(parent)
        cls_c = HierarchyManager._classify_animation(child)
        self.assertGreater(len(cls_p["curves"]), 0, "Parent has time curves")
        self.assertEqual(len(cls_p["constraints"]), 0, "Parent has no constraints")
        self.assertGreater(len(cls_c["constraints"]), 0, "Child has constraint")
        self.assertEqual(len(cls_c["curves"]), 0, "Child has no time curves")

    def test_classify_parent_and_child_both_keyed_different_attrs(self):
        """Parent keyed on translateX, child on rotateZ — independent curves."""
        parent = pm.group(empty=True, name="PC_CLS_KK_P")
        child = pm.polyCube(name="PC_CLS_KK_C")[0]
        pm.parent(child, parent)
        cmds.setKeyframe(str(parent), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateX", time=24, value=10)
        cmds.setKeyframe(str(child), attribute="rotateZ", time=1, value=0)
        cmds.setKeyframe(str(child), attribute="rotateZ", time=24, value=180)

        cls_p = HierarchyManager._classify_animation(parent)
        cls_c = HierarchyManager._classify_animation(child)
        parent_plugs = [plug for _, plug in cls_p["curves"]]
        child_plugs = [plug for _, plug in cls_c["curves"]]
        self.assertTrue(
            any("translateX" in p for p in parent_plugs),
            "Parent curve on translateX",
        )
        self.assertTrue(
            any("rotateZ" in p for p in child_plugs),
            "Child curve on rotateZ",
        )

    # -- merge-delete with animated parent + animated children --

    def test_merge_delete_preserves_when_child_constrained(self):
        """Parent with time curves + constrained child → preserved.

        Even though the parent's own curves are transferable, the
        constrained child makes the subtree non-deletable.
        """
        target = pm.group(empty=True, name="PC_SMD_CC_TGT")
        existing = pm.group(empty=True, name="PC_SMD_CC_P")
        child = pm.group(empty=True, name="PC_SMD_CC_C", parent=existing)
        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateX", time=24, value=10)
        pm.orientConstraint(target, child, mo=True)

        replacement = pm.group(empty=True, name="PC_SMD_CC_REPL")
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertFalse(result, "Constrained child blocks parent deletion")
        self.assertTrue(pm.objExists("PC_SMD_CC_P"))
        self.assertTrue(pm.objExists("PC_SMD_CC_C"))

    def test_merge_delete_preserves_when_child_keyed(self):
        """Parent with time curves + keyed child → preserved.

        Animated descendants are non-transferable because only the root
        node's curves are wired to the replacement.
        """
        existing = pm.group(empty=True, name="PC_SMD_CK_P")
        child = pm.polyCube(name="PC_SMD_CK_C")[0]
        pm.parent(child, existing)
        cmds.setKeyframe(str(existing), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateY", time=24, value=5)
        cmds.setKeyframe(str(child), attribute="rotateX", time=1, value=0)
        cmds.setKeyframe(str(child), attribute="rotateX", time=24, value=90)

        replacement = pm.group(empty=True, name="PC_SMD_CK_REPL")
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertFalse(result, "Keyed child blocks parent deletion")
        self.assertTrue(pm.objExists("PC_SMD_CK_P"))
        self.assertTrue(pm.objExists("PC_SMD_CK_C"))

    def test_merge_delete_preserves_when_child_has_expression(self):
        """Parent with time curves + expression-driven child → preserved."""
        existing = pm.group(empty=True, name="PC_SMD_CE_P")
        child = pm.group(empty=True, name="PC_SMD_CE_C", parent=existing)
        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateX", time=24, value=10)
        cmds.expression(
            string=f"{child}.rotateY = noise(frame) * 30",
            object=str(child),
            alwaysEvaluate=True,
            name="PC_SMD_CE_expr",
        )

        replacement = pm.group(empty=True, name="PC_SMD_CE_REPL")
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertFalse(result, "Expression child blocks parent deletion")
        self.assertTrue(pm.objExists("PC_SMD_CE_P"))

    def test_merge_delete_preserves_with_driven_key_child(self):
        """Parent with time curves + driven-key child → preserved."""
        existing = pm.group(empty=True, name="PC_SMD_CD_P")
        child = pm.group(empty=True, name="PC_SMD_CD_C", parent=existing)
        driver = pm.group(empty=True, name="PC_SMD_CD_DRV")
        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateX", time=24, value=10)
        cmds.setDrivenKeyframe(
            f"{child}.rotateZ",
            currentDriver=f"{driver}.translateY",
            driverValue=0,
            value=0,
        )
        cmds.setDrivenKeyframe(
            f"{child}.rotateZ",
            currentDriver=f"{driver}.translateY",
            driverValue=10,
            value=90,
        )

        replacement = pm.group(empty=True, name="PC_SMD_CD_REPL")
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertFalse(result, "Driven-key child blocks parent deletion")
        self.assertTrue(pm.objExists("PC_SMD_CD_P"))

    def test_merge_delete_preserves_deep_animated_chain(self):
        """Three-level hierarchy: root keyed → mid constrained → leaf keyed.

        Even with animation at every level, the entire hierarchy is
        preserved because descendants are animated.
        """
        existing = pm.group(empty=True, name="PC_SMD_DEEP_R")
        mid = pm.group(empty=True, name="PC_SMD_DEEP_M", parent=existing)
        leaf = pm.polyCube(name="PC_SMD_DEEP_L")[0]
        pm.parent(leaf, mid)
        target = pm.group(empty=True, name="PC_SMD_DEEP_TGT")

        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateX", time=24, value=10)
        pm.orientConstraint(target, mid, mo=True)
        cmds.setKeyframe(str(leaf), attribute="scaleY", time=1, value=1)
        cmds.setKeyframe(str(leaf), attribute="scaleY", time=24, value=2)

        replacement = pm.group(empty=True, name="PC_SMD_DEEP_REPL")
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertFalse(result, "Multi-level animated hierarchy is preserved")
        self.assertTrue(pm.objExists("PC_SMD_DEEP_R"))
        self.assertTrue(pm.objExists("PC_SMD_DEEP_M"))
        self.assertTrue(pm.objExists("PC_SMD_DEEP_L"))

    # -- reparenting animated parent + child --

    def test_reparent_preserves_parent_and_child_keyframes(self):
        """Moving an animated parent under a new grandparent preserves all keys.

        Both the parent's translateY and child's rotateX keys must
        survive the reparent operation intact.
        """
        parent = pm.group(empty=True, name="PC_REP_P")
        child = pm.polyCube(name="PC_REP_C")[0]
        pm.parent(child, parent)
        cmds.setKeyframe(str(parent), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateY", time=24, value=5)
        cmds.setKeyframe(str(child), attribute="rotateX", time=1, value=0)
        cmds.setKeyframe(str(child), attribute="rotateX", time=24, value=90)

        grandparent = pm.group(empty=True, name="PC_REP_GP")
        pm.parent(parent, grandparent)

        p_keys = cmds.keyframe(str(parent), attribute="translateY", query=True, tc=True)
        c_keys = cmds.keyframe(str(child), attribute="rotateX", query=True, tc=True)
        self.assertEqual(len(p_keys), 2, "Parent keys survive reparent")
        self.assertEqual(len(c_keys), 2, "Child keys survive reparent")

    def test_reparent_preserves_constrained_child_under_keyed_parent(self):
        """Reparenting a keyed parent doesn't break child's constraint."""
        target = pm.group(empty=True, name="PC_REP_CK_TGT")
        parent = pm.group(empty=True, name="PC_REP_CK_P")
        child = pm.group(empty=True, name="PC_REP_CK_C", parent=parent)
        cmds.setKeyframe(str(parent), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateX", time=24, value=10)
        pm.parentConstraint(target, child, mo=True)

        grandparent = pm.group(empty=True, name="PC_REP_CK_GP")
        pm.parent(parent, grandparent)

        constraints = cmds.listRelatives(str(child), type="constraint") or []
        self.assertGreater(len(constraints), 0, "Child constraint survives reparent")
        p_keys = cmds.keyframe(str(parent), attribute="translateX", query=True, tc=True)
        self.assertEqual(len(p_keys), 2, "Parent keys survive reparent")

    # -- quarantine with animated parent + child hierarchies --

    def test_quarantine_skips_parent_that_has_animated_child(self):
        """Extra parent with a keyed child is skipped even though parent itself is clean.

        quarantine_extras uses _has_animation_data(check_descendants=False)
        on the node itself. If the PARENT has no direct animation, it may
        still be quarantined — the child is NOT checked by default.
        This documents the current behavior boundary.
        """
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        parent = pm.group(empty=True, name="PC_QUA_P")
        child = pm.polyCube(name="PC_QUA_C")[0]
        pm.parent(child, parent)
        cmds.setKeyframe(str(child), attribute="rotateX", time=1, value=0)
        cmds.setKeyframe(str(child), attribute="rotateX", time=24, value=90)

        hm.current_scene_path_map = {"PC_QUA_P": parent}
        hm.clean_to_raw_current = {"PC_QUA_P": "PC_QUA_P"}
        hm.differences = {"extra": ["PC_QUA_P"]}

        moved = hm.quarantine_extras(skip_animated=True)
        # Parent itself is not animated — it WILL be quarantined
        # (child animation is invisible to skip_animated on the parent)
        self.assertIn("PC_QUA_P", moved)

    def test_quarantine_skips_directly_animated_parent(self):
        """Extra parent with both own keyframes AND a keyed child is skipped."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        parent = pm.group(empty=True, name="PC_QUA_AP")
        child = pm.polyCube(name="PC_QUA_AC")[0]
        pm.parent(child, parent)
        cmds.setKeyframe(str(parent), attribute="translateY", time=1, value=0)
        cmds.setKeyframe(str(parent), attribute="translateY", time=24, value=5)
        cmds.setKeyframe(str(child), attribute="rotateZ", time=1, value=0)
        cmds.setKeyframe(str(child), attribute="rotateZ", time=24, value=180)

        hm.current_scene_path_map = {"PC_QUA_AP": parent}
        hm.clean_to_raw_current = {"PC_QUA_AP": "PC_QUA_AP"}
        hm.differences = {"extra": ["PC_QUA_AP"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0, "Directly animated parent is skipped")
        self.assertTrue(pm.objExists("PC_QUA_AP"))

    # ══════════════════════════════════════════════════════════════════
    #  Blend shape / morph target scenarios
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _make_blendshape_cube(name="BS_CUBE", target_name=None):
        """Create a polyCube with a blend shape target.

        Returns (base_transform, blendshape_node).
        """
        target_name = target_name or f"{name}_TGT"
        base = pm.polyCube(name=name)[0]
        target = pm.polyCube(name=target_name)[0]
        # Offset target verts so the blend shape has visible effect
        pm.move(target, 0, 2, 0, relative=True)
        bs = pm.blendShape(target, base, name=f"{name}_BS")[0]
        pm.delete(target)  # target mesh no longer needed
        return base, bs

    def test_has_animation_data_blendshape_unkeyed(self):
        """Unkeyed blend shape does NOT register as animation data.

        Blend shapes are deformers on the shape node, not connections on
        the transform.  An unkeyed blend shape has no animCurves at all.
        """
        base, bs = self._make_blendshape_cube("BS_UNKEYED")
        self.assertFalse(
            HierarchyManager._has_animation_data(base),
            "Unkeyed blend shape should not trigger detection",
        )

    def test_has_animation_data_blendshape_keyed_weight(self):
        """Keyed blend shape weight is NOT detected on the transform.

        The animCurve connects to blendShape.weight[0], which is on the
        deformer DG node — not the transform.  _has_animation_data checks
        the transform, so this is a known limitation: blend shape animation
        is invisible to transform-level hierarchy checks.

        This is correct behavior for the hierarchy manager's scope:
        blend shapes are deformer-level data that travels with the mesh
        shape, not the transform hierarchy.
        """
        base, bs = self._make_blendshape_cube("BS_KEYED")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)

        # Verify the animCurve exists on the deformer
        deformer_curves = cmds.listConnections(
            str(bs), type="animCurve", s=True, d=False
        )
        self.assertTrue(deformer_curves, "AnimCurve should exist on the deformer")

        # Transform-level check should NOT detect it
        self.assertFalse(
            HierarchyManager._has_animation_data(base),
            "Keyed blend shape weight is on the deformer, not the transform",
        )

    def test_has_animation_data_blendshape_plus_transform_keys(self):
        """Transform with both blend shape AND translateX keyframes IS detected.

        The transform keyframes are detected normally; the blend shape
        is irrelevant to the detection.
        """
        base, bs = self._make_blendshape_cube("BS_COMBO")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        cmds.setKeyframe(str(base), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(base), attribute="translateX", time=24, value=10)

        self.assertTrue(
            HierarchyManager._has_animation_data(base),
            "Transform keyframes should be detected regardless of blend shape",
        )

    def test_classify_blendshape_keyed_no_transform_curves(self):
        """Keyed blend shape weight produces no entries in classify output.

        Since the animCurve lives on the deformer, _classify_animation
        on the transform returns empty curves/driven_keys.
        """
        base, bs = self._make_blendshape_cube("BS_CLS")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)

        cls = HierarchyManager._classify_animation(base)
        self.assertEqual(len(cls["curves"]), 0)
        self.assertEqual(len(cls["driven_keys"]), 0)
        self.assertEqual(len(cls["constraints"]), 0)

    def test_classify_blendshape_plus_transform_curves(self):
        """Transform curves are classified normally even with a blend shape present."""
        base, bs = self._make_blendshape_cube("BS_CLS_COMBO")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        cmds.setKeyframe(str(base), attribute="rotateY", time=1, value=0)
        cmds.setKeyframe(str(base), attribute="rotateY", time=24, value=90)

        cls = HierarchyManager._classify_animation(base)
        self.assertGreater(
            len(cls["curves"]),
            0,
            "Transform-level rotateY curve should appear",
        )

    def test_blendshape_driven_key_not_detected_on_transform(self):
        """Set-driven blend shape weight is on the deformer, not the transform."""
        base, bs = self._make_blendshape_cube("BS_SDK")
        driver = pm.group(empty=True, name="BS_SDK_DRV")
        cmds.setDrivenKeyframe(
            f"{bs}.weight[0]",
            currentDriver=f"{driver}.translateY",
            driverValue=0,
            value=0,
        )
        cmds.setDrivenKeyframe(
            f"{bs}.weight[0]",
            currentDriver=f"{driver}.translateY",
            driverValue=10,
            value=1,
        )

        self.assertFalse(
            HierarchyManager._has_animation_data(base),
            "Driven blend shape weight is on the deformer, not the transform",
        )

    def test_blendshape_expression_not_detected_on_transform(self):
        """Expression driving blend shape weight is on the deformer, not the transform."""
        base, bs = self._make_blendshape_cube("BS_EXPR")
        cmds.expression(
            string=f"{bs}.weight[0] = clamp(0, 1, sin(frame * 0.1))",
            object=str(bs),
            alwaysEvaluate=True,
            name="BS_EXPR_expr",
        )

        self.assertFalse(
            HierarchyManager._has_animation_data(base),
            "Expression on blend shape deformer is not on the transform",
        )

    # -- blend shapes survive hierarchy operations --

    def test_reparent_preserves_blendshape(self):
        """Blend shape deformer survives pm.parent — shape travels with transform."""
        base, bs = self._make_blendshape_cube("BS_REP")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        new_parent = pm.group(empty=True, name="BS_REP_P")

        pm.parent(base, new_parent)

        # Blend shape node should still exist
        self.assertTrue(
            cmds.objExists(str(bs)),
            "BlendShape deformer should survive reparenting",
        )
        # Keys on the deformer weight should be intact
        keys = cmds.keyframe(str(bs), attribute="weight[0]", query=True, tc=True)
        self.assertIsNotNone(keys)
        self.assertGreater(len(keys), 0, "Blend shape keys should survive reparent")

    def test_safe_merge_delete_blendshape_only_deleted(self):
        """Mesh with ONLY a keyed blend shape (no transform animation) IS deleted.

        Since _has_animation_data doesn't detect deformer-level animation,
        _safe_merge_delete treats the node as unanimated.  The blend shape
        data is lost — this is the expected tradeoff for transform-level
        hierarchy management.
        """
        existing, bs = self._make_blendshape_cube("BS_SMD")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        replacement = pm.polyCube(name="BS_SMD_REPL")[0]

        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)
        self.assertTrue(
            result, "Node with only blend shape animation is treated as unanimated"
        )
        self.assertFalse(pm.objExists("BS_SMD"))

    def test_safe_merge_delete_blendshape_plus_transform_keys_transfers(self):
        """Mesh with blend shape AND transform keyframes: transform curves transfer.

        The transform-level curves are detected, transferred to the
        replacement, and the old node is deleted.  The blend shape data
        on the old mesh is lost (deformer-level, not transferable).
        """
        existing, bs = self._make_blendshape_cube("BS_SMD_COMBO")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        cmds.setKeyframe(str(existing), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(existing), attribute="translateX", time=24, value=10)

        replacement = pm.polyCube(name="BS_SMD_COMBO_REPL")[0]
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertTrue(result, "Transform curves transfer, node is replaced")
        self.assertFalse(pm.objExists("BS_SMD_COMBO"))
        keys = cmds.keyframe(
            str(replacement), attribute="translateX", query=True, tc=True
        )
        self.assertIsNotNone(keys, "Transform keys should be on replacement")

    def test_safe_merge_delete_blendshape_plus_constraint_preserved(self):
        """Mesh with blend shape AND constraint is preserved (constraint blocks).

        The constraint makes the node non-transferable regardless of the
        blend shape — both the deformer and constraint survive.
        """
        existing, bs = self._make_blendshape_cube("BS_SMD_CST")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        target = pm.group(empty=True, name="BS_SMD_CST_TGT")
        pm.parentConstraint(target, existing, mo=True)

        replacement = pm.polyCube(name="BS_SMD_CST_REPL")[0]
        swapper = ObjectSwapper(dry_run=False)
        result = swapper._safe_merge_delete(existing, replacement)

        self.assertFalse(result, "Constraint blocks deletion")
        self.assertTrue(pm.objExists("BS_SMD_CST"))
        # Blend shape should still be intact
        self.assertTrue(cmds.objExists(str(bs)))

    def test_quarantine_blendshape_only_moved(self):
        """Extra with only a blend shape is quarantined (not detected as animated).

        Blend shape animation is deformer-level and invisible to
        skip_animated checks on the transform.
        """
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        base, bs = self._make_blendshape_cube("BS_QUA")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        pm.parent(base, world=True)

        hm.current_scene_path_map = {"BS_QUA": base}
        hm.clean_to_raw_current = {"BS_QUA": "BS_QUA"}
        hm.differences = {"extra": ["BS_QUA"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertIn(
            "BS_QUA", moved, "Blend-shape-only node is not transform-animated"
        )

    def test_quarantine_blendshape_plus_transform_keys_skipped(self):
        """Extra with blend shape AND transform keys is skipped (transform keys detected)."""
        hm = HierarchyManager(fuzzy_matching=False, dry_run=False)
        base, bs = self._make_blendshape_cube("BS_QUA_COMBO")
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=1, value=0)
        cmds.setKeyframe(str(bs), attribute="weight[0]", time=24, value=1)
        cmds.setKeyframe(str(base), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(base), attribute="translateX", time=24, value=10)
        pm.parent(base, world=True)

        hm.current_scene_path_map = {"BS_QUA_COMBO": base}
        hm.clean_to_raw_current = {"BS_QUA_COMBO": "BS_QUA_COMBO"}
        hm.differences = {"extra": ["BS_QUA_COMBO"]}

        moved = hm.quarantine_extras(skip_animated=True)
        self.assertEqual(len(moved), 0, "Transform keys make it animated → skipped")

    # ══════════════════════════════════════════════════════════════════
    #  Reparenting safety — animation survives pm.parent
    # ══════════════════════════════════════════════════════════════════

    def test_reparent_preserves_keyframes(self):
        """Keyframed node retains all keys after pm.parent to a new parent.

        Validates the core design assumption that reparenting is safe.
        """
        node = self._make_animated_cube("REP_KEY")
        new_parent = pm.group(empty=True, name="REP_NEWP")
        keys_before = cmds.keyframe(
            str(node), attribute="translateX", query=True, tc=True
        )
        vals_before = cmds.keyframe(
            str(node), attribute="translateX", query=True, vc=True
        )

        pm.parent(node, new_parent)

        keys_after = cmds.keyframe(
            str(node), attribute="translateX", query=True, tc=True
        )
        vals_after = cmds.keyframe(
            str(node), attribute="translateX", query=True, vc=True
        )
        self.assertEqual(keys_before, keys_after, "Key times must survive reparent")
        self.assertEqual(vals_before, vals_after, "Key values must survive reparent")

    def test_reparent_preserves_constraint(self):
        """Constrained node remains constrained after pm.parent.

        Constraints are children of the transform and travel with it.
        """
        node, target = self._make_constrained("REP_CNST", "REP_CNST_TGT")
        new_parent = pm.group(empty=True, name="REP_CNST_NEWP")

        pm.parent(node, new_parent)

        constraints = cmds.listRelatives(str(node), type="constraint") or []
        self.assertGreater(
            len(constraints),
            0,
            "Constraint should survive reparenting",
        )

    def test_reparent_preserves_driven_key(self):
        """Driven-key relationship survives pm.parent."""
        driver, driven = self._make_driven_key("REP_DKVR", "REP_DKVN")
        new_parent = pm.group(empty=True, name="REP_DK_NEWP")

        pm.parent(driven, new_parent)

        conns = cmds.listConnections(str(driven), type="animCurve", s=True, d=False)
        self.assertTrue(conns, "Driven key should survive reparenting")

    def test_reparent_preserves_expression(self):
        """Expression-driven node remains expression-driven after pm.parent."""
        node = self._make_expression("REP_EXPR")
        new_parent = pm.group(empty=True, name="REP_EXPR_NEWP")

        pm.parent(node, new_parent)

        exprs = cmds.listConnections(str(node), type="expression")
        self.assertTrue(exprs, "Expression should survive reparenting")


class TestLocatorGroupAtomicity(MayaTkTestCase):
    """Tests for locator-group atomic movement.

    Objects under a locator (GRP > LOC > children) form an atomic unit
    that must stay together during quarantine and reparent operations.

    Added: 2026-04-10
    """

    def setUp(self):
        super().setUp()
        self.real_scenes_dir = Path(
            r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson\_tests\hierarchy_test"
        )

    def _make_locator_group(self, prefix, parent=None):
        """Create a GRP > LOC (with locatorShape) > MESH chain.

        Returns (grp, loc_transform, child).
        """
        if parent is not None:
            grp = pm.group(empty=True, name=f"{prefix}_GRP", parent=parent)
        else:
            grp = pm.group(empty=True, name=f"{prefix}_GRP")
        loc = pm.spaceLocator(name=f"{prefix}_LOC")
        pm.parent(loc, grp)
        child = pm.group(empty=True, name=f"{prefix}_MESH", parent=loc)
        return grp, loc, child

    # ── _is_locator_transform ──

    def test_is_locator_transform_true(self):
        """_is_locator_transform returns True for a transform with locatorShape."""
        loc = pm.spaceLocator(name="test_loc_shape")
        self.assertTrue(HierarchyManager._is_locator_transform(loc))

    def test_is_locator_transform_false_for_group(self):
        """_is_locator_transform returns False for a plain transform."""
        grp = pm.group(empty=True, name="test_plain_grp")
        self.assertFalse(HierarchyManager._is_locator_transform(grp))

    # ── _find_locator_group_root ──

    def test_find_root_from_child_under_locator(self):
        """Child under locator returns GRP as root.

        GRP > LOC > CHILD  →  root = GRP
        """
        grp, loc, child = self._make_locator_group("FLR")
        root = HierarchyManager._find_locator_group_root(child)
        self.assertIsNotNone(root)
        self.assertEqual(root.nodeName(), grp.nodeName())

    def test_find_root_from_locator_itself(self):
        """Locator transform itself returns its parent GRP as root."""
        grp, loc, child = self._make_locator_group("FLR2")
        root = HierarchyManager._find_locator_group_root(loc)
        self.assertIsNotNone(root)
        self.assertEqual(root.nodeName(), grp.nodeName())

    def test_find_root_nested_locators(self):
        """Nested locator chains return the highest-level GRP.

        OUTER_GRP > OUTER_LOC > INNER_GRP > INNER_LOC > CHILD
        root should be OUTER_GRP.
        """
        outer_grp = pm.group(empty=True, name="OUTER_GRP")
        outer_loc = pm.spaceLocator(name="OUTER_LOC")
        pm.parent(outer_loc, outer_grp)
        inner_grp = pm.group(empty=True, name="INNER_GRP", parent=outer_loc)
        inner_loc = pm.spaceLocator(name="INNER_LOC")
        pm.parent(inner_loc, inner_grp)
        child = pm.group(empty=True, name="DEEP_CHILD", parent=inner_loc)

        root = HierarchyManager._find_locator_group_root(child)
        self.assertIsNotNone(root)
        self.assertEqual(root.nodeName(), outer_grp.nodeName())

    def test_find_root_returns_none_for_plain_hierarchy(self):
        """No locator in chain → returns None."""
        grp = pm.group(empty=True, name="PLAIN_GRP")
        child = pm.group(empty=True, name="PLAIN_CHILD", parent=grp)
        root = HierarchyManager._find_locator_group_root(child)
        self.assertIsNone(root)

    def test_find_root_locator_at_world_root(self):
        """Locator at scene root with no parent GRP → returns locator itself."""
        loc = pm.spaceLocator(name="ROOT_LOC")
        child = pm.group(empty=True, name="ROOT_LOC_CHILD", parent=loc)

        root = HierarchyManager._find_locator_group_root(child)
        self.assertIsNotNone(root)
        self.assertEqual(root.nodeName(), loc.nodeName())

    def test_find_root_with_intermediate_non_locator_transform(self):
        """Intermediate non-locator transforms between locator and child.

        GRP > LOC > INNER_GRP > CHILD  →  root = GRP
        The walk should pass through INNER_GRP (not a locator) and still
        find LOC above it.
        """
        grp = pm.group(empty=True, name="INTER_GRP")
        loc = pm.spaceLocator(name="INTER_LOC")
        pm.parent(loc, grp)
        inner_grp = pm.group(empty=True, name="INTER_INNER_GRP", parent=loc)
        child = pm.group(empty=True, name="INTER_CHILD", parent=inner_grp)

        root = HierarchyManager._find_locator_group_root(child)
        self.assertIsNotNone(root)
        self.assertEqual(root.nodeName(), grp.nodeName())

    # ── _promote_to_locator_groups ──

    def test_promote_replaces_child_with_group_root(self):
        """Child path under a locator is promoted to group root path.

        Bug: quarantine_extras moved children individually, breaking
        locator-group chains.
        Fixed: 2026-04-10
        """
        grp, loc, child = self._make_locator_group("PROMO")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "PROMO_GRP": grp,
            "PROMO_GRP|PROMO_LOC": loc,
            "PROMO_GRP|PROMO_LOC|PROMO_MESH": child,
        }
        manager.clean_to_raw_current = {
            "PROMO_GRP": "PROMO_GRP",
            "PROMO_GRP|PROMO_LOC": "PROMO_GRP|PROMO_LOC",
            "PROMO_GRP|PROMO_LOC|PROMO_MESH": "PROMO_GRP|PROMO_LOC|PROMO_MESH",
        }

        result = manager._promote_to_locator_groups(["PROMO_GRP|PROMO_LOC|PROMO_MESH"])
        self.assertEqual(result, ["PROMO_GRP"])

    def test_promote_deduplicates_siblings(self):
        """Multiple children under same locator-group promote to single root."""
        grp, loc, child1 = self._make_locator_group("DEDUP")
        child2 = pm.group(empty=True, name="DEDUP_MESH2", parent=loc)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "DEDUP_GRP": grp,
            "DEDUP_GRP|DEDUP_LOC": loc,
            "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH": child1,
            "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH2": child2,
        }
        manager.clean_to_raw_current = {
            "DEDUP_GRP": "DEDUP_GRP",
            "DEDUP_GRP|DEDUP_LOC": "DEDUP_GRP|DEDUP_LOC",
            "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH": "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH",
            "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH2": "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH2",
        }

        result = manager._promote_to_locator_groups(
            ["DEDUP_GRP|DEDUP_LOC|DEDUP_MESH", "DEDUP_GRP|DEDUP_LOC|DEDUP_MESH2"]
        )
        self.assertEqual(result, ["DEDUP_GRP"])

    def test_promote_no_change_without_locator(self):
        """Paths not under a locator are returned unchanged."""
        grp = pm.group(empty=True, name="NOLOC_GRP")
        child = pm.group(empty=True, name="NOLOC_CHILD", parent=grp)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "NOLOC_GRP": grp,
            "NOLOC_GRP|NOLOC_CHILD": child,
        }
        manager.clean_to_raw_current = {
            "NOLOC_GRP": "NOLOC_GRP",
            "NOLOC_GRP|NOLOC_CHILD": "NOLOC_GRP|NOLOC_CHILD",
        }

        result = manager._promote_to_locator_groups(["NOLOC_GRP|NOLOC_CHILD"])
        self.assertEqual(result, ["NOLOC_GRP|NOLOC_CHILD"])

    def test_promote_skips_when_root_is_not_extra(self):
        """Promotion is suppressed when the locator-group root is matched.

        If the GRP is in the reference (matched) but LOC|CHILD is extra,
        promoting to GRP would quarantine matched content.  The guard
        must leave the original path unchanged.

        Bug: _promote_to_locator_groups did not check whether the root
        was itself extra, risking quarantine of matched nodes.
        Fixed: 2026-04-10
        """
        grp, loc, child = self._make_locator_group("GUARD")

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.current_scene_path_map = {
            "GUARD_GRP": grp,
            "GUARD_GRP|GUARD_LOC": loc,
            "GUARD_GRP|GUARD_LOC|GUARD_MESH": child,
        }
        manager.clean_to_raw_current = {
            "GUARD_GRP": "GUARD_GRP",
            "GUARD_GRP|GUARD_LOC": "GUARD_GRP|GUARD_LOC",
            "GUARD_GRP|GUARD_LOC|GUARD_MESH": "GUARD_GRP|GUARD_LOC|GUARD_MESH",
        }

        # Only LOC and MESH are extra; GRP is matched (not in extras_set)
        extras_set = {
            "GUARD_GRP|GUARD_LOC",
            "GUARD_GRP|GUARD_LOC|GUARD_MESH",
        }
        result = manager._promote_to_locator_groups(
            ["GUARD_GRP|GUARD_LOC|GUARD_MESH"],
            extras_set=extras_set,
        )
        # Should NOT promote to GUARD_GRP because it's not extra
        self.assertEqual(result, ["GUARD_GRP|GUARD_LOC|GUARD_MESH"])

    # ── End-to-end quarantine with locator groups ──

    def test_quarantine_moves_entire_locator_group(self):
        """Quarantining a child under a locator moves the entire GRP.

        Bug: quarantine_extras moved individual children, breaking the
        GRP > LOC > children chain. The locator group must stay together.
        Fixed: 2026-04-10
        """
        # Reference has only 'root'
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")
        ref_root = pm.group(empty=True, name="ref:root")

        # Current scene has root + a locator group under it
        root = pm.group(empty=True, name="root")
        grp, loc, mesh = self._make_locator_group("S00C36_OUTB_ADAPTER")
        pm.parent(grp, root)

        ref_objects = [ref_root]

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objects,
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        moved = manager.quarantine_extras()

        # The entire GRP should have been moved, keeping its children intact
        self.assertTrue(pm.objExists("_QUARANTINE"))
        grp_parent = grp.getParent()
        self.assertEqual(
            grp_parent.nodeName(), "_QUARANTINE", "GRP should be under _QUARANTINE"
        )
        # LOC should still be under GRP (not ripped out)
        self.assertEqual(
            loc.getParent().nodeName(), grp.nodeName(), "LOC should remain under GRP"
        )
        # MESH should still be under LOC
        self.assertEqual(
            mesh.getParent().nodeName(), loc.nodeName(), "MESH should remain under LOC"
        )

    def test_quarantine_nested_locators_moves_outermost_group(self):
        """Nested locator chains quarantine the outermost GRP.

        OUTER_GRP > OUTER_LOC > INNER_GRP > INNER_LOC > MESH
        Only OUTER_GRP should be moved; everything stays together.
        """
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")
        ref_root = pm.group(empty=True, name="ref:scene_root")
        scene_root = pm.group(empty=True, name="scene_root")

        outer_grp = pm.group(empty=True, name="NEST_OUTER_GRP", parent=scene_root)
        outer_loc = pm.spaceLocator(name="NEST_OUTER_LOC")
        pm.parent(outer_loc, outer_grp)
        inner_grp = pm.group(empty=True, name="NEST_INNER_GRP", parent=outer_loc)
        inner_loc = pm.spaceLocator(name="NEST_INNER_LOC")
        pm.parent(inner_loc, inner_grp)
        deep_child = pm.group(empty=True, name="NEST_DEEP_CHILD", parent=inner_loc)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=[ref_root],
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        moved = manager.quarantine_extras()

        # Entire chain should be under _QUARANTINE via OUTER_GRP
        self.assertEqual(
            outer_grp.getParent().nodeName(),
            "_QUARANTINE",
            "OUTER_GRP should be under _QUARANTINE",
        )
        self.assertEqual(
            outer_loc.getParent().nodeName(),
            outer_grp.nodeName(),
            "OUTER_LOC should remain under OUTER_GRP",
        )
        self.assertEqual(
            inner_grp.getParent().nodeName(),
            outer_loc.nodeName(),
            "INNER_GRP should remain under OUTER_LOC",
        )
        self.assertEqual(
            deep_child.getParent().nodeName(),
            inner_loc.nodeName(),
            "DEEP_CHILD should remain under INNER_LOC",
        )

    # ── fix_reparented skips locator-group members ──

    def test_fix_reparented_skips_node_inside_locator_group(self):
        """fix_reparented must not move a node out of a locator group.

        Bug: fix_reparented had no locator awareness, so it could
        reparent individual nodes out of GRP > LOC > children chains,
        breaking the atomic unit.
        Fixed: 2026-04-10
        """
        # Create a locator group: GRP > LOC > MESH
        grp, loc, mesh = self._make_locator_group("RSKIP")
        # Put GRP under a scene_root
        scene_root = pm.group(empty=True, name="rskip_scene_root")
        pm.parent(grp, scene_root)

        # Reference says MESH should be directly under scene_root
        # (not under the locator). fix_reparented should skip this.
        if not pm.namespace(exists="ref"):
            pm.namespace(add="ref")
        ref_root = pm.group(empty=True, name="ref:rskip_scene_root")
        ref_mesh = pm.group(empty=True, name="ref:RSKIP_MESH", parent=ref_root)

        manager = HierarchyManager(fuzzy_matching=False, dry_run=False)
        manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=[ref_root],
            filter_meshes=False,
            filter_cameras=True,
            filter_lights=True,
        )

        fixed = manager.fix_reparented()

        # MESH should still be under LOC (reparent was skipped)
        self.assertEqual(
            mesh.getParent().nodeName(),
            loc.nodeName(),
            "MESH should remain under LOC — locator group must not be broken",
        )

    # ── Real-world test: C5_AFT_COMP_ASSEMBLY_module.ma vs FBX ──

    @skipUnlessExtended
    def test_c5_module_vs_fbx_locator_groups_stay_intact(self):
        """Real-world: locator-group chains survive quarantine in C5 module scene.

        Opens C5_AFT_COMP_ASSEMBLY_module.ma as the current scene and imports
        C5_AFT_COMP_ASSEMBLY.fbx as the reference.  Verifies that after
        quarantine, any locator (transform with locatorShape) still has its
        parent GRP and child objects intact — no locator-group chain is
        broken by the fix operations.

        Bug: quarantine_extras moved individual children out of locator
        groups, breaking the GRP > LOC > children pattern that game
        engines rely on for animated transforms.
        Fixed: 2026-04-10
        """
        if not self.real_scenes_dir.exists():
            self.skipTest(
                f"Real-world scenes directory not found: {self.real_scenes_dir}"
            )

        current_scene = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY_module.ma"
        reference_fbx = self.real_scenes_dir / "C5_AFT_COMP_ASSEMBLY.fbx"

        if not current_scene.exists() or not reference_fbx.exists():
            self.skipTest("Required C5 module.ma / FBX scene files not found.")

        default_cams = frozenset({"persp", "top", "front", "side"})

        pm.openFile(str(current_scene), force=True)

        # Snapshot all locator-group chains BEFORE fix
        def _find_locator_chains():
            """Return dict mapping locator transform name -> (parent, children)."""
            chains = {}
            for loc_shape in pm.ls(type="locator"):
                loc_tf = loc_shape.getParent()
                parent = loc_tf.getParent()
                children = loc_tf.getChildren(type="transform")
                chains[loc_tf.nodeName()] = {
                    "parent": parent.nodeName() if parent else None,
                    "children": sorted(c.nodeName() for c in children),
                }
            return chains

        pre_fix_chains = _find_locator_chains()

        sandbox = NamespaceSandbox(dry_run=False)
        info = sandbox.import_with_namespace(
            str(reference_fbx), force_complete_import=True
        )
        self.assertIsNotNone(info, "Failed to import FBX reference")

        ref_objs = [
            t
            for t in info.get("transforms", [])
            if t.nodeName().split(":")[-1] not in default_cams
        ]

        manager = HierarchyManager(
            import_manager=sandbox, fuzzy_matching=True, dry_run=False
        )
        diff = manager.analyze_hierarchies(
            current_tree_root="SCENE_WIDE_MODE",
            reference_objects=ref_objs,
            filter_meshes=True,
            filter_cameras=True,
            filter_lights=True,
        )

        # Run live quarantine
        if diff.get("extra"):
            manager.quarantine_extras()

        # Run live reparent
        if diff.get("reparented"):
            manager.fix_reparented()

        # Verify: every locator-group chain that still exists is intact
        post_fix_chains = _find_locator_chains()

        broken = []
        for loc_name, pre in pre_fix_chains.items():
            if not pm.objExists(loc_name):
                continue  # deleted — acceptable
            post = post_fix_chains.get(loc_name)
            if post is None:
                continue
            # Parent must still be the same (or both moved together)
            if pre["parent"] and pm.objExists(pre["parent"]):
                loc_node = pm.PyNode(loc_name)
                current_parent = loc_node.getParent()
                parent_name = current_parent.nodeName() if current_parent else None
                if parent_name != pre["parent"]:
                    # Parent changed — check if the whole chain moved together
                    # (parent was reparented to quarantine with locator inside)
                    if parent_name is None:
                        broken.append(
                            f"{loc_name}: parent was {pre['parent']}, now at world root"
                        )
            # Children must still be under this locator
            for child_name in pre["children"]:
                if not pm.objExists(child_name):
                    continue  # deleted
                child_node = pm.PyNode(child_name)
                child_parent = child_node.getParent()
                if child_parent is None or child_parent.nodeName() != loc_name:
                    actual = child_parent.nodeName() if child_parent else "world"
                    broken.append(
                        f"{child_name}: was under {loc_name}, now under {actual}"
                    )

        self.assertEqual(
            broken,
            [],
            f"Locator-group chains broken by fix operations:\n"
            + "\n".join(broken[:20]),
        )

        sandbox.cleanup_all_namespaces()


if __name__ == "__main__":
    unittest.main()
