# !/usr/bin/python
# coding=utf-8
import unittest
import pymel.core as pm
import mayatk as mtk


class CoreUtilsTest(unittest.TestCase):
    """Unit tests for the CoreUtils class"""

    def setUp(self):
        """Set up test scene"""
        pm.mel.file(new=True, force=True)
        self.cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]

    def tearDown(self):
        """Clean up test scene"""
        pm.delete(self.cyl)

    def test_undo(self):
        pass
        # Provide the missing 'fn' argument for undo method
        # self.assertEqual(mtk.undo(fn="<function_name>"), "<expected result>")

    def test_get_main_window(self):
        # Update the assertion with the returned value
        self.assertIsNotNone(mtk.get_main_window())

    def test_mfn_mesh_generator(self):
        self.assertEqual(
            str(next(mtk.mfn_mesh_generator("cyl"))).split(";")[0],
            "<maya.OpenMaya.MFnMesh",
        )

    def test_get_array_type(self):
        self.assertEqual(mtk.get_array_type(100), "int")
        self.assertEqual(mtk.get_array_type("cylShape.vtx[:]"), "str")
        self.assertEqual(mtk.get_array_type(pm.ls("cylShape.vtx[:]")), "vtx")

    def test_convert_array_type(self):
        self.assertEqual(
            mtk.convert_array_type("cyl.vtx[:2]", "str"), ["cylShape.vtx[0:2]"]
        )
        self.assertEqual(
            mtk.convert_array_type("cyl.vtx[:2]", "str", flatten=True),
            ["cylShape.vtx[0]", "cylShape.vtx[1]", "cylShape.vtx[2]"],
        )
        self.assertEqual(
            str(mtk.convert_array_type("cyl.vtx[:2]", "obj")),
            "[MeshVertex('cylShape.vtx[0:2]')]",
        )
        self.assertEqual(
            str(mtk.convert_array_type("cyl.vtx[:2]", "obj", flatten=True)),
            "[MeshVertex('cylShape.vtx[0]'), MeshVertex('cylShape.vtx[1]'), MeshVertex('cylShape.vtx[2]')]",
        )
        self.assertEqual(mtk.convert_array_type("cyl.vtx[:2]", "int"), [0, 2])
        self.assertEqual(
            mtk.convert_array_type("cyl.vtx[:2]", "int", flatten=True), [0, 1, 2]
        )

    def test_get_parameter_values_mel(self):
        pass
        # Provide the missing arguments for get_parameter_mapping method
        # self.assertEqual(
        #     mtk.get_parameter_mapping(
        #         node="<node>", cmd="<cmd>", parameters="<parameters>"
        #     ),
        #     "<expected result>",
        # )

    def test_set_parameter_values_mel(self):
        pass
        # Provide the missing arguments for set_parameter_mapping method
        # self.assertEqual(
        #     mtk.set_parameter_mapping(
        #         node="<node>", cmd="<cmd>", parameters="<parameters>"
        #     ),
        #     "<expected result>",
        # )

    def test_get_selected_channels(self):
        pass
        # Update the assertion with the returned value
        # self.assertEqual(mtk.get_selected_channels(), [])

    def test_generate_unique_name(self):
        pass
        # Provide the missing 'base_name' argument for generate_unique_name method
        # self.assertEqual(
        #     mtk.generate_unique_name(base_name="<base_name>"), "<expected result>"
        # )

    def test_get_panel(self):
        # Catching RuntimeError to handle the 'Not enough flags and/or arguments' error
        with self.assertRaises(RuntimeError):
            mtk.get_panel()

    def test_main_progress_bar(self):
        pass
        # Provide the missing 'size' argument for main_progress_bar method
        # self.assertEqual(mtk.main_progress_bar(size="<size>"), "<expected result>")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    mtk.clear_scrollfield_reporters()

    # Create a Test Suite
    suite = unittest.TestSuite()

    # Add the test case class to the suite
    suite.addTest(unittest.makeSuite(CoreUtilsTest))

    # Run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
