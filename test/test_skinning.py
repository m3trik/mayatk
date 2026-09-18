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
    return [
        (flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]) for i in range(len(flat) // 3)
    ]


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

    def test_set_weights_batched_write_is_one_undo_step(self):
        """The batched ``MFnSkinCluster.setWeights`` write is recorded: one undo
        restores every influence's weights, leaves the edit before it alone,
        and one redo writes them again -- for all influences and for a subset."""
        _, joints, sc = self._bound()
        cmds.undoInfo(state=False)
        cmds.undoInfo(state=True, infinity=True)
        prior = cmds.spaceLocator(name="weights_prior")[0]
        before, influences = SkinUtils.get_weights(sc)
        uniform = [1.0 / len(influences)] * len(before)
        writes = (
            lambda: SkinUtils.set_weights(sc, uniform, normalize=False),
            lambda: SkinUtils.set_weights(
                sc, [1.0], influences=[joints[1]], vertices=[0], normalize=True
            ),
        )
        # A different value on each pass: were the second to set the 9.0 the
        # first one left, an undo that also reverted it would read as untouched.
        for value, write in zip((9.0, 11.0), writes):
            cmds.setAttr(f"{prior}.translateZ", value)
            write()
            written, _ = SkinUtils.get_weights(sc)
            self.assertTrue(any(abs(a - b) > 1e-4 for a, b in zip(written, before)))
            cmds.undo()
            for a, b in zip(SkinUtils.get_weights(sc)[0], before):
                self.assertAlmostEqual(a, b, places=9)
            self.assertEqual(cmds.getAttr(f"{prior}.translateZ"), value)
            cmds.redo()
            for a, b in zip(SkinUtils.get_weights(sc)[0], written):
                self.assertAlmostEqual(a, b, places=9)
            cmds.undo()

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

    def _solved(
        self, profile="smoothstep", sy=10, sx=12, joints_at=None, **solve_kwargs
    ):
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
        # STRAIGHT centerline: per-vertex projection is exact here. The bent
        # case — where it is not — is test_rings_share_one_station below.
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
        via_centerline, _ = CurveWeights.solve(tube, joints, centerline=self.CENTERLINE)
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
            CurveWeights.solve(tube, joints, curve=curve, centerline=self.CENTERLINE)
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

    def test_rings_share_one_station(self):
        # Ring grouping must give every member of a ring byte-identical
        # weights. Asserted against a BENT centerline: on a straight tube
        # per-vertex projection is already uniform, so a straight fixture
        # (test_ring_uniformity above) cannot tell the two apart.
        tube = _make_cylinder(sx=8, sy=6)
        joints = _make_chain(self.CENTERLINE)
        rings = list(_rings_by_x(tube).values())
        bent = [(-5, 0, 0), (-2, 2.5, 0), (2, 2.5, 0), (5, 0, 0)]
        n = len(joints)

        ringed, _ = CurveWeights.solve(tube, joints, centerline=bent, rings=rings)
        for ring in rings:
            rows = [_row(ringed, n, v) for v in ring]
            for row in rows[1:]:
                self.assertEqual(row, rows[0])

        # ...and without the rings the same bend visibly skews a ring: the
        # inside of the cross-section projects short of the outside.
        plain, _ = CurveWeights.solve(tube, joints, centerline=bent)
        worst = 0.0
        for ring in rings:
            for i in range(n):
                column = [_row(plain, n, v)[i] for v in ring]
                worst = max(worst, max(column) - min(column))
        self.assertGreater(
            worst, 1e-3, "fixture is too gentle to exercise the ring grouping"
        )

    def test_rings_out_of_range_ids_are_ignored(self):
        # Partial / stale ring maps must degrade to per-vertex projection
        # rather than raising or shifting weights.
        tube = _make_cylinder(sx=8, sy=6)
        joints = _make_chain(self.CENTERLINE)
        plain, _ = CurveWeights.solve(tube, joints, centerline=self.CENTERLINE)
        junk, _ = CurveWeights.solve(
            tube, joints, centerline=self.CENTERLINE, rings=[[10**6, -1], []]
        )
        for a, b in zip(plain, junk):
            self.assertAlmostEqual(a, b, places=9)


# ----------------------------------------------------------------------
# Curve geometry: weighting a NURBS curve's CVs (the IK logic curve)
# ----------------------------------------------------------------------


