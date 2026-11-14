# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.core_utils.units: conversions by the kind of query."""

import unittest

import maya.cmds as cmds

from mayatk.core_utils.units import Units
from base_test import MayaTkTestCase


class TestUnits(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(cmds.currentUnit, linear="cm", angle="deg")

    def test_internal_is_the_centimetre_whatever_the_working_unit(self):
        cmds.currentUnit(linear="m")
        self.assertAlmostEqual(Units.convert(250.0), 2.5)
        self.assertAlmostEqual(Units.metres_per("internal"), 0.01)

    def test_working_is_read_when_the_value_is(self):
        cmds.currentUnit(linear="m")
        self.assertAlmostEqual(Units.convert(2.5, "working"), 2.5)
        cmds.currentUnit(linear="mm")
        self.assertAlmostEqual(Units.convert(2.5, "working"), 0.0025)
        self.assertAlmostEqual(Units.convert(1.0, "m", "working"), 1000.0)

    def test_a_working_read_matches_maya_own_conversion(self):
        loc = cmds.spaceLocator()[0]
        cmds.setAttr(f"{loc}.translateX", 150.0)  # in cm
        cmds.currentUnit(linear="in")
        read = cmds.getAttr(f"{loc}.translateX")
        self.assertAlmostEqual(Units.convert(read, "working"), 1.5)
        matrix = cmds.xform(loc, query=True, worldSpace=True, matrix=True)
        self.assertAlmostEqual(Units.convert(matrix[12]), 1.5)

    def test_angles_by_the_working_unit(self):
        cmds.currentUnit(angle="rad")
        self.assertAlmostEqual(Units.angle(3.14159265358979), 180.0)
        cmds.currentUnit(angle="deg")
        self.assertAlmostEqual(Units.angle(90.0, target="rad"), 1.5707963267949)
        self.assertAlmostEqual(Units.angle(1.0, "internal"), 57.29577951308232)

    def test_sequences_nest_and_none_passes(self):
        self.assertEqual(Units.convert([[100.0, 200.0], [300.0]]), [[1.0, 2.0], [3.0]])
        self.assertIsNone(Units.convert(None))

    def test_an_unknown_unit_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            Units.convert(1.0, "furlong")
        with self.assertRaises(ValueError):
            Units.angle(1.0, "grad")


if __name__ == "__main__":
    unittest.main()
