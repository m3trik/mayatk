import math
import unittest

import maya.cmds as cmds

from mayatk.rig_utils.tube_rig import TubeRig, TubePath


def _make_tube(axis=(1, 0, 0), h=10.0, sy=10, sx=12):
    tube = cmds.polyCylinder(r=1, h=h, sy=sy, sx=sx, ax=axis)[0]
    cmds.makeIdentity(tube, apply=True, t=1, r=1, s=1, n=0, pn=1)
    return tube


def _ws(node):
    return cmds.xform(str(node), q=True, ws=True, t=True)


def _all_vertex_positions(mesh):
    flat = cmds.xform(f"{mesh}.vtx[*]", q=True, ws=True, t=True) or []
    return [flat[i : i + 3] for i in range(0, len(flat), 3)]


class TestTubeRigBuild(unittest.TestCase):
    """Functional coverage: joint placement, rebuilds, twist, and stretch.

    Regression suite for the 2026-07-09 audit — every test here failed
    against the post-pymel-migration implementation before being fixed.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_rebuild_same_mesh(self):
        """Rebuilding on an already-rigged mesh must tear down and succeed.

        Regression: joint names collided with the first build
        (``No object matches name: |<rig>_jnt_1``) and the re-bind failed.
        """
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Rebuild")
        rig.build(strategy="spline", num_joints=-1)
        n_joints_first = len(cmds.ls(type="joint"))

        rig.build(strategy="spline", num_joints=-1)  # must not raise
        self.assertEqual(len(cmds.ls(type="joint")), n_joints_first)
        # Exactly one skinCluster on the mesh (the ik curve has its own).
        mesh_skins = cmds.ls(cmds.listHistory(tube) or [], type="skinCluster")
        self.assertEqual(len(mesh_skins), 1)

    def test_spline_explicit_count_covers_ends(self):
        """Regression: interior-only centerline sampling left ~36% of the
        tube unrigged when an explicit joint count was requested."""
        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name="SplineN")
        rig.build(strategy="spline", num_joints=10)
        xs = sorted([_ws(rig.bundle.joints[0])[0], _ws(rig.bundle.joints[-1])[0]])
        self.assertLess(abs(xs[0] - (-5.0)), 0.35, f"start joint at x={xs[0]}")
        self.assertLess(abs(xs[1] - 5.0), 0.35, f"end joint at x={xs[1]}")

    def test_anchor_joints_at_tube_ends(self):
        """Regression: anchor joints/controls landed at 1/3 and 2/3 of the
        tube (interior samples treated as tube ends)."""
        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name="AnchorEnds")
        rig.build(strategy="anchor")
        xs = sorted(_ws(j)[0] for j in rig.bundle.joints)
        self.assertLess(abs(xs[0] - (-5.0)), 0.25, f"start joint at x={xs[0]}")
        self.assertLess(abs(xs[1] - 5.0), 0.25, f"end joint at x={xs[1]}")

    def test_anchor_stretch_along_tube_axis(self):
        """Regression: anchor joints were never oriented down the tube, so
        the distance-driven ``scaleX`` stretched in world X regardless of
        tube direction — a Y-tube bulged sideways when stretched."""
        tube = _make_tube(axis=(0, 1, 0))  # spans y in [-5, 5]
        rig = TubeRig(tube, rig_name="AnchorStretch")
        rig.build(strategy="anchor")

        start_ctrl, end_ctrl = (str(c) for c in rig.bundle.controls)
        if _ws(start_ctrl)[1] > _ws(end_ctrl)[1]:
            start_ctrl, end_ctrl = end_ctrl, start_ctrl

        before = _all_vertex_positions(tube)
        pos = _ws(end_ctrl)
        cmds.xform(end_ctrl, ws=True, t=(pos[0], pos[1] + 3.0, pos[2]))
        cmds.refresh()
        after = _all_vertex_positions(tube)

        max_dx = max(abs(a[0] - b[0]) for a, b in zip(after, before))
        top = max(range(len(before)), key=lambda i: before[i][1])
        dy_top = after[top][1] - before[top][1]

        self.assertLess(max_dx, 0.1, f"stretch leaked into world X (dx={max_dx:.3f})")
        self.assertGreater(dy_top, 2.0, f"top of tube did not follow (dy={dy_top:.3f})")

    def test_spline_twist_follows_start_control(self):
        """Regression: the twist up-locators were only point-constrained and
        never rotated, so ``dWorldUpType=4`` read static matrices — rotating
        a control about the tube axis did nothing."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Twist")
        rig.build(strategy="spline", num_joints=-1, enable_twist=True)

        vtx = f"{tube}.vtx[60]"  # mid-tube surface vertex
        before = cmds.pointPosition(vtx, world=True)
        cmds.setAttr(f"{rig.bundle.controls[0]}.rotateX", 90)
        cmds.refresh()
        after = cmds.pointPosition(vtx, world=True)
        self.assertGreater(
            math.dist(before, after), 0.2, "start-control twist had no effect"
        )

    def test_for_node_finds_rig_from_joint(self):
        """Joint/control-based lookup must resolve to the owning rig (b002 /
        b004 select joints, not the mesh)."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Lookup")
        rig.build(strategy="spline", num_joints=-1)
        self.assertIs(TubeRig.for_node(rig.bundle.joints[-1]), rig)
        self.assertIs(TubeRig.for_node(rig.bundle.controls[0]), rig)
        self.assertIs(TubeRig.for_node(tube), rig)

    def test_constrain_end_with_falloff_weights(self):
        """Regression: strategies never recorded the mesh skinCluster, so
        anchor falloff weighting silently no-oped."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Falloff")
        rig.build(strategy="spline", num_joints=-1)

        anchor = cmds.spaceLocator(name="Falloff_anchor_LOC")[0]
        cmds.xform(anchor, ws=True, t=(5.5, 0, 0))
        anchor_joint = rig.constrain_end_with_falloff(
            rig.bundle.joints, anchor, falloff=3.0, joint_index=-1
        )
        self.assertIsNotNone(anchor_joint)

        skin = cmds.ls(cmds.listHistory(tube) or [], type="skinCluster")
        self.assertTrue(skin, "mesh lost its skinCluster")
        influences = cmds.skinCluster(skin[0], q=True, influence=True) or []
        self.assertIn(str(anchor_joint).split("|")[-1], [i.split("|")[-1] for i in influences])

        end_vtx = max(
            range(cmds.polyEvaluate(tube, vertex=True)),
            key=lambda i: cmds.pointPosition(f"{tube}.vtx[{i}]", world=True)[0],
        )
        w = cmds.skinPercent(
            skin[0], f"{tube}.vtx[{end_vtx}]", q=True, transform=str(anchor_joint)
        )
        self.assertGreater(w, 0.05, "no falloff weight applied at the tube end")


