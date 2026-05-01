# !/usr/bin/python
# coding=utf-8
"""Test Suite for misc edit_utils tool modules.

Covers:
    - Graph + dijkstra + a_star (mesh_graph.py — pure Python)
    - Primitives.create_default_primitive (primitives.py)
    - Selection.select_by_type / select_children / select_hierarchy_*
      / get_available_selection_types / get_selection_categories (selection.py)

DynamicPipe is rigging-heavy and skipped here — covered by integration tests if
needed.
"""
import unittest

import maya.cmds as cmds

from mayatk.edit_utils.mesh_graph import Graph
from mayatk.edit_utils.primitives import Primitives
from mayatk.edit_utils.selection import Selection

from base_test import MayaTkTestCase, QuickTestCase


class TestGraph(QuickTestCase):
    """Pure-Python pathfinding graph — no Maya needed."""

    def _build_simple_graph(self):
        # Layout (4 nodes, distances as edge weights):
        #   1 -- 1 --> 2
        #   |         |
        #   3         1
        #   |         |
        #   3 -- 1 --> 4
        g = Graph()
        for n in (1, 2, 3, 4):
            g.add_node(n, data=(n, 0, 0))
        g.add_edge(1, 2, weight=1)
        g.add_edge(1, 3, weight=3)
        g.add_edge(2, 4, weight=1)
        g.add_edge(3, 4, weight=1)
        return g

    def test_add_node_without_data_raises(self):
        g = Graph()
        with self.assertRaises(ValueError):
            g.add_node(1, data=None)

    def test_add_edge_with_missing_node_raises(self):
        g = Graph()
        g.add_node(1, data=(0, 0, 0))
        with self.assertRaises(ValueError):
            g.add_edge(1, 2, weight=1)

    def test_add_edge_creates_undirected_edge(self):
        g = Graph()
        g.add_node(1, data=(0, 0, 0))
        g.add_node(2, data=(1, 0, 0))
        g.add_edge(1, 2, weight=5)
        self.assertEqual(g.nodes[1][2], 5)
        self.assertEqual(g.nodes[2][1], 5)

    def test_a_star_finds_shortest_path(self):
        g = self._build_simple_graph()
        path = g.a_star(1, 4)
        # Optimal path: 1 -> 2 -> 4 (cost 2). Both A* and Dijkstra agree.
        self.assertEqual(path[0], 1)
        self.assertEqual(path[-1], 4)
        # Sum of edge weights along path should be 2
        cost = sum(g.nodes[a][b] for a, b in zip(path, path[1:]))
        self.assertEqual(cost, 2)

    def test_dijkstra_finds_shortest_path(self):
        g = self._build_simple_graph()
        path = g.dijkstra(1, 4)
        self.assertEqual(path[0], 1)
        self.assertEqual(path[-1], 4)
        cost = sum(g.nodes[a][b] for a, b in zip(path, path[1:]))
        self.assertEqual(cost, 2)

    def test_find_path_dispatches_by_algorithm(self):
        g = self._build_simple_graph()
        a = g.find_path(1, 4, algorithm="a_star")
        d = g.find_path(1, 4, algorithm="dijkstra")
        self.assertEqual(a[-1], 4)
        self.assertEqual(d[-1], 4)

    def test_find_path_unknown_algorithm_raises(self):
        g = Graph()
        with self.assertRaises(ValueError):
            g.find_path(1, 2, algorithm="bfs")

    def test_no_path_returns_empty(self):
        g = Graph()
        g.add_node(1, data=(0, 0, 0))
        g.add_node(2, data=(1, 0, 0))
        # No edge between 1 and 2 — no path exists
        self.assertEqual(g.a_star(1, 2), [])
        self.assertEqual(g.dijkstra(1, 2), [])

    def test_default_heuristic_returns_zero(self):
        g = Graph()
        self.assertEqual(g.heuristic(1, 2), 0)


class TestPrimitives(MayaTkTestCase):
    """Primitives.create_default_primitive — wraps cmds.poly* commands."""

    def test_create_polygon_cube(self):
        result = Primitives.create_default_primitive("polygon", "cube")
        self.assertIsNotNone(result)
        # A cube is now in the scene
        self.assertGreater(len(cmds.ls(type="mesh")), 0)

    def test_create_polygon_sphere(self):
        before = set(cmds.ls(type="mesh"))
        Primitives.create_default_primitive("polygon", "sphere")
        after = set(cmds.ls(type="mesh"))
        self.assertGreater(len(after), len(before))

    def test_create_polygon_cylinder(self):
        before = set(cmds.ls(type="mesh"))
        Primitives.create_default_primitive("polygon", "cylinder")
        after = set(cmds.ls(type="mesh"))
        self.assertGreater(len(after), len(before))


class TestSelectionDispatch(MayaTkTestCase):
    """Selection.select_by_type — dispatches based on _SELECTION_CONFIG."""

    def test_unknown_selection_type_raises(self):
        cube = cmds.polyCube(name="sel_unknown")[0]
        with self.assertRaises(ValueError):
            Selection.select_by_type("Bogus", objects=[cube])

    def test_select_polygon_meshes_returns_meshes(self):
        # Handler scans shapes — pass cmds.ls() result so shapes are included
        cube = cmds.polyCube(name="sel_mesh")[0]
        cmds.spaceLocator(name="sel_loc")
        result = Selection.select_by_type("Polygon Meshes", objects=cmds.ls())
        self.assertIn(cube, result)

    def test_select_locators_returns_locators(self):
        cmds.polyCube(name="sel_lc_cube")
        loc = cmds.spaceLocator(name="sel_lc_loc")[0]
        result = Selection.select_by_type("Locators", objects=cmds.ls())
        self.assertIn(loc, result)


class TestSelectionHelpers(MayaTkTestCase):
    """Selection.select_children / select_hierarchy_*."""

    def test_select_children_returns_immediate_children(self):
        parent = cmds.group(em=True, name="sel_parent")
        a = cmds.group(em=True, parent=parent, name="sel_child_a")
        b = cmds.group(em=True, parent=parent, name="sel_child_b")
        cmds.group(em=True, parent=a, name="sel_grandchild")

        result = Selection.select_children([parent])
        # Only direct children
        self.assertIn(a, result)
        self.assertIn(b, result)

    def test_select_hierarchy_above_returns_ancestors(self):
        grand = cmds.group(em=True, name="sel_g")
        parent = cmds.group(em=True, parent=grand, name="sel_p")
        child = cmds.group(em=True, parent=parent, name="sel_c")

        result = Selection.select_hierarchy_above([child])
        # Should return ancestors (parent + grand)
        self.assertGreater(len(result), 0)

    def test_select_hierarchy_below_returns_descendants(self):
        grand = cmds.group(em=True, name="sel_g2")
        parent = cmds.group(em=True, parent=grand, name="sel_p2")
        child = cmds.group(em=True, parent=parent, name="sel_c2")

        result = Selection.select_hierarchy_below([grand])
        self.assertGreater(len(result), 0)


class TestSelectionMetadata(QuickTestCase):
    """Selection.get_available_selection_types / get_selection_categories."""

    def test_categories_return_dict(self):
        cats = Selection.get_selection_categories()
        self.assertIsInstance(cats, dict)
        self.assertIn("Animation", cats)
        self.assertIn("Geometry", cats)

    def test_available_types_return_list(self):
        types = Selection.get_available_selection_types()
        self.assertIsInstance(types, list)
        self.assertIn("Polygon Meshes", types)
        self.assertIn("Locators", types)


if __name__ == "__main__":
    unittest.main()
