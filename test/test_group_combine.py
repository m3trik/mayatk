# !/usr/bin/python
# coding=utf-8
"""
Test Suite for Grouping and Combining operations in EditUtils.
"""
import unittest
import pymel.core as pm
import mayatk as mtk
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.mat_utils._mat_utils import MatUtils

from base_test import MayaTkTestCase


class TestGroupCombine(MayaTkTestCase):
    """Tests for group_objects and combine_objects."""

    def setUp(self):
        super().setUp()
        # Create some test objects
        self.cube1 = pm.polyCube(n="cube1")[0]
        self.cube2 = pm.polyCube(n="cube2")[0]
        self.cube3 = pm.polyCube(n="cube3")[0]

        # Assign materials
        self.mat1 = pm.shadingNode("lambert", asShader=True, n="mat1")
        self.mat2 = pm.shadingNode("lambert", asShader=True, n="mat2")

        pm.select(self.cube1, self.cube2)
        pm.hyperShade(assign=self.mat1)

        pm.select(self.cube3)
        pm.hyperShade(assign=self.mat2)

    def test_group_objects(self):
        """Test EditUtils.group_objects."""
        # Group with explicit list to ensure order
        grp = EditUtils.group_objects([self.cube1, self.cube2])

        self.assertTrue(pm.objExists(grp))
        self.assertEqual(grp.nodeType(), "transform")

        # Check children
        children = grp.getChildren()
        self.assertIn(self.cube1, children)
        self.assertIn(self.cube2, children)

        # Check naming (should be named after first object)
        # Use nodeName() to avoid pipe issues if full path is returned
        self.assertTrue(grp.nodeName().startswith("cube1"))

    def test_combine_objects_basic(self):
        """Test basic combine (no grouping)."""
        combined = EditUtils.combine_objects([self.cube1, self.cube2])

        self.assertTrue(pm.objExists(combined))
        self.assertEqual(combined.nodeType(), "transform")

        # Should be one mesh now
        # Note: combine_objects renames the result to the first object's name (cube1)
        self.assertTrue(pm.objExists("cube1"))
        self.assertFalse(pm.objExists("cube2"))

    def test_combine_objects_material_grouping(self):
        """Test combine with group_by_material=True."""
        # cube1, cube2 -> mat1
        # cube3 -> mat2
        # Should result in 2 meshes: (cube1+cube2) and (cube3)
        # But combine requires >1 object. cube3 is alone, so it might be skipped or just returned?
        # The logic says: "if len(group_objs) < 2: continue"

        # Let's add another object to mat2 to ensure it combines
        cube4 = pm.polyCube(n="cube4")[0]
        pm.select(cube4)
        pm.hyperShade(assign=self.mat2)

        objects = [self.cube1, self.cube2, self.cube3, cube4]

        results = EditUtils.combine_objects(objects, group_by_material=True)

        self.assertEqual(len(results), 2)

        # Verify materials of results
        # Result 1 should have mat1
        # Result 2 should have mat2

        # We can't easily predict order, so check both
        mats_found = []
        for res in results:
            shapes = res.getShapes()
            # Get assigned shader
            # Simple check: select and graph? or use MatUtils
            mats = MatUtils.get_mats(res)
            mats_found.extend(mats)

        self.assertIn(self.mat1, mats_found)
        self.assertIn(self.mat2, mats_found)

    def test_combine_objects_clustering(self):
        """Test combine with clustering."""
        # Create 2 cubes far apart with same material
        c1 = pm.polyCube(n="c1")[0]
        c2 = pm.polyCube(n="c2")[0]
        pm.move(c2, 100, 0, 0)  # Move 100 units away

        # Create 2 cubes close to c1
        c3 = pm.polyCube(n="c3")[0]
        pm.move(c3, 2, 0, 0)

        # Create 2 cubes close to c2
        c4 = pm.polyCube(n="c4")[0]
        pm.move(c4, 102, 0, 0)

        # Assign same material to all
        pm.select(c1, c2, c3, c4)
        pm.hyperShade(assign=self.mat1)

        # Threshold 10. c1-c3 are close. c2-c4 are close. (c1/c3) far from (c2/c4).
        # Should result in 2 clusters -> 2 combined meshes.

        results = EditUtils.combine_objects(
            [c1, c2, c3, c4],
            group_by_material=True,
            cluster_by_distance=True,
            threshold=50.0,
        )

        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    import sys

    # Manually run the test runner if executed directly
    # But we'll use the run_tests.py wrapper usually
    unittest.main()
