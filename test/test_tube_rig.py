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

    def test_build_nodes_carry_rig_prefix(self):
        """Regression: the driver-curve bind left default-named debris
        ('skinCluster1' + 'bindPose1/2') that name-based cleanup sweeps and
        multi-rig scenes can't attribute — every DG node a build creates
        must carry the rig prefix."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Prefixed")
        rig.build(strategy="spline", num_joints=-1)
        self.assertEqual(cmds.ls("skinCluster*", type="skinCluster") or [], [])
        self.assertEqual(cmds.ls("bindPose*", type="dagPose") or [], [])

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

    def test_triangulated_tube_fallback_covers_ends(self):
        """Regression: triangulated meshes (imported/booleaned geo) have no
        quad loops, so the centerline silently fell back to surface-normal
        sampling whose end estimates were pulled ~1 radius inward by the cap
        planes — ~20% of the tube was left unrigged."""
        tube = _make_tube()  # spans x in [-5, 5]
        cmds.polyTriangulate(tube)
        cmds.delete(tube, ch=True)
        pts, n = TubePath.get_centerline(tube, num_joints=-1)
        self.assertGreaterEqual(len(pts), 2)
        xs = sorted(p[0] for p in pts)
        self.assertLess(abs(xs[0] - (-5.0)), 0.35, f"start at x={xs[0]}")
        self.assertLess(abs(xs[-1] - 5.0), 0.35, f"end at x={xs[-1]}")

    def test_for_node_after_create_joints_only(self):
        """The step workflow (b001 'Create Joints' → b002 'Create Controls')
        never calls build(), and only build() registered the rig group — so
        looking the rig up from a selected joint always missed and b002
        silently spun up a second rig with a mangled name."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="StepWise")
        centerline, n = TubePath.get_centerline(tube, num_joints=-1)
        joints = rig.generate_joint_chain(centerline, num_joints=n)
        self.assertIs(TubeRig.for_node(joints[-1]), rig)

    def test_group_selection_resolves_to_tube(self):
        """Regression (live 2026-07-09): selecting the GROUP containing the
        tube (common outliner pick) crashed ``get_centerline`` —
        ``polyListComponentConversion`` expands a group's descendants to
        edges, but ``polySelect`` drops non-mesh transforms, dying with
        'This command requires at least 1 argument...; found 0'."""
        tube = _make_tube()  # spans x in [-5, 5]
        grp = cmds.group(tube, name="Hose_GRP")
        pts, _ = TubePath.get_centerline(grp, num_joints=-1)
        xs = sorted(p[0] for p in pts)
        self.assertLess(abs(xs[0] - (-5.0)), 0.3, f"start at x={xs[0]}")
        self.assertLess(abs(xs[-1] - 5.0), 0.3, f"end at x={xs[-1]}")
        # Full build from the group selection (b000 path) must also work.
        rig = TubeRig(grp, rig_name="GroupSel")
        rig.build(strategy="spline", num_joints=-1)
        self.assertTrue(rig.bundle.joints)
        self.assertTrue(
            cmds.ls(cmds.listHistory(tube) or [], type="skinCluster"),
            "mesh was not skinned when the rig was built from its group",
        )

    def test_multi_shape_transform_uses_first_shape(self):
        """Same live crash, second trigger: a transform carrying a leftover
        second mesh shape made ``polySelect``'s shape resolution ambiguous."""
        tube = _make_tube()  # spans x in [-5, 5]
        extra = cmds.polyCylinder(r=1, h=4, sy=2, sx=8, ax=(1, 0, 0))[0]
        extra_shape = cmds.listRelatives(extra, shapes=True)[0]
        cmds.parent(extra_shape, tube, shape=True, relative=True)
        cmds.delete(extra)
        pts, _ = TubePath.get_centerline(tube, num_joints=-1)
        xs = sorted(p[0] for p in pts)
        self.assertLess(abs(xs[0] - (-5.0)), 0.3, f"start at x={xs[0]}")
        self.assertLess(abs(xs[-1] - 5.0), 0.3, f"end at x={xs[-1]}")

    def test_meshless_input_raises_cleanly(self):
        """An empty group used to reach the sampler fallback and die with
        the cryptic \"The source attribute 'None.outMesh' cannot be
        found\" — it must raise a clear ValueError instead."""
        grp = cmds.group(empty=True, name="NoMesh_GRP")
        with self.assertRaisesRegex(ValueError, "[Nn]o polygon mesh"):
            TubePath.get_centerline(grp, num_joints=-1)

    def test_rig_name_with_illegal_characters(self):
        """Regression: user-typed rig names flow verbatim from the UI
        (txt000) into ``cmds.ls`` patterns and node names. 'hose-01' /
        'my rig' raised RuntimeError in the stale-joint sweep before any
        joint was created; names Maya auto-sanitizes on createNode (leading
        digit, '*') no longer matched the sweep pattern, so reruns
        accumulated duplicate chains."""
        tube = _make_tube()
        centerline, n = TubePath.get_centerline(tube, num_joints=-1)
        for bad_name in ("hose-01", "my rig", "hose*", "1hose"):
            rig = TubeRig(tube, rig_name=bad_name)
            joints = rig.generate_joint_chain(centerline, num_joints=n)
            self.assertEqual(len(joints), n, f"rig_name={bad_name!r}")
            self.assertTrue(all(cmds.objExists(str(j)) for j in joints))
            count_first = len(cmds.ls(type="joint"))
            rig.generate_joint_chain(centerline, num_joints=n)  # rerun replaces
            self.assertEqual(
                len(cmds.ls(type="joint")),
                count_first,
                f"rerun accumulated joints for rig_name={bad_name!r}",
            )


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


