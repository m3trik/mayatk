# !/usr/bin/python
# coding=utf-8
import os, sys
import unittest
import inspect

import pymel.core as pm

from mayatk import Rig


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


class Rig_test(Main, Rig):
    """ """

    # Tear down the any previous test by creating a new scene:
    pm.mel.file(new=True, force=True)

    # assemble the test scene:
    if not pm.objExists("loc"):
        loc = pm.spaceLocator(name="loc")

    if not pm.objExists("cyl"):
        cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=6,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )

    def test_createLocator(self):
        """ """
        self.perform_test(
            {
                "self.createLocator('_loc')": "_loc",
            }
        )

    def test_removeLocator(self):
        """ """
        self.perform_test(
            {
                "self.removeLocator('loc')": None,
            }
        )

    def test_resetPivotTransforms(self):
        """ """
        self.perform_test(
            {
                "self.resetPivotTransforms('cyl')": None,
            }
        )

    def test_bakeCustomPivot(self):
        """ """
        self.perform_test(
            {
                "self.bakeCustomPivot('cyl')": None,
                "self.bakeCustomPivot('cyl', position=True)": None,
                "self.bakeCustomPivot('cyl', orientation=True)": None,
            }
        )

    def test_setAttrLockState(self):
        """ """
        self.perform_test(
            {
                "self.setAttrLockState('cyl')": None,
            }
        )

    def test_createGroup(self):
        """ """
        self.perform_test(
            {
                "self.createGroup(name='emptyGrp').name()": "emptyGrp",
            }
        )

    def test_createGroupLRA(self):
        """ """
        self.perform_test(
            {
                "self.createGroupLRA('cyl', 'LRAgrp').name()": "LRAgrp",
            }
        )

    def test_createLocatorAtObject(self):
        """ """
        self.perform_test(
            {
                "self.createLocatorAtObject('cyl')": None,
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
