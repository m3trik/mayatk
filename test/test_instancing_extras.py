# !/usr/bin/python
# coding=utf-8
"""Test Suite for instancing modules not covered elsewhere.

Covers:
    - InstancingStrategy's Maya binding (instancing_strategy.py)
    - AssemblyReconstructor smoke / API surface (assembly_reconstructor.py)

`auto_instancer` and `geometry_matcher` are covered by existing tests.
"""

import unittest

import maya.cmds as cmds

import pythontk as ptk

from mayatk.core_utils.auto_instancer.instancing_strategy import (
    InstancingStrategy,
    StrategyConfig,
    StrategyType,
)
from mayatk.core_utils.auto_instancer.assembly_reconstructor import (
    AssemblyReconstructor,
)
from mayatk.core_utils.auto_instancer.geometry_matcher import GeometryMatcher

from base_test import MayaTkTestCase, QuickTestCase


class TestInstancingStrategyBinding(QuickTestCase):
    """The mayatk names bind pythontk's engine (its decision tree is tested
    in pythontk ``test_instancing_strategy.py``); only the triangle count is
    Maya's."""

    def test_names_are_the_engine(self):
        self.assertTrue(issubclass(InstancingStrategy, ptk.InstancingStrategy))
        self.assertIs(StrategyConfig, ptk.StrategyConfig)
        self.assertIs(StrategyType, ptk.StrategyType)

    def test_explicit_triangle_count_wins_over_mesh_node(self):
        s = InstancingStrategy(StrategyConfig())
        # Even passing a non-existent mesh, explicit count must take effect
        result = s.evaluate(group_size=10, mesh_node="nonexistent", triangle_count=5000)
        self.assertEqual(result, StrategyType.GPU_INSTANCE)


class TestInstancingStrategyTriangleCount(MayaTkTestCase):
    """_get_triangle_count uses polyEvaluate; needs Maya."""

    def test_get_triangle_count_returns_int(self):
        cube = cmds.polyCube(name="strat_cube")[0]
        s = InstancingStrategy(StrategyConfig())
        n = s._get_triangle_count(cube)
        self.assertIsInstance(n, int)
        self.assertGreater(n, 0)

    def test_get_triangle_count_invalid_node_returns_zero(self):
        s = InstancingStrategy(StrategyConfig())
        self.assertEqual(s._get_triangle_count("nonexistent_node"), 0)


class TestAssemblyReconstructorAPI(MayaTkTestCase):
    """AssemblyReconstructor smoke tests — covers API surface, not deep logic."""

    def setUp(self):
        super().setUp()
        self.matcher = GeometryMatcher()
        self.recon = AssemblyReconstructor(matcher=self.matcher, verbose=False)

    def test_separate_combined_meshes_passes_through_single_shell(self):
        cube = cmds.polyCube(name="single_cube")[0]
        result = self.recon.separate_combined_meshes([cube])
        # A single-shell mesh should not be separated
        self.assertEqual(len(result), 1)

    def test_separate_combined_meshes_handles_nonexistent_nodes(self):
        # Should not raise
        result = self.recon.separate_combined_meshes(["does_not_exist"])
        self.assertEqual(result, [])

    def test_separate_combined_meshes_separates_multi_shell(self):
        a = cmds.polyCube(name="multi_a")[0]
        b = cmds.polyCube(name="multi_b")[0]
        cmds.move(5, 0, 0, b)
        combined = cmds.polyUnite(a, b, ch=False, name="combined")[0]

        result = self.recon.separate_combined_meshes([combined])
        # polySeparate yields >= 2 nodes for a 2-shell combine
        self.assertGreaterEqual(len(result), 2)

    def test_is_mesh_transform_true_for_polycube(self):
        cube = cmds.polyCube(name="mesh_check_cube")[0]
        self.assertTrue(AssemblyReconstructor._is_mesh_transform(cube))

    def test_is_mesh_transform_false_for_locator(self):
        loc = cmds.spaceLocator(name="loc_check")[0]
        self.assertFalse(AssemblyReconstructor._is_mesh_transform(loc))


if __name__ == "__main__":
    unittest.main()
