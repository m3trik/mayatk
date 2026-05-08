# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.display_utils module

Tests for DisplayUtils class functionality including:
- Visibility operations
- Template mode
- Isolation sets
- Visible geometry queries
"""
import unittest
import maya.cmds as cmds
import mayatk as mtk

from base_test import MayaTkTestCase


class TestDisplayUtils(MayaTkTestCase):
    """Tests for DisplayUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_display_cube")[0]
        self.sphere = cmds.polySphere(name="test_display_sphere")[0]

    def tearDown(self):
        """Clean up."""
        for obj in ["test_display_cube", "test_display_sphere"]:
            if cmds.objExists(obj):
                cmds.delete(obj)
        super().tearDown()

    def test_set_visibility_show(self):
        """Test making objects visible."""
        cmds.hide(self.cube)
        mtk.set_visibility(self.cube, visibility=True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.visibility"))

    def test_set_visibility_hide(self):
        """Test hiding objects."""
        mtk.set_visibility(self.cube, visibility=False)
        self.assertFalse(cmds.getAttr(f"{self.cube}.visibility"))

    def test_is_templated(self):
        """Test checking if object is templated."""
        result = mtk.is_templated(self.cube)
        self.assertFalse(result)

        cmds.setAttr(f"{self.cube}.template", True)
        result = mtk.is_templated(self.cube)
        self.assertTrue(result)

    def test_get_visible_geometry(self):
        """Test getting visible geometry in scene."""
        result = mtk.get_visible_geometry()
        self.assertIsInstance(result, list)
        self.assertIn(self.cube, result)


class TestExplodedView(MayaTkTestCase):
    """Regression: ExplodedView must operate on cmds-style string node names.

    Bug fixed 2026-05-07: ``arrange_objects``, ``un_explode``, and
    ``un_explode_all`` called ``node.name()`` / ``obj.original_position`` /
    ``obj_attr.node()`` — PyMEL idioms — against plain strings.
    """

    def setUp(self):
        super().setUp()
        from mayatk.display_utils.exploded_view import ExplodedView

        self.ExplodedView = ExplodedView
        # Reset the class-level cache so tests don't leak state.
        ExplodedView.exploded_objects = {}

        self.parent = cmds.group(empty=True, name="ev_parent")
        self.cubes = []
        for i in range(3):
            c = cmds.polyCube(name=f"ev_cube_{i}")[0]
            cmds.parent(c, self.parent)
            cmds.xform(c, translation=[i * 2.0, 0, 0], worldSpace=True)
            self.cubes.append(c)

    def test_arrange_objects_accepts_string_nodes(self):
        """arrange_objects builds its cache key from string names without crashing."""
        ev = self.ExplodedView()
        # Children come back from get_unique_children as strings.
        children = [
            c for c in cmds.listRelatives(self.parent, children=True, fullPath=False) or []
        ]
        # Should not raise AttributeError on .name()
        ev.arrange_objects(children)

        # Cache key must be a tuple of node-name strings.
        keys = list(self.ExplodedView.exploded_objects.keys())
        self.assertTrue(keys, "arrange_objects should populate the cache")
        self.assertTrue(
            all(isinstance(k, str) for k in keys[0]),
            f"Cache key should hold strings, got {keys[0]!r}",
        )

    def test_explode_un_explode_round_trip(self):
        """explode followed by un_explode_all must restore world positions."""
        ev = self.ExplodedView(objects=[self.parent])
        original_positions = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in self.cubes
        }

        ev.explode()
        # Every cube should now carry the original_position attr.
        for c in self.cubes:
            self.assertTrue(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"explode() should set original_position on {c}",
            )

        ev.un_explode_all()

        for c in self.cubes:
            restored = cmds.xform(c, query=True, translation=True, worldSpace=True)
            for axis, expected in enumerate(original_positions[c]):
                self.assertAlmostEqual(
                    restored[axis],
                    expected,
                    places=3,
                    msg=f"{c} axis {axis}: restored {restored[axis]} != original {expected}",
                )
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"un_explode_all should remove original_position from {c}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
