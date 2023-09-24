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
                f"\n\n# Error: {path}\n#\tCall: {method_name})\n#\tExpected {type(expected_result)}: {expected_result}\n#\tReturned {type(result)}: {result}",
            )


class XformUtils_test(unittest.TestCase, XformUtils):
    """Unit tests for the XformUtils class"""

    def setUp(self):
        """Set up test scene"""
        pm.mel.file(new=True, force=True)
        self.cube1 = pm.polyCube(name="cube1")[0]
        self.cube2 = pm.polyCube(name="cube2")[0]
        self.sph = pm.polySphere(name="sph")[0]

    def tearDown(self):
        """Clean up test scene"""
        pm.delete(self.cube1, self.cube2, self.sph)

    def test_moveTo(self):
        self.assertEqual(self.move_to(self.cube1, self.cube2), None)

    def test_dropToGrid(self):
        self.assertEqual(
            self.drop_to_grid(
                self.cube1,
                align="Min",
                origin=True,
                center_pivot=True,
                freeze_transforms=True,
            ),
            None,
        )

    def test_resetTranslation(self):
        self.assertEqual(self.reset_translation(self.cube1), None)

    def test_setTranslationToPivot(self):
        self.assertEqual(self.set_translation_to_pivot(self.cube1), None)

    def test_alignPivotToSelection(self):
        self.assertEqual(self.align_pivot_to_selection(self.cube1, self.cube2), None)

    def test_aimObjectAtPoint(self):
        self.assertEqual(
            self.aim_object_at_point([self.cube1, self.cube2], (0, 15, 15)), None
        )

    def test_rotateAxis(self):
        self.assertEqual(self.rotate_axis([self.cube1, self.cube2], (0, 15, 15)), None)

    def test_getOrientation(self):
        self.assertEqual(
            self.get_orientation(self.cube1), ([1, 0, 0], [0, 1, 0], [0, 0, 1])
        )

    def test_getDistanceBetweenTwoObjects(self):
        self.drop_to_grid([self.cube1, self.cube2], origin=True, center_pivot=True)
        pm.move(self.cube2, 0, 0, 15)
        self.assertEqual(self.get_dist_between_two_objects(self.cube1, self.cube2), 15)

    def test_getCenterPoint(self):
        self.assertEqual(self.get_center_point(self.sph), (0, 0, 0))

    def test_getBoundingBox(self):
        self.assertEqual(self.get_bounding_box(self.sph, "size"), (10, 10, 10))

    def test_sortByBoundingBoxValue(self):
        self.assertEqual(
            str(self.sort_by_bounding_box_value(["sph.vtx[0]", "sph.f[0]"])),
            "[MeshFace('sphShape.f[0]'), MeshVertex('sphShape.vtx[0]')]",
        )

    def test_matchScale(self):
        self.assertEqual(
            self.match_scale(self.cube1, self.cube2, scale=False),
            [1.3063946090989371, 0.539387725343009, 0.539387708993454],
        )

    def test_snap3PointsTo3Points(self):
        """ """
        # self.assertEqual(self.align_using_three_points(), None)

    def test_isOverlapping(self):
        """ """
        # self.assertEqual(self.is_overlapping(), None)

    def test_alignVertices(self):
        """ """
        # self.assertEqual(self.align_vertices(), None)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(exit=False)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