class TestCurveGeometryWeights(MayaTkTestCase):
    """The tube rig skins its IK curve to driver joints through the SAME
    parametric path as the mesh — CV stations come from Greville abscissae.
    """

    CENTERLINE = [(-5, 0, 0), (-1, 2, 0), (3, 2, 0), (5, 0, 0)]

    def _curve_and_drivers(self):
        curve = cmds.curve(ep=self.CENTERLINE, d=3, name="axisCurve")
        drivers = _make_loose_joints(
            [cmds.pointPosition(f"{curve}.ep[{i}]", w=True) for i in range(4)],
            prefix="drvJnt",
        )
        return curve, drivers

    def test_greville_pins_ends_and_increases(self):
        curve, _ = self._curve_and_drivers()
        lengths = NurbsUtils.get_greville_arc_lengths(curve)
        n_cvs = len(cmds.ls(f"{curve}.cv[*]", flatten=True))
        self.assertEqual(len(lengths), n_cvs)
        # Clamped curve: first/last CV sit exactly at the curve's ends.
        # The end compares with a relative tolerance because Maya's
        # findLengthFromParam (numeric integration) and MFnNurbsCurve.length()
        # disagree at ~1e-6 relative; production stations all come from
        # findLengthFromParam, so the two are never mixed.
        total = NurbsUtils.get_curve_length(curve)
        self.assertAlmostEqual(lengths[0], 0.0, places=6)
        self.assertAlmostEqual(lengths[-1], total, delta=total * 1e-4)
        for a, b in zip(lengths, lengths[1:]):
            self.assertLessEqual(a, b + 1e-9, "greville stations must not go backward")

    def test_greville_matches_projection_on_a_degree_one_curve(self):
        # A degree-1 curve's CVs lie ON the curve, so the exact (Greville)
        # and approximate (projected) stations must agree — this pins the
        # off-by-one knot-slice convention Maya's knots() array imposes.
        curve = cmds.curve(p=[(0, 0, 0), (3, 0, 0), (3, 4, 0)], d=1)
        greville = NurbsUtils.get_greville_arc_lengths(curve)
        projected = NurbsUtils.get_arc_lengths(curve, [(0, 0, 0), (3, 0, 0), (3, 4, 0)])
        for a, b in zip(greville, projected):
            self.assertAlmostEqual(a, b, places=6)

    def test_solve_on_curve_geometry(self):
        curve, drivers = self._curve_and_drivers()
        weights, influences = CurveWeights.solve(curve, drivers, curve=curve)
        n = len(influences)
        n_cvs = len(cmds.ls(f"{curve}.cv[*]", flatten=True))
        self.assertEqual(len(weights), n_cvs * n)
        dominant = []
        for v in range(n_cvs):
            row = _row(weights, n, v)
            self.assertAlmostEqual(sum(row), 1.0, places=9)
            self.assertLessEqual(len([w for w in row if w > 1e-9]), 4)
            dominant.append(max(range(n), key=lambda i: row[i]))
        # Influence order must follow the curve: a CV may never be dominated
        # by a driver BEHIND the previous CV's driver (the closest-distance
        # failure that kinked posed tubes).
        for a, b in zip(dominant, dominant[1:]):
            self.assertLessEqual(a, b, f"dominance goes backward: {dominant}")

    def test_bind_to_curve_on_curve_geometry(self):
        curve, drivers = self._curve_and_drivers()
        sc = SkinUtils.bind_to_curve(curve, drivers, curve=curve, name="curveSkin")
        self.assertTrue(cmds.objExists(sc))
        self.assertEqual(SkinUtils.get_skin_cluster(curve), sc)
        weights, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        n_cvs = len(cmds.ls(f"{curve}.cv[*]", flatten=True))
        self.assertEqual(len(weights) // n, n_cvs)
        for v in range(n_cvs):
            self.assertAlmostEqual(sum(_row(weights, n, v)), 1.0, places=5)

    def test_failed_weight_write_leaves_geometry_unbound(self):
        """A weight write that fails must roll the cluster back.

        Callers (TubeRig.skin_mesh / skin_curve_to_drivers) catch a failed
        parametric bind and fall back to a plain bind. A half-written
        cluster left behind makes that fallback raise 'already bound', so
        the geometry would keep DEFAULT weights while the caller reports
        failure — worse than never having tried.
        """
        curve, drivers = self._curve_and_drivers()
        original = SkinUtils.set_weights

        def boom(*args, **kwargs):
            raise RuntimeError("simulated weight-write failure")

        SkinUtils.set_weights = staticmethod(boom)
        try:
            with self.assertRaises(RuntimeError):
                SkinUtils.bind_to_curve(curve, drivers, curve=curve, name="failSkin")
        finally:
            SkinUtils.set_weights = original
        self.assertIsNone(
            SkinUtils.get_skin_cluster(curve), "partial skinCluster was not rolled back"
        )
        # ...and the geometry is genuinely re-bindable afterwards.
        self.assertTrue(SkinUtils.bind_to_curve(curve, drivers, curve=curve))

    def test_curve_skin_weight_io_uses_cv_components(self):
        # get/set/prune/normalize must address .cv[] on a skinned curve;
        # a hardcoded .vtx[] selector raises or silently misses.
        curve, drivers = self._curve_and_drivers()
        sc = SkinUtils.bind_to_curve(curve, drivers, curve=curve, name="curveSkin2")
        before, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        flat = [1.0 if i % n == 0 else 0.0 for i in range(len(before))]
        SkinUtils.set_weights(sc, flat, normalize=False, undoable=True)
        after, _ = SkinUtils.get_weights(sc)
        self.assertAlmostEqual(after[0], 1.0, places=6)
        SkinUtils.prune_weights(sc, below=0.001)
        SkinUtils.normalize_weights(sc)
        final, _ = SkinUtils.get_weights(sc)
        for v in range(len(final) // n):
            self.assertAlmostEqual(sum(_row(final, n, v)), 1.0, places=5)


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
        self.assertGreater(
            twist_par, 35.0, f"parametric twist too low: {twist_par:.1f}"
        )
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
        self.assertIn(anchor, [i.split("|")[-1] for i in SkinUtils.get_influences(sc)])

    def test_center_from_node(self):
        tube, joints, sc, anchor = self._setup()
        count = SkinUtils.apply_falloff(sc, anchor, center=anchor, radius=2.0)
        self.assertGreater(count, 0)


class TestAddInfluence(MayaTkTestCase):
    """``add_influence`` — the late-influence primitive behind the tube rig's
    end anchors (2026-08-30): an influence can be registered at an explicit
    bind pose instead of wherever it happens to stand."""

    def _setup(self):
        tube = _make_cylinder(sx=8, sy=10)
        joints = _make_chain([(-5, 0, 0), (5, 0, 0)])
        sc = SkinUtils.bind(tube, joints)
        cmds.select(clear=True)
        anchor = cmds.joint(p=(5, 0, 0), name="addInfluenceJnt")
        return tube, joints, sc, anchor

    def test_returns_physical_index_and_is_idempotent(self):
        tube, joints, sc, anchor = self._setup()
        idx = SkinUtils.add_influence(sc, anchor)
        leaves = [i.split("|")[-1] for i in SkinUtils.get_influences(sc)]
        self.assertEqual(leaves[idx], anchor)
        self.assertEqual(SkinUtils.add_influence(sc, anchor), idx)
        self.assertEqual(len(SkinUtils.get_influences(sc)), 3, "added twice")

    def test_bind_matrix_registers_the_bind_pose_elsewhere(self):
        """Registered 4 units below where it stands, the influence contributes
        a +4 lift to the vertices it fully drives — the deformation is measured
        from the given pose, not from the joint's current one."""
        tube, joints, sc, anchor = self._setup()
        before = _vertex_positions(tube)
        end_verts = [v for v, (x, _, _) in enumerate(before) if x > 4.9]
        self.assertTrue(end_verts)
        bind_below = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, -4, 0, 1]

        SkinUtils.add_influence(sc, anchor, bind_matrix=bind_below)
        SkinUtils.set_vertex_weights(sc, {v: {anchor: 1.0} for v in end_verts})

        after = _vertex_positions(tube)
        for v in end_verts:
            self.assertAlmostEqual(after[v][1] - before[v][1], 4.0, places=3)
            self.assertAlmostEqual(after[v][0] - before[v][0], 0.0, places=3)


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


# ----------------------------------------------------------------------
# flatten_influences: one shear-free skeleton per skin, for export
# ----------------------------------------------------------------------


def _top_joint(path):
    """The topmost joint on *path*'s ancestor chain (the path itself included)."""
    parts = path.split("|")
    top = path
    for i in range(len(parts) - 1, 1, -1):
        candidate = "|".join(parts[:i])
        if cmds.nodeType(candidate) != "joint":
            break
        top = candidate
    return top


def _axes_orthogonal(matrix, tolerance=1e-6):
    """True when the 4x4 (row-major, 16 floats) has mutually orthogonal axes."""
    axes = (matrix[0:3], matrix[4:7], matrix[8:11])
    for a in range(3):
        for b in range(a + 1, 3):
            if abs(sum(x * y for x, y in zip(axes[a], axes[b]))) > tolerance:
                return False
    return True


class TestFlattenInfluences(MayaTkTestCase):
    """``SkinUtils.flatten_influences`` against the production shape it exists
    for: a dual-quaternion skin whose influences span TWO joint roots (a chain
    plus an anchor joint under a separately animated plug), a stretched,
    scale-compensated chain (a sheared parent-relative matrix), the mesh pinned
    at a build pose its module has since left. Frames 1-10, module at x=20..70."""

    FRAMES = list(range(1, 11))

    def _build(self):
        from mayatk.xform_utils.matrices import Matrices

        cmds.currentUnit(time="film")
        cmds.playbackOptions(
            animationStartTime=1, animationEndTime=10, minTime=1, maxTime=10
        )
        assy = cmds.group(empty=True, name="fi_ASSY")
        cmds.setKeyframe(assy, attribute="translateX", t=1, v=20)
        cmds.setKeyframe(assy, attribute="translateX", t=10, v=70)
        cmds.setKeyframe(assy, attribute="rotateY", t=1, v=0)
        cmds.setKeyframe(assy, attribute="rotateY", t=10, v=35)
        cmds.currentTime(1)
        tube = _make_cylinder("fi_tube")
        tube = cmds.ls(cmds.parent(tube, assy, relative=True)[0], long=True)[0]
        rig = cmds.group(empty=True, name="fi_RIG", parent=assy)
        chain = _make_chain(
            [(-5, 0, 0), (-1.7, 0, 0), (1.7, 0, 0), (5, 0, 0)], "fi_jnt"
        )
        cmds.parent(chain[0], rig, relative=True)
        chain = cmds.ls([f"fi_jnt{i + 1}" for i in range(4)], long=True)
        plug = cmds.group(empty=True, name="fi_PLUG")
        cmds.setKeyframe(plug, attribute="translateY", t=1, v=0)
        cmds.setKeyframe(plug, attribute="translateY", t=10, v=3)
        cmds.select(clear=True)
        anchor = cmds.joint(p=(25, 0, 0), name="fi_anchor")
        anchor = cmds.ls(cmds.parent(anchor, plug)[0], long=True)[0]
        # The anchor follows a target through a constraint, and an IK handle solves
        # the chain: both drive the influences and must be gone after the flatten.
        target = cmds.spaceLocator(name="fi_anchor_target")[0]
        cmds.xform(target, ws=True, t=(25, 0, 0))
        cmds.parent(target, plug)
        cmds.pointConstraint(target, anchor)
        cmds.ikHandle(
            startJoint=chain[0], endEffector=chain[3], solver="ikSCsolver", name="fi_ik"
        )
        # Bend AND stretch the second joint: with segmentScaleCompensate on, its
        # child's parent-relative matrix shears -- what a TRS decomposition drops.
        cmds.setKeyframe(chain[1], attribute="rotateZ", t=1, v=0)
        cmds.setKeyframe(chain[1], attribute="rotateZ", t=10, v=30)
        cmds.setKeyframe(chain[1], attribute="scaleX", t=1, v=1.0)
        cmds.setKeyframe(chain[1], attribute="scaleX", t=10, v=1.3)
        sc = cmds.skinCluster(
            chain + [anchor],
            tube,
            toSelectedBones=True,
            skinMethod=1,
            maximumInfluences=2,
        )[0]
        cmds.currentTime(1)
        self.assertTrue(Matrices.pin_world_matrix(tube), "fixture: the mesh pins")
        return tube, sc, chain, anchor, assy

    def _worlds(self, nodes):
        out = {}
        for frame in self.FRAMES:
            cmds.currentTime(frame)
            out[frame] = {
                cmds.ls(n, uuid=True)[0]: cmds.xform(n, q=True, ws=True, m=True)
                for n in nodes
            }
        return out

    def test_one_root_worlds_kept_mesh_unpinned(self):
        tube, sc, chain, anchor, assy = self._build()
        influences = SkinUtils.get_influences(sc, long_names=True)
        self.assertEqual(
            len({_top_joint(j) for j in influences}), 2, "fixture spans two roots"
        )
        before = self._worlds(influences)

        result = SkinUtils.flatten_influences(frames=self.FRAMES)

        self.assertEqual(len(result), 1, result)
        root, placed = next(iter(result.items()))
        self.assertEqual(len(placed), 5)
        self.assertEqual(cmds.nodeType(root), "joint")
        self.assertEqual(
            cmds.listRelatives(root, parent=True, fullPath=True)[0],
            cmds.ls(assy, long=True)[0],
            "the root sits under the mesh's parent (what the un-pinned mesh rides)",
        )
        for joint in placed:
            self.assertEqual(
                cmds.listRelatives(joint, parent=True, fullPath=True)[0], root
            )
        self.assertEqual(
            len({_top_joint(j) for j in SkinUtils.get_influences(sc, long_names=True)}),
            1,
            "every influence of the skin is under ONE root joint",
        )
        after = self._worlds(placed)
        worst = 0.0
        for frame in self.FRAMES:
            for uuid, matrix in before[frame].items():
                worst = max(
                    worst, max(abs(a - b) for a, b in zip(matrix, after[frame][uuid]))
                )
        self.assertLess(worst, 1e-4, f"world matrices drifted by {worst}")
        # Shear-free locals: relative to the unscaled root every local is TRS.
        cmds.currentTime(10)
        for joint in placed:
            local = cmds.getAttr(f"{joint}.matrix")
            self.assertTrue(_axes_orthogonal(local), f"{joint} local shears: {local}")
            self.assertFalse(cmds.getAttr(f"{joint}.segmentScaleCompensate"))
        # The stretch survives as scale keys, not as a dropped shear.
        stretched = next(j for j in placed if j.endswith("fi_jnt2"))
        self.assertAlmostEqual(cmds.getAttr(f"{stretched}.scaleX"), 1.3, places=4)
        # The mesh inherits its module again; its own channels were never touched.
        self.assertTrue(cmds.getAttr(f"{tube}.inheritsTransform"))
        # What drove the influences is gone, not merely disabled: a later driven-
        # animation pass must find nothing to re-bake them from.
        self.assertFalse(
            cmds.objExists("fi_ik"), "the IK handle on the chain is deleted"
        )
        self.assertFalse(
            cmds.ls(
                cmds.listRelatives(list(placed), children=True, fullPath=True) or [],
                type="pointConstraint",
            ),
            "the anchor's constraint node is deleted",
        )
        opm = cmds.getAttr(f"{tube}.offsetParentMatrix")
        self.assertTrue(
            all(
                abs(v - (1.0 if i in (0, 5, 10, 15) else 0.0)) < 1e-9
                for i, v in enumerate(opm)
            )
        )

    def test_two_skins_get_two_roots_from_one_sampling_pass(self):
        """A second skin in the scene: its own root, and both skins' influence
        worlds kept -- the plan is sampled in ONE timeline pass for every root."""
        from mayatk.anim_utils.world_fit_bake import WorldFitBake

        tube, sc, chain, anchor, assy = self._build()
        other = _make_cylinder("fi_other")
        other = cmds.ls(cmds.parent(other, assy, relative=True)[0], long=True)[0]
        cmds.move(0, 6, 0, other, relative=True)
        rig2 = cmds.group(empty=True, name="fi_RIG2", parent=assy)
        chain2 = _make_chain([(-5, 6, 0), (0, 6, 0), (5, 6, 0)], "fi_jntB")
        cmds.parent(chain2[0], rig2, relative=True)
        chain2 = cmds.ls([f"fi_jntB{i + 1}" for i in range(3)], long=True)
        cmds.setKeyframe(chain2[1], attribute="rotateY", t=1, v=0)
        cmds.setKeyframe(chain2[1], attribute="rotateY", t=10, v=-25)
        sc2 = cmds.skinCluster(chain2, other, toSelectedBones=True)[0]
        both = SkinUtils.get_influences(sc, long_names=True) + SkinUtils.get_influences(
            sc2, long_names=True
        )
        before = self._worlds(both)
        passes = []
        original = WorldFitBake.sample_locals

        def counting(plan, frames, **kwargs):
            passes.append(len(plan))
            return original(plan, frames, **kwargs)

        WorldFitBake.sample_locals = staticmethod(counting)
        try:
            result = SkinUtils.flatten_influences(frames=self.FRAMES)
        finally:
            WorldFitBake.sample_locals = staticmethod(original)
        self.assertEqual(passes, [8], "one sampling pass covering both skins' plans")
        self.assertEqual(len(result), 2, result)
        placed = [j for joints in result.values() for j in joints]
        self.assertEqual(len(placed), 8)
        after = self._worlds(placed)
        worst = max(
            abs(a - b)
            for frame in self.FRAMES
            for uuid, matrix in before[frame].items()
            for a, b in zip(matrix, after[frame][uuid])
        )
        self.assertLess(worst, 1e-4, f"world matrices drifted by {worst}")

    def _deformed(self, mesh):
        """The mesh's WORLD points per frame -- what a skin actually has to keep."""
        import maya.api.OpenMaya as om2

        out = {}
        for frame in self.FRAMES:
            cmds.currentTime(frame)
            sel = om2.MSelectionList()
            sel.add(mesh)
            points = om2.MFnMesh(sel.getDagPath(0)).getPoints(om2.MSpace.kWorld)
            out[frame] = [(p.x, p.y, p.z) for p in points]
        return out

    @staticmethod
    def _axis_along(parent, child, axis=1):
        """``parent``'s local *axis*, world-normalised, dotted with the direction to
        ``child`` -- 1.0 when that axis runs straight down the bone."""
        matrix = cmds.xform(parent, query=True, worldSpace=True, matrix=True)
        row = matrix[axis * 4 : axis * 4 + 3]
        delta = [
            a - b
            for a, b in zip(
                cmds.xform(child, query=True, worldSpace=True, translation=True),
                cmds.xform(parent, query=True, worldSpace=True, translation=True),
            )
        ]
        rn = sum(v * v for v in row) ** 0.5
        dn = sum(v * v for v in delta) ** 0.5
        if not (rn and dn):
            return 0.0
        return sum(a * b for a, b in zip(row, delta)) / (rn * dn)

    def _deform_shift(self, tube, **kwargs):
        """Max deformed-point movement across the flatten, with the mesh left
        PINNED: un-pinning deliberately moves the geometry in Maya (an
        armature-relative importer needs the mesh to ride its parent as the joints
        do), so it must not be the variable when the skin itself is under test."""
        before = self._deformed(tube)
        SkinUtils.flatten_influences(frames=self.FRAMES, unpin_geometry=False, **kwargs)
        after = self._deformed(tube)
        return max(
            max(abs(a - b) for a, b in zip(p, q))
            for frame in self.FRAMES
            for p, q in zip(before[frame], after[frame])
        )

    def test_every_deformed_point_survives_the_flatten(self):
        """The skin is what the flatten exists to preserve -- influence world
        matrices are only the means."""
        tube, sc, chain, anchor, assy = self._build()
        worst = self._deform_shift(tube)
        self.assertLess(worst, 1e-4, f"the deformed mesh moved by {worst}")

    def test_orient_bones_reframes_onto_y_without_moving_the_skin(self):
        """``orient_bones``: the influences' AXES are relabelled so +Y runs down the
        chain -- the axis FBX and USD carriers draw a bone along, where a Maya
        chain is X-down-bone -- and each skinCluster's bindPreMatrix moves by the
        inverse, so not one deformed point shifts."""
        tube, sc, chain, anchor, assy = self._build()
        self.assertGreater(
            self._axis_along(chain[1], chain[2], axis=0), 0.99, "fixture is X-down-bone"
        )
        worst = self._deform_shift(tube, orient_bones=True)
        self.assertLess(worst, 1e-4, f"the deformed mesh moved by {worst}")
        placed = {
            j.rsplit("|", 1)[-1]: j
            for j in cmds.ls(SkinUtils.get_influences(sc, long_names=True), long=True)
        }
        for a, b in (
            ("fi_jnt1", "fi_jnt2"),
            ("fi_jnt2", "fi_jnt3"),
            ("fi_jnt3", "fi_jnt4"),
        ):
            cmds.currentTime(1)
            self.assertGreater(
                self._axis_along(placed[a], placed[b]),
                0.99,
                f"{a}'s +Y should now run at {b}",
            )
        # Still TRS-exact: an axis swap permutes a diagonal scale, never shears it.
        for joint in placed.values():
            self.assertTrue(_axes_orthogonal(cmds.getAttr(f"{joint}.matrix")))

    def test_orient_bones_is_off_by_default(self):
        """The default keeps the stronger contract -- every world matrix as it was."""
        tube, sc, chain, anchor, assy = self._build()
        result = SkinUtils.flatten_influences(frames=self.FRAMES)
        placed = {j.rsplit("|", 1)[-1]: j for j in next(iter(result.values()))}
        cmds.currentTime(1)
        self.assertGreater(
            self._axis_along(placed["fi_jnt2"], placed["fi_jnt3"], axis=0), 0.99
        )

    def test_orient_bones_declines_a_driven_bind(self):
        """A driven bindPreMatrix cannot be rebased, and a PARTLY rebased skin would
        tear -- so that skeleton keeps its authored axis instead."""
        tube, sc, chain, anchor, assy = self._build()
        # A STATIC driver: the point is that the plug is connected, not that its
        # value moves (a live one would change the bind under the flatten itself).
        hold = cmds.createNode("multMatrix", name="fi_held_bind")
        cmds.setAttr(
            f"{hold}.matrixIn[0]",
            cmds.getAttr(f"{sc}.bindPreMatrix[0]"),
            type="matrix",
        )
        cmds.connectAttr(f"{hold}.matrixSum", f"{sc}.bindPreMatrix[0]")
        worst = self._deform_shift(tube, orient_bones=True)
        placed = {
            j.rsplit("|", 1)[-1]: j
            for j in cmds.ls(SkinUtils.get_influences(sc, long_names=True), long=True)
        }
        cmds.currentTime(1)
        self.assertGreater(
            self._axis_along(placed["fi_jnt2"], placed["fi_jnt3"], axis=0),
            0.99,
            "declined: the chain is still X-down-bone",
        )
        self.assertLess(worst, 1e-4, "and the skin is intact")

    def test_root_stays_on_its_ancestor_so_its_identity_bind_is_true(self):
        """The synthetic root influences nothing, so mayaUsd gives it NO bind and
        writes IDENTITY -- which is only true while it sits where its SkelRoot does.
        Moving it to the middle of the chain (to stop it arriving as a lone bone
        metres from its own skin) is free inside Maya but makes the payload lie:
        measured 380 mm of error on a production pull, against 0.31 mm with it left
        alone. The bone is placed importer-side instead, where nothing is weighted
        to it and it provably cannot move a skin."""
        tube, sc, chain, anchor, assy = self._build()
        ancestor = cmds.xform(assy, query=True, worldSpace=True, translation=True)
        result = SkinUtils.flatten_influences(frames=self.FRAMES)
        root = next(iter(result))
        cmds.currentTime(1)
        at = cmds.xform(root, query=True, worldSpace=True, translation=True)
        self.assertLess(
            max(abs(a - b) for a, b in zip(at, ancestor)),
            1e-3,
            f"the root must ride its ancestor's origin: {at} vs {ancestor}",
        )

    def test_result_records_the_hierarchy_the_flatten_removed(self):
        """Each influence maps to the parent it had BEFORE the flatten -- the only
        record of a chain the carriers need to size their bones (a flat skeleton is
        drawn at the root's spread: 2.49 m bones on 0.89 cm production joints)."""
        tube, sc, chain, anchor, assy = self._build()
        result = SkinUtils.flatten_influences(frames=self.FRAMES)
        placed = next(iter(result.values()))

        self.assertEqual(len(placed), 5)
        short = {p.rsplit("|", 1)[-1]: q.rsplit("|", 1)[-1] for p, q in placed.items()}
        self.assertEqual(
            short,
            {
                "fi_jnt1": "fi_RIG",
                "fi_jnt2": "fi_jnt1",
                "fi_jnt3": "fi_jnt2",
                "fi_jnt4": "fi_jnt3",
                "fi_anchor": "fi_PLUG",
            },
        )
        # The parents are the PRE-flatten paths: every influence sits under the new
        # root now, so a recorded path is stale by design and only its leaf is used.
        for joint, parent in placed.items():
            self.assertNotEqual(joint.rsplit("|", 1)[0], parent)
        # What the templates derive from it: the pairs where BOTH ends are
        # influences -- the bones an armature actually has.
        names = set(short)
        bones = {j: p for j, p in short.items() if p in names}
        self.assertEqual(
            bones, {"fi_jnt2": "fi_jnt1", "fi_jnt3": "fi_jnt2", "fi_jnt4": "fi_jnt3"}
        )

    def test_world_root_lives_in_a_new_top_level_group(self):
        """``root_parent="world"``: the root joint under a fresh top-level group,
        every influence's local now ITS world matrix, worlds still kept."""
        tube, sc, chain, anchor, assy = self._build()
        influences = SkinUtils.get_influences(sc, long_names=True)
        before = self._worlds(influences)
        result = SkinUtils.flatten_influences(frames=self.FRAMES, root_parent="world")
        root, placed = next(iter(result.items()))
        group = cmds.listRelatives(root, parent=True, fullPath=True)[0]
        self.assertEqual(group.count("|"), 1, f"group is top-level: {group}")
        self.assertTrue(group.endswith("fi_tube_skeleton_GRP"), group)
        self.assertEqual(len(placed), 5)
        after = self._worlds(placed)
        worst = 0.0
        for frame in self.FRAMES:
            cmds.currentTime(frame)
            for uuid, matrix in before[frame].items():
                worst = max(
                    worst, max(abs(a - b) for a, b in zip(matrix, after[frame][uuid]))
                )
                node = cmds.ls(uuid, long=True)[0]
                # Local relative to the ROOT, which sits with the skin rather than
                # on the group's origin -- what matters for the FBX route is that
                # the armature node Blender makes of that GROUP is top level.
                local = cmds.getAttr(f"{node}.matrix")
                self.assertTrue(
                    _axes_orthogonal(local),
                    f"{node} local shears at frame {frame}",
                )
        self.assertLess(worst, 1e-4, f"world matrices drifted by {worst}")
        with self.assertRaises(ValueError):
            SkinUtils.flatten_influences(frames=self.FRAMES, root_parent="elsewhere")

    def test_the_exporter_now_writes_the_skin(self):
        """The contract: mayaUSDExport writes NO skin for a two-root skinCluster
        (control, the production defect) and a dual-quaternion UsdSkel binding
        after the flatten."""
        from mayatk.env_utils.usd import UsdUtils

        tube, sc, chain, anchor, assy = self._build()
        UsdUtils.load_plugin()
        from pxr import Usd

        def skin_of(path):
            stage = Usd.Stage.Open(path)
            prim = next(p for p in stage.Traverse() if p.GetName() == "fi_tube")
            indices = prim.GetAttribute("primvars:skel:jointIndices")
            method = prim.GetAttribute("primvars:skel:skinningMethod")
            return (
                bool(indices and indices.HasAuthoredValue()),
                method.Get() if method and method.HasAuthoredValue() else None,
                prim.GetAttribute("points").GetNumTimeSamples(),
            )

        def export(name):
            path = self.temp_path(name).replace("\\", "/")
            cmds.mayaUSDExport(
                file=path,
                exportSkels="auto",
                exportSkin="auto",
                shadingMode="none",
                mergeTransformAndShape=True,
                frameRange=(1, 3),
            )
            return path

        control = skin_of(export("flatten_influences_control.usda"))
        self.assertEqual(
            control[0], False, f"control: two roots -> no skin written ({control})"
        )
        result = SkinUtils.flatten_influences(frames=self.FRAMES)
        path = export("flatten_influences_flat.usda")
        skinned, method, point_samples = skin_of(path)
        self.assertTrue(skinned, "the skin is written once its influences share a root")
        self.assertEqual(method, "dualQuaternion")
        self.assertLessEqual(point_samples, 1, "rest points, not a per-frame bake")

        # The skeleton's joint list must MATCH the skinned mesh's, so no consumer
        # has to remap. Making the synthetic root a zero-weight influence (to give it
        # a bind, since mayaUsd derives bindTransforms from bindPreMatrix and a joint
        # that influences nothing goes out as IDENTITY) is exactly deformation-neutral
        # in Maya -- measured 0.000000 cm across 7 production looms -- but mayaUsd
        # PRUNES the zero-weight joint from the mesh's `skel:joints` while keeping it
        # in the skeleton's, leaving a payload whose two joint orders differ by one.
        # The pulled scene missed Maya by 380 mm. The root is placed on the importer
        # side instead, where a bone nothing is weighted to cannot move a skin.
        from pxr import UsdSkel

        stage = Usd.Stage.Open(path)
        skel = next(
            UsdSkel.Skeleton(p) for p in stage.Traverse() if UsdSkel.Skeleton(p)
        )
        joints = [str(j) for j in skel.GetJointsAttr().Get()]
        bound = next(
            UsdSkel.BindingAPI(p)
            for p in stage.Traverse()
            if UsdSkel.BindingAPI(p) and UsdSkel.BindingAPI(p).GetJointIndicesAttr()
        )
        mesh_joints = [str(j) for j in (bound.GetJointsAttr().Get() or joints)]
        root = next(iter(result)).rsplit("|", 1)[-1]
        # The skeleton carries the root; the MESH binds only what influences it, so
        # the two orders differ by exactly that one joint and a consumer must remap.
        # Pinned because it decides where the root may be placed: it reaches the
        # carrier with no bind at all (IDENTITY), so Maya must leave it where its
        # SkelRoot is and the importer must be the one to move its bone.
        self.assertEqual(joints[0], root)
        self.assertNotIn(root, mesh_joints)
        self.assertEqual(joints[1:], mesh_joints, "no reordering, only the root")
