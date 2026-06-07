# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.edit_utils.curtain.

Separation of concerns mirrors the module: :class:`Rail` (rail geometry),
:class:`CurtainMesh` (the drape/deformation), and :class:`CurtainRig` (the
wire deformer + cluster rig) are exercised independently.
"""
import statistics
import types
import unittest
from pathlib import Path

import maya.cmds as cmds
import maya.api.OpenMaya as om

try:
    from qtpy import QtWidgets
except ImportError:
    QtWidgets = None

from base_test import MayaTkTestCase
from mayatk.edit_utils.curtain import (
    CurtainMesh,
    Rail,
    CurtainRig,
    CurtainSlots,
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

    def test_center_offsets_straight_rail(self):
        pts, _ = Rail.make(width=4.0, center=(5.0, 2.0, 3.0))
        xs = [p[0] for p in pts]
        self.assertAlmostEqual((min(xs) + max(xs)) * 0.5, 5.0, places=6)  # x-centered
        self.assertTrue(all(abs(p[1] - 2.0) < 1e-9 for p in pts), "y = center y")
        self.assertAlmostEqual(min(p[2] for p in pts), 3.0, places=6)  # flat at center z

    def test_center_offsets_closed_ring(self):
        pts, _ = Rail.make(width=4.0, closed=True, center=(5.0, 2.0, 3.0))
        xs = [p[0] for p in pts]
        zs = [p[2] for p in pts]
        self.assertAlmostEqual((min(xs) + max(xs)) * 0.5, 5.0, places=6)
        self.assertAlmostEqual((min(zs) + max(zs)) * 0.5, 3.0, places=6)
        self.assertTrue(all(abs(p[1] - 2.0) < 1e-9 for p in pts), "ring level at center y")


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

    def test_creases_add_relief(self):
        # On a flat panel (no belly/gravity/noise) the only Z relief comes from
        # the V-creases, so turning them on must push the surface out of plane.
        flat = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0
        ).build()
        folded = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
            creases=2.0, crease_seed=1,
        ).build()
        self.assertLess(self._z_range(flat), 1e-3)
        self.assertGreater(self._z_range(folded), 0.02)

    def test_creases_seed_changes_pattern(self):
        a = CurtainMesh(self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        creases=2.0, crease_seed=1).build()
        b = CurtainMesh(self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        creases=2.0, crease_seed=2).build()
        # Different seeds -> different crease placement -> different relief.
        self.assertNotAlmostEqual(self._z_range(a), self._z_range(b), places=4)

    def test_midfolds_add_relief(self):
        # On a flat panel the only Z relief comes from the mid-fold forks, so
        # turning Mid Folds on must push the surface out of plane.
        flat = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0
        ).build()
        forked = CurtainMesh(
            self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
            mid_folds=3.0, mid_fold_seed=1,
        ).build()
        self.assertLess(self._z_range(flat), 1e-3)
        self.assertGreater(self._z_range(forked), 0.02)

    def test_midfolds_seed_changes_pattern(self):
        a = CurtainMesh(self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        mid_folds=3.0, mid_fold_seed=1).build()
        b = CurtainMesh(self.rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        mid_folds=3.0, mid_fold_seed=2).build()
        # Different seeds -> different hang points fork -> different relief.
        self.assertNotAlmostEqual(self._z_range(a), self._z_range(b), places=4)

    def test_midfolds_anchor_at_subset_of_hang_points(self):
        # The point of Mid Folds: fork only ~1/4-1/2 of the interior hang points,
        # each apex landing exactly on a hang point. No geometry needed.
        m = CurtainMesh(self.rail, hanging_points=20, mid_folds=1.0, mid_fold_seed=3)
        folds = m._make_midfolds()
        interior = m.spans - 1  # interior hang points on an open rail
        self.assertGreaterEqual(len(folds), 1)
        self.assertLess(len(folds), interior, "some hang points must stay unforked")
        self.assertLessEqual(len(folds), interior // 2 + 1, "only ~1/4-1/2 fork")
        for u0, *_ in folds:
            # u0 must sit on a hang point: u0 * spans is an integer.
            self.assertAlmostEqual(u0 * m.spans, round(u0 * m.spans), places=9)

    def test_midfolds_off_leaves_no_forks(self):
        m = CurtainMesh(self.rail, hanging_points=20, mid_folds=0.0)
        self.assertEqual(m._make_midfolds(), [])

    def test_midfolds_pin_top_edge(self):
        # The forks ramp in just below the rail, so the pinned rail row itself
        # (v = 1, the hook line) is untouched no matter how strong the folds are.
        m = CurtainMesh(self.rail, hanging_points=12, mid_folds=3.0, mid_fold_seed=7)
        m._midfolds = m._make_midfolds()
        self.assertTrue(m._midfolds, "expected some forks for this seed")
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            self.assertAlmostEqual(m._midfold_offset(u, 1.0), 0.0, places=9)

    def test_midfolds_run_to_near_the_top(self):
        # The small top fade means a fork reaches near-full strength just below
        # the rail (it "runs to the top"), not faded to a sliver as before.
        m = CurtainMesh(self.rail, hanging_points=12, mid_folds=3.0, mid_fold_seed=7)
        m._midfolds = m._make_midfolds()
        u0, length, *_ = max(m._midfolds, key=lambda f: f[1])  # the longest fork
        near_top = abs(m._midfold_offset(u0, 0.96))             # depth 0.04
        mid = abs(m._midfold_offset(u0, 1.0 - min(0.3, length * 0.5)))
        self.assertGreater(mid, 1e-4, "expected relief inside the fork")
        self.assertGreater(near_top, 0.7 * mid, "fork should run nearly to the top")

    def test_midfolds_push_both_ways(self):
        # Material conservation: each ricker crease pushes the cloth out at its
        # line and pulls it in to the sides, so a horizontal scan crosses both
        # signs -- not a one-sided bulge (which is what the old gaussian gave).
        m = CurtainMesh(self.rail, hanging_points=12, mid_folds=3.0, mid_fold_seed=7)
        m._midfolds = m._make_midfolds()
        self.assertTrue(m._midfolds, "expected some forks for this seed")
        vals = [m._midfold_offset(i / 400.0, 0.75) for i in range(401)]
        self.assertGreater(max(vals), 1e-4, "folds should push the cloth out")
        self.assertLess(min(vals), -1e-4, "folds should also pull the cloth in")

    def test_irregularity_is_coherent_not_white_noise(self):
        # The grain must be band-limited/coherent: a tiny step in u changes the
        # value only a little (per-vertex white noise jumped arbitrarily), while
        # still actually varying across the surface.
        m = CurtainMesh(self.rail, irregularity=1.0)
        m._billow = m._make_billow()
        self.assertTrue(m._billow, "billow populated when irregularity > 0")
        vals = [m._billow_offset(i / 500.0, 0.5) for i in range(501)]
        steps = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
        self.assertGreater(max(vals) - min(vals), 0.05, "grain must vary")
        self.assertLess(max(steps), 0.1, "neighbouring samples must stay close")

    def test_billow_wraps_seamlessly_when_closed(self):
        # On a ring the grain must match across the u=0/u=1 seam (whole-cycle
        # frequencies), else the closed curtain shows a vertical crack.
        pts, closed = Rail.make(closed=True)
        m = CurtainMesh(pts, closed=closed, irregularity=1.0)
        m._billow = m._make_billow()
        for v in (0.0, 0.5, 1.0):
            self.assertAlmostEqual(
                m._billow_offset(0.0, v), m._billow_offset(1.0, v), places=6
            )

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

    # ---------------------------------------------------------------- sway
    def test_sway_off_is_empty(self):
        self.assertEqual(CurtainMesh(self.rail, sway=0.0)._make_sway(), [])

    def test_sway_leans_random_subset(self):
        # Only ~half the spans lean (the rest sit at 0), each a signed factor
        # in [0.4, 1.0] — "certain areas" drift, not a uniform shear.
        m = CurtainMesh(self.rail, hanging_points=20, sway=1.0, sway_seed=3)
        leans = m._make_sway()
        self.assertEqual(len(leans), m.spans)
        nonzero = [x for x in leans if x != 0.0]
        self.assertGreaterEqual(len(nonzero), 1)
        self.assertLess(len(nonzero), m.spans, "some spans must stay un-swayed")
        for x in nonzero:
            self.assertTrue(0.4 <= abs(x) <= 1.0, f"lean magnitude out of range: {x}")

    def test_sway_seed_changes_pattern(self):
        a = CurtainMesh(self.rail, hanging_points=20, sway=1.0, sway_seed=1)._make_sway()
        b = CurtainMesh(self.rail, hanging_points=20, sway=1.0, sway_seed=2)._make_sway()
        self.assertNotEqual(a, b)

    def test_sway_pinned_at_hang_points(self):
        # The lateral lean rides |sin(pi*phase)|, which is zero at every hang
        # point, so adjacent (oppositely-leaning) spans can't tear at the pin.
        m = CurtainMesh(self.rail, hanging_points=12, sway=3.0, sway_seed=5)
        m._sway = m._make_sway()
        for k in range(m.spans + 1):
            for vv in (0.0, 0.5, 1.0):
                self.assertAlmostEqual(m._sway_offset(k / m.spans, vv), 0.0, places=9)

    def test_sway_displaces_along_the_rail(self):
        # Straight rail along X: sway leans folds along X (the tangent), not in/
        # out (Z). Compare per-vertex (same topology) against a no-sway build.
        rail = [(x, 0.0, 0.0) for x in (0.0, 2.5, 5.0, 7.5, 10.0)]
        base = self._verts(
            CurtainMesh(rail, fullness=1.0, gravity=0.0, irregularity=0.0).build()
        )
        swayed = self._verts(
            CurtainMesh(rail, fullness=1.0, gravity=0.0, irregularity=0.0,
                        sway=3.0, sway_seed=1).build()
        )
        self.assertEqual(len(base), len(swayed))
        max_dx = max(abs(a.x - b.x) for a, b in zip(base, swayed))
        max_dz = max(abs(a.z - b.z) for a, b in zip(base, swayed))
        self.assertGreater(max_dx, 0.02, "sway shifts vertices sideways along the rail")
        self.assertAlmostEqual(
            max_dz, 0.0, places=6, msg="sway must not change in/out depth"
        )

    @staticmethod
    def _verts(transform):
        sel = om.MSelectionList()
        sel.add(transform)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        return om.MFnMesh(dag).getPoints(om.MSpace.kWorld)

    def test_hang_jitter_off_is_uniform(self):
        m = CurtainMesh(self.rail, hanging_points=9)  # spans = 8
        self.assertEqual(len(m._hang_points), m.spans + 1)
        for i, u in enumerate(m._hang_points):
            self.assertAlmostEqual(u, i / m.spans, places=9)

    def test_hang_jitter_perturbs_interior_but_pins_the_ends(self):
        m = CurtainMesh(self.rail, hanging_points=12, hang_jitter=1.0, hang_seed=4)
        hp = m._hang_points
        self.assertAlmostEqual(hp[0], 0.0, places=9)   # outer ends pinned
        self.assertAlmostEqual(hp[-1], 1.0, places=9)
        # strictly increasing -> no crossed / zero-width spans at any jitter
        self.assertTrue(all(b > a for a, b in zip(hp, hp[1:])))
        moved = any(abs(hp[i] - i / m.spans) > 1e-3 for i in range(1, m.spans))
        self.assertTrue(moved, "hang_jitter must perturb the interior spacing")

    def test_hang_jitter_seed_changes_pattern(self):
        a = CurtainMesh(self.rail, hanging_points=12, hang_jitter=1.0, hang_seed=1)
        b = CurtainMesh(self.rail, hanging_points=12, hang_jitter=1.0, hang_seed=2)
        self.assertNotEqual(a._hang_points, b._hang_points)

    def test_hang_jitter_changes_the_drape(self):
        # Same topology, jittered spacing -> the cloth gathers/sags at different
        # places, so the vertices move.
        opts = dict(hanging_points=10, fullness=2.5, irregularity=0.0)
        base = self._verts(CurtainMesh(self.rail, **opts).build())
        jit = self._verts(
            CurtainMesh(self.rail, hang_jitter=1.0, hang_seed=7, **opts).build()
        )
        self.assertEqual(len(base), len(jit))
        maxd = max(
            max(abs(a.x - b.x), abs(a.y - b.y), abs(a.z - b.z))
            for a, b in zip(base, jit)
        )
        self.assertGreater(maxd, 0.02, "jittered hang spacing should shift the drape")

    def test_span_at_uniform_matches_phase(self):
        # With jitter off, _span_at reproduces the old uniform phase (k + t ==
        # u * spans), so the belly / sway half-sines are unchanged.
        m = CurtainMesh(self.rail, hanging_points=9)  # spans = 8
        for u in (0.0, 0.1, 0.37, 0.5, 0.99, 1.0):
            k, t = m._span_at(u)
            self.assertAlmostEqual(k + t, u * m.spans, places=9)

    def test_round_points_builds(self):
        t = CurtainMesh(self.rail, round_points=1.0, irregularity=0.0).build()
        self.assertNodeExists(t)
        self.assertGreater(cmds.polyEvaluate(t, vertex=True), 100)

    def test_gather_puckers_up_at_pins_and_dips_inside(self):
        # Push-pull gather: vs a no-gather build (same topology), the fabric
        # lifts UP near the hang points (push) and sags lower just inside them
        # (pull), while leaving in/out (x, z) untouched.
        rail = [(x, 0.0, 0.0) for x in (0.0, 2.5, 5.0, 7.5, 10.0)]
        opts = dict(hanging_points=6, gravity=0.5, fullness=1.0, irregularity=0.0)
        base = self._verts(CurtainMesh(rail, **opts).build())
        gathered = self._verts(CurtainMesh(rail, round_gather=1.0, **opts).build())
        self.assertEqual(len(base), len(gathered))
        dys = [g.y - b.y for g, b in zip(base, gathered)]
        self.assertGreater(max(dys), 0.02, "gather lifts the fabric up at the pins")
        self.assertLess(min(dys), -0.01, "gather dips the fabric lower just inside")
        max_dxz = max(max(abs(g.x - b.x), abs(g.z - b.z)) for g, b in zip(base, gathered))
        self.assertAlmostEqual(
            max_dxz, 0.0, places=6, msg="gather only changes vertical sag"
        )

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


class TestFoldsPerPleat(MayaTkTestCase):
    """Hanging Points map ~1:1 to folds.

    The catenary sag + push-pull gather fire once per hang point (one clean
    pleat/cusp at the rail), while the belly runs ``_BELLY_HUMPS_PER_SPAN``
    humps (one full fold) per pleat-span — so the body fold density is decoupled
    from (and double) the top cusp frequency, and the sag depth is normalized so
    halving the dial reproduces the old depth instead of ballooning.
    """

    def setUp(self):
        super().setUp()
        # Straight rail along X (length 6, y = 0): the drape rides the in-plane
        # normal (-Z) and the rail tangent (X), so every vertex in a column
        # shares one X — letting us read the top edge and belly per column.
        self.rail, self.closed = Rail.make()

    @staticmethod
    def _columns(transform):
        """Bucket draped verts into rail columns sorted by X.

        Returns ``(bellies, top_ys)``: per column the in/out belly (``-z``, taper
        is 0 in these tests so it's constant down the column) and the rail-row
        height (``max y`` = ``-sag`` at that point, since lower rows drop by the
        height term).
        """
        sel = om.MSelectionList()
        sel.add(transform)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        pts = om.MFnMesh(dag).getPoints(om.MSpace.kWorld)
        cols = {}
        for p in pts:
            cols.setdefault(round(p.x, 4), []).append((p.y, p.z))
        xs = sorted(cols)
        bellies = [-statistics.median([z for _, z in cols[x]]) for x in xs]
        top_ys = [max(y for y, _ in cols[x]) for x in xs]
        return bellies, top_ys

    @staticmethod
    def _count_peaks(vals):
        """Local maxima (endpoints count when above their one neighbor)."""
        n = len(vals)
        return sum(
            1
            for i in range(n)
            if (i == 0 or vals[i] > vals[i - 1])
            and (i == n - 1 or vals[i] > vals[i + 1])
        )

    @staticmethod
    def _count_positive_runs(vals, tol=0.01):
        """Number of contiguous runs above ``tol`` (one per out-ridge)."""
        runs, inside = 0, False
        for v in vals:
            if v > tol and not inside:
                runs, inside = runs + 1, True
            elif v <= tol:
                inside = False
        return runs

    def test_pinch_count_equals_hanging_points(self):
        # The catenary returns the rail row to its peak height (sag -> 0) once
        # per hang point: one clean cusp/pleat at the rail, not two per fold.
        hp = 6
        t = CurtainMesh(
            self.rail, hanging_points=hp, gravity=0.5, fullness=4.0, taper=0.0,
            round_points=0.0, irregularity=0.0, density=16.0,
        ).build()
        _, top_ys = self._columns(t)
        self.assertEqual(self._count_peaks(top_ys), hp)

    def test_fold_density_is_doubled_vs_pinches(self):
        # The belly runs _BELLY_HUMPS_PER_SPAN (2) humps = one full fold per
        # span, so out-ridges number ~ spans (= hanging_points - 1) -- DOUBLE the
        # old half-hump-per-span (which gave ceil(spans/2) = 3 here).
        hp = 6
        t = CurtainMesh(
            self.rail, hanging_points=hp, gravity=0.0, fullness=4.0, taper=0.0,
            irregularity=0.0, density=16.0,
        ).build()
        bellies, _ = self._columns(t)
        self.assertEqual(CurtainMesh._BELLY_HUMPS_PER_SPAN, 2)
        self.assertEqual(self._count_positive_runs(bellies), hp - 1)

    def test_sag_depth_normalized_to_per_fold_width(self):
        # Halving the dial doubles each span's width; normalizing the sag by
        # _BELLY_HUMPS_PER_SPAN keeps the depth at the per-hump scale (the
        # previous look). Deepest dip == gravity * (L / spans) / HUMPS, NOT the
        # un-normalized gravity * (L / spans) (which would be twice as deep).
        hp, gravity = 6, 0.5
        t = CurtainMesh(
            self.rail, hanging_points=hp, gravity=gravity, fullness=1.0,
            taper=0.0, round_points=0.0, irregularity=0.0, density=24.0,
        ).build()
        _, top_ys = self._columns(t)
        length = Rail.length(self.rail, self.closed)
        spans = hp - 1
        expected = gravity * (length / spans) / CurtainMesh._BELLY_HUMPS_PER_SPAN
        self.assertAlmostEqual(min(top_ys), -expected, delta=0.02)


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
            {"Stage Swag", "Shower Curtain"},
        )
        # Each preset is a widget-state dict the panel can apply.
        for n in names:
            data = store.load(n)
            self.assertIn("s003", data)  # hanging points
            self.assertIn("s004", data)  # gravity


class FooterTest(MayaTkTestCase):
    """The footer reports the result's triangle count, and clears when empty."""

    class _Footer:
        def __init__(self):
            self._t = ""

        def setStatusText(self, t):
            self._t = t

    def test_update_footer_reports_tris(self):
        curtain = cmds.polyPlane(name="curt_footer", subdivisionsX=4, subdivisionsY=4)[0]
        tris = cmds.polyEvaluate(curtain, triangle=True)
        fake = types.SimpleNamespace(
            ui=types.SimpleNamespace(footer=self._Footer()), last_curtain=curtain
        )
        CurtainSlots._update_footer(fake)
        self.assertEqual(fake.ui.footer._t, f"{tris:,} tris")

        # No result -> cleared (falls back to the default hint).
        fake.last_curtain = None
        CurtainSlots._update_footer(fake)
        self.assertEqual(fake.ui.footer._t, "")


if __name__ == "__main__":
    unittest.main()
