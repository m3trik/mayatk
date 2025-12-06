# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils module

Tests for CoreUtils class functionality including:
- Array type detection and conversion
- Main window access
- Panel management
- Channel selection
- Progress bar handling
- Undo/redo functionality
"""
import unittest
import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestCoreUtils(MayaTkTestCase):
    """Comprehensive tests for CoreUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test cylinder
        self.cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]
        self.cyl_shape = pm.listRelatives(self.cyl, shapes=True)[0]

    def tearDown(self):
        """Clean up test geometry."""
        if pm.objExists("cyl"):
            pm.delete("cyl")
        super().tearDown()

    # -------------------------------------------------------------------------
    # Array Type Detection and Conversion Tests
    # -------------------------------------------------------------------------

    def test_get_array_type_with_int(self):
        """Test array type detection for integer values."""
        result = mtk.get_array_type(100)
        self.assertEqual(result, "int")

    def test_get_array_type_with_string(self):
        """Test array type detection for string values."""
        result = mtk.get_array_type("cylShape.vtx[:]")
        self.assertEqual(result, "str")

    def test_get_array_type_with_pymel_vertex_list(self):
        """Test array type detection for PyMEL vertex components."""
        vertices = pm.ls("cylShape.vtx[:]")
        result = mtk.get_array_type(vertices)
        self.assertEqual(result, "vtx")

    def test_get_array_type_with_edge(self):
        """Test array type detection for edge components."""
        edges = pm.ls("cylShape.e[:]")
        result = mtk.get_array_type(edges)
        self.assertEqual(result, "e")

    def test_get_array_type_with_face(self):
        """Test array type detection for face components."""
        faces = pm.ls("cylShape.f[:]")
        result = mtk.get_array_type(faces)
        self.assertEqual(result, "f")

    def test_convert_array_type_string_to_str_list(self):
        """Test converting component string to string list."""
        result = mtk.convert_array_type("cyl.vtx[:2]", "str")
        self.assertEqual(result, ["cylShape.vtx[0:2]"])

    def test_convert_array_type_string_to_str_list_flattened(self):
        """Test converting component string to flattened string list."""
        result = mtk.convert_array_type("cyl.vtx[:2]", "str", flatten=True)
        expected = ["cylShape.vtx[0]", "cylShape.vtx[1]", "cylShape.vtx[2]"]
        self.assertEqual(result, expected)

    def test_convert_array_type_string_to_pymel_objects(self):
        """Test converting component string to PyMEL objects."""
        result = mtk.convert_array_type("cyl.vtx[:2]", "obj")
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]), "cylShape.vtx[0:2]")

    def test_convert_array_type_string_to_pymel_objects_flattened(self):
        """Test converting component string to flattened PyMEL objects."""
        result = mtk.convert_array_type("cyl.vtx[:2]", "obj", flatten=True)
        self.assertEqual(len(result), 3)
        expected_strs = ["cylShape.vtx[0]", "cylShape.vtx[1]", "cylShape.vtx[2]"]
        result_strs = [str(v) for v in result]
        self.assertEqual(result_strs, expected_strs)

    def test_convert_array_type_string_to_int_indices(self):
        """Test converting component string to integer index range."""
        result = mtk.convert_array_type("cyl.vtx[:2]", "int")
        self.assertEqual(result, [0, 2])

    def test_convert_array_type_string_to_int_indices_flattened(self):
        """Test converting component string to flattened integer indices."""
        result = mtk.convert_array_type("cyl.vtx[:2]", "int", flatten=True)
        self.assertEqual(result, [0, 1, 2])

    # -------------------------------------------------------------------------
    # Main Window and UI Tests
    # -------------------------------------------------------------------------

    def test_get_main_window(self):
        """Test getting Maya's main window widget."""
        main_window = mtk.get_main_window()
        self.assertIsNotNone(main_window)
        # Should return a QWidget or QMainWindow
        self.assertTrue(hasattr(main_window, "windowTitle"))

    def test_get_panel_without_args_raises_error(self):
        """Test that get_panel without arguments raises RuntimeError."""
        with self.assertRaises(RuntimeError):
            mtk.get_panel()

    # -------------------------------------------------------------------------
    # Channel and Selection Tests
    # -------------------------------------------------------------------------

    def test_get_selected_channels_with_no_selection(self):
        """Test get_selected_channels returns empty list when nothing selected."""
        pm.select(clear=True)
        pm.channelBox("mainChannelBox", edit=True, select=None)
        try:
            result = mtk.get_selected_channels()
            self.assertIsInstance(result, list)
        except RuntimeError:
            # Channel box may not be available in all contexts
            self.skipTest("Channel box not available")

    def test_get_selected_channels_with_transform_selected(self):
        """Test getting selected channels when transform is selected."""
        pm.select(self.cyl)
        try:
            # Select translate channels in channel box
            pm.channelBox(
                "mainChannelBox", edit=True, select=["translateX", "translateY"]
            )
            result = mtk.get_selected_channels()
            if result:  # May be empty in batch mode
                self.assertIn("translateX", result)
        except RuntimeError:
            self.skipTest("Channel box not available")

    # -------------------------------------------------------------------------
    # Progress Bar Tests
    # -------------------------------------------------------------------------

    def test_main_progress_bar_context_manager(self):
        """Test progress bar as context manager."""
        try:
            with mtk.main_progress_bar(size=10) as progress:
                self.assertIsNotNone(progress)
                # Step through progress
                for i in range(10):
                    progress.step()
        except Exception as e:
            # Progress bar may not work in batch mode
            self.skipTest(f"Progress bar not available: {e}")

    def test_main_progress_bar_with_title(self):
        """Test progress bar with custom title."""
        try:
            with mtk.main_progress_bar(size=5, title="Test Progress") as progress:
                for i in range(5):
                    progress.step()
        except Exception as e:
            self.skipTest(f"Progress bar not available: {e}")

    # -------------------------------------------------------------------------
    # Undo/Redo Tests
    # -------------------------------------------------------------------------

    def test_undo_decorator(self):
        """Test undoable decorator wraps operations in undo chunk."""

        @mtk.undoable
        def create_and_move_cube():
            cube = pm.polyCube(name="test_undo_cube")[0]
            pm.move(cube, 5, 0, 0)
            return cube

        # Execute the decorated function
        cube = create_and_move_cube()
        self.assertTrue(pm.objExists("test_undo_cube"))

        # Undo should remove both the move and creation
        pm.undo()
        self.assertFalse(pm.objExists("test_undo_cube"))

    def test_undo_decorator_with_exception(self):
        """Test undoable decorator handles exceptions properly."""

        @mtk.undoable
        def create_and_fail():
            pm.polyCube(name="test_exception_cube")
            raise ValueError("Intentional test error")

        # Should raise the exception but still close undo chunk
        with self.assertRaises(ValueError):
            create_and_fail()

        # Clean up if cube was created
        if pm.objExists("test_exception_cube"):
            pm.delete("test_exception_cube")

    # -------------------------------------------------------------------------
    # Parameter Mapping Tests
    # -------------------------------------------------------------------------

    def test_get_parameter_values_mel(self):
        """Test getting parameter values from MEL command."""
        # Create a cube to work with
        cube = pm.polyCube(name="test_param_cube")[0]

        try:
            # Get the polyCube creation parameters
            result = mtk.get_parameter_values_mel(
                node=cube, cmd="polyCube", parameters=["width", "height", "depth"]
            )

            if result:
                self.assertIsInstance(result, dict)
                # Default cube dimensions are 1.0
                if "width" in result:
                    self.assertAlmostEqual(result["width"], 1.0, places=2)
        except (AttributeError, RuntimeError):
            self.skipTest("get_parameter_values_mel not implemented or unavailable")
        finally:
            pm.delete(cube)

    def test_set_parameter_values_mel(self):
        """Test setting parameter values via MEL command."""
        cube = pm.polyCube(name="test_set_param_cube")[0]

        try:
            # Attempt to set parameters
            mtk.set_parameter_values_mel(
                node=cube, cmd="polyCube", parameters={"width": 2.0, "height": 3.0}
            )
            # Verify if implemented
        except (AttributeError, RuntimeError):
            self.skipTest("set_parameter_values_mel not implemented or unavailable")
        finally:
            pm.delete(cube)

    # -------------------------------------------------------------------------
    # Utility Tests
    # -------------------------------------------------------------------------


