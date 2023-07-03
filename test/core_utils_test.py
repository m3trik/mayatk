# !/usr/bin/python
# coding=utf-8
import os
import unittest
import inspect
import pymel.core as pm
from mayatk import CoreUtils


class Main(unittest.TestCase):
    """Main test class."""

    def perform_test(self, cases):
        """Execute the test cases."""
        for case in cases:
            if isinstance(case, str):
                expression = case
                expected_result = cases[case]
                method_name = str(expression).split("(")[0]
            else:
                result, expected_result = case
                method_name = result.__class__.__name__
                expression = None

            try:
                path = os.path.abspath(inspect.getfile(eval(method_name)))
            except (TypeError, IOError):
                path = ""

            if expression:
                result = eval(expression)

            self.assertEqual(
                result,
                expected_result,
                f"\n\n# Error: {path}\n#\tCall: {method_name}({', '.join(map(str, function_args)) if 'function_args' in locals() else ''})\n#\tExpected {type(expected_result)}: {expected_result}\n#\tReturned {type(result)}: {result}",
            )

    @staticmethod
    def replace_mem_address(obj):
        """Replace memory addresses in a string representation of an object with a fixed format of '0x00000000000'.

        Parameters:
                obj (object): The input object. The function first converts this object to a string using the `str` function.

        Returns:
                (str) The string representation of the object with all memory addresses replaced.

        Example:
                >>> replace_mem_address("<class 'str'> <PySide2.QtWidgets.QWidget(0x1ebe2677e80, name='MayaWindow') at 0x000001EBE6D48500>")
                "<class 'str'> <PySide2.QtWidgets.QWidget(0x00000000000, name='MayaWindow') at 0x00000000000>"
        """
        import re

        return re.sub(r"0x[a-fA-F\d]+", "0x00000000000", str(obj))


class CoreUtils_test(Main, CoreUtils):
    """
    set object mode:
            pm.selectMode(object=1)

    set component mode:
            pm.selectMode(component=1)

    set component mode type:
            pm.selectType(allObjects=1)
            pm.selectType(mc=1)
            pm.selectType(vertex=1)
            pm.selectType(edge=1)
            pm.selectType(facet=1)
            pm.selectType(polymeshUV=1)
            pm.selectType(meshUVShell=1)
    """

    # test imports:
    import mayatk as mtk
    from mayatk import CmptUtils
    from mayatk import get_components

    # Tear down the any previous test by creating a new scene:
    pm.mel.file(new=True, force=True)

    # assemble the test scene:
    if not pm.objExists("cyl"):
        cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )

    def test_undo(self):
        """ """
        self.assertEqual(
            self.replace_mem_address(self.undo()),
            "<function CoreUtils.undo.<locals>.wrapper at 0x00000000000>",
        )

    def test_getMainWindow(self):
        """ """
        self.perform_test(
            {
                "self.replace_mem_address(self.get_main_window())": '<PySide2.QtWidgets.QWidget(0x00000000000, name="MayaWindow") at 0x00000000000>'
                or None,
            }
        )

    def test_mfnMeshGenerator(self):
        """ """
        self.perform_test(
            {
                "str(next(self.mfn_mesh_generator('cyl'))).split(';')[0]": "<maya.OpenMaya.MFnMesh",
            }
        )

    def test_getArrayType(self):
        """ """
        self.assertEqual(self.get_array_type(100), "int")
        self.assertEqual(self.get_array_type("cylShape.vtx[:]"), "str")
        self.assertEqual(self.get_array_type(pm.ls("cylShape.vtx[:]")), "vtx")

    def test_convertArrayType(self):
        """ """
        self.perform_test(
            {
                "self.convert_array_type('cyl.vtx[:2]', 'str')": ["cylShape.vtx[0:2]"],
                "self.convert_array_type('cyl.vtx[:2]', 'str', flatten=True)": [
                    "cylShape.vtx[0]",
                    "cylShape.vtx[1]",
                    "cylShape.vtx[2]",
                ],
                "str(self.convert_array_type('cyl.vtx[:2]', 'obj'))": "[MeshVertex('cylShape.vtx[0:2]')]",
                "str(self.convert_array_type('cyl.vtx[:2]', 'obj', flatten=True))": "[MeshVertex('cylShape.vtx[0]'), MeshVertex('cylShape.vtx[1]'), MeshVertex('cylShape.vtx[2]')]",
                "self.convert_array_type('cyl.vtx[:2]', 'int')": [0, 2],
                "self.convert_array_type('cyl.vtx[:2]', 'int', flatten=True)": [
                    0,
                    1,
                    2,
                ],
            }
        )

    def test_getParameterValuesMEL(self):
        """ """
        self.perform_test(
            {
                # "self.get_parameter_mapping()": None,
            }
        )

    def test_setParameterValuesMEL(self):
        """ """
        self.perform_test(
            {
                # "self.set_parameter_mapping()": None,
            }
        )

    def test_getSelectedChannels(self):
        """ """
        self.perform_test(
            {
                # "self.get_selected_channels()": None,
            }
        )

    def test_getPanel(self):
        """ """
        self.perform_test(
            {
                # "self.get_panel()": None,
            }
        )

    def test_mainProgressBar(self):
        """ """
        self.perform_test(
            {
                # "self.main_progress_bar()": None,
            }
        )

    def test_viewportMessage(self):
        """ """
        self.perform_test(
            {
                # "self.viewport_message()": None,
            }
        )


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(exit=False)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------

# """

# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       "self.()": None,
#   })

# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })

# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })

# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })
# """

# Deprecated ---------------------
