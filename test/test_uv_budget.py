# !/usr/bin/python
# coding=utf-8
"""Tests for UvUtils.analyze_uv_budget and its measurement layer.

The planning arithmetic is pinned in pythontk's ``test_uv_budget``. What
belongs here is everything the Maya side is responsible for: that the
measurement agrees with Maya's own numbers, that the three multiplicities
(instances, stacked shells, source map size) are actually divided out, and that
an analysis leaves the scene untouched.
"""

import unittest

import maya.cmds as cmds

from mayatk.uv_utils._uv_utils import UvUtils
from mayatk.uv_utils._uv_budget import _UvBudgetInternal

from base_test import MayaTkTestCase


def projected(node):
    """Give *node* a real multi-shell layout to measure."""
    cmds.polyAutoProjection(node, lm=0, pb=0, ibd=1, cm=0, l=2, sc=1, o=1, p=6, ps=0.2)
    cmds.delete(node, constructionHistory=True)
    return node


class TestMeshMeasurement(MayaTkTestCase):
    """Per-mesh numbers must match what Maya reports independently."""

    def test_density_matches_get_texel_density(self):
        cube = projected(cmds.polyCube(w=2, h=2, d=2, name="bud_cube")[0])
        metrics = _UvBudgetInternal._shell_metrics(cube, collapse_stacked=False)
        # Relative, not absolute: this module sums triangle areas in float64
        # while polyEvaluate sums Maya's own, so the two agree to ~1e-8
        # relative on a value of several hundred.
        self.assertAlmostEqual(
            metrics.density * 4096 / UvUtils.get_texel_density([cube], 4096),
            1.0,
            places=6,
        )

    def test_area_is_world_space(self):
        """A 2x2x2 cube is 24 square units; a local-space mixup would not be."""
        cube = projected(cmds.polyCube(w=2, h=2, d=2, name="bud_area")[0])
        metrics = _UvBudgetInternal._shell_metrics(cube)
        self.assertAlmostEqual(metrics.area_3d, 24.0, places=6)

    def test_area_follows_the_transform(self):
        cube = projected(cmds.polyCube(w=2, h=2, d=2, name="bud_scaled")[0])
        cmds.setAttr(f"{cube}.scale", 2, 2, 2)
        metrics = _UvBudgetInternal._shell_metrics(cube)
        self.assertAlmostEqual(metrics.area_3d, 96.0, places=5)

    def test_perimeter_is_the_chart_border(self):
        """Six 2x2 charts have a border of 8 units each."""
        cube = projected(cmds.polyCube(w=2, h=2, d=2, name="bud_perim")[0])
        metrics = _UvBudgetInternal._shell_metrics(cube, collapse_stacked=False)
        self.assertEqual(metrics.charts, 6)
        self.assertAlmostEqual(metrics.perimeter, 48.0, places=4)

    def test_unmapped_mesh_reports_rather_than_measuring_zero(self):
        plane = cmds.polyPlane(name="bud_unmapped")[0]
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        cmds.polyMapDel(f"{shape}.map[*]")
        with self.assertRaises(ValueError):
            _UvBudgetInternal._shell_metrics(plane)


class TestStackedShells(MayaTkTestCase):
    """Shells sharing a UV region are one claim on map space, not two."""

    def _stacked_pair(self, name):
        a = cmds.polyPlane(w=1, h=1, sx=1, sy=1)[0]
        b = cmds.polyPlane(w=1, h=1, sx=1, sy=1)[0]
        cmds.polyUnite(a, b, ch=False, name=name)
        return cmds.ls(name, type="transform")[0]

    def test_stack_collapses_to_one_chart(self):
        union = self._stacked_pair("bud_stack")
        loose = _UvBudgetInternal._shell_metrics(union, collapse_stacked=False)
        tight = _UvBudgetInternal._shell_metrics(union, collapse_stacked=True)
        self.assertEqual(loose.charts, 2)
        self.assertEqual(tight.charts, 1)

    def test_stack_factor_reports_the_multiplicity(self):
        union = self._stacked_pair("bud_factor")
        metrics = _UvBudgetInternal._shell_metrics(union, collapse_stacked=True)
        self.assertAlmostEqual(metrics.stack_factor, 2.0, places=6)

    def test_collapsed_area_is_one_copy(self):
        union = self._stacked_pair("bud_copy")
        loose = _UvBudgetInternal._shell_metrics(union, collapse_stacked=False)
        tight = _UvBudgetInternal._shell_metrics(union, collapse_stacked=True)
        self.assertAlmostEqual(tight.area_3d, loose.area_3d / 2, places=6)

    def test_distinct_shells_are_not_collapsed(self):
        """Two planes at different UV positions must both keep their space."""
        a = cmds.polyPlane(w=1, h=1, sx=1, sy=1)[0]
        b = cmds.polyPlane(w=1, h=1, sx=1, sy=1)[0]
        cmds.polyEditUV(f"{b}.map[*]", u=2.0, v=0.0)
        cmds.polyUnite(a, b, ch=False, name="bud_apart")
        union = cmds.ls("bud_apart", type="transform")[0]
        metrics = _UvBudgetInternal._shell_metrics(union, collapse_stacked=True)
        self.assertEqual(metrics.charts, 2)
        self.assertAlmostEqual(metrics.stack_factor, 1.0, places=6)


