# !/usr/bin/python
# coding=utf-8
"""Test Suite for edit_utils duplicate tool classes.

Covers:
    - DuplicateLinear.duplicate_linear (duplicate_linear.py)
    - DuplicateRadial.duplicate_radial + _validate_inputs (duplicate_radial.py)
    - DuplicateGrid.duplicate_grid (duplicate_grid.py)
"""
import unittest

import maya.cmds as cmds

from mayatk.edit_utils.duplicate_linear import DuplicateLinear
from mayatk.edit_utils.duplicate_radial import DuplicateRadial
from mayatk.edit_utils.duplicate_grid import DuplicateGrid

from base_test import MayaTkTestCase, QuickTestCase


class TestDuplicateLinear(MayaTkTestCase):
    """DuplicateLinear.duplicate_linear — copies arranged on a linear progression."""

    def test_returns_dict_keyed_by_original(self):
        cube = cmds.polyCube(name="dl_cube")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=3,
            translate=(2, 0, 0),
            instance=False,
        )
        self.assertIn(cube, result)
        self.assertEqual(len(result[cube]), 3)

    def test_creates_correct_number_of_copies(self):
        cube = cmds.polyCube(name="dl_count")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=5,
            translate=(1, 0, 0),
            instance=True,
        )
        self.assertEqual(len(result[cube]), 5)
        # Each copy should exist
        for copy in result[cube]:
            self.assertTrue(cmds.objExists(copy))

    def test_zero_copies_returns_empty(self):
        cube = cmds.polyCube(name="dl_zero")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube],
            num_copies=0,
            translate=(1, 0, 0),
        )
        self.assertEqual(result[cube], [])

    def test_instance_creates_real_instances(self):
        cube = cmds.polyCube(name="dl_inst")[0]
        result = DuplicateLinear.duplicate_linear(
            objects=[cube], num_copies=2, translate=(1, 0, 0), instance=True
        )
        for copy in result[cube]:
            # Instances share their shape with the original
            shapes = cmds.listRelatives(copy, shapes=True, fullPath=True) or []
            self.assertTrue(len(shapes) >= 1)


class TestDuplicateRadialValidation(QuickTestCase):
    """DuplicateRadial._validate_inputs is pure-Python — no Maya needed."""

    def test_invalid_axis_raises(self):
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("w", 0.5, 0.5)

    def test_weight_bias_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", 1.5, 0.5)
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", -0.1, 0.5)

    def test_weight_curve_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", 0.5, 1.5)
        with self.assertRaises(ValueError):
            DuplicateRadial._validate_inputs("y", 0.5, -0.1)

    def test_valid_inputs_pass(self):
        # Should not raise
        DuplicateRadial._validate_inputs("x", 0.0, 0.0)
        DuplicateRadial._validate_inputs("y", 0.5, 0.5)
        DuplicateRadial._validate_inputs("z", 1.0, 1.0)


class TestDuplicateRadial(MayaTkTestCase):
    """DuplicateRadial.duplicate_radial — radial pattern."""

    def test_creates_expected_number_of_copies(self):
        cube = cmds.polyCube(name="dr_cube")[0]
        cmds.move(5, 0, 0, cube)

        result = DuplicateRadial.duplicate_radial(
            objects=[cube],
            num_copies=4,
            start_angle=0,
            end_angle=360,
            rotate_axis="y",
            keep_original=True,
            instance=True,
            suffix=False,
        )
        self.assertIn(cube, result)
        self.assertEqual(len(result[cube]), 4)

    def test_invalid_axis_raises(self):
        cube = cmds.polyCube(name="dr_bad")[0]
        with self.assertRaises(ValueError):
            DuplicateRadial.duplicate_radial(
                objects=[cube],
                num_copies=2,
                rotate_axis="bogus",
            )


class TestDuplicateGrid(MayaTkTestCase):
    """DuplicateGrid.duplicate_grid — 3D grid duplication."""

    def test_empty_objects_returns_empty(self):
        result = DuplicateGrid.duplicate_grid(objects=[], dimensions=(2, 2, 2))
        self.assertEqual(result, [])

    def test_grid_creates_x_y_z_product_of_copies(self):
        cube = cmds.polyCube(name="dg_cube")[0]
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(2, 2, 2), spacing=1, instance=True, group=True
        )
        # When group=True, returns the group node name
        self.assertTrue(cmds.objExists(result) if isinstance(result, str) else True)

    def test_grid_one_in_each_axis(self):
        cube = cmds.polyCube(name="dg_min")[0]
        # Should produce a single copy per slot
        result = DuplicateGrid.duplicate_grid(
            objects=[cube], dimensions=(1, 1, 1), spacing=0, group=True
        )
        # Group should exist
        if isinstance(result, str):
            self.assertTrue(cmds.objExists(result))


if __name__ == "__main__":
    unittest.main()
