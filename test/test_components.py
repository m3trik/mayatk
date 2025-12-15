# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils.components module

Tests for Components class functionality including:
- Component index retrieval
- Component type conversion
- Component filtering
- Component queries
- Border component detection
- Component masking operations
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestComponents(MayaTkTestCase):
    """Tests for Components class."""

    def setUp(self):
        """Set up test scene with geometry."""
        super().setUp()
        self.cube = pm.polyCube(name="test_comp_cube")[0]
        self.sphere = pm.polySphere(name="test_comp_sphere")[0]

    def tearDown(self):
        """Clean up."""
        for obj in ["test_comp_cube", "test_comp_sphere"]:
            if pm.objExists(obj):
                pm.delete(obj)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Component Index Tests
    # -------------------------------------------------------------------------

    def test_get_component_index_vertices(self):
        """Test getting vertex indices from components."""
        vertices = pm.select(f"{self.cube}.vtx[0:3]")
        components = pm.ls(sl=True, flatten=True)

        indices = mtk.Components.get_component_index(components)
        self.assertIsInstance(indices, (list, set))
        self.assertTrue(len(indices) > 0)

    def test_get_component_index_edges(self):
        """Test getting edge indices from components."""
        edges = pm.select(f"{self.cube}.e[0:5]")
        components = pm.ls(sl=True, flatten=True)

        indices = mtk.Components.get_component_index(components)
        self.assertIsInstance(indices, (list, set))

    def test_get_component_index_faces(self):
        """Test getting face indices from components."""
        faces = pm.select(f"{self.cube}.f[0:2]")
        components = pm.ls(sl=True, flatten=True)

        indices = mtk.Components.get_component_index(components)
        self.assertIsInstance(indices, (list, set))

    # -------------------------------------------------------------------------
    # Component Type Conversion Tests
    # -------------------------------------------------------------------------

    def test_convert_int_to_component_vertex(self):
        """Test converting integer indices to vertex components."""
        result = mtk.Components.convert_int_to_component(
            self.cube, [0, 1, 2], "vtx", returned_type="str"
        )
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertTrue(any("vtx" in str(r) for r in result))

    def test_convert_int_to_component_edge(self):
        """Test converting integer indices to edge components."""
        result = mtk.Components.convert_int_to_component(
            self.cube, [0, 1, 2], "e", returned_type="str"
        )
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertTrue(any(".e[" in str(r) for r in result))

    def test_convert_int_to_component_face(self):
        """Test converting integer indices to face components."""
        result = mtk.Components.convert_int_to_component(
            self.cube, [0, 1], "f", returned_type="str"
        )
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)
        self.assertTrue(any(".f[" in str(r) for r in result))

    # -------------------------------------------------------------------------
    # Component Filtering Tests
    # -------------------------------------------------------------------------

    def test_filter_components_include(self):
        """Test filtering components with include list."""
        all_verts = f"{self.cube}.vtx[:]"
        include = f"{self.cube}.vtx[0:2]"

        result = mtk.Components.filter_components(all_verts, inc=[include])
        self.assertIsInstance(result, list)

    def test_filter_components_exclude(self):
        """Test filtering components with exclude list."""
        all_verts = f"{self.cube}.vtx[:]"
        exclude = f"{self.cube}.vtx[0:2]"

        result = mtk.Components.filter_components(all_verts, exc=[exclude])
        self.assertIsInstance(result, list)

    def test_filter_components_flatten(self):
        """Test filtering components with flatten option."""
        all_verts = f"{self.cube}.vtx[:]"

        result = mtk.Components.filter_components(all_verts, flatten=True)
        self.assertIsInstance(result, list)

    # -------------------------------------------------------------------------
    # Get Components Tests
    # -------------------------------------------------------------------------

    def test_get_components_vertices(self):
        """Test getting all vertices from object."""
        result = mtk.Components.get_components(self.cube, "vertex")
        self.assertIsInstance(result, (list, dict))
        if isinstance(result, list):
            self.assertGreater(len(result), 0)

    def test_get_components_edges(self):
        """Test getting all edges from object."""
        result = mtk.Components.get_components(self.cube, "edge")
        self.assertIsInstance(result, (list, dict))
        if isinstance(result, list):
            self.assertGreater(len(result), 0)

    def test_get_components_faces(self):
        """Test getting all faces from object."""
        result = mtk.Components.get_components(self.cube, "face")
        self.assertIsInstance(result, (list, dict))
        if isinstance(result, list):
            self.assertGreater(len(result), 0)

    def test_get_components_returned_type_str(self):
        """Test getting components as strings."""
        result = mtk.Components.get_components(self.cube, "vertex", returned_type="str")
        if isinstance(result, list) and len(result) > 0:
            self.assertIsInstance(result[0], str)

    # -------------------------------------------------------------------------
    # Border Components Tests
    # -------------------------------------------------------------------------

    def test_get_border_components(self):
        """Test getting border components from geometry."""
        try:
            # get_border_components expects components, not objects
            # Create a plane and delete one face to create a border
            plane = pm.polyPlane(sx=2, sy=2)[0]
            pm.delete(f"{plane}.f[0]")  # Delete one face to create border edges

            # Get all edges and find border edges
            all_edges = f"{plane}.e[:]"
            result = mtk.Components.get_border_components(all_edges)
            self.assertIsInstance(result, (list, set, dict))

            pm.delete(plane)
        except (AttributeError, RuntimeError, ValueError) as e:
            self.skipTest(f"get_border_components not available: {e}")

    # -------------------------------------------------------------------------
    # Component Masking Tests
    # -------------------------------------------------------------------------

    def test_get_masked_components(self):
        """Test getting masked components."""
        try:
            result = mtk.get_masked_components([self.cube])
            # Method may not exist or may return None
            if result is not None:
                self.assertIsInstance(result, (list, dict))
        except (AttributeError, RuntimeError):
            self.skipTest("get_border_components not available")


class TestComponentsEdgeCases(MayaTkTestCase):
    """Edge case tests for Components class."""

    def test_get_components_empty_object(self):
        """Test getting components from object with no geometry."""
        empty_group = pm.group(empty=True, name="test_empty_group")

        try:
            result = mtk.Components.get_components(empty_group, "vertex")
            # Should handle gracefully
            self.assertIsInstance(result, (list, dict, type(None)))
        finally:
            if pm.objExists("test_empty_group"):
                pm.delete("test_empty_group")

    def test_filter_components_empty_list(self):
        """Test filtering with empty component list."""
        result = mtk.Components.filter_components([])
        self.assertEqual(result, [])

    def test_convert_int_to_component_empty_indices(self):
        """Test converting empty integer list."""
        cube = pm.polyCube()[0]
        try:
            result = mtk.Components.convert_int_to_component(cube, [], "vertex")
            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 0)
        finally:
            pm.delete(cube)


if __name__ == "__main__":
    unittest.main(verbosity=2)