class TestInstancing(MayaTkTestCase):
    """N instance paths are one shape, so one claim on map space."""

    def test_instances_do_not_inflate_the_budget(self):
        cube = projected(cmds.polyCube(w=2, h=2, d=2, name="bud_inst")[0])
        alone = UvUtils.analyze_uv_budget(
            [cube],
            map_size=1024,
            density=32.0,
            read_textures=False,
            alternates=False,
        )
        for _ in range(3):
            cmds.instance(cube)
        many = UvUtils.analyze_uv_budget(
            cmds.ls("bud_inst*", type="transform"),
            map_size=1024,
            density=32.0,
            read_textures=False,
            alternates=False,
        )
        self.assertEqual(len(many.items), len(alone.items))
        self.assertAlmostEqual(many.items[0].area, alone.items[0].area, places=9)
        self.assertEqual(many.plan.chosen.pages, alone.plan.chosen.pages)


class TestAnalysis(MayaTkTestCase):
    """End-to-end behaviour of the public entry point."""

    def _scene(self):
        made = [
            projected(cmds.polyCube(w=4, h=4, d=4)[0]),
            projected(cmds.polySphere(r=2)[0]),
            projected(cmds.polyCylinder(r=1.5, h=5)[0]),
        ]
        for i, node in enumerate(made):
            cmds.move(i * 20, 0, 0, node)
        return made

    def test_analysis_does_not_touch_the_scene(self):
        meshes = self._scene()
        before = {m: cmds.polyEditUV(f"{m}.map[*]", query=True) for m in meshes}
        UvUtils.analyze_uv_budget(meshes, map_size=1024, density=40.0)
        for mesh in meshes:
            self.assertEqual(
                cmds.polyEditUV(f"{mesh}.map[*]", query=True), before[mesh]
            )

    def test_both_solve_directions_agree(self):
        meshes = self._scene()
        fwd = UvUtils.analyze_uv_budget(
            meshes,
            map_size=2048,
            density=32.0,
            read_textures=False,
            alternates=False,
        )
        self.assertTrue(fwd, fwd.plan.chosen.note)
        inv = UvUtils.analyze_uv_budget(
            meshes,
            map_size=2048,
            pages=fwd.plan.chosen.pages,
            density_from="min",
            read_textures=False,
            alternates=False,
        )
        self.assertGreaterEqual(inv.plan.chosen.density, 32.0 - 1e-6)

    def test_group_by_material_keeps_sets_whole(self):
        meshes = self._scene()
        shader = cmds.shadingNode("lambert", asShader=True, name="bud_shared")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(meshes, edit=True, forceElement=sg)
        by_mesh = UvUtils.analyze_uv_budget(
            meshes,
            map_size=1024,
            density=20.0,
            read_textures=False,
            alternates=False,
        )
        by_mat = UvUtils.analyze_uv_budget(
            meshes,
            map_size=1024,
            density=20.0,
            group_by="material",
            read_textures=False,
            alternates=False,
        )
        self.assertEqual(len(by_mesh.items), 3)
        self.assertEqual(len(by_mat.items), 1)

    def test_mip_levels_widen_the_gutter(self):
        meshes = self._scene()
        plain = UvUtils.analyze_uv_budget(
            meshes,
            map_size=1024,
            density=40.0,
            read_textures=False,
            alternates=False,
        )
        mipped = UvUtils.analyze_uv_budget(
            meshes,
            map_size=1024,
            density=40.0,
            mip_levels=6,
            read_textures=False,
            alternates=False,
        )
        self.assertEqual(plain.plan.chosen.padding, 4.0)
        self.assertEqual(mipped.plan.chosen.padding, 64.0)

    def test_assumed_map_size_is_reported_not_hidden(self):
        """A lambert with no textures cannot supply a size; the result says so."""
        meshes = self._scene()
        result = UvUtils.analyze_uv_budget(
            meshes, map_size=2048, density=20.0, alternates=False
        )
        self.assertEqual(result.measured_sets, 0)
        self.assertTrue(any("assumed" in w for w in result.warnings))
        for info in result.sets:
            self.assertFalse(info.map_size_measured)
            self.assertEqual(info.map_size, 2048)

    def test_stacked_owner_is_stable_across_runs(self):
        """Stacked duplicates have EQUAL area, so the tie-break must not drift.

        Which mesh owns a shared chart decides which budget item carries it,
        and a tie broken by float summation order would move it between runs.
        """
        made = []
        for i in range(4):
            plane = cmds.polyPlane(w=2, h=2, sx=2, sy=2, name=f"bud_tie{i}")[0]
            cmds.move(i * 5, 0, 0, plane)
            made.append(plane)
        shader = cmds.shadingNode("lambert", asShader=True, name="bud_tie_mat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(made, edit=True, forceElement=sg)

        runs = [
            UvUtils.analyze_uv_budget(
                order,
                map_size=1024,
                density=8.0,
                read_textures=False,
                alternates=False,
            )
            for order in (made, list(reversed(made)))
        ]
        # Four identical planes stack onto one chart, so exactly one item.
        for result in runs:
            self.assertEqual(len(result.items), 1)
        self.assertEqual(runs[0].items[0].key, runs[1].items[0].key)
        self.assertAlmostEqual(runs[0].sets[0].stack_factor, 4.0, places=6)

    def test_empty_scope_reports_instead_of_planning(self):
        result = UvUtils.analyze_uv_budget([], map_size=1024, density=20.0)
        self.assertFalse(result)
        self.assertIsNone(result.plan)
        self.assertIn("no meshes in scope", result.warnings)

    def test_report_is_renderable(self):
        result = UvUtils.analyze_uv_budget(
            self._scene(), map_size=1024, density=30.0, read_textures=False
        )
        text = result.report()
        self.assertIn("PLAN", text)
        self.assertIn("px/unit", text)

    def test_density_and_scale_are_separate_currencies(self):
        """The same number must not mean texels/unit and a multiplier at once."""
        meshes = self._scene()
        absolute = UvUtils.analyze_uv_budget(
            meshes,
            map_size=4096,
            density=1.0,
            read_textures=False,
            alternates=False,
        )
        relative = UvUtils.analyze_uv_budget(
            meshes,
            map_size=4096,
            scale=0.1,
            read_textures=False,
            alternates=False,
        )
        self.assertFalse(absolute.density_is_scale)
        self.assertTrue(relative.density_is_scale)
        self.assertTrue(absolute, absolute.plan.chosen.note)
        self.assertTrue(relative, relative.plan.chosen.note)
        # 1 texel/unit against a tenth of the scene's authored ~450. At a
        # density of 1 the fixed per-chart gutter cost is nearly the whole
        # bill, which is why the gap is ~20x rather than the ~2000x the area
        # term alone would give -- but the two readings are still worlds apart.
        flat = sum(p.demand for p in absolute.plan.chosen.page_list)
        kept = sum(p.demand for p in relative.plan.chosen.page_list)
        self.assertGreater(kept, flat * 10)

    def test_only_one_direction_may_be_given(self):
        with self.assertRaises(ValueError):
            UvUtils.analyze_uv_budget(
                self._scene(), density=32.0, pages=2, read_textures=False
            )
        with self.assertRaises(ValueError):
            UvUtils.analyze_uv_budget(
                self._scene(), density=32.0, scale=1.0, read_textures=False
            )

    def test_a_mesh_too_big_for_a_page_is_named(self):
        """The fix is that mesh, not another page -- so the note has to say which."""
        big = projected(cmds.polyPlane(w=200, h=200, name="bud_huge")[0])
        result = UvUtils.analyze_uv_budget(
            [big],
            map_size=512,
            density=400.0,
            read_textures=False,
            alternates=False,
        )
        self.assertFalse(result)
        self.assertFalse(result.plan.chosen.feasible)
        self.assertIn("bud_huge", result.plan.chosen.note)

    def test_pages_assignment_covers_every_item(self):
        result = UvUtils.analyze_uv_budget(
            self._scene(),
            map_size=2048,
            density=40.0,
            read_textures=False,
            alternates=False,
        )
        self.assertTrue(result, result.plan.chosen.note)
        placed = [k for _, keys in result.pages() for k in keys]
        self.assertEqual(sorted(placed), sorted(i.key for i in result.items))


if __name__ == "__main__":
    unittest.main()
