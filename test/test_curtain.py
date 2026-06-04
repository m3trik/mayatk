# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.edit_utils.curtain.

Separation of concerns mirrors the module: :class:`Rail` (rail geometry),
:class:`CurtainMesh` (the drape/deformation), and :class:`CurtainRig` (the
wire deformer + cluster rig) are exercised independently.
"""
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om

from base_test import MayaTkTestCase
from mayatk.edit_utils.curtain import (
    CurtainMesh,
    Rail,
    CurtainRig,
    catenary_shape,
    sag_profile,
    _PRESETS_DIR,
)


class CatenaryMathTest(MayaTkTestCase):
    """The gravity model rests on a true catenary profile."""

    def test_endpoints_and_center(self):
        self.assertAlmostEqual(catenary_shape(0.0, 1.5), 1.0, places=9)
        self.assertAlmostEqual(catenary_shape(1.0, 1.5), 0.0, places=9)
        self.assertAlmostEqual(catenary_shape(-1.0, 1.5), 0.0, places=9)

    def test_tension_zero_is_parabola(self):
        self.assertAlmostEqual(catenary_shape(0.5, 0.0), 0.75, places=9)

    def test_clamped_outside_span(self):
        self.assertAlmostEqual(catenary_shape(2.0, 1.5), 0.0, places=9)

    def test_peak_is_one_for_any_tension(self):
        # Depth is owned by gravity; tension only reshapes, so the center peaks
        # at 1 regardless of tension.
        for tens in (0.1, 1.5, 5.0):
            self.assertAlmostEqual(catenary_shape(0.0, tens), 1.0, places=9)

    def test_higher_tension_deepens_midspan(self):
        # Higher tension holds the curve fuller across the middle (flat top,
        # steep only near the supports) -> a larger normalized value mid-span,
        # i.e. a deeper, heavier-looking drape. The two converge to 0 only at
        # the supports.
        shallow = catenary_shape(0.5, 0.5)
        deep = catenary_shape(0.5, 4.0)
        self.assertGreater(deep, shallow)
        self.assertAlmostEqual(catenary_shape(1.0, 0.5), catenary_shape(1.0, 4.0), places=9)


class SagProfileTest(MayaTkTestCase):
    """Rounding the hanging points blends the catenary toward sin²."""

    def test_no_round_matches_catenary(self):
        for t in (-1.0, -0.5, 0.0, 0.3, 1.0):
            self.assertAlmostEqual(
                sag_profile(t, 1.5, 0.0), catenary_shape(t, 1.5), places=9
            )

    def test_endpoints_zero_center_one(self):
        self.assertAlmostEqual(sag_profile(0.0, 1.5, 1.0), 1.0, places=9)
        self.assertAlmostEqual(sag_profile(-1.0, 1.5, 1.0), 0.0, places=9)
        self.assertAlmostEqual(sag_profile(1.0, 1.5, 1.0), 0.0, places=9)

    def test_rounding_flattens_endpoint_slope(self):
        # Near the support the rounded profile rises quadratically (zero slope)
        # while the crisp catenary rises ~linearly, so it sits lower there.
        eps = 1e-3
        cat = catenary_shape(-1.0 + eps, 3.0)
        rnd = sag_profile(-1.0 + eps, 3.0, 1.0)
        self.assertLess(rnd, cat)


class MakeRailTest(MayaTkTestCase):
    def test_default_rail_is_straight(self):
        pts, closed = Rail.make()
        self.assertFalse(closed)
        self.assertTrue(all(abs(p[2]) < 1e-9 for p in pts), "default rail must be flat (z=0)")
        self.assertTrue(all(abs(p[1]) < 1e-9 for p in pts), "default rail must be level (y=0)")
        xs = [p[0] for p in pts]
        self.assertAlmostEqual(max(xs) - min(xs), 6.0, places=6)

    def test_width_scales_the_rail(self):
        pts, _ = Rail.make(width=12.0)
        xs = [p[0] for p in pts]
        self.assertAlmostEqual(max(xs) - min(xs), 12.0, places=6)

    def test_curvature_bows_the_rail(self):
        pts, _ = Rail.make(curvature=0.5)
        self.assertGreater(max(p[2] for p in pts), 0.1, "curvature should bow the rail in +Z")

    def test_negative_curvature_bows_back(self):
        pts, _ = Rail.make(curvature=-0.5)
        self.assertLess(min(p[2] for p in pts), -0.1, "negative curvature should bow the rail in -Z")

    def test_resample_hits_requested_count(self):
        rail, _ = Rail.make(width=6.0)
        self.assertEqual(len(Rail.resample(rail, 8)), 8)


class CurtainBuildTest(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.rail, self.closed = Rail.make()

    def test_builds_mesh_with_geometry(self):
        t = CurtainMesh(self.rail, hanging_points=8).build()
        self.assertNodeExists(t)
        self.assertGreater(cmds.polyEvaluate(t, vertex=True), 100)

    def test_drop_matches_height_without_gravity(self):
        t = CurtainMesh(self.rail, height=3.0, gravity=0.0, irregularity=0.0).build()
        bb = cmds.exactWorldBoundingBox(t)
        self.assertAlmostEqual(bb[4] - bb[1], 3.0, delta=0.15)

    def test_gravity_deepens_the_sag(self):
        flat = CurtainMesh(self.rail, gravity=0.0, irregularity=0.0).build()
        slack = CurtainMesh(self.rail, gravity=1.0, irregularity=0.0).build()
        # more gravity -> the fabric falls lower between hang points
        self.assertLess(cmds.exactWorldBoundingBox(slack)[1],
                        cmds.exactWorldBoundingBox(flat)[1] - 0.05)

    def test_fewer_points_sag_more(self):
        # Same gravity: wider spans (fewer points) sag further.
        many = CurtainMesh(self.rail, hanging_points=12, gravity=0.5, irregularity=0.0).build()
        few = CurtainMesh(self.rail, hanging_points=3, gravity=0.5, irregularity=0.0).build()
        self.assertLess(cmds.exactWorldBoundingBox(few)[1],
                        cmds.exactWorldBoundingBox(many)[1])

    def test_pleats_create_depth(self):
        flat = CurtainMesh(self.rail, fullness=1.0, irregularity=0.0).build()
        folded = CurtainMesh(self.rail, fullness=4.0, irregularity=0.0).build()
        flat_bb = cmds.exactWorldBoundingBox(flat)
        fold_bb = cmds.exactWorldBoundingBox(folded)
        self.assertGreater(fold_bb[5] - fold_bb[2], flat_bb[5] - flat_bb[2])

    def test_thickness_shells(self):
        t = CurtainMesh(self.rail, thickness=0.05).build()
        self.assertNodeExists(t)
        self.assertGreater(cmds.polyEvaluate(t, face=True), 0)

    def test_reduce_decimates(self):
        full = CurtainMesh(self.rail, density=10).build()
        reduced = CurtainMesh(self.rail, density=10, reduce=50.0).build()
        self.assertLess(
            cmds.polyEvaluate(reduced, face=True), cmds.polyEvaluate(full, face=True)
        )

    @staticmethod
    def _z_range(transform):
        bb = cmds.exactWorldBoundingBox(transform)
        return bb[5] - bb[2]

    def test_mid_folds_add_relief(self):
        # On a flat panel (no belly/gravity/noise) the only Z relief comes from
        # the V-creases, so turning them on must push the surface out of plane.
        flat = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0
        ).build()
        folded = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
            mid_folds=2.0, fold_seed=1,
        ).build()
        self.assertLess(self._z_range(flat), 1e-3)
        self.assertGreater(self._z_range(folded), 0.02)

    def test_mid_folds_seed_changes_pattern(self):
        a = CurtainMesh(self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        mid_folds=2.0, fold_seed=1).build()
        b = CurtainMesh(self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        mid_folds=2.0, fold_seed=2).build()
        # Different seeds -> different crease placement -> different relief.
        self.assertNotAlmostEqual(self._z_range(a), self._z_range(b), places=4)

    def test_end_bend_displaces_ends(self):
        flat = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0
        ).build()
        bent = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
            end_bend_left=1.0, end_bend_right=-1.0, end_bend_falloff=0.3,
        ).build()
        self.assertLess(self._z_range(flat), 1e-3)
        self.assertGreater(self._z_range(bent), 0.5)

    def test_round_points_builds(self):
        t = CurtainMesh(self.rail, round_points=1.0, irregularity=0.0).build()
        self.assertNodeExists(t)
        self.assertGreater(cmds.polyEvaluate(t, vertex=True), 100)

    def test_invert_reverses_normals(self):
        plain = CurtainMesh(self.rail, irregularity=0.0, soften=False).build()
        flipped = CurtainMesh(self.rail, irregularity=0.0, soften=False, invert=True).build()
        n0 = self._face0_normal(plain)
        n1 = self._face0_normal(flipped)
        dot = n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2]
        self.assertLess(dot, 0.0, "inverted curtain's face normal should point the other way")

    @staticmethod
    def _face0_normal(transform):
        sel = om.MSelectionList()
        sel.add(transform)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        nrm = om.MFnMesh(dag).getPolygonNormal(0, om.MSpace.kWorld)
        return (nrm.x, nrm.y, nrm.z)

    def test_closed_ring(self):
        pts, closed = Rail.make(closed=True)
        t = CurtainMesh(pts, closed=closed, hanging_points=12).build()
        self.assertNodeExists(t)

    def test_requires_two_points(self):
        with self.assertRaises(ValueError):
            CurtainMesh([(0, 0, 0)])

    def test_density_controls_resolution(self):
        lo = CurtainMesh(self.rail, density=2.0, hanging_points=4).build()
        hi = CurtainMesh(self.rail, density=16.0, hanging_points=4).build()
        self.assertGreater(
            cmds.polyEvaluate(hi, vertex=True), cmds.polyEvaluate(lo, vertex=True)
        )


class RailResolutionTest(MayaTkTestCase):
    def test_curve(self):
        crv = cmds.curve(point=[(0, 5, 0), (2, 5, 1), (4, 5, 0), (6, 5, 1)], degree=3)
        rail = Rail.from_selection([crv])
        self.assertIsNotNone(rail)
        self.assertGreaterEqual(len(rail[0]), 2)
        t = CurtainMesh(rail[0], closed=rail[1]).build()
        self.assertLess(cmds.exactWorldBoundingBox(t)[1], 5.0)

    def test_locators(self):
        locs = []
        for i in range(3):
            loc = cmds.spaceLocator()[0]
            cmds.xform(loc, ws=True, t=(i * 2.0, 4.0, 0.0))
            locs.append(loc)
        rail = Rail.from_selection(locs)
        self.assertIsNotNone(rail)
        self.assertEqual(len(rail[0]), 3)

    def test_edges(self):
        plane = cmds.polyPlane(width=4, height=1, subdivisionsWidth=4, subdivisionsHeight=1)[0]
        rail = Rail.from_selection([f"{plane}.e[0:4]"])
        self.assertIsNotNone(rail)
        self.assertGreaterEqual(len(rail[0]), 2)

    def test_empty_selection_returns_none(self):
        self.assertIsNone(Rail.from_selection([]))


class RigTest(MayaTkTestCase):
    """The rail should drive the curtain via a wire deformer + cluster controls."""

    def setUp(self):
        super().setUp()
        self.rail, _ = Rail.make()

    def test_attach_builds_wire_and_clusters(self):
        curtain = CurtainMesh(self.rail, hanging_points=6).build()
        crv = cmds.curve(point=self.rail, degree=3)
        grp = CurtainRig.attach(curtain, crv, dropoff=4.0, cluster=True)
        self.assertNodeExists(grp)
        self.assertTrue(cmds.ls(type="wire"), "a wire deformer should drive the curtain")
        self.assertTrue(cmds.ls(type="cluster"), "cluster controls should be created")

    def test_wire_driver_deforms_curtain(self):
        curtain = CurtainMesh(
            self.rail, hanging_points=6, gravity=0.0, irregularity=0.0
        ).build()
        crv = cmds.curve(point=self.rail, degree=3)
        before = cmds.exactWorldBoundingBox(curtain)[4]  # ymax
        CurtainRig.attach(curtain, crv, dropoff=10.0, cluster=False)
        cmds.move(0, 3, 0, f"{crv}.cv[1]", relative=True)
        after = cmds.exactWorldBoundingBox(curtain)[4]
        self.assertGreater(after, before + 0.1, "moving the rail should lift the curtain")


class PresetTest(MayaTkTestCase):
    """Built-in presets ship as JSON and load through the shared PresetStore."""

    def test_builtin_presets_load(self):
        import pythontk as ptk

        self.assertTrue(Path(_PRESETS_DIR).is_dir(), "presets dir must exist")
        store = ptk.PresetStore("curtain", package="mayatk", builtin_dir=str(_PRESETS_DIR))
        names = store.list(tier="builtin")
        self.assertEqual(
            set(names),
            {"Flat Backdrop", "Stage Swag", "Shower Curtain", "Booth Ring"},
        )
        # Each preset is a widget-state dict the panel can apply.
        for n in names:
            data = store.load(n)
            self.assertIn("s003", data)  # hanging points
            self.assertIn("s004", data)  # gravity


if __name__ == "__main__":
    import unittest

    unittest.main()
