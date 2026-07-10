# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.rig_utils.skinning (SkinUtils + CurveWeights).

Run with mayapy:
    & $MAYAPY mayatk\\test\\run_tests.py skinning
"""
import math
import os
import shutil

import maya.cmds as cmds

from base_test import MayaTkTestCase

from mayatk.rig_utils.skinning import CurveWeights, SkinUtils
from mayatk.nurbs_utils._nurbs_utils import NurbsUtils


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_cylinder(name="skinTestTube", sx=12, sy=10, h=10.0, r=1.0):
    """A frozen polyCylinder along +X spanning x in [-h/2, h/2]."""
    obj = cmds.polyCylinder(name=name, height=h, sx=sx, sy=sy, r=r, axis=(1, 0, 0))[0]
    cmds.makeIdentity(obj, apply=True, t=True, r=True, s=True)
    return obj


def _make_chain(positions, prefix="skinTestJnt"):
    """A parented joint chain through *positions*."""
    cmds.select(clear=True)
    joints = []
    for i, p in enumerate(positions):
        joints.append(cmds.joint(p=p, name=f"{prefix}{i + 1}"))
    return joints


def _make_loose_joints(positions, prefix="looseJnt"):
    """Unparented joints at *positions*."""
    joints = []
    for i, p in enumerate(positions):
        cmds.select(clear=True)
        joints.append(cmds.joint(p=p, name=f"{prefix}{i + 1}"))
    return joints


def _vertex_positions(mesh):
    flat = cmds.xform(f"{mesh}.vtx[*]", q=True, ws=True, t=True) or []
    return [(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]) for i in range(len(flat) // 3)]


def _rings_by_x(mesh, decimals=3):
    """Group vertex indices into cross-section rings keyed by rounded x."""
    rings = {}
    for i, (x, _, _) in enumerate(_vertex_positions(mesh)):
        rings.setdefault(round(x, decimals), []).append(i)
    return rings


def _row(weights, n_inf, vertex):
    return weights[vertex * n_inf : (vertex + 1) * n_inf]


# ----------------------------------------------------------------------
# Binding
# ----------------------------------------------------------------------


class TestSkinBind(MayaTkTestCase):
    def _bound_cylinder(self, **bind_kwargs):
        tube = _make_cylinder()
        joints = _make_chain([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind(tube, joints, **bind_kwargs)
        return tube, joints, sc

    def test_get_skin_cluster(self):
        tube = _make_cylinder()
        self.assertIsNone(SkinUtils.get_skin_cluster(tube))
        joints = _make_chain([(-5, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind(tube, joints)
        self.assertEqual(SkinUtils.get_skin_cluster(tube), sc)
        # Resolves from the shape as well as the transform.
        shape = cmds.listRelatives(tube, shapes=True)[0]
        self.assertEqual(SkinUtils.get_skin_cluster(shape), sc)

    def test_bind_maps_skinning_method(self):
        for method, expected in (("classic", 0), ("dqs", 1), ("blended", 2)):
            cmds.file(new=True, force=True)
            _, _, sc = self._bound_cylinder(skinning_method=method)
            self.assertEqual(cmds.getAttr(f"{sc}.skinningMethod"), expected)

    def test_bind_maps_max_influences(self):
        _, _, sc = self._bound_cylinder(max_influences=3)
        self.assertEqual(cmds.getAttr(f"{sc}.maxInfluences"), 3)
        self.assertTrue(cmds.getAttr(f"{sc}.maintainMaxInfluences"))

    def test_bind_method_names(self):
        # heatmap/geodesic may take the documented closest-distance fallback
        # headless — assert a live cluster either way.
        for method in ("closest", "hierarchy", "heatmap", "geodesic"):
            cmds.file(new=True, force=True)
            tube, joints, sc = self._bound_cylinder(bind_method=method)
            self.assertTrue(
                cmds.objExists(sc), f"bind_method={method} produced no skinCluster"
            )
            self.assertEqual(cmds.nodeType(sc), "skinCluster")

    def test_bind_invalid_args_raise(self):
        tube = _make_cylinder()
        joints = _make_chain([(-5, 0, 0), (5, 0, 0)])
        with self.assertRaises(ValueError):
            SkinUtils.bind(tube, joints, bind_method="bogus")
        with self.assertRaises(ValueError):
            SkinUtils.bind(tube, joints, skinning_method="bogus")
        with self.assertRaises(ValueError):
            SkinUtils.bind(tube, ["no_such_joint"])
        SkinUtils.bind(tube, joints)
        with self.assertRaises(ValueError):  # already bound
            SkinUtils.bind(tube, joints)

    def test_unbind(self):
        tube, joints, sc = self._bound_cylinder()
        self.assertTrue(SkinUtils.unbind(tube))
        self.assertIsNone(SkinUtils.get_skin_cluster(tube))
        self.assertFalse(SkinUtils.unbind(tube))  # nothing left to unbind
        SkinUtils.bind(tube, joints)  # re-bindable

    def test_get_influences_order_and_count(self):
        tube, joints, sc = self._bound_cylinder()
        influences = SkinUtils.get_influences(sc)
        self.assertEqual(len(influences), 3)
        self.assertEqual(
            [i.split("|")[-1] for i in influences],
            [j.split("|")[-1] for j in joints],
        )


# ----------------------------------------------------------------------
# Batch weight I/O
# ----------------------------------------------------------------------


class TestWeightIO(MayaTkTestCase):
    def _bound(self):
        tube = _make_cylinder(sx=8, sy=6)
        joints = _make_chain([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind(tube, joints)
        return tube, joints, sc

    def test_get_set_weights_roundtrip(self):
        _, _, sc = self._bound()
        weights, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        self.assertEqual(len(weights) % n, 0)
        for v in range(len(weights) // n):
            self.assertAlmostEqual(sum(_row(weights, n, v)), 1.0, places=9)
        # Reverse each row's columns, write, read back exactly.
        flipped = []
        for v in range(len(weights) // n):
            flipped.extend(reversed(_row(weights, n, v)))
        SkinUtils.set_weights(sc, flipped, normalize=False, undoable=False)
        after, _ = SkinUtils.get_weights(sc)
        for a, b in zip(after, flipped):
            self.assertAlmostEqual(a, b, places=9)

    def test_set_weights_returns_old(self):
        _, _, sc = self._bound()
        before, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        uniform = [1.0 / n] * len(before)
        old = SkinUtils.set_weights(sc, uniform, normalize=False, undoable=False)
        for a, b in zip(old, before):
            self.assertAlmostEqual(a, b, places=9)
        # Manual restore from the returned snapshot.
        SkinUtils.set_weights(sc, old, normalize=False, undoable=False)
        restored, _ = SkinUtils.get_weights(sc)
        for a, b in zip(restored, before):
            self.assertAlmostEqual(a, b, places=9)

    def test_set_weights_undoable_undo(self):
        _, _, sc = self._bound()
        # Full-suite runs inherit unknown undo state (a chunk leaked open or a
        # flushed/disabled queue from an earlier module makes cmds.undo() a
        # silent no-op). Toggling state off discards the queue and any
        # dangling chunk; re-enabling starts deterministic and clean without
        # altering the session's queue-length settings.
        cmds.undoInfo(state=False)
        cmds.undoInfo(state=True)
        before, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        uniform = [1.0 / n] * len(before)
        SkinUtils.set_weights(sc, uniform, undoable=True)
        changed, _ = SkinUtils.get_weights(sc)
        self.assertTrue(any(abs(a - b) > 1e-4 for a, b in zip(changed, before)))
        cmds.undo()
        restored, _ = SkinUtils.get_weights(sc)
        for a, b in zip(restored, before):
            self.assertAlmostEqual(a, b, places=6)

    def test_influence_indexing_after_removal(self):
        """Physical-index regression trap: logical plug indices diverge from
        physical order once an influence is removed."""
        _, joints, sc = self._bound()
        cmds.skinCluster(sc, edit=True, removeInfluence=joints[1])
        influences = SkinUtils.get_influences(sc)
        self.assertEqual(len(influences), 2)
        weights, _ = SkinUtils.get_weights(sc)
        n = len(influences)
        for v in range(len(weights) // n):
            self.assertAlmostEqual(sum(_row(weights, n, v)), 1.0, places=6)
        # Roundtrip still consistent through the physical mapping.
        SkinUtils.set_weights(sc, weights, normalize=False, undoable=False)
        after, _ = SkinUtils.get_weights(sc)
        for a, b in zip(after, weights):
            self.assertAlmostEqual(a, b, places=9)

    def test_set_weights_by_influence_subset(self):
        _, joints, sc = self._bound()
        n_verts = len(SkinUtils.get_weights(sc)[0]) // 3
        # Weight vertex 0 fully to the middle joint via a single-influence column.
        SkinUtils.set_weights(
            sc, [1.0], influences=[joints[1]], vertices=[0], normalize=True
        )
        weights, influences = SkinUtils.get_weights(sc, vertices=[0])
        row = dict(zip([i.split("|")[-1] for i in influences], weights))
        self.assertAlmostEqual(row[joints[1].split("|")[-1]], 1.0, places=6)

    def test_set_weights_length_mismatch_raises(self):
        _, _, sc = self._bound()
        with self.assertRaises(ValueError):
            SkinUtils.set_weights(sc, [0.5, 0.5], undoable=False)

    def test_set_vertex_weights_redistribution(self):
        _, joints, sc = self._bound()
        j1, j2, j3 = [j.split("|")[-1] for j in joints]
        SkinUtils.set_weights(
            sc, [0.5, 0.3, 0.2], vertices=[0], normalize=False, undoable=False
        )
        for undoable in (False, True):
            with self.subTest(undoable=undoable):
                SkinUtils.set_weights(
                    sc, [0.5, 0.3, 0.2], vertices=[0], normalize=False, undoable=False
                )
                SkinUtils.set_vertex_weights(sc, {0: {j1: 0.6}}, undoable=undoable)
                weights, _ = SkinUtils.get_weights(sc, vertices=[0])
                self.assertAlmostEqual(weights[0], 0.6, places=6)
                self.assertAlmostEqual(weights[1], 0.24, places=6)
                self.assertAlmostEqual(weights[2], 0.16, places=6)

    def test_set_vertex_weights_overshoot_normalizes(self):
        """Specified weights summing past 1 must renormalize (skinPercent
        semantics) instead of writing a >1 row."""
        _, joints, sc = self._bound()
        j1, j2, _ = [j.split("|")[-1] for j in joints]
        SkinUtils.set_weights(
            sc, [0.5, 0.3, 0.2], vertices=[0], normalize=False, undoable=False
        )
        SkinUtils.set_vertex_weights(sc, {0: {j1: 0.9, j2: 0.6}}, undoable=False)
        weights, _ = SkinUtils.get_weights(sc, vertices=[0])
        self.assertAlmostEqual(weights[0], 0.6, places=6)  # 0.9 / 1.5
        self.assertAlmostEqual(weights[1], 0.4, places=6)  # 0.6 / 1.5
        self.assertAlmostEqual(weights[2], 0.0, places=6)
        self.assertAlmostEqual(sum(weights), 1.0, places=6)

    def test_prune_and_normalize(self):
        _, _, sc = self._bound()
        SkinUtils.set_weights(
            sc, [0.795, 0.2, 0.005], vertices=[0], normalize=False, undoable=False
        )
        SkinUtils.prune_weights(sc, below=0.01)
        weights, _ = SkinUtils.get_weights(sc, vertices=[0])
        self.assertEqual(weights[2], 0.0)
        SkinUtils.normalize_weights(sc)
        weights, _ = SkinUtils.get_weights(sc, vertices=[0])
        self.assertAlmostEqual(sum(weights), 1.0, places=6)

    def test_set_max_influences_enforces(self):
        _, _, sc = self._bound()
        SkinUtils.set_weights(
            sc, [0.5, 0.3, 0.2], vertices=[0], normalize=False, undoable=False
        )
        SkinUtils.set_max_influences(sc, 2)
        self.assertEqual(cmds.getAttr(f"{sc}.maxInfluences"), 2)
        weights, _ = SkinUtils.get_weights(sc, vertices=[0])
        for actual, expected in zip(sorted(weights), sorted([0.625, 0.375, 0.0])):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_set_skinning_method(self):
        _, _, sc = self._bound()
        SkinUtils.set_skinning_method(sc, "dqs")
        self.assertEqual(cmds.getAttr(f"{sc}.skinningMethod"), 1)
        SkinUtils.set_skinning_method(sc, "classic")
        self.assertEqual(cmds.getAttr(f"{sc}.skinningMethod"), 0)


# ----------------------------------------------------------------------
# Transfer / persistence
# ----------------------------------------------------------------------


class TestWeightTransfer(MayaTkTestCase):
    def test_copy_weights_auto_binds_target(self):
        source = _make_cylinder("copySrc")
        joints = _make_chain([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])
        SkinUtils.bind_to_curve(
            source, joints, centerline=[(-5, 0, 0), (0, 0, 0), (5, 0, 0)]
        )
        target = cmds.duplicate(source, name="copyDst")[0]
        target_sc = SkinUtils.copy_weights(source, target)
        self.assertTrue(cmds.objExists(target_sc))
        src_w, _ = SkinUtils.get_weights(SkinUtils.get_skin_cluster(source))
        dst_w, _ = SkinUtils.get_weights(target_sc)
        self.assertEqual(len(src_w), len(dst_w))
        for a, b in zip(src_w, dst_w):
            self.assertAlmostEqual(a, b, places=3)

    def test_mirror_weights(self):
        tube = _make_cylinder(sx=8, sy=10)
        joints = _make_chain([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind(tube, joints)
        influences = SkinUtils.get_influences(sc)
        n = len(influences)
        positions = _vertex_positions(tube)
        # Author an asymmetric state: +X side fully on the end joint, the rest
        # on the middle joint.
        weights = []
        for x, _, _ in positions:
            weights.extend([0.0, 0.0, 1.0] if x > 0.1 else [0.0, 1.0, 0.0])
        SkinUtils.set_weights(sc, weights, normalize=False, undoable=False)
        SkinUtils.mirror_weights(
            tube, axis="YZ", influence_association=("closestJoint",)
        )
        mirrored, _ = SkinUtils.get_weights(sc)
        neg_verts = [i for i, (x, _, _) in enumerate(positions) if x < -0.1]
        self.assertTrue(neg_verts)
        j1_mean = sum(_row(mirrored, n, v)[0] for v in neg_verts) / len(neg_verts)
        self.assertGreater(j1_mean, 0.9)

    def test_export_import_roundtrip(self):
        tube = _make_cylinder()
        joints = _make_chain([(-5, 0, 0), (0, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind_to_curve(
            tube, joints, centerline=[(-5, 0, 0), (0, 0, 0), (5, 0, 0)]
        )
        export_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "temp_tests", "skin_weights"
        )
        try:
            path = SkinUtils.export_weights(
                tube, os.path.join(export_dir, "roundtrip.xml")
            )
            self.assertTrue(os.path.isfile(path))
            before, _ = SkinUtils.get_weights(sc)
            # Scramble: everything onto the first joint.
            n_verts = len(before) // 3
            SkinUtils.set_weights(
                sc, [1.0, 0.0, 0.0] * n_verts, normalize=False, undoable=False
            )
            SkinUtils.import_weights(tube, path, method="index")
            after, _ = SkinUtils.get_weights(sc)
            for a, b in zip(after, before):
                self.assertAlmostEqual(a, b, places=6)
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)


# ----------------------------------------------------------------------
# Parametric curve solver
# ----------------------------------------------------------------------


class TestCurveWeights(MayaTkTestCase):
    CENTERLINE = [(-5, 0, 0), (0, 0, 0), (5, 0, 0)]

    def _solved(self, profile="smoothstep", sy=10, sx=12, joints_at=None, **solve_kwargs):
        tube = _make_cylinder(sx=sx, sy=sy)
        joints = _make_chain(joints_at or self.CENTERLINE)
        weights, influences = CurveWeights.solve(
            tube, joints, centerline=self.CENTERLINE, profile=profile, **solve_kwargs
        )
        return tube, joints, weights, influences

    def test_get_arc_lengths(self):
        curve = cmds.curve(ep=[(-5, 0, 0), (5, 0, 0)], d=1)
        lengths = NurbsUtils.get_arc_lengths(
            curve, [(-5, 0, 0), (0, 0, 0), (2.5, 0, 0), (99, 3, 4)]
        )
        self.assertAlmostEqual(lengths[0], 0.0, places=6)
        self.assertAlmostEqual(lengths[1], 5.0, places=6)
        self.assertAlmostEqual(lengths[2], 7.5, places=6)
        self.assertAlmostEqual(lengths[3], 10.0, places=6)  # clamped past the end
        self.assertAlmostEqual(NurbsUtils.get_curve_length(curve), 10.0, places=6)

    def test_ring_uniformity(self):
        tube, joints, weights, influences = self._solved()
        n = len(influences)
        for x, verts in _rings_by_x(tube).items():
            for i in range(n):
                column = [_row(weights, n, v)[i] for v in verts]
                self.assertLess(
                    max(column) - min(column),
                    1e-6,
                    f"ring x={x} influence {i} is not uniform",
                )

    def test_weights_sum_to_one(self):
        _, _, weights, influences = self._solved()
        n = len(influences)
        for v in range(len(weights) // n):
            self.assertAlmostEqual(sum(_row(weights, n, v)), 1.0, places=9)

    def test_degree_one_max_two_influences(self):
        _, _, weights, influences = self._solved(degree=1)
        n = len(influences)
        for v in range(len(weights) // n):
            nonzero = [w for w in _row(weights, n, v) if w > 1e-9]
            self.assertLessEqual(len(nonzero), 2)

    def test_influence_support_bounded_by_degree(self):
        # 6 joints, default cubic: at most degree + 1 = 4 influences.
        joints_at = [(x, 0, 0) for x in (-5, -3, -1, 1, 3, 5)]
        _, _, weights, influences = self._solved(joints_at=joints_at)
        n = len(influences)
        for v in range(len(weights) // n):
            nonzero = [w for w in _row(weights, n, v) if w > 1e-9]
            self.assertLessEqual(len(nonzero), 4)

    def test_quadratic_basis_values(self):
        # 3 joints clamp the default cubic to degree 2 — a single Bezier
        # segment over the [0, 10] arc: at the mid ring (u = 0.5) the basis
        # is exactly ((1-u)^2, 2u(1-u), u^2) = (0.25, 0.5, 0.25).
        tube, joints, weights, influences = self._solved(sy=4)
        n = len(influences)
        for v in _rings_by_x(tube)[0.0]:
            row = _row(weights, n, v)
            self.assertAlmostEqual(row[0], 0.25, places=4)
            self.assertAlmostEqual(row[1], 0.50, places=4)
            self.assertAlmostEqual(row[2], 0.25, places=4)

    def test_smooth_basis_spreads_station_rings(self):
        # The smooth basis must NOT rigidly pin interior joint-station rings
        # to their joint (the degree-1 hinge-crease behavior): weight spreads
        # across neighbors while the end stations stay pinned (clamped knots).
        joints_at = [(x, 0, 0) for x in (-5, -2.5, 0, 2.5, 5)]
        tube, joints, weights, influences = self._solved(sy=4, joints_at=joints_at)
        n = len(influences)
        rings = _rings_by_x(tube)
        for v in rings[0.0]:  # interior station: blended, not rigid
            row = _row(weights, n, v)
            self.assertLess(max(row), 1.0 - 1e-3)
            self.assertGreaterEqual(len([w for w in row if w > 0.05]), 2)
        for v in rings[-5.0]:  # end station: fully pinned
            self.assertAlmostEqual(_row(weights, n, v)[0], 1.0, places=6)

    def test_caps_clamp_to_end_joints(self):
        tube, joints, weights, influences = self._solved()
        n = len(influences)
        for v, (x, _, _) in enumerate(_vertex_positions(tube)):
            if x <= -5 + 1e-4:
                self.assertAlmostEqual(_row(weights, n, v)[0], 1.0, places=6)
            elif x >= 5 - 1e-4:
                self.assertAlmostEqual(_row(weights, n, v)[-1], 1.0, places=6)

    def test_profile_values(self):
        # 2 joints spanning [-5, 5]; sy=4 puts a ring exactly at x=-2.5,
        # i.e. t=0.25 along the single joint segment:
        # linear -> 0.25, smoothstep -> 3t^2 - 2t^3 = 0.15625.
        tube = _make_cylinder(sx=8, sy=4)
        joints = _make_chain([(-5, 0, 0), (5, 0, 0)])
        for profile, expected in (("linear", 0.25), ("smoothstep", 0.15625)):
            weights, influences = CurveWeights.solve(
                tube, joints, centerline=[(-5, 0, 0), (5, 0, 0)], profile=profile
            )
            n = len(influences)
            ring = _rings_by_x(tube)[-2.5]
            for v in ring:
                self.assertAlmostEqual(_row(weights, n, v)[1], expected, places=6)

    def test_curve_node_equals_centerline(self):
        tube = _make_cylinder()
        joints = _make_chain(self.CENTERLINE)
        curve = cmds.curve(ep=self.CENTERLINE, d=1)
        via_curve, _ = CurveWeights.solve(tube, joints, curve=curve)
        via_centerline, _ = CurveWeights.solve(
            tube, joints, centerline=self.CENTERLINE
        )
        for a, b in zip(via_curve, via_centerline):
            self.assertAlmostEqual(a, b, places=6)

    def test_unordered_joints_raise(self):
        tube = _make_cylinder()
        joints = _make_loose_joints([(0, 0, 0), (-5, 0, 0), (5, 0, 0)])
        with self.assertRaises(ValueError):
            CurveWeights.solve(tube, joints, centerline=self.CENTERLINE)

    def test_solve_arg_validation(self):
        tube = _make_cylinder()
        joints = _make_chain(self.CENTERLINE)
        with self.assertRaises(ValueError):  # neither curve nor centerline
            CurveWeights.solve(tube, joints)
        with self.assertRaises(ValueError):  # both
            curve = cmds.curve(ep=self.CENTERLINE, d=1)
            CurveWeights.solve(
                tube, joints, curve=curve, centerline=self.CENTERLINE
            )
        with self.assertRaises(ValueError):  # unknown profile
            CurveWeights.solve(
                tube, joints, centerline=self.CENTERLINE, profile="bogus"
            )

    def test_temp_curve_cleanup(self):
        tube = _make_cylinder()
        joints = _make_chain(self.CENTERLINE)
        curves_before = set(cmds.ls(type="nurbsCurve"))
        CurveWeights.solve(tube, joints, centerline=self.CENTERLINE)
        self.assertEqual(set(cmds.ls(type="nurbsCurve")), curves_before)


# ----------------------------------------------------------------------
# Quality: the candy-wrapper metric
# ----------------------------------------------------------------------


class TestSkinQuality(MayaTkTestCase):
    """The two failure modes of naive tube skinning, asserted separately:

    - candy-wrapper: linear blending collapses a ring's radius under twist
      (fixed by dual quaternion skinning);
    - dead twist: Maya's closest-distance bind weights mid-tube rings to the
      bone segment, so an end joint's twist never propagates down the tube
      (fixed by parametric arc-length weights).
    """

    CENTERLINE = [(-5, 0, 0), (0, 0, 0), (5, 0, 0)]

    def _min_ring_radius(self, mesh, verts):
        cmds.refresh()
        positions = _vertex_positions(mesh)
        return min(math.hypot(positions[v][1], positions[v][2]) for v in verts)

    def _mean_ring_twist_deg(self, mesh, verts, rest_positions):
        """Mean angular displacement of ring verts around the +X tube axis."""
        cmds.refresh()
        positions = _vertex_positions(mesh)
        deltas = []
        for v in verts:
            before = math.atan2(rest_positions[v][2], rest_positions[v][1])
            after = math.atan2(positions[v][2], positions[v][1])
            delta = math.degrees(after - before)
            while delta > 180:
                delta -= 360
            while delta < -180:
                delta += 360
            deltas.append(abs(delta))
        return sum(deltas) / len(deltas)

    def test_dqs_preserves_ring_radius_under_twist(self):
        """Identical parametric weights, classic vs DQS blending: at the ring
        weighted 0.5/0.5 a 90-deg twist collapses classic to ~cos(45deg) while
        DQS holds the radius (candy-wrapper metric)."""
        results = {}
        for method in ("classic", "dqs"):
            tube = _make_cylinder(f"quality_{method}", sx=16, sy=4)
            joints = _make_chain(self.CENTERLINE, prefix=f"quality_{method}Jnt")
            SkinUtils.bind_to_curve(
                tube, joints, centerline=self.CENTERLINE, skinning_method=method
            )
            ring = _rings_by_x(tube)[2.5]
            cmds.setAttr(f"{joints[-1]}.rotateX", 90)
            results[method] = self._min_ring_radius(tube, ring)
        self.assertLess(
            results["classic"],
            0.85,
            f"expected classic linear blending to candy-wrap ({results['classic']:.3f})",
        )
        self.assertGreater(
            results["dqs"],
            0.97,
            f"parametric+DQS ring collapsed to {results['dqs']:.3f} (candy-wrapper)",
        )
        self.assertGreater(results["dqs"], results["classic"] + 0.1)

    def test_parametric_twist_distribution(self):
        """A 90-deg end twist must reach the ring halfway down the last
        segment substantially (~51 deg under the quadratic basis; a linear
        blend would give 45). Parametric weights deliver it; the raw
        closest-distance bind leaves the ring nearly rigid."""
        tube_raw = _make_cylinder("twistRaw", sx=16, sy=4)
        joints_raw = _make_chain(self.CENTERLINE, prefix="twistRawJnt")
        cmds.skinCluster(joints_raw, tube_raw, toSelectedBones=True)

        tube_par = _make_cylinder("twistPar", sx=16, sy=4)
        joints_par = _make_chain(self.CENTERLINE, prefix="twistParJnt")
        SkinUtils.bind_to_curve(tube_par, joints_par, centerline=self.CENTERLINE)

        rest_raw = _vertex_positions(tube_raw)
        rest_par = _vertex_positions(tube_par)
        ring_raw = _rings_by_x(tube_raw)[2.5]
        ring_par = _rings_by_x(tube_par)[2.5]

        cmds.setAttr(f"{joints_raw[-1]}.rotateX", 90)
        cmds.setAttr(f"{joints_par[-1]}.rotateX", 90)

        twist_raw = self._mean_ring_twist_deg(tube_raw, ring_raw, rest_raw)
        twist_par = self._mean_ring_twist_deg(tube_par, ring_par, rest_par)
        self.assertGreater(twist_par, 35.0, f"parametric twist too low: {twist_par:.1f}")
        self.assertLess(twist_par, 55.0, f"parametric twist too high: {twist_par:.1f}")
        self.assertLess(
            twist_raw,
            15.0,
            "expected the raw closest-distance bind to strand the twist "
            f"(got {twist_raw:.1f} deg)",
        )


# ----------------------------------------------------------------------
# Procedural falloff / deformers
# ----------------------------------------------------------------------


class TestApplyFalloff(MayaTkTestCase):
    def _setup(self):
        tube = _make_cylinder(sx=8, sy=10)
        joints = _make_chain([(-5, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind(tube, joints)
        cmds.select(clear=True)
        anchor = cmds.joint(p=(5, 0, 0), name="falloffAnchorJnt")
        return tube, joints, sc, anchor

    def test_linear_matches_legacy_formula(self):
        tube, joints, sc, anchor = self._setup()
        radius = 3.0
        count = SkinUtils.apply_falloff(
            sc,
            target_influence=anchor,
            source_influence=joints[-1],
            center=(5, 0, 0),
            radius=radius,
            profile="linear",
        )
        self.assertGreater(count, 0)
        influences = SkinUtils.get_influences(sc)
        anchor_idx = [i.split("|")[-1] for i in influences].index(anchor)
        weights, _ = SkinUtils.get_weights(sc)
        n = len(influences)
        for v, (x, y, z) in enumerate(_vertex_positions(tube)):
            d = math.sqrt((x - 5) ** 2 + y**2 + z**2)
            actual = _row(weights, n, v)[anchor_idx]
            if d <= radius:
                self.assertAlmostEqual(actual, 1.0 - d / radius, places=4)
            else:
                self.assertAlmostEqual(actual, 0.0, places=6)

    def test_adds_influence(self):
        tube, joints, sc, anchor = self._setup()
        self.assertNotIn(
            anchor, [i.split("|")[-1] for i in SkinUtils.get_influences(sc)]
        )
        SkinUtils.apply_falloff(sc, anchor, center=(5, 0, 0), radius=2.0)
        self.assertIn(
            anchor, [i.split("|")[-1] for i in SkinUtils.get_influences(sc)]
        )

    def test_center_from_node(self):
        tube, joints, sc, anchor = self._setup()
        count = SkinUtils.apply_falloff(sc, anchor, center=anchor, radius=2.0)
        self.assertGreater(count, 0)


class TestDeltaMush(MayaTkTestCase):
    def test_add_delta_mush(self):
        tube = _make_cylinder()
        joints = _make_chain([(-5, 0, 0), (5, 0, 0)])
        SkinUtils.bind(tube, joints)
        node = SkinUtils.add_delta_mush(
            tube, smoothing_iterations=7, smoothing_step=0.4, pin_border_vertices=False
        )
        self.assertEqual(cmds.nodeType(node), "deltaMush")
        self.assertEqual(cmds.getAttr(f"{node}.smoothingIterations"), 7)
        self.assertAlmostEqual(cmds.getAttr(f"{node}.smoothingStep"), 0.4, places=6)
        self.assertFalse(cmds.getAttr(f"{node}.pinBorderVertices"))


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