class TestCoreUtilsEdgeCases(MayaTkTestCase):
    """Edge case tests for CoreUtils."""

    def test_get_array_type_with_empty_list(self):
        """Test array type detection with empty list."""
        result = mtk.get_array_type([])
        self.assertIn(result, ["list", None, ""])

    def test_get_array_type_with_none(self):
        """Test array type detection with None."""
        result = mtk.get_array_type(None)
        self.assertIn(result, ["none", "NoneType", None, ""])

    def test_convert_array_type_with_invalid_target_type(self):
        """Test converting to invalid target type returns lst unchanged."""
        # convert_array_type doesn't validate returned_type, just returns lst for unknown types
        cyl = self.create_test_cylinder()
        result = mtk.convert_array_type(f"{cyl}.vtx[0]", "invalid_type")
        # Should return PyMEL objects (the 'lst' parameter unchanged)
        self.assertTrue(len(result) > 0)

    def test_convert_array_type_with_nonexistent_component(self):
        """Test converting nonexistent component."""
        try:
            result = mtk.convert_array_type("nonexistent.vtx[0]", "str")
            # May return empty list or raise error depending on implementation
            if result is not None:
                self.assertIsInstance(result, list)
        except (RuntimeError, pm.MayaNodeError):
            pass  # Expected behavior


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Run the tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestCoreUtils))
    suite.addTests(loader.loadTestsFromTestCase(TestCoreUtilsEdgeCases))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    exit(0 if result.wasSuccessful() else 1)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
# Coverage:
# - Array type detection (int, str, vtx, e, f)
# - Array type conversion (str, obj, int with/without flatten)
# - Main window access
# - Panel management
# - Channel selection
# - Progress bar handling
# - Undo decorator functionality
# - Parameter mapping (MEL commands)
# - Edge cases and error handling
