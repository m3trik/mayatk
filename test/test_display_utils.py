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

    def test_explode_actually_moves_objects(self):
        """explode() must change world positions, not just stamp attrs.

        Without this, an inverse round-trip can pass trivially (move nothing,
        restore nothing).
        """
        ev = self.ExplodedView(objects=[self.parent])
        before = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in self.cubes
        }

        ev.explode()

        moved = [
            c
            for c in self.cubes
            if cmds.xform(c, query=True, translation=True, worldSpace=True) != before[c]
        ]
        self.assertTrue(
            moved,
            "explode() should change world positions of at least one child",
        )

    def test_re_explode_uses_cached_positions(self):
        """A second explode of the same node-set replays cached positions.

        Locks in the documented cache-restore behavior in
        ``arrange_objects``: same nodes → same final pose, no re-simulation.
        """
        ev = self.ExplodedView(objects=[self.parent])
        ev.explode()
        first_positions = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in self.cubes
        }

        ev.un_explode_all()
        ev.explode()

        for c in self.cubes:
            replayed = cmds.xform(c, query=True, translation=True, worldSpace=True)
            for axis, expected in enumerate(first_positions[c]):
                self.assertAlmostEqual(
                    replayed[axis],
                    expected,
                    places=3,
                    msg=(
                        f"re-explode of {c} axis {axis} should hit cache "
                        f"({replayed[axis]} != {expected})"
                    ),
                )

    def test_un_explode_only_affects_targeted_hierarchy(self):
        """un_explode(objects=[group_a]) leaves group_b's exploded cubes alone."""
        parent_b = cmds.group(empty=True, name="ev_parent_b")
        cubes_b = []
        for i in range(3):
            c = cmds.polyCube(name=f"ev_cube_b_{i}")[0]
            cmds.parent(c, parent_b)
            cmds.xform(c, translation=[i * 2.0, 5.0, 0], worldSpace=True)
            cubes_b.append(c)

        ev = self.ExplodedView()
        ev.explode(objects=[self.parent, parent_b])

        ev.un_explode(objects=[self.parent])

        for c in self.cubes:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"{c} (group_a) should be un-exploded",
            )
        for c in cubes_b:
            self.assertTrue(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"{c} (group_b) should still be exploded",
            )

    def test_un_explode_skips_unexploded_objects(self):
        """un_explode filters to nodes carrying original_position only.

        Pre-explode one cube manually, then un_explode the parent: the bare
        cubes are ignored, the marked cube is restored to the stamped value.
        """
        from mayatk.node_utils.attributes._attributes import Attributes

        target_cube = self.cubes[0]
        original = cmds.xform(target_cube, query=True, translation=True, worldSpace=True)
        # Stamp original_position and push the cube somewhere obviously wrong.
        Attributes.set_attributes(target_cube, create=True, original_position=original)
        cmds.xform(target_cube, translation=[99.0, 99.0, 99.0], worldSpace=True)

        ev = self.ExplodedView()
        ev.un_explode(objects=[self.parent])

        restored = cmds.xform(target_cube, query=True, translation=True, worldSpace=True)
        for axis, expected in enumerate(original):
            self.assertAlmostEqual(
                restored[axis],
                expected,
                places=3,
                msg=f"marked cube axis {axis}: {restored[axis]} != {expected}",
            )
        # Untouched cubes should still lack the attr.
        for c in self.cubes[1:]:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"{c} should not have been touched by un_explode",
            )

    def test_toggle_explode_unexploded_to_exploded(self):
        """toggle on a fully-unexploded group runs explode()."""
        ev = self.ExplodedView(objects=[self.parent])
        ev.toggle_explode()

        for c in self.cubes:
            self.assertTrue(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"toggle should have exploded {c}",
            )

    def test_toggle_explode_exploded_to_unexploded(self):
        """toggle on a fully-exploded group runs un_explode()."""
        ev = self.ExplodedView(objects=[self.parent])
        ev.explode()
        self.assertTrue(
            all(
                cmds.attributeQuery("original_position", node=c, exists=True)
                for c in self.cubes
            ),
            "precondition: all cubes exploded",
        )

        ev.toggle_explode()

        for c in self.cubes:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"toggle should have un-exploded {c}",
            )

    def test_empty_selection_is_noop(self):
        """No selection and no objects arg → warns, returns without raising."""
        cmds.select(clear=True)
        ev = self.ExplodedView()
        # Should not raise; cmds.warning is non-fatal.
        ev.explode()
        ev.un_explode()
        # No cube should have been touched.
        for c in self.cubes:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"empty-selection explode should not have touched {c}",
            )

    def test_namespaced_nodes_round_trip(self):
        """Round-trip works on nodes inside a namespace."""
        cmds.namespace(addNamespace="ev_ns")
        ns_parent = cmds.group(empty=True, name="ev_ns:ns_parent")
        ns_cubes = []
        for i in range(3):
            c = cmds.polyCube(name=f"ev_ns:ns_cube_{i}")[0]
            cmds.parent(c, ns_parent)
            cmds.xform(c, translation=[i * 2.0, 0, 0], worldSpace=True)
            ns_cubes.append(c)

        original_positions = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in ns_cubes
        }

        ev = self.ExplodedView(objects=[ns_parent])
        ev.explode()
        ev.un_explode_all()

        for c in ns_cubes:
            restored = cmds.xform(c, query=True, translation=True, worldSpace=True)
            for axis, expected in enumerate(original_positions[c]):
                self.assertAlmostEqual(
                    restored[axis],
                    expected,
                    places=3,
                    msg=f"{c} axis {axis}: {restored[axis]} != {expected}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
