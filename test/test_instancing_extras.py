# !/usr/bin/python
# coding=utf-8
"""Test Suite for instancing modules not covered elsewhere.

Covers:
    - InstancingStrategy + StrategyConfig + StrategyType (instancing_strategy.py)
    - AssemblyReconstructor smoke / API surface (assembly_reconstructor.py)

`auto_instancer` and `geometry_matcher` are covered by existing tests.
"""
import unittest

import maya.cmds as cmds

from mayatk.core_utils.instancing.instancing_strategy import (
    InstancingStrategy,
    StrategyConfig,
    StrategyType,
)
from mayatk.core_utils.instancing.assembly_reconstructor import AssemblyReconstructor
from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher

from base_test import MayaTkTestCase, QuickTestCase


class TestStrategyType(QuickTestCase):
    """StrategyType enum."""

    def test_all_members_present(self):
        members = {s.name for s in StrategyType}
        self.assertEqual(
            members,
            {"BAKE", "COMBINE", "GPU_INSTANCE", "KEEP_SEPARATE"},
        )

    def test_value_round_trip(self):
        self.assertEqual(StrategyType("BAKE"), StrategyType.BAKE)


class TestStrategyConfig(QuickTestCase):
    """StrategyConfig dataclass defaults."""

    def test_defaults(self):
        c = StrategyConfig()
        self.assertTrue(c.is_static)
        self.assertFalse(c.needs_individual)
        self.assertFalse(c.will_be_lightmapped)
        self.assertTrue(c.can_gpu_instance)


class TestInstancingStrategyDecisions(QuickTestCase):
    """Pure decision-tree behavior — no Maya required."""

    def _strat(self, **overrides):
        return InstancingStrategy(StrategyConfig(**overrides))

    def test_needs_individual_overrides_everything(self):
        s = self._strat(needs_individual=True)
        self.assertEqual(s.evaluate(group_size=100, triangle_count=10000), StrategyType.KEEP_SEPARATE)

    def test_dynamic_with_gpu_instance_returns_gpu(self):
        s = self._strat(is_static=False, can_gpu_instance=True)
        self.assertEqual(s.evaluate(group_size=2, triangle_count=10), StrategyType.GPU_INSTANCE)

    def test_dynamic_without_gpu_instance_keeps_separate(self):
        s = self._strat(is_static=False, can_gpu_instance=False)
        self.assertEqual(s.evaluate(group_size=2, triangle_count=10), StrategyType.KEEP_SEPARATE)

    def test_micro_geometry_large_group_combines(self):
        s = self._strat()
        self.assertEqual(s.evaluate(group_size=20, triangle_count=100), StrategyType.COMBINE)

    def test_micro_geometry_small_group_combines_when_repeated(self):
        s = self._strat()
        self.assertEqual(s.evaluate(group_size=3, triangle_count=100), StrategyType.COMBINE)

    def test_micro_geometry_lone_unique_keeps_separate(self):
        s = self._strat()
        self.assertEqual(s.evaluate(group_size=1, triangle_count=100), StrategyType.KEEP_SEPARATE)

    def test_static_no_gpu_instancing_combines(self):
        s = self._strat(can_gpu_instance=False)
        self.assertEqual(s.evaluate(group_size=10, triangle_count=2000), StrategyType.COMBINE)

    def test_worth_instancing_threshold_standard(self):
        s = self._strat()
        # Group=10, tris>=800 — qualifies
        self.assertEqual(s.evaluate(group_size=10, triangle_count=800), StrategyType.GPU_INSTANCE)
        # Group=10, tris=799 — falls through (and is not heavy)
        self.assertEqual(s.evaluate(group_size=10, triangle_count=799), StrategyType.COMBINE)

    def test_worth_instancing_threshold_lightmap_stricter(self):
        s = self._strat(will_be_lightmapped=True)
        # Standard threshold 800 not enough for lightmapped; needs 1500
        self.assertEqual(s.evaluate(group_size=10, triangle_count=900), StrategyType.COMBINE)
        self.assertEqual(s.evaluate(group_size=10, triangle_count=1500), StrategyType.GPU_INSTANCE)

    def test_heavy_mesh_exception(self):
        s = self._strat()
        # Tris>=5000 + group>=3 qualifies even when group < 10
        self.assertEqual(s.evaluate(group_size=3, triangle_count=5000), StrategyType.GPU_INSTANCE)
        # Below heavy threshold + small group falls through to COMBINE
        self.assertEqual(s.evaluate(group_size=3, triangle_count=4999), StrategyType.COMBINE)

    def test_default_fallback_combine(self):
        s = self._strat()
        # Mid-range tris, small group, no special flags — default static fallback
        self.assertEqual(s.evaluate(group_size=4, triangle_count=600), StrategyType.COMBINE)

    def test_explicit_triangle_count_wins_over_mesh_node(self):
        s = self._strat()
        # Even passing a non-existent mesh, explicit count must take effect
        result = s.evaluate(
            group_size=10, mesh_node="nonexistent", triangle_count=5000
        )
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

    def test_combine_targets_initialized_empty(self):
        self.assertEqual(self.recon.combine_targets, [])

    def test_is_mesh_transform_true_for_polycube(self):
        cube = cmds.polyCube(name="mesh_check_cube")[0]
        self.assertTrue(AssemblyReconstructor._is_mesh_transform(cube))

    def test_is_mesh_transform_false_for_locator(self):
        loc = cmds.spaceLocator(name="loc_check")[0]
        self.assertFalse(AssemblyReconstructor._is_mesh_transform(loc))


if __name__ == "__main__":
    unittest.main()
