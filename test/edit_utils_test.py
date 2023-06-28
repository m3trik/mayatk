# !/usr/bin/python
# coding=utf-8
import os
import unittest
import inspect
import pymel.core as pm
from mayatk import EditUtils


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


class EditUtils_test(Main, EditUtils):
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

    if not pm.objExists("cyl"):
        cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )

    def test_rename(self):
        """ """
        self.perform_test(
            {
                "self.rename('cube1', 'newName')": None,
                "self.rename('newName', 'cube1')": None,
            }
        )

    def test_setCase(self):
        """ """
        self.perform_test(
            {
                "self.set_case('cube1', 'lower')": None,
            }
        )

    def test_setSuffixByObjLocation(self):
        """ """
        if not pm.objExists("c1"):
            c1 = pm.polyCube(
                width=2,
                height=2,
                depth=8,
                subdivisionsX=1,
                subdivisionsY=1,
                subdivisionsZ=1,
                name="c1",
            )

        if not pm.objExists("c2"):
            c2 = pm.polyCube(
                width=8,
                height=2,
                depth=2,
                subdivisionsX=1,
                subdivisionsY=1,
                subdivisionsZ=1,
                name="c2",
            )
            pm.move(0, 0, 5, c2)

        self.perform_test(
            {
                "self.append_location_based_suffix(['c1', 'c2'])": None,
            }
        )

    def test_snapClosestVerts(self):
        """ """
        self.perform_test(
            {
                "self.snap_closest_verts('cube1', 'cube2')": None,
            }
        )

    def test_mergeVertices(self):
        """ """
        self.perform_test(
            {
                "self.merge_vertices('cube1')": None,
            }
        )

    def test_deleteAlongAxis(self):
        """ """
        self.perform_test(
            {
                "self.delete_along_axis('cube1')": None,
            }
        )

    def test_getAllFacesOnAxis(self):
        """ """
        self.perform_test(
            {
                "self.get_all_faces_on_axis('cube1')": [],  # faces should have been deleted by the previous test 'delete_along_axis'.
            }
        )

    def test_cleanGeometry(self):
        """ """
        self.perform_test(
            {
                "self.clean_geometry('cyl')": None,
            }
        )

    def test_getOverlappingDupObjects(self):
        """ """
        self.perform_test(
            {
                "self.get_overlapping_dup_objects(['cyl', 'cube1', 'cube2'])": set(),
            }
        )

    def test_findNonManifoldVertex(self):
        """ """
        self.perform_test(
            {
                "self.find_non_manifold_vertex('cyl')": set(),
            }
        )

    def test_splitNonManifoldVertex(self):
        """ """
        self.perform_test(
            {
                "self.split_non_manifold_vertex('cyl')": None,
            }
        )

    def test_getNGons(self):
        """ """
        self.perform_test(
            {
                "self.get_ngons('cyl')": [],
            }
        )

    def test_getOverlappingVertices(self):
        """ """
        self.perform_test(
            {
                "self.get_overlapping_vertices('cyl')": [],
            }
        )

    def test_getOverlappingFaces(self):
        """ """
        self.perform_test(
            {
                "self.get_overlapping_faces('cyl')": [],
                "self.get_overlapping_faces('cyl.f[:]')": [],
            }
        )

    def test_getSimilarMesh(self):
        """ """
        self.perform_test(
            {
                "self.get_similar_mesh('cyl')": [],
            }
        )

    def test_getSimilarTopo(self):
        """ """
        self.perform_test(
            {
                "self.get_similar_topo('cyl')": [],
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