def _min_surface_distance(mesh, points):
    """Smallest distance from any point to the mesh surface (interiority)."""
    shape = (cmds.listRelatives(mesh, shapes=True) or [None])[0]
    cpom = cmds.createNode("closestPointOnMesh")
    cmds.connectAttr(f"{shape}.outMesh", f"{cpom}.inMesh")
    cmds.connectAttr(f"{shape}.worldMatrix[0]", f"{cpom}.inputMatrix")
    try:
        dmin = float("inf")
        for p in points:
            cmds.setAttr(
                f"{cpom}.inPosition", p[0], p[1], p[2], type="double3"
            )
            hit = cmds.getAttr(f"{cpom}.position")[0]
            dmin = min(dmin, math.dist((p[0], p[1], p[2]), hit))
        return dmin
    finally:
        cmds.delete(cpom)


class TestJointChainRobustness(unittest.TestCase):
    """The standalone 'Create Joints' step (b001) must survive reruns, stale
    caches, and arbitrary tube geometry — and produce oriented joints."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_rig_group_recreated_after_manual_delete(self):
        """Regression: after an undo or manual delete, the cached rig-group
        path went stale and the property returned None → cmds.parent crashed
        with 'No object matches name: None'."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Stale")
        centerline, n = TubePath.get_centerline(tube, num_joints=-1)
        rig.generate_joint_chain(centerline, num_joints=n)

        cmds.delete("Stale_GRP")  # simulates undo of a build / manual cleanup
        joints = rig.generate_joint_chain(centerline, num_joints=n)  # must not raise
        self.assertTrue(cmds.objExists("Stale_GRP"))
        self.assertTrue(all(cmds.objExists(str(j)) for j in joints))

    def test_create_joints_twice_replaces_chain(self):
        """Regression: rerunning 'Create Joints' collided on joint names."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Twice")
        centerline, n = TubePath.get_centerline(tube, num_joints=-1)
        rig.generate_joint_chain(centerline, num_joints=n)
        count_first = len(cmds.ls(type="joint"))

        rig.generate_joint_chain(centerline, num_joints=n)  # must not raise
        self.assertEqual(len(cmds.ls(type="joint")), count_first)

    def test_build_with_stray_same_named_joints(self):
        """Regression: leftover joints sharing the rig's naming prefix
        elsewhere in the scene (older crashed sessions, duplicates) made the
        short names ambiguous — 'More than one object matches name'."""
        tube = _make_tube()
        stray_grp = cmds.group(empty=True, name="OldStuff_GRP")
        cmds.select(clear=True)
        j1 = cmds.joint(name="Debris_jnt_1", p=(0, 20, 0))
        cmds.joint(name="Debris_jnt_2", p=(1, 20, 0))
        cmds.parent(j1, stray_grp)

        rig = TubeRig(tube, rig_name="Debris")
        rig.build(strategy="spline", num_joints=-1)  # must not raise
        self.assertTrue(rig.bundle.joints)
        self.assertTrue(all(cmds.objExists(str(j)) for j in rig.bundle.joints))

    def test_anchor_build_with_stray_same_named_joints(self):
        """Anchor strategy names its joints directly (not via
        generate_joint_chain) — debris with those names must not break it."""
        tube = _make_tube()
        stray_grp = cmds.group(empty=True, name="OldAnchor_GRP")
        cmds.select(clear=True)
        j = cmds.joint(name="Adeb_start_jnt", p=(0, 20, 0))
        cmds.parent(j, stray_grp)
        cmds.select(clear=True)
        j = cmds.joint(name="Adeb_end_jnt", p=(1, 20, 0))
        cmds.parent(j, stray_grp)

        rig = TubeRig(tube, rig_name="Adeb")
        rig.build(strategy="anchor")  # must not raise
        self.assertTrue(all(cmds.objExists(str(j)) for j in rig.bundle.joints))

    def test_joint_chain_auto_oriented(self):
        """Regression: a chain created outside build() was left world-aligned;
        each joint's X axis must aim at its child."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Orient")
        centerline, n = TubePath.get_centerline(tube, num_joints=-1)
        joints = rig.generate_joint_chain(centerline, num_joints=n)

        for jnt, child in zip(joints, joints[1:]):
            m = cmds.xform(str(jnt), q=True, ws=True, matrix=True)
            x_axis = m[0:3]
            p0, p1 = _ws(jnt), _ws(child)
            to_child = [p1[i] - p0[i] for i in range(3)]
            length = math.sqrt(sum(v * v for v in to_child))
            dot = sum(x_axis[i] * to_child[i] / length for i in range(3))
            self.assertGreater(
                dot, 0.99, f"{jnt} X axis does not aim at its child (dot={dot:.3f})"
            )
        # End joint has no child to aim at — its orient must be zeroed.
        end_orient = cmds.getAttr(f"{joints[-1]}.jointOrient")[0]
        self.assertTrue(all(abs(v) < 1e-4 for v in end_orient))

    def test_centerline_on_bent_tube(self):
        """Accuracy on curved geometry: centerline points must sit deep
        inside the tube (≈ on-axis) with roughly uniform spacing."""
        tube = cmds.polyCylinder(r=1, h=10, sy=24, sx=12, ax=(1, 0, 0))[0]
        _, handle = cmds.nonLinear(tube, type="bend", lowBound=-1, highBound=1, curvature=90)
        cmds.setAttr(f"{handle}.rotateZ", 90)  # bend along the tube's length
        # Bounds are handle-local: scale the handle to span the whole tube,
        # giving a smooth arc rather than a sharp kink in the middle.
        cmds.xform(handle, s=(5, 5, 5))
        cmds.delete(tube, ch=True)  # bake the bend

        pts, n = TubePath.get_centerline(tube, num_joints=-1)
        self.assertGreaterEqual(n, 8)
        # Near the tube ends the closest surface is the cap plane, so the
        # surface-distance metric only reflects *radial* accuracy for points
        # well away from both ends.
        def _d(a, b):
            return math.dist((a[0], a[1], a[2]), (b[0], b[1], b[2]))

        interior = [p for p in pts if _d(p, pts[0]) > 1.2 and _d(p, pts[-1]) > 1.2]
        self.assertGreater(len(interior), 5)
        self.assertGreater(
            _min_surface_distance(tube, interior),
            0.6,
            "centerline points hug the surface instead of the axis",
        )
        spacings = [
            math.dist(
                (pts[i][0], pts[i][1], pts[i][2]),
                (pts[i + 1][0], pts[i + 1][1], pts[i + 1][2]),
            )
            for i in range(len(pts) - 1)
        ]
        self.assertLess(
            max(spacings) / max(min(spacings), 1e-6),
            3.0,
            f"uneven spacing suggests mis-ordered path: {spacings}",
        )

    def test_open_tube_ends(self):
        """Capless tubes: border rings are real loops, ends must be covered."""
        tube = _make_tube()  # spans x in [-5, 5]
        n_faces = cmds.polyEvaluate(tube, face=True)
        # polyCylinder face order: sides first, then the two cap fans.
        cmds.delete(f"{tube}.f[{12 * 10}:{n_faces - 1}]")
        pts, _ = TubePath.get_centerline(tube, num_joints=-1)
        xs = sorted(p[0] for p in pts)
        self.assertLess(abs(xs[0] - (-5.0)), 0.3, f"start at x={xs[0]}")
        self.assertLess(abs(xs[-1] - 5.0), 0.3, f"end at x={xs[-1]}")


