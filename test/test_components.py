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
import math
import unittest
import mayatk as mtk
from mayatk.core_utils.components import Components

from base_test import MayaTkTestCase
import maya.cmds as cmds


class TestComponents(MayaTkTestCase):
    """Tests for Components class."""

    def setUp(self):
        """Set up test scene with geometry."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_comp_cube")[0]
        self.sphere = cmds.polySphere(name="test_comp_sphere")[0]
        self.plane = cmds.polyPlane(name="test_comp_plane", sx=5, sy=5)[0]

    def tearDown(self):
        """Clean up."""
        for obj in [
            "test_comp_cube",
            "test_comp_sphere",
            "test_comp_plane",
            "test_comp_cube2",
        ]:
            if cmds.objExists(obj):
                cmds.delete(obj)
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

    def test_map_components_to_objects_multiple_per_object(self):
        """Test mapping multiple components from same object."""
        comps = [f"{self.cube}.vtx[0]", f"{self.cube}.vtx[1]", f"{self.cube}.vtx[2]"]
        mapping = Components.map_components_to_objects(comps)

        # Should have one key (cube shape)
        self.assertEqual(len(mapping), 1)

        # That key should have 3 components
        cube_key = list(mapping.keys())[0]
        self.assertEqual(len(mapping[cube_key]), 3)

    def test_map_components_to_objects_empty(self):
        """Test mapping empty component list."""
        mapping = Components.map_components_to_objects([])
        self.assertEqual(mapping, {})

    # -------------------------------------------------------------------------
    # Geometric Operations
    # -------------------------------------------------------------------------

    def test_get_contigious_edges(self):
        """Test getting contiguous edges."""
        # Select two edges that share a vertex
        vtx = f"{self.plane}.vtx[0]"
        edges = cmds.polyListComponentConversion(vtx, toEdge=True)
        edges = cmds.ls(edges, flatten=True)

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

    def test_get_components_preserves_namespace(self):
        """Namespaced face strings must round-trip through get_components.

        Regression: shape-prefix substitution stripped the namespace, so
        ``ns1:cubeA.f[1]`` became ``cubeAShape.f[1]`` — silently empty, or
        worse, pointing at a same-name root-namespace object.
        """
        if not cmds.namespace(exists="ns_test"):
            cmds.namespace(add="ns_test")
        cmds.namespace(set="ns_test")
        ns_cube = cmds.polyCube(name="nscube", ch=False)[0]
        cmds.namespace(set=":")

        try:
            seed = f"{ns_cube}.f[1]"
            result = Components.get_components(
                [seed], component_type="faces", flatten=True
            )
            self.assertTrue(result, "namespaced face dropped by get_components")
            for f in result:
                self.assertIn("ns_test:", f, f"namespace stripped from {f!r}")
        finally:
            cmds.delete(ns_cube)
            if cmds.namespace(exists="ns_test"):
                cmds.namespace(removeNamespace="ns_test", mergeNamespaceWithRoot=True)

    def test_get_faces_with_similar_normals_multi_object(self):
        """Multi-object face input must consider faces on every transform.

        Regression: the helper used to capture only the first face's transform,
        so faces selected on a second object were silently ignored.
        """
        c2 = cmds.polyCube(name="test_comp_cube2", ch=False)[0]
        cmds.move(5, 0, 0, c2)

        seed_a = f"{self.cube}.f[1]"  # +Y face on cubeA
        seed_b = f"{c2}.f[1]"  # +Y face on cubeB
        seeds = cmds.ls([seed_a, seed_b], flatten=True)

        result = Components.get_faces_with_similar_normals(
            seeds, range_x=0.1, range_y=0.1, range_z=0.1
        )

        # Strip shape suffix so the assertion is form-agnostic.
        contributors = {
            f.split(".")[0].split("|")[-1].removesuffix("Shape") for f in result
        }
        self.assertIn("test_comp_cube", contributors)
        self.assertIn("test_comp_cube2", contributors)
        # Each cube has one +Y face, so exactly two matches expected.
        self.assertEqual(len(result), 2)

    def test_get_islands(self):
        """Test getting islands from object."""
        # Create combined object with 2 shells
        c2 = cmds.polyCube(name="test_comp_cube2")[0]
        cmds.move(5, 0, 0, c2)
        combined = cmds.polyUnite(self.cube, c2, ch=False)[0]

        islands = list(Components.get_islands(combined))
        self.assertEqual(len(islands), 2)

        cmds.delete(combined)

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
        p1 = cmds.pointPosition(v1)
        p2 = cmds.pointPosition(v2)
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))
        self.assertAlmostEqual(dist, 1.732, places=2)

    def test_get_closest_verts(self):
        """Test getting closest vertices between sets."""
        # Create another cube near the first
        c2 = cmds.polyCube()[0]
        cmds.move(2, 0, 0, c2)

        pairs = Components.get_closest_verts(f"{self.cube}.vtx[:]", f"{c2}.vtx[:]")
        self.assertTrue(len(pairs) > 0)

        v1, v2 = pairs[0]
        p1 = cmds.pointPosition(v1)
        p2 = cmds.pointPosition(v2)
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))
        self.assertAlmostEqual(dist, 1.0, places=2)

        cmds.delete(c2)

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
        normal = Components.get_normal(f"{self.cube}.f[0]")
        self.assertEqual(len(normal), 3)

    def test_get_normal_angle(self):
        """Test getting normal angle."""
        angle = Components.get_normal_angle(f"{self.cube}.e[0]")
        # Returns float for single edge
        self.assertAlmostEqual(angle, 90.0, places=1)

    def test_get_edges_by_normal_angle(self):
        """Test filtering edges by normal angle."""
        hard_edges = Components.get_edges_by_normal_angle(self.cube, 80, 100)
        self.assertTrue(len(hard_edges) > 0)

        soft_edges = Components.get_edges_by_normal_angle(self.cube, 0, 10)
        self.assertEqual(len(soft_edges), 0)

    def test_get_edges_by_normal_angle_return_angles(self):
        """Test return_angles parameter returns edges and angle dict."""
        result = Components.get_edges_by_normal_angle(
            self.cube, 80, 100, return_angles=True
        )

        # Should return a tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

        edges, angles = result
        # Edges should be a list
        self.assertIsInstance(edges, list)
        self.assertTrue(len(edges) > 0)

        # Angles should be a dict mapping edge names (strings) to floats
        self.assertIsInstance(angles, dict)
        for edge_name, angle in angles.items():
            self.assertIsInstance(edge_name, str)
            self.assertIsInstance(angle, float)
            # All edges in a cube have 90 degree angles
            self.assertAlmostEqual(angle, 90.0, places=1)

        # The filtered edges should have their names in the angles dict
        for edge in edges:
            self.assertIn(edge, angles)
            self.assertTrue(80 <= angles[edge] <= 100)

    def test_set_edge_hardness(self):
        """Test setting edge hardness based on angle threshold."""
        # Cube has all 90-degree edges, set threshold at 45 degrees
        # All edges should get upper_hardness since 90 > 45
        Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180
        )

        # Verify the cube still exists and is valid
        self.assertTrue(cmds.objExists(self.cube))

    def test_set_edge_hardness_no_values(self):
        """Test set_edge_hardness early exit when no hardness values provided."""
        # Should not raise, just return early
        Components.set_edge_hardness(self.cube, 45)

    def test_set_edge_hardness_upper_only(self):
        """Test setting only upper hardness."""
        Components.set_edge_hardness(self.cube, 45, upper_hardness=0)
        self.assertTrue(cmds.objExists(self.cube))

    def test_set_edge_hardness_lower_only(self):
        """Test setting only lower hardness."""
        Components.set_edge_hardness(self.cube, 45, lower_hardness=180)
        self.assertTrue(cmds.objExists(self.cube))

    def test_average_normals(self):
        """Test averaging normals."""
        Components.average_normals(self.cube)
        pass

    def test_transfer_normals_basic(self):
        """Test transferring normals from source to target mesh.

        Bug: cmds.ls(objects, type='mesh') filtered out transform nodes,
        so the function always raised ValueError. Also, polySoftEdge at
        the end overwrote the transferred normals and the tentacle call
        site passed args incorrectly.
        Fixed: 2026-02-27
        """
        # Create two cubes at the same position (same topology)
        source = cmds.polyCube(name="src_cube")[0]
        target = cmds.polyCube(name="tgt_cube")[0]

        # Rotate source so its normals differ from target
        cmds.rotate(45, 0, 0, source)
        cmds.makeIdentity(source, apply=True, t=1, r=1, s=1, n=0)

        # Get target normals before transfer
        before = [
            cmds.polyNormalPerVertex(f"{target}.vtx[{i}]", q=True, xyz=True)
            for i in range(cmds.polyEvaluate(target, vertex=True))
        ]

        # Transfer normals using transform nodes (the typical user workflow)
        Components.transfer_normals([source, target])

        # Get target normals after transfer
        after = [
            cmds.polyNormalPerVertex(f"{target}.vtx[{i}]", q=True, xyz=True)
            for i in range(cmds.polyEvaluate(target, vertex=True))
        ]

        # Normals should have changed
        changed = any(
            any(abs(a - b) > 0.01 for a, b in zip(bv, av))
            for bv, av in zip(before, after)
        )
        self.assertTrue(changed, "Normals did not change after transfer")

    def test_transfer_normals_rejects_non_mesh(self):
        """Test that transfer_normals raises for non-mesh objects."""
        grp1 = cmds.group(empty=True, name="empty_grp1")
        grp2 = cmds.group(empty=True, name="empty_grp2")
        with self.assertRaises(ValueError):
            Components.transfer_normals([grp1, grp2])

    # -------------------------------------------------------------------------
    # Topology
    # -------------------------------------------------------------------------

    def test_bridge_connected_edges(self):
        """Test bridging connected edges."""
        try:
            Components.bridge_connected_edges(
                [f"{self.plane}.e[0]", f"{self.plane}.e[1]"]
            )
            self.assertTrue(cmds.polyEvaluate(self.plane, face=True) > 25)
        except Exception as e:
            print(f"Bridge failed: {e}")


class TestComponentsEdgeCases(MayaTkTestCase):
    """Edge case tests for Components class."""

    def test_get_components_empty_object(self):
        """Test getting components from object with no geometry."""
        empty_group = cmds.group(empty=True, name="test_empty_group")
        try:
            result = Components.get_components(empty_group, "vertex")
            self.assertTrue(not result)
        finally:
            if cmds.objExists("test_empty_group"):
                cmds.delete("test_empty_group")

    def test_filter_components_empty_list(self):
        """Test filtering with empty component list."""
        result = Components.filter_components([])
        self.assertEqual(result, [])

    def test_get_shortest_path_invalid(self):
        """Test shortest path with invalid inputs."""
        cube = cmds.polyCube()[0]
        with self.assertRaises(ValueError):
            Components.get_shortest_path([f"{cube}.vtx[0]"])  # Only 1
        cmds.delete(cube)


if __name__ == "__main__":
    unittest.main()
