# !/usr/bin/python
# coding=utf-8
import os
import unittest
import inspect
import pymel.core as pm
from mayatk import XformUtils


# sfr = pm.melGlobals['cmdScrollFieldReporter']
# pm.cmdScrollFieldReporter(sfr, edit=1, clear=1)


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


class XformUtils_test(Main, XformUtils):
    """ """

    # Tear down the any previous test by creating a new scene:
    pm.mel.file(new=True, force=True)

    # assemble the test scene:
    if not pm.objExists("cube1"):
        cube1 = pm.polyCube(
            width=5,
            height=5,
            depth=5,
            subdivisionsX=1,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cube1",
        )

    if not pm.objExists("cube2"):
        cube2 = pm.polyCube(
            width=2,
            height=4,
            depth=8,
            subdivisionsX=3,
            subdivisionsY=3,
            subdivisionsZ=3,
            name="cube2",
        )

    if not pm.objExists("sph"):
        sph = pm.polySphere(radius=5, subdivisionsX=12, subdivisionsY=12, name="sph")

    def test_moveTo(self):
        """ """
        self.perform_test(
            {
                "self.move_to('cube1', 'cube2')": None,
            }
        )

    def test_dropToGrid(self):
        """ """
        self.perform_test(
            {
                "self.drop_to_grid('cube1', align='Min', origin=True, center_pivot=True, freeze_transforms=True)": None,
            }
        )

    def test_resetTranslation(self):
        """ """
        self.perform_test(
            {
                "self.reset_translation('cube1')": None,
            }
        )

    def test_setTranslationToPivot(self):
        """ """
        self.perform_test(
            {
                "self.set_translation_to_pivot('cube1')": None,
            }
        )

    def test_alignPivotToSelection(self):
        """ """
        self.perform_test(
            {
                "self.align_pivot_to_selection('cube1', 'cube2')": None,
            }
        )

    def test_aimObjectAtPoint(self):
        """ """
        self.perform_test(
            {
                "self.aim_object_at_point(['cube1', 'cube2'], (0, 15, 15))": None,
            }
        )

    def test_rotateAxis(self):
        """ """
        self.perform_test(
            {
                "self.rotate_axis(['cube1', 'cube2'], (0, 15, 15))": None,
            }
        )

    def test_getOrientation(self):
        """ """
        self.perform_test(
            {
                "self.get_orientation('cube1')": ([1, 0, 0], [0, 1, 0], [0, 0, 1]),
            }
        )

    def test_getDistanceBetweenTwoObjects(self):
        """ """
        self.drop_to_grid(["cube1", "cube2"], origin=True, center_pivot=True)
        pm.move("cube2", 0, 0, 15)

        self.perform_test(
            {
                "self.get_dist_between_two_objects('cube1', 'cube2')": 15,
            }
        )

    def test_getCenterPoint(self):
        """ """
        self.perform_test(
            {
                "self.get_center_point('sph')": (0, 0, 0),
                "self.get_center_point('sph.vtx[*]')": (0, 0, 0),
            }
        )

    def test_getBoundingBox(self):
        """ """
        self.perform_test(
            {
                "self.get_bounding_box('sph', 'size')": (10, 10, 10),
            }
        )

    def test_sortByBoundingBoxValue(self):
        """ """
        self.perform_test(
            {
                "str(self.sort_by_bounding_box_value(['sph.vtx[0]', 'sph.f[0]']))": "[MeshFace('sphShape.f[0]'), MeshVertex('sphShape.vtx[0]')]",
            }
        )

    def test_matchScale(self):
        """ """
        self.perform_test(
            {
                "self.match_scale('cube1', 'cube2', scale=False)": [
                    1.3063946090989371,
                    0.539387725343009,
                    0.539387708993454,
                ],
            }
        )

    def test_snap3PointsTo3Points(self):
        """ """
        self.perform_test(
            {
                # "self.align_using_three_points()": None,
            }
        )

    def test_isOverlapping(self):
        """ """
        self.perform_test(
            {
                # "self.is_overlapping()": None,
            }
        )

    def test_alignVertices(self):
        """ """
        self.perform_test(
            {
                # "self.align_vertices()": None,
            }
        )


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


# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })
# """

# # Deprecated ---------------------
