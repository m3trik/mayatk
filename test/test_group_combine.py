# !/usr/bin/python
# coding=utf-8
"""
Test Suite for Grouping and Combining operations in EditUtils.
"""
import unittest
import mayatk as mtk
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.mat_utils._mat_utils import MatUtils

from base_test import MayaTkTestCase
import maya.cmds as cmds


class TestGroupCombine(MayaTkTestCase):
    """Tests for group_objects and combine_objects."""

    def setUp(self):
        super().setUp()
        # Create some test objects
        self.cube1 = cmds.polyCube(n="cube1")[0]
        self.cube2 = cmds.polyCube(n="cube2")[0]
        self.cube3 = cmds.polyCube(n="cube3")[0]

        # Assign materials
        self.mat1 = cmds.shadingNode("lambert", asShader=True, n="mat1")
        self.mat2 = cmds.shadingNode("lambert", asShader=True, n="mat2")

        cmds.select(self.cube1, self.cube2)
        cmds.hyperShade(assign=self.mat1)

        cmds.select(self.cube3)
        cmds.hyperShade(assign=self.mat2)

    def test_group_objects(self):
        """Test EditUtils.group_objects."""
        # Group with explicit list to ensure order
        grp = EditUtils.group_objects([self.cube1, self.cube2])

        self.assertTrue(cmds.objExists(str(grp)))
        self.assertEqual(cmds.nodeType(grp), "transform")

        # Check children
        children = cmds.listRelatives(str(grp), children=True) or []
        c1_short = str(self.cube1).split("|")[-1]
        c2_short = str(self.cube2).split("|")[-1]
        children_short = [c.split("|")[-1] for c in children]
        self.assertIn(c1_short, children_short)
        self.assertIn(c2_short, children_short)

        # Check naming (should be named after first object)
        # Use nodeName() to avoid pipe issues if full path is returned
        self.assertTrue(grp.split('|')[-1].split(':')[-1].startswith("cube1"))

    def test_combine_objects_basic(self):
        """Test basic combine (no grouping)."""
        combined = EditUtils.combine_objects([self.cube1, self.cube2])

        self.assertTrue(cmds.objExists(combined))
        self.assertEqual(cmds.nodeType(combined), "transform")

        # Should be one mesh now
        # Note: combine_objects renames the result to the first object's name (cube1)
        self.assertTrue(cmds.objExists("cube1"))
        self.assertFalse(cmds.objExists("cube2"))

    def test_combine_objects_material_grouping(self):
        """Test combine with group_by_material=True."""
        # cube1, cube2 -> mat1
        # cube3 -> mat2
        # Should result in 2 meshes: (cube1+cube2) and (cube3)
        # But combine requires >1 object. cube3 is alone, so it might be skipped or just returned?
        # The logic says: "if len(group_objs) < 2: continue"

        # Let's add another object to mat2 to ensure it combines
        cube4 = cmds.polyCube(n="cube4")[0]
        cmds.select(cube4)
        cmds.hyperShade(assign=self.mat2)

        objects = [self.cube1, self.cube2, self.cube3, cube4]

        results = EditUtils.combine_objects(objects, group_by_material=True)

        self.assertEqual(len(results), 2)

        # Verify materials of results
        # Result 1 should have mat1
        # Result 2 should have mat2

        # We can't easily predict order, so check both
        mats_found = []
        for res in results:
            shapes = cmds.listRelatives(str(res), shapes=True, ni=True) or []
            # Get assigned shader
            # Simple check: select and graph? or use MatUtils
            mats = MatUtils.get_mats(res)
            mats_found.extend(mats)

        self.assertIn(self.mat1, mats_found)
        self.assertIn(self.mat2, mats_found)

    def test_combine_objects_clustering(self):
        """Test combine with clustering."""
        # Create 2 cubes far apart with same material
        c1 = cmds.polyCube(n="c1")[0]
        c2 = cmds.polyCube(n="c2")[0]
        cmds.move(100, 0, 0, c2)  # Move 100 units away

        # Create 2 cubes close to c1
        c3 = cmds.polyCube(n="c3")[0]
        cmds.move(2, 0, 0, c3)

        # Create 2 cubes close to c2
        c4 = cmds.polyCube(n="c4")[0]
        cmds.move(102, 0, 0, c4)

        # Assign same material to all
        cmds.select(c1, c2, c3, c4)
        cmds.hyperShade(assign=self.mat1)

        # Threshold 10. c1-c3 are close. c2-c4 are close. (c1/c3) far from (c2/c4).
        # Should result in 2 clusters -> 2 combined meshes.

        results = EditUtils.combine_objects(
            [c1, c2, c3, c4],
            group_by_material=True,
            cluster_by_distance=True,
            threshold=50.0,
        )

        self.assertEqual(len(results), 2)

    def test_combine_preserves_parent_group(self):
        """Verify combined object is placed under the same parent group.

        Bug: When all children of a group were combined, the group was
        auto-deleted by Maya (became empty) before _finalize_reparent could
        parent the result back. The temp-null was only created for single-child
        parents, not when all children were consumed by the operation.
        Fixed: 2026-02-26
        """
        grp = cmds.group(em=True, n="container_grp")
        c1 = cmds.polyCube(n="child_a")[0]
        c2 = cmds.polyCube(n="child_b")[0]
        cmds.parent(c1, grp)
        cmds.parent(c2, grp)

        combined = EditUtils.combine_objects([c1, c2])

        self.assertTrue(
            cmds.objExists(grp),
            "Parent group should still exist after combine",
        )
        self.assertTrue(
            cmds.objExists(combined),
            "Combined mesh should exist",
        )
        result_parent = cmds.listRelatives(combined, parent=True)
        self.assertTrue(
            result_parent and result_parent[0] == grp,
            f"Combined mesh should be under '{grp}', got '{result_parent}'",
        )

    def test_combine_preserves_parent_with_extra_children(self):
        """Verify combine works when parent has additional non-combined children.

        The parent group has 3 children but only 2 are combined. The parent
        should survive (it still has a remaining child) and the result should
        be reparented under it.
        """
        grp = cmds.group(em=True, n="mixed_grp")
        c1 = cmds.polyCube(n="combine_a")[0]
        c2 = cmds.polyCube(n="combine_b")[0]
        c3 = cmds.polyCube(n="keep_me")[0]
        cmds.parent(c1, grp)
        cmds.parent(c2, grp)
        cmds.parent(c3, grp)

        combined = EditUtils.combine_objects([c1, c2])

        self.assertTrue(cmds.objExists(grp))
        self.assertTrue(cmds.objExists(combined))
        result_parent = cmds.listRelatives(combined, parent=True)
        self.assertTrue(
            result_parent and result_parent[0] == grp,
            f"Combined mesh should be under '{grp}', got '{result_parent}'",
        )
        # The untouched child should still be there
        self.assertTrue(cmds.objExists("keep_me"))


if __name__ == "__main__":
    import sys

    # Manually run the test runner if executed directly
    # But we'll use the run_tests.py wrapper usually
    unittest.main()
