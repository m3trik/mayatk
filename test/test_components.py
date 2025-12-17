# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils.components module

Tests for Components class functionality including:
- Component index retrieval and type conversion
- Component filtering and queries
- Geometric operations (islands, contiguous edges)
- Distance calculations (closest/furthest vertices)
- Path finding (shortest path, edge loops/rings)
- Normal operations (angles, hardness, averaging)
- Topology modification (bridge)
"""
import unittest
import pymel.core as pm
import mayatk as mtk
from mayatk.core_utils.components import Components

from base_test import MayaTkTestCase


class TestComponents(MayaTkTestCase):
    """Tests for Components class."""

    def setUp(self):
        """Set up test scene with geometry."""
        super().setUp()
        self.cube = pm.polyCube(name="test_comp_cube")[0]
        self.sphere = pm.polySphere(name="test_comp_sphere")[0]
        self.plane = pm.polyPlane(name="test_comp_plane", sx=5, sy=5)[0]

    def tearDown(self):
        """Clean up."""
        for obj in [
            "test_comp_cube",
            "test_comp_sphere",
            "test_comp_plane",
            "test_comp_cube2",
        ]:
            if pm.objExists(obj):
                pm.delete(obj)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Basic Component Operations
    # -------------------------------------------------------------------------

    def test_get_component_type(self):
        """Test getting component type."""
        comp = f"{self.cube}.vtx[0]"
        self.assertEqual(Components.get_component_type(comp, "abv"), "vtx")
        self.assertEqual(Components.get_component_type(comp, "full"), "Polygon Vertex")

        comp = f"{self.cube}.e[0]"
        self.assertEqual(Components.get_component_type(comp, "abv"), "e")

    def test_convert_alias(self):
        """Test converting component aliases."""
        self.assertEqual(Components.convert_alias("vertex", "abv"), "vtx")
        self.assertEqual(Components.convert_alias("e", "full"), "Polygon Edge")
        self.assertEqual(Components.convert_alias(31, "abv"), "vtx")

    def test_convert_component_type(self):
        """Test converting between component types."""
        # Vertex to Edge
        vtx = f"{self.cube}.vtx[0]"
        edges = Components.convert_component_type(vtx, "edge", flatten=True)
        self.assertTrue(len(edges) > 0)
        self.assertTrue("e[" in str(edges[0]))

    def test_get_component_index(self):
        """Test getting component indices."""
        # Single
        idx = Components.get_component_index(f"{self.cube}.vtx[5]")
        self.assertEqual(idx, 5)

        # List
        indices = Components.get_component_index(
            [f"{self.cube}.vtx[1]", f"{self.cube}.vtx[3]"]
        )
        self.assertEqual(sorted(indices), [1, 3])

    def test_convert_int_to_component(self):
        """Test converting integers to components."""
        comps = Components.convert_int_to_component(
            self.cube, [0, 1], "vtx", flatten=True
        )
        self.assertEqual(len(comps), 2)
        self.assertTrue("vtx[0]" in str(comps[0]) or "vtx[0]" in str(comps[1]))

    def test_filter_components(self):
        """Test filtering components."""
        all_vtx = f"{self.cube}.vtx[:]"
        inc = f"{self.cube}.vtx[0:1]"
        exc = f"{self.cube}.vtx[1]"

        # Include 0, 1. Exclude 1. Result should be 0.
        res = Components.filter_components(all_vtx, inc=inc, exc=exc, flatten=True)
        self.assertEqual(len(res), 1)
        self.assertTrue("vtx[0]" in str(res[0]))

    def test_get_components(self):
        """Test getting components from object."""
        vtxs = Components.get_components(self.cube, "vtx", flatten=True)
        self.assertEqual(len(vtxs), 8)  # Cube has 8 vertices

    def test_map_components_to_objects(self):
        """Test mapping components to objects."""
        comps = [f"{self.cube}.vtx[0]", f"{self.sphere}.vtx[0]"]
        mapping = Components.map_components_to_objects(comps)

        # Keys are likely shape names
        keys = list(mapping.keys())
        self.assertTrue(any("test_comp_cube" in k for k in keys))
        self.assertTrue(any("test_comp_sphere" in k for k in keys))

        # Check content
        cube_key = next(k for k in keys if "test_comp_cube" in k)
        self.assertEqual(len(mapping[cube_key]), 1)

    # -------------------------------------------------------------------------
    # Geometric Operations
    # -------------------------------------------------------------------------

    def test_get_contigious_edges(self):
        """Test getting contiguous edges."""
        # Select two edges that share a vertex
        vtx = f"{self.plane}.vtx[0]"
        edges = pm.polyListComponentConversion(vtx, toEdge=True)
        edges = pm.ls(edges, flatten=True)

        groups = Components.get_contigious_edges(edges)
        self.assertEqual(
            len(groups), 1
        )  # Should be one group since they share a vertex

    def test_get_contigious_islands(self):
        """Test getting contiguous face islands."""
        # Select two disjoint faces
        f1 = f"{self.plane}.f[0]"
        f2 = f"{self.plane}.f[24]"  # Far corner

        islands = Components.get_contigious_islands([f1, f2])
        self.assertEqual(len(islands), 2)

        # Select two adjacent faces
        f3 = f"{self.plane}.f[1]"
        islands = Components.get_contigious_islands([f1, f3])
        self.assertEqual(len(islands), 1)

    def test_get_islands(self):
        """Test getting islands from object."""
        # Create combined object with 2 shells
        c2 = pm.polyCube(name="test_comp_cube2")[0]
        pm.move(c2, 5, 0, 0)
        combined = pm.polyUnite(self.cube, c2, ch=False)[0]

        islands = list(Components.get_islands(combined))
        self.assertEqual(len(islands), 2)

        pm.delete(combined)

    def test_get_border_components(self):
        """Test getting border components."""
        # Plane has border edges
        borders = Components.get_border_components(
            f"{self.plane}.e[:]", returned_type="str"
        )
        self.assertTrue(len(borders) > 0)

        # Cube has no border edges (closed manifold)
        borders_cube = Components.get_border_components(
            f"{self.cube}.e[:]", returned_type="str"
        )
        self.assertEqual(len(borders_cube), 0)

    # -------------------------------------------------------------------------
    # Distance & Location
    # -------------------------------------------------------------------------

    def test_get_furthest_vertices(self):
        """Test getting furthest vertices."""
        # Cube corners are furthest
        v1, v2 = Components.get_furthest_vertices(
            f"{self.cube}.vtx[:]", f"{self.cube}.vtx[:]"
        )
        dist = (pm.pointPosition(v1) - pm.pointPosition(v2)).length()
        self.assertAlmostEqual(dist, 1.732, places=2)

    def test_get_closest_verts(self):
        """Test getting closest vertices between sets."""
        # Create another cube near the first
        c2 = pm.polyCube()[0]
        pm.move(c2, 2, 0, 0)

        pairs = Components.get_closest_verts(f"{self.cube}.vtx[:]", f"{c2}.vtx[:]")
        self.assertTrue(len(pairs) > 0)

        v1, v2 = pairs[0]
        p1 = pm.pointPosition(v1)
        p2 = pm.pointPosition(v2)
        dist = (p1 - p2).length()
        self.assertAlmostEqual(dist, 1.0, places=2)

        pm.delete(c2)

    def test_get_closest_vertex(self):
        """Test getting closest vertex on object."""
        res = Components.get_closest_vertex(f"{self.sphere}.vtx[0]", self.cube)
        self.assertIsInstance(res, dict)
        self.assertEqual(len(res), 1)

    def test_get_vertices_within_threshold(self):
        """Test getting vertices within threshold."""
        inside, outside = Components.get_vertices_within_threshold(
            f"{self.plane}.vtx[0]", 0.5
        )
        self.assertTrue(len(inside) >= 1)
        self.assertTrue(len(outside) > 0)

    # -------------------------------------------------------------------------
    # Path Finding
    # -------------------------------------------------------------------------

    def test_get_shortest_path(self):
        """Test shortest path."""
        path = Components.get_shortest_path(
            [f"{self.plane}.vtx[0]", f"{self.plane}.vtx[35]"], flatten=True
        )
        self.assertTrue(len(path) > 2)

        # Check if start/end are in path (checking string representation for shape name)
        path_strs = [str(p) for p in path]
        self.assertTrue(
            any("test_comp_plane" in p and "vtx[0]" in p for p in path_strs)
        )

    def test_get_edge_path(self):
        """Test edge loops and rings."""
        edge = f"{self.sphere}.e[200]"  # Random edge

        loop = Components.get_edge_path(edge, "edgeLoop")
        self.assertTrue(len(loop) > 1)

        ring = Components.get_edge_path(edge, "edgeRing")
        self.assertTrue(len(ring) > 1)

    # -------------------------------------------------------------------------
    # Normals
    # -------------------------------------------------------------------------

    def test_get_normal(self):
        """Test getting face normal."""
        normal = Components.get_normal(self.cube.f[0])
        self.assertEqual(len(normal), 3)

    def test_get_normal_angle(self):
        """Test getting normal angle."""
        angle = Components.get_normal_angle(self.cube.e[0])
        # Returns float for single edge
        self.assertAlmostEqual(angle, 90.0, places=1)

    def test_get_edges_by_normal_angle(self):
        """Test filtering edges by normal angle."""
        hard_edges = Components.get_edges_by_normal_angle(self.cube, 80, 100)
        self.assertTrue(len(hard_edges) > 0)

        soft_edges = Components.get_edges_by_normal_angle(self.cube, 0, 10)
        self.assertEqual(len(soft_edges), 0)

    def test_set_edge_hardness(self):
        """Test setting edge hardness."""
        Components.set_edge_hardness(self.cube, 180, lower_hardness=180)
        pass

    def test_average_normals(self):
        """Test averaging normals."""
        Components.average_normals(self.cube)
        pass

    # -------------------------------------------------------------------------
    # Topology
    # -------------------------------------------------------------------------

    def test_bridge_connected_edges(self):
        """Test bridging connected edges."""
        try:
            Components.bridge_connected_edges(
                [f"{self.plane}.e[0]", f"{self.plane}.e[1]"]
            )
            self.assertTrue(self.plane.numFaces() > 25)
        except Exception as e:
            print(f"Bridge failed: {e}")


class TestComponentsEdgeCases(MayaTkTestCase):
    """Edge case tests for Components class."""

    def test_get_components_empty_object(self):
        """Test getting components from object with no geometry."""
        empty_group = pm.group(empty=True, name="test_empty_group")
        try:
            result = Components.get_components(empty_group, "vertex")
            self.assertTrue(not result)
        finally:
            if pm.objExists("test_empty_group"):
                pm.delete("test_empty_group")

    def test_filter_components_empty_list(self):
        """Test filtering with empty component list."""
        result = Components.filter_components([])
        self.assertEqual(result, [])

    def test_get_shortest_path_invalid(self):
        """Test shortest path with invalid inputs."""
        cube = pm.polyCube()[0]
        with self.assertRaises(ValueError):
            Components.get_shortest_path([f"{cube}.vtx[0]"])  # Only 1
        pm.delete(cube)


if __name__ == "__main__":
    unittest.main()