def _make_tube_longitudinal_first_edge(
    stations=8,
    sides=6,
    length=14.0,
    radius=1.0,
    name="longEdgeTube",
    capped=True,
    end_tilt=0.0,
):
    """A tube whose edge 0 is LONGITUDINAL.

    Maya numbers edges in face-creation order, so authoring face 0 with its
    first edge running down the tube guarantees ``e[0]`` is longitudinal —
    the seed orientation that transposes edge-loop traversal (user-modeled
    pipes commonly have this layout; polyCylinder does not).

    Parameters:
        capped (bool): False leaves the ends as open boundary rings.
        end_tilt (float): Shears each end station along X by
            ``end_tilt * radius * cos(a)`` — an angled opening whose rim
            extends past the ring centroid's plane (user-modeled pipe ends
            are commonly cut at an angle).
    """
    import maya.api.OpenMaya as om

    points = []
    for s in range(stations):
        x = -length / 2 + length * s / (stations - 1)
        for k in range(sides):
            a = 2.0 * math.pi * k / sides
            tilt = 0.0
            if end_tilt and s in (0, stations - 1):
                tilt = end_tilt * radius * math.cos(a) * (1 if s else -1)
            points.append(
                om.MPoint(x + tilt, radius * math.cos(a), radius * math.sin(a))
            )

    def vid(s, k):
        return s * sides + (k % sides)

    counts, connects = [], []
    for s in range(stations - 1):
        for k in range(sides):
            counts.append(4)
            # First edge vid(s,k) -> vid(s+1,k) is longitudinal.
            connects += [vid(s, k), vid(s + 1, k), vid(s + 1, k + 1), vid(s, k + 1)]
    if capped:
        cap_start = len(points)
        points.append(om.MPoint(-length / 2, 0, 0))
        cap_end = len(points)
        points.append(om.MPoint(length / 2, 0, 0))
        for k in range(sides):  # cap fans
            counts.append(3)
            connects += [cap_start, vid(0, k + 1), vid(0, k)]
            counts.append(3)
            connects += [cap_end, vid(stations - 1, k), vid(stations - 1, k + 1)]

    fn = om.MFnMesh()
    transform_obj = fn.create(points, counts, connects)
    transform = om.MFnDagNode(transform_obj).setName(name)
    cmds.sets(f"{transform}.f[*]", forceElement="initialShadingGroup")
    return transform


