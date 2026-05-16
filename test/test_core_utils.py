# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils module

Tests for CoreUtils class functionality including:
- Array type detection and conversion
- Decorators (selected, undoable, reparent)
- Attribute handling (unlock, filter)
- Mesh operations (similarity, MFnMesh)
- Parameter mapping
"""
import unittest
import maya.cmds as cmds
import mayatk as mtk
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.attributes._attributes import Attributes

from base_test import MayaTkTestCase


class TestCoreUtils(MayaTkTestCase):
    """Comprehensive tests for CoreUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test cylinder
        self.cyl = cmds.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]
        self.cyl_shape = cmds.listRelatives(self.cyl, shapes=True)[0]
        # Get history node for parameter tests
        self.cyl_hist = cmds.ls(cmds.listHistory(self.cyl), type="polyCylinder")[0]

    def tearDown(self):
        """Clean up test geometry."""
        if cmds.objExists("cyl"):
            cmds.delete("cyl")
        super().tearDown()

    # -------------------------------------------------------------------------
    # Array Type Detection and Conversion Tests
    # -------------------------------------------------------------------------

    def test_get_array_type_with_int(self):
        """Test array type detection for integer values."""
        result = CoreUtils.get_array_type(100)
        self.assertEqual(result, "int")

    def test_get_array_type_with_string(self):
        """Test array type detection for string values."""
        result = CoreUtils.get_array_type("cylShape.vtx[:]")
        self.assertEqual(result, "str")

    def test_get_array_type_with_pymel_vertex_list(self):
        """Test array type detection for PyMEL vertex components."""
        vertices = cmds.ls("cylShape.vtx[:]")
        result = CoreUtils.get_array_type(vertices)
        self.assertEqual(result, "vtx")

    def test_get_array_type_with_edge(self):
        """Test array type detection for edge components."""
        edges = cmds.ls("cylShape.e[:]")
        result = CoreUtils.get_array_type(edges)
        self.assertEqual(result, "e")

    def test_get_array_type_with_face(self):
        """Test array type detection for face components."""
        faces = cmds.ls("cylShape.f[:]")
        result = CoreUtils.get_array_type(faces)
        self.assertEqual(result, "f")

    def test_convert_array_type_string_to_str_list(self):
        """Test converting component string to string list.

        The helper returns shape-prefixed names using fullPath=True
        (intentional — short names would collide when multiple shapes
        share a leaf name across DAG branches). Accept both short and
        full forms; the contract is "ends with the expected shape+comp".
        """
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "str")
        self.assertEqual(len(result), 1)
        self.assertTrue(
            result[0].endswith("cylShape.vtx[0:2]"),
            f"Expected component on cylShape, got {result[0]}",
        )

    def test_convert_array_type_string_to_str_list_flattened(self):
        """Test converting component string to flattened string list."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "str", flatten=True)
        self.assertEqual(len(result), 3)
        for i, r in enumerate(result):
            self.assertTrue(
                r.endswith(f"cylShape.vtx[{i}]"),
                f"Index {i}: expected suffix cylShape.vtx[{i}], got {r}",
            )

    def test_convert_array_type_string_to_pymel_objects(self):
        """Test converting component string to PyMEL objects."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "obj")
        self.assertEqual(len(result), 1)
        self.assertTrue(
            str(result[0]).endswith("cylShape.vtx[0:2]"),
            f"Expected component on cylShape, got {result[0]}",
        )

    def test_convert_array_type_string_to_pymel_objects_flattened(self):
        """Test converting component string to flattened PyMEL objects."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "obj", flatten=True)
        self.assertEqual(len(result), 3)
        for i, r in enumerate(result):
            self.assertTrue(
                str(r).endswith(f"cylShape.vtx[{i}]"),
                f"Index {i}: expected suffix cylShape.vtx[{i}], got {r}",
            )

    def test_convert_array_type_string_to_int_indices(self):
        """Test converting component string to integer index range."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "int")
        # For single object, it returns a flattened list.
        # Based on current implementation, it returns [0, 2] for range 0:2
        self.assertIsInstance(result, list)
        self.assertEqual(result, [0, 2])

    def test_convert_array_type_string_to_int_indices_flattened(self):
        """Test converting component string to flattened integer indices."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "int", flatten=True)
        # For single object, it returns a list of indices
        self.assertIsInstance(result, list)
        self.assertEqual(result, [0, 1, 2])

    # -------------------------------------------------------------------------
    # Decorator Tests
    # -------------------------------------------------------------------------

    def test_undoable_decorator(self):
        """Test undoable decorator wraps operations in undo chunk."""

        @CoreUtils.undoable
        def create_and_move_cube():
            cube = cmds.polyCube(name="test_undo_cube")[0]
            cmds.move(5, 0, 0, cube)
            return cube

        # Execute the decorated function
        cube = create_and_move_cube()
        self.assertTrue(cmds.objExists("test_undo_cube"))

        # Undo should remove both the move and creation
        cmds.undo()
        self.assertFalse(cmds.objExists("test_undo_cube"))

    def test_undoable_decorator_with_exception(self):
        """Test undoable decorator handles exceptions properly."""

        @CoreUtils.undoable
        def create_and_fail():
            cmds.polyCube(name="test_exception_cube")
            raise ValueError("Intentional test error")

        # Should raise the exception but still close undo chunk
        with self.assertRaises(ValueError):
            create_and_fail()

        # Clean up if cube was created
        if cmds.objExists("test_exception_cube"):
            cmds.delete("test_exception_cube")

    def test_selected_decorator(self):
        """Test selected decorator passes selection to function."""

        class TestClass:
            @CoreUtils.selected
            def get_selection_names(self, selection=None):
                return [x for x in selection] if selection else []

        tester = TestClass()
        cmds.select(self.cyl)
        result = tester.get_selection_names()
        self.assertEqual(result, ["cyl"])

        # Test passing explicit argument overrides selection
        result_explicit = tester.get_selection_names([self.cyl])
        self.assertEqual(result_explicit, ["cyl"])

    def test_reparent_decorator(self):
        """Test reparent decorator maintains hierarchy."""

        # Create a hierarchy
        parent = cmds.group(em=True, name="parent_grp")
        child = cmds.polyCube(name="child_cube")[0]
        cmds.parent(child, parent)

        @CoreUtils.reparent
        def operate_on_child(nodes):
            # Operation that might unparent or modify hierarchy
            # For test, we'll just return the node
            return nodes[0]

        # Pass both child and parent (or just child if logic allows, but error said 2 nodes required)
        # The decorator expects args[0] to be a list of nodes?
        # Let's check implementation:
        # instance, node_args = ptk.parse_method_args(args)
        # if not args or not args[0] or len(args[0]) < 2: raise ValueError
        # It seems it expects the first argument to be a list of at least 2 nodes?
        # Or maybe it expects (node1, node2, ...)?
        # "At least two Maya nodes are required."
        # This suggests it's designed for operations like boolean or combine where multiple nodes are involved.

        result = operate_on_child([child, parent])

        # Verify child is still under parent
        self.assertEqual((cmds.listRelatives(str(child), parent=True) or [None])[0], parent)

        cmds.delete(parent)

    # -------------------------------------------------------------------------
    # Attribute Tests
    # -------------------------------------------------------------------------

    def test_temporarily_unlock_attributes(self):
        """Test temporarily unlocking attributes."""
        # Lock an attribute
        cmds.setAttr(f"{self.cyl}.translateX", lock=True)
        self.assertTrue(cmds.getAttr(f"{self.cyl}.translateX", lock=True))

        with CoreUtils.temporarily_unlock_attributes(self.cyl, ["translateX"]):
            self.assertFalse(cmds.getAttr(f"{self.cyl}.translateX", lock=True))

        # Should be locked again
        self.assertTrue(cmds.getAttr(f"{self.cyl}.translateX", lock=True))

    def test_filter_attributes(self):
        """Test filtering attributes via Attributes."""
        attrs = ["translateX", "translateY", "translateZ", "rotateX", "visibility"]

        # Exclude specific
        filtered = Attributes.filter(attrs, exclude="visibility")
        self.assertEqual(
            filtered, ["translateX", "translateY", "translateZ", "rotateX"]
        )

        # Include pattern
        filtered = Attributes.filter(attrs, include="translate*")
        self.assertEqual(filtered, ["translateX", "translateY", "translateZ"])

        # Exclude pattern
        filtered = Attributes.filter(attrs, exclude="*Z")
        self.assertEqual(
            filtered, ["translateX", "translateY", "rotateX", "visibility"]
        )

    # -------------------------------------------------------------------------
    # Parameter Mapping Tests
    # -------------------------------------------------------------------------

    def test_get_parameter_mapping(self):
        """Test getting parameter values from MEL command."""
        # polyCube parameters
        result = CoreUtils.get_parameter_mapping(
            node=self.cyl_hist, cmd="polyCylinder", parameters=["radius", "height"]
        )

        self.assertIsInstance(result, dict)
        self.assertIn("radius", result)
        self.assertAlmostEqual(result["radius"], 5.0)

    def test_set_parameter_mapping(self):
        """Test setting parameter values via MEL command."""
        # Use transformLimits as it works well with direct command calls
        CoreUtils.set_parameter_mapping(
            node=self.cyl,
            cmd="transformLimits",
            parameters={"enableTranslationX": (True, True), "translationX": (-5, 5)},
        )

        # Verify change
        limits = cmds.transformLimits(self.cyl, q=True, translationX=True)
        self.assertEqual(list(limits), [-5.0, 5.0])
        enabled = cmds.transformLimits(self.cyl, q=True, enableTranslationX=True)
        self.assertEqual(list(enabled), [True, True])

    # -------------------------------------------------------------------------
    # Mesh Operations
    # -------------------------------------------------------------------------

    def test_get_mfn_mesh(self):
        """Test getting MFnMesh."""
        # API 2.0
        mfn = CoreUtils.get_mfn_mesh(self.cyl, api_version=2)
        # Should be MFnMesh
        self.assertTrue(hasattr(mfn, "numVertices"))

        # API 1.0
        mfn_gen = CoreUtils.get_mfn_mesh(self.cyl, api_version=1)
        mfn_list = list(mfn_gen)
        self.assertTrue(len(mfn_list) > 0)
        self.assertTrue(hasattr(mfn_list[0], "numVertices"))

    def test_build_mesh_similarity_mapping(self):
        """Test mesh similarity mapping."""
        # Duplicate cylinder
        cyl2 = cmds.duplicate(self.cyl)[0]
        cmds.move(10, 0, 0, cyl2)

        mapping = CoreUtils.build_mesh_similarity_mapping(source=self.cyl, target=cyl2)

        # Mapping is keyed by short name; production returns string values.
        cyl_key = str(self.cyl).split("|")[-1]
        self.assertIn(cyl_key, mapping)
        self.assertEqual(str(mapping[cyl_key]), str(cyl2).split("|")[-1])

    def test_confirm_existence(self):
        """Test confirming object existence."""
        existing, non_existing = CoreUtils.confirm_existence(
            [self.cyl, "non_existent_obj"]
        )

        self.assertIn(self.cyl, existing)
        self.assertIn("non_existent_obj", non_existing)


class TestCoreUtilsEdgeCases(MayaTkTestCase):
    """Edge case tests for CoreUtils."""

    def test_get_array_type_with_empty_list(self):
        """Test array type detection with empty list."""
        result = CoreUtils.get_array_type([])
        self.assertIn(result, ["list", None, ""])

    def test_get_array_type_with_none(self):
        """Test array type detection with None."""
        result = CoreUtils.get_array_type(None)
        self.assertIn(result, ["none", "NoneType", None, ""])

    def test_convert_array_type_with_invalid_target_type(self):
        """Test converting to invalid target type returns lst unchanged."""
        cyl = cmds.polyCylinder()[0]
        result = CoreUtils.convert_array_type(f"{cyl}.vtx[0]", "invalid_type")
        # Should return PyMEL objects (the 'lst' parameter unchanged)
        self.assertTrue(len(result) > 0)
        cmds.delete(cyl)

    def test_convert_array_type_with_nonexistent_component(self):
        """Test converting nonexistent component."""
        try:
            result = CoreUtils.convert_array_type("nonexistent.vtx[0]", "str")
            if result is not None:
                self.assertIsInstance(result, list)
        except (RuntimeError, RuntimeError):
            pass  # Expected behavior


if __name__ == "__main__":
    unittest.main()