class TestGetCenterlineUsingEdges(unittest.TestCase):
    """``get_centerline_using_edges`` must return points on the tube's
    central axis — not the raw edge vertices, which lie on the surface.

    Also retains the 2026-05-07 regression: plain ``[x, y, z]`` lists from
    ``cmds.pointPosition`` fed into ``ptk.Polyline.order_points`` raised
    ``TypeError`` with the default distance metric.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        self.tube = _make_tube()

    def test_returns_ordered_points(self):
        edges = [f"{self.tube}.e[{i}]" for i in (0, 12, 24, 36, 48)]
        result = TubePath.get_centerline_using_edges(edges)
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 2)
        for p in result:
            self.assertEqual(len(p), 3)

    def test_points_lie_on_centerline(self):
        """Regression: returned points sat on the tube wall (radial distance
        == tube radius) instead of the central axis."""
        # One edge per cross-section band, walking along the tube.
        ring = cmds.polySelect(self.tube, q=True, edgeRing=0) or []
        edges = [f"{self.tube}.e[{i}]" for i in ring]
        pts = TubePath.get_centerline_using_edges(edges)
        self.assertGreaterEqual(len(pts), 5)
        max_r = max(math.hypot(p[1], p[2]) for p in pts)
        self.assertLess(max_r, 0.2, f"points off-axis by {max_r:.3f} (radius=1)")


class TestTubeRigCleanExport(unittest.TestCase):
    """Hierarchy cleanliness + double-transform guards (pre-existing suite,
    folded in from test_tube_rig_cleanliness.py)."""

    def setUp(self):
        cmds.file(new=True, force=True)
        self.tube = cmds.polyCylinder(r=1, h=10, sy=10, sx=12, ax=(1, 0, 0))[0]
        cmds.rotate(0, 45, 45, self.tube)
        cmds.makeIdentity(self.tube, apply=True, t=1, r=1, s=1, n=0, pn=1)

    def _orphan_groups(self, rig):
        return [
            n
            for n in (cmds.ls(assemblies=True) or [])
            if n.endswith("_GRP") and n != rig.rig_group
        ]

    def _assert_ctrl_under_rig(self, rig, ctrl_name, require_ctrl_grp_suffix=False):
        ctrl_grp = (cmds.listRelatives(ctrl_name, parent=True) or [None])[0]
        self.assertIsNotNone(
            ctrl_grp, f"Control {ctrl_name} has no parent offset group"
        )
        if require_ctrl_grp_suffix:
            self.assertTrue(
                ctrl_grp.endswith("_CTRL_GRP"),
                f"Control parent is {ctrl_grp}, expected *_CTRL_GRP",
            )
        self.assertEqual(
            (cmds.listRelatives(ctrl_grp, parent=True) or [None])[0],
            rig.rig_group,
            "Control Group should be parented to Rig Group",
        )

    def test_spline_mode_cleanliness(self):
        """Verify no empty groups are left at root after Spline rig build."""
        rig = TubeRig(self.tube, rig_name="SplineTest")
        rig.build(strategy="spline")

        orphans = self._orphan_groups(rig)
        self.assertEqual(orphans, [], f"Found orphaned groups at root: {orphans}")
        self._assert_ctrl_under_rig(
            rig, "SplineTest_start_CTRL", require_ctrl_grp_suffix=True
        )

    def test_anchor_mode_cleanliness(self):
        """Verify no empty groups are left at root after Anchor rig build."""
        rig = TubeRig(self.tube, rig_name="AnchorTest")
        rig.build(strategy="anchor")

        orphans = self._orphan_groups(rig)
        self.assertEqual(orphans, [], f"Found orphaned groups at root: {orphans}")
        self._assert_ctrl_under_rig(rig, "AnchorTest_start_CTRL")

    def _scale_rig_group(self, rig, factor=2.0):
        for axis in "XYZ":
            cmds.setAttr(f"{rig.rig_group}.scale{axis}", factor)

    def test_anchor_scale_rig_group(self):
        """Test if scaling the rig group causes double transforms on Anchor joints."""
        rig = TubeRig(self.tube, rig_name="ScaleTest")
        rig.build(strategy="anchor")
        self._scale_rig_group(rig, 2.0)

        ws = cmds.xform("ScaleTest_start_jnt", q=True, ws=True, s=True)

        self.assertAlmostEqual(
            ws[0],
            2.0,
            places=3,
            msg="Joint X scale incorrect (Double Transform scaling failure)",
        )
        self.assertAlmostEqual(ws[1], 2.0, places=3, msg="Joint Y scale incorrect")

    def test_spline_scale_rig_group(self):
        """Test if scaling the rig group causes double transforms on Spline joints."""
        rig = TubeRig(self.tube, rig_name="SplineScaleTest")
        rig.build(strategy="spline")
        self._scale_rig_group(rig, 2.0)

        joints = rig.bundle.joints
        jnt = joints[len(joints) // 2]
        ws = cmds.xform(str(jnt), q=True, ws=True, s=True)

        self.assertAlmostEqual(
            ws[0], 2.0, places=3, msg="Spline Joint X scale incorrect"
        )


if __name__ == "__main__":
    unittest.main()