class TestEdgeLoopOrientation(unittest.TestCase):
    """Regression (2026-07-09, live report): auto-joints clustered in a ring
    near one bend on a user-modeled pipe. ``get_edge_loop_centers`` seeded
    from ``all_edges[0]`` assuming it was circumferential; on meshes where
    e[0] is longitudinal the loop/ring traversal transposes and every
    "cross-section centre" is a longitudinal-strip centroid — a small ring
    of points around the mesh centroid instead of a path down the tube."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_longitudinal_first_edge_tube(self):
        length, stations = 14.0, 8
        tube = _make_tube_longitudinal_first_edge(length=length, stations=stations)

        # The edge-loop path itself must find the cross-section rings — a
        # sampler fallback also spans the tube, so assert the unit directly
        # (closed pentagon/hex rings come back from polySelect with the seed
        # edge repeated; the closure test must not misread that as open).
        centers, count = TubePath.get_edge_loop_centers(tube)
        self.assertGreaterEqual(
            count,
            stations - 1,
            f"edge-loop path found only {count} cross-sections of {stations}",
        )

        pts, resolved = TubePath.get_centerline(tube, num_joints=-1)
        self.assertGreaterEqual(resolved, 4)
        xs = [p[0] for p in pts]
        span = max(xs) - min(xs)
        self.assertGreater(
            span,
            0.8 * length,
            f"centerline collapsed: x-span {span:.2f} of {length} "
            f"(longitudinal loops mistaken for cross-sections)",
        )
        for p in pts:
            r = math.hypot(p[1], p[2])
            self.assertLess(
                r, 0.35, f"centerline point off-axis by {r:.3f} (radius=1)"
            )

    def test_open_angled_tube_ends_stay_on_axis(self):
        """Regression (2026-07-09, live report): on an OPEN tube whose ends
        are cut at an angle, ``_complete_cap_ends`` appended a RIM vertex as
        the "end centre" (there is no cap for the past-the-end seed to hit,
        and an angled rim projects beyond the end ring's centroid plane) —
        hooking the end joints off-axis toward an opening vertex."""
        tube = _make_tube_longitudinal_first_edge(
            name="openAngledTube", capped=False, end_tilt=0.8
        )
        pts, _ = TubePath.get_centerline(tube, num_joints=-1)
        self.assertGreaterEqual(len(pts), 4)
        for p in pts:
            r = math.hypot(p[1], p[2])
            self.assertLess(
                r,
                0.35,
                f"end centerline point hooked to the rim: off-axis by {r:.3f} "
                f"(radius=1)",
            )


class TestTubeRigSkinning(unittest.TestCase):
    """Precision skinning wired through SkinUtils (2026-07-09).

    Every strategy now solves analytic arc-length weights along its
    centerline/IK curve (ring-uniform, smooth cubic basis — max 4
    influences) and binds with dual quaternion skinning.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _mesh_skin_cluster(self, mesh):
        clusters = cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster")
        self.assertEqual(len(clusters), 1)
        return clusters[0]

    def _rings_by_x(self, mesh):
        rings = {}
        for i, (x, _, _) in enumerate(_all_vertex_positions(mesh)):
            rings.setdefault(round(x, 3), []).append(i)
        return rings

    def test_spline_build_dqs_parametric(self):
        from mayatk.rig_utils.skinning import SkinUtils

        tube = _make_tube()
        rig = TubeRig(tube, rig_name="SkinSpline")
        rig.build(strategy="spline")

        sc = self._mesh_skin_cluster(tube)
        self.assertEqual(cmds.getAttr(f"{sc}.skinningMethod"), 1, "expected DQS")

        weights, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        for v in range(len(weights) // n):
            row = weights[v * n : (v + 1) * n]
            self.assertAlmostEqual(sum(row), 1.0, places=6)
            # Cubic basis: at most degree + 1 = 4 influences per vertex.
            self.assertLessEqual(len([w for w in row if w > 1e-9]), 4)
        # Ring-uniform: every vertex in a cross-section shares its weights.
        for x, verts in self._rings_by_x(tube).items():
            for i in range(n):
                column = [weights[v * n + i] for v in verts]
                self.assertLess(
                    max(column) - min(column),
                    1e-4,
                    f"ring x={x} influence {i} is not uniform",
                )

    def test_anchor_build_parametric_midpoint(self):
        from mayatk.rig_utils.skinning import SkinUtils

        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name="SkinAnchor")
        rig.build(strategy="anchor")

        sc = self._mesh_skin_cluster(tube)
        weights, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        mid_ring = self._rings_by_x(tube).get(0.0) or []
        self.assertTrue(mid_ring)
        for v in mid_ring:
            row = weights[v * n : (v + 1) * n]
            for w in (row[0], row[-1]):
                self.assertLess(abs(w - 0.5), 0.05, f"midpoint weights {row}")

    def test_fk_build_skinned(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="SkinFK")
        rig.build(strategy="fk")

        sc = self._mesh_skin_cluster(tube)
        self.assertEqual(cmds.getAttr(f"{sc}.skinningMethod"), 1, "expected DQS")


class TestStepOneClickParity(unittest.TestCase):
    """The step-by-step operations (UI Steps 1/2/3) must run the same rig
    methods as the one-click build — same components, same skinning."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def _mesh_skin_cluster(self, mesh):
        clusters = cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster")
        self.assertEqual(len(clusters), 1)
        return clusters[0]

    def test_spline_step_sequence_matches_one_click(self):
        """Step 1 → 2 → 3 assembled by hand must yield the one-click result:
        IK handle + drivers + a DQS parametric bind (max 4 influences)."""
        from mayatk.rig_utils.skinning import SkinUtils

        tube = _make_tube()
        rig = TubeRig(tube, rig_name="StepSpline")

        centerline, n = rig.resolve_centerline(-1)
        joint_radius, size = rig.resolve_sizes(centerline, -1.0)
        joints = rig.generate_joint_chain(centerline, num_joints=n, radius=joint_radius)
        controls, ik_handle, curve = rig.create_spline_controls(
            joints, centerline=centerline, size=size
        )
        sc = rig.bind_joint_chain(tube, joints)

        self.assertTrue(cmds.objExists(str(ik_handle)))
        self.assertTrue(cmds.objExists(str(curve)))
        self.assertEqual(len(controls), 3)
        self.assertEqual(sc, self._mesh_skin_cluster(tube))
        self.assertEqual(cmds.getAttr(f"{sc}.skinningMethod"), 1, "expected DQS")
        weights, influences = SkinUtils.get_weights(sc)
        n_inf = len(influences)
        for v in range(len(weights) // n_inf):
            row = weights[v * n_inf : (v + 1) * n_inf]
            self.assertAlmostEqual(sum(row), 1.0, places=6)
            self.assertLessEqual(
                len([w for w in row if w > 1e-9]), 4, "parametric bind expected"
            )

    def test_anchor_step_sequence(self):
        """Anchor Steps 1+2 (create_anchor_joints/controls) must reproduce the
        strategy's structure: sibling end joints, constrained controls, and a
        working distance-stretch network."""
        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name="StepAnchor")

        centerline, _ = rig.resolve_centerline(2)
        joint_radius, size = rig.resolve_sizes(centerline, -1.0)
        joints = rig.create_anchor_joints(centerline, radius=joint_radius)
        controls = rig.create_anchor_controls(joints, size=size)
        sc = rig.bind_joint_chain(tube, joints)

        self.assertTrue(sc)
        self.assertEqual(len(joints), 2)
        # Siblings, not a chain.
        j2_parents = cmds.listRelatives(joints[1], parent=True, fullPath=True) or []
        self.assertNotIn(str(joints[0]).split("|")[-1], str(j2_parents[0]))
        # Stretch drives the start joint's scaleX.
        self.assertTrue(
            cmds.listConnections(f"{joints[0]}.scaleX", source=True, destination=False)
        )
        # Controls constrain their joints.
        for jnt in joints:
            self.assertTrue(cmds.listRelatives(jnt, type="pointConstraint"))
            self.assertTrue(cmds.listRelatives(jnt, type="orientConstraint"))
        self.assertEqual(len(controls), 2)

    def test_fk_step_sequence(self):
        """FK Step 2 must create one nested control per joint (the old b002
        fallback wrongly built an RP-solver IK instead)."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="StepFK")

        centerline, n = rig.resolve_centerline(5)
        joint_radius, size = rig.resolve_sizes(centerline, -1.0)
        joints = rig.generate_joint_chain(centerline, num_joints=5, radius=joint_radius)
        controls = rig.create_fk_controls(joints, size=size)

        self.assertEqual(len(controls), len(joints))
        self.assertFalse(cmds.ls(type="ikHandle"), "FK must not create IK handles")
        for jnt in joints:
            self.assertTrue(
                cmds.listRelatives(jnt, type="parentConstraint"),
                f"{jnt} is not constrained to its control",
            )

    def test_one_click_reverse(self):
        """The Reverse Direction option must be honored by the one-click
        build (previously only Step 1 respected it)."""
        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name="Rev")
        rig.build(strategy="spline", num_joints=5, reverse=True)
        xs = [_ws(j)[0] for j in rig.bundle.joints]
        self.assertGreater(xs[0], 4.0, f"root joint at x={xs[0]} — not reversed")
        self.assertLess(xs[-1], -4.0, f"end joint at x={xs[-1]} — not reversed")

    def test_anchor_controls_reject_wrong_joint_count(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Reject")
        centerline, n = rig.resolve_centerline(3)
        joints = rig.generate_joint_chain(centerline, num_joints=3, radius=0.5)
        with self.assertRaises(ValueError):
            rig.create_anchor_controls(joints)

    def test_rebind_replaces_skin_cluster(self):
        """Re-running the bind step must replace the existing skinCluster,
        not fail on an already-bound mesh."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Rebind")
        centerline, n = rig.resolve_centerline(-1)
        joints = rig.generate_joint_chain(centerline, num_joints=n, radius=0.5)
        first = rig.bind_joint_chain(tube, joints)
        self.assertTrue(first)
        second = rig.bind_joint_chain(tube, joints)
        self.assertTrue(second)
        self.assertEqual(len(self._mesh_skin_cluster_list(tube)), 1)

    def _mesh_skin_cluster_list(self, mesh):
        return cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster")


class TestNameCollisionSafety(unittest.TestCase):
    """Builds must not fail on short-name ambiguity when nodes sharing the
    rig's names already exist elsewhere (a second rig, debris, a re-run).

    Regression: ``Controls.create`` returned the control's PRE-parent path,
    which no longer resolves once a same-named control exists — every later
    constraint on it raised 'No object matches name'. The control builders
    now re-derive each control's path from its group after reparenting."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def _build(self, strategy, num_joints, **kwargs):
        tube = _make_tube()
        # Pre-seed debris that collides with this rig's control names.
        for nm in ("Dup_start_CTRL", "Dup_1_CTRL", "Dup_mid_CTRL"):
            grp = cmds.group(empty=True, name=f"{nm}_debris_GRP")
            cmds.group(empty=True, name=nm, parent=grp)  # same leaf, different path
        rig = TubeRig(tube, rig_name="Dup")
        rig.build(strategy=strategy, num_joints=num_joints, **kwargs)  # must not raise
        return rig

    def test_spline_build_under_control_name_collision(self):
        # auto_bend exercises the follow-group + auto-bend hierarchy inserts,
        # whose stored control paths must survive restructuring under collision.
        rig = self._build("spline", 6, enable_auto_bend=True)
        for c in rig.bundle.controls:
            self.assertTrue(cmds.objExists(c), f"control {c} missing")
            self.assertTrue(
                cmds.ls(c, long=True), f"control {c} not resolvable"
            )

    def test_fk_build_under_control_name_collision(self):
        rig = self._build("fk", 6)
        # Each FK control must actually constrain its joint (proves the
        # re-derived control path was the real node, not a stale twin).
        for jnt in rig.bundle.joints:
            self.assertTrue(cmds.listRelatives(jnt, type="parentConstraint"))

    def test_anchor_build_under_control_name_collision(self):
        rig = self._build("anchor", 2)
        for jnt in rig.bundle.joints:
            self.assertTrue(cmds.listRelatives(jnt, type="pointConstraint"))


class TestEndConstraints(unittest.TestCase):
    """'Add End Constraints' (b004) regression — 2026-07-10 live report: the
    utility did nothing (or errored) regardless of anchor selection order.

    Chain joints are the wrong constraint target on every built rig
    (probe-verified): spline joints are IK-driven so a direct constraint is
    silently overridden, anchor joints already carry control constraints
    ('Object is already connected'), and FK joints blend 50/50 against their
    control's constraint. The anchor constraint must route through the rig's
    end controls."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def _rigged_tube(self, strategy, num_joints):
        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name=f"EC{strategy}")
        rig.build(strategy=strategy, num_joints=num_joints)
        a1 = cmds.polyCylinder(r=0.4, h=0.8)[0]
        cmds.xform(a1, ws=True, t=(-5, 0, 0))
        a2 = cmds.polyCylinder(r=0.4, h=0.8)[0]
        cmds.xform(a2, ws=True, t=(5, 0, 0))
        return tube, rig, a1, a2

    def _constrain_and_move(self, tube, rig, a1, a2):
        """Constrain both ends, move the +X anchor up 3, return the vertical
        motion of the tube's extreme end and start vertices."""
        joints = [str(j) for j in rig.bundle.joints]
        self.assertIsNotNone(
            rig.constrain_end_with_falloff(joints, a1, falloff=2.0, joint_index=0)
        )
        self.assertIsNotNone(
            rig.constrain_end_with_falloff(joints, a2, falloff=2.0, joint_index=-1)
        )
        before = _all_vertex_positions(tube)
        cmds.xform(a2, ws=True, t=(5, 3, 0))
        cmds.refresh()
        after = _all_vertex_positions(tube)
        end_i = max(range(len(before)), key=lambda i: before[i][0])
        start_i = min(range(len(before)), key=lambda i: before[i][0])
        return (
            after[end_i][1] - before[end_i][1],
            after[start_i][1] - before[start_i][1],
        )

    def test_spline_end_follows_anchor(self):
        tube, rig, a1, a2 = self._rigged_tube("spline", -1)
        dy_end, dy_start = self._constrain_and_move(tube, rig, a1, a2)
        self.assertGreater(
            dy_end, 2.5, f"tube end did not follow the anchor (dy={dy_end:.2f})"
        )
        self.assertLess(abs(dy_start), 0.3, "opposite end must stay pinned")
        # The whole end assembly moved coherently — the end control follows.
        ctrl_y = cmds.xform(str(rig.bundle.controls[-1]), q=True, ws=True, t=True)[1]
        self.assertGreater(ctrl_y, 2.5, "end control did not follow the anchor")

    def test_anchor_end_follows_anchor(self):
        # Pre-fix this RAISED ('Object is already connected').
        tube, rig, a1, a2 = self._rigged_tube("anchor", 2)
        dy_end, dy_start = self._constrain_and_move(tube, rig, a1, a2)
        self.assertGreater(dy_end, 2.5, f"tube end dy={dy_end:.2f}")
        self.assertLess(abs(dy_start), 0.3)

    def test_fk_end_follows_anchor_fully(self):
        # Pre-fix the joint got exactly HALF the motion (constraint fight).
        tube, rig, a1, a2 = self._rigged_tube("fk", 8)
        dy_end, dy_start = self._constrain_and_move(tube, rig, a1, a2)
        self.assertGreater(
            dy_end, 2.5, f"FK end blended 50/50 against its control (dy={dy_end:.2f})"
        )
        self.assertLess(abs(dy_start), 0.3)


class TestHoseNaturalBehavior(unittest.TestCase):
    """Constrained-hose behavior (2026-07-10 live report: 'the tube still
    moves away from its end constraints').

    Root cause measured: the mid control stayed nailed to its build position
    (carrying both anchors +5 moved the tube middle only 1.18), and
    compression accordioned dead-straight. Intermediate controls now ride
    between the end controls via point-constrained follow groups, and
    auto-bend defaults on (attr dv 0.5) so compression bows the hose."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def _constrained_hose(self, **build_kwargs):
        tube = _make_tube()  # spans x in [-5, 5]
        rig = TubeRig(tube, rig_name="Hose")
        rig.build(strategy="spline", num_joints=-1, **build_kwargs)
        a1 = cmds.polyCylinder(r=0.4, h=0.8)[0]
        cmds.xform(a1, ws=True, t=(-5, 0, 0))
        a2 = cmds.polyCylinder(r=0.4, h=0.8)[0]
        cmds.xform(a2, ws=True, t=(5, 0, 0))
        joints = [str(j) for j in rig.bundle.joints]
        rig.constrain_end_with_falloff(joints, a1, falloff=2.0, joint_index=0)
        rig.constrain_end_with_falloff(joints, a2, falloff=2.0, joint_index=-1)
        return tube, rig, a1, a2

    @staticmethod
    def _mid_section_y(mesh):
        pts = _all_vertex_positions(mesh)
        mid = [p for p in pts if abs(p[0]) < 1.0]
        return sum(p[1] for p in mid) / max(len(mid), 1)

    def test_carrying_anchors_carries_whole_hose(self):
        """Moving both anchors must carry the tube BODY, not just its tips
        (pre-fix the middle moved 1.18 of 5.0)."""
        tube, rig, a1, a2 = self._constrained_hose()
        cmds.xform(a1, ws=True, t=(-5, 5, 0))
        cmds.xform(a2, ws=True, t=(5, 5, 0))
        cmds.refresh()
        self.assertGreater(
            self._mid_section_y(tube),
            4.5,
            "tube middle lagged its end constraints",
        )
        # The mid control itself carries no constraint — the follow group
        # above it does — so it stays hand-animatable on top.
        mid_ctrl = str(rig.bundle.controls[1])
        self.assertFalse(
            cmds.listRelatives(mid_ctrl, type="pointConstraint"),
            "mid control must stay unconstrained (follow group takes it)",
        )

    def test_vertical_hose_auto_bend_bows_perpendicular(self):
        """Regression: auto-bend drove the mid group's translateY, and
        translation runs in PARENT space (world-aligned) — on a VERTICAL
        hose the mid control slid ALONG the tube axis instead of bowing
        outward, so auto-bend was a silent no-op on any non-horizontal
        tube. The bow must run perpendicular to the hose's chord."""
        tube = _make_tube(axis=(0, 1, 0))  # spans y in [-5, 5]
        rig = TubeRig(tube, rig_name="VHose")
        rig.build(strategy="spline", num_joints=-1, enable_auto_bend=True)

        start_ctrl, end_ctrl = str(rig.bundle.controls[0]), str(rig.bundle.controls[-1])
        s, e = _ws(start_ctrl), _ws(end_ctrl)
        d = [s[i] - e[i] for i in range(3)]
        length = math.sqrt(sum(v * v for v in d))
        # Compress by 4 along the hose axis.
        cmds.xform(
            end_ctrl, ws=True, t=[e[i] + d[i] / length * 4.0 for i in range(3)]
        )
        cmds.refresh()
        max_perp = max(
            math.hypot(p[0], p[2]) for p in _all_vertex_positions(tube)
        )
        self.assertGreater(
            max_perp,
            1.8,
            f"vertical hose accordioned straight (max perpendicular offset "
            f"{max_perp:.2f}, tube radius 1)",
        )

    def test_compression_bows_not_accordions(self):
        """Compressing the hose must bow it outward, not accordion it
        dead-straight (auto-bend on by default, attr dv 0.5)."""
        tube, rig, a1, a2 = self._constrained_hose(enable_auto_bend=True)
        cmds.xform(a2, ws=True, t=(1, 0, 0))  # compress by 4
        cmds.refresh()
        pts = _all_vertex_positions(tube)
        max_y = max(abs(p[1]) for p in pts)
        self.assertGreater(
            max_y, 1.5, f"hose accordioned straight (max |y| = {max_y:.2f})"
        )
        # End stays near its anchor while the slack bows out.
        end_x = max(p[0] for p in pts)
        self.assertLess(abs(end_x - 1.0), 0.7, f"end at x={end_x:.2f}, anchor at 1")


class TestProportionalSizing(unittest.TestCase):
    """Rig components scale to the measured tube radius (2026-07-09)."""

    def setUp(self):
        cmds.file(new=True, force=True)

    @staticmethod
    def _make_tube_r(radius, h=20.0):
        tube = cmds.polyCylinder(r=radius, h=h, sy=10, sx=12, ax=(1, 0, 0))[0]
        cmds.makeIdentity(tube, apply=True, t=1, r=1, s=1, n=0, pn=1)
        return tube

    @staticmethod
    def _max_dim(node):
        # Measure the control's own curve shape — a transform-level bbox
        # would include child controls/locators parented beneath it.
        shapes = cmds.listRelatives(str(node), shapes=True, fullPath=True) or [node]
        bb = cmds.exactWorldBoundingBox(shapes)
        return max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])

    def test_estimate_radius(self):
        tube = self._make_tube_r(3.0)
        rig = TubeRig(tube, rig_name="RadEst")
        r = rig.estimate_tube_radius()
        self.assertIsNotNone(r)
        # A 12-gon reads slightly under the circumscribed radius (apothem).
        self.assertAlmostEqual(r, 3.0, delta=0.45)

    def test_controls_scale_with_mesh(self):
        """Same build on a 3x-radius tube must yield ~3x-sized controls."""
        sizes = {}
        for radius in (1.0, 3.0):
            cmds.file(new=True, force=True)
            tube = self._make_tube_r(radius)
            rig = TubeRig(tube, rig_name=f"Prop{int(radius)}")
            rig.build(strategy="spline", num_joints=5)
            sizes[radius] = self._max_dim(rig.bundle.controls[0])
        ratio = sizes[3.0] / sizes[1.0]
        self.assertAlmostEqual(ratio, 3.0, delta=0.6, msg=f"control ratio {ratio}")

    def test_auto_joint_radius(self):
        """Joint Size = Auto (-1) derives the display radius from the tube."""
        tube = self._make_tube_r(3.0)
        rig = TubeRig(tube, rig_name="AutoJnt")
        rig.build(strategy="spline", num_joints=5, radius=-1.0)
        jr = cmds.getAttr(f"{rig.bundle.joints[0]}.radius")
        self.assertAlmostEqual(jr, 1.5, delta=0.3)

    def test_explicit_joint_radius_respected(self):
        """An explicit Joint Size must pass through untouched."""
        tube = self._make_tube_r(3.0)
        rig = TubeRig(tube, rig_name="ExplJnt")
        rig.build(strategy="spline", num_joints=5, radius=2.0)
        jr = cmds.getAttr(f"{rig.bundle.joints[0]}.radius")
        self.assertAlmostEqual(jr, 2.0, places=5)


if __name__ == "__main__":
    unittest.main()
