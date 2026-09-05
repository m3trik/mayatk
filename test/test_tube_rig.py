import math
import unittest

import maya.cmds as cmds
import maya.api.OpenMaya as om

from mayatk.rig_utils.tube_rig import TubeRig, TubePath
from mayatk.rig_utils.skinning import SkinUtils


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

    def test_tweak_layer_rebuild_keeps_the_ik_curve(self):
        """A second ``create_tweak_controls`` must not eat the rig.

        The first tweak build re-homes the spline solver, which parks
        ``<rig>_ik_curve`` INSIDE ``<rig>_proxy_GRP`` -- precisely what the
        method's own stale sweep (``<rig>_proxy_*``) deletes on the next call.
        The curve went with the group, ``curveInfo`` lost its input, and the
        rebuild then raised "build the spline controls first" about controls
        that were built -- leaving the rig unrecoverable.
        """
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Retweak")
        rig.build(strategy="spline", num_joints=8)
        self.assertTrue(cmds.objExists("Retweak_ik_curve"), "no curve to begin with")

        rig.create_tweak_controls([str(j) for j in rig.joints], size=1.0)

        self.assertTrue(
            cmds.objExists("Retweak_ik_curve"),
            "the rebuild deleted the IK curve that drives the rig",
        )
        self.assertTrue(
            cmds.ls("Retweak_proxy_jnt_*"), "the proxy chain was not rebuilt"
        )

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
        self.assertIn(
            str(anchor_joint).split("|")[-1], [i.split("|")[-1] for i in influences]
        )

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
            cmds.setAttr(f"{cpom}.inPosition", p[0], p[1], p[2], type="double3")
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
        _, handle = cmds.nonLinear(
            tube, type="bend", lowBound=-1, highBound=1, curvature=90
        )
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
        """The control's group chain must reach the rig group through
        rig-owned groups only (offset group, plus documented inserts like
        the space/follow/auto-bend groups) — no stray nesting, nothing
        orphaned."""
        ctrl_grp = (cmds.listRelatives(ctrl_name, parent=True) or [None])[0]
        self.assertIsNotNone(
            ctrl_grp, f"Control {ctrl_name} has no parent offset group"
        )
        if require_ctrl_grp_suffix:
            self.assertTrue(
                ctrl_grp.endswith("_CTRL_GRP"),
                f"Control parent is {ctrl_grp}, expected *_CTRL_GRP",
            )
        node, hops = ctrl_grp, 0
        while node is not None and hops < 5:
            parent = (cmds.listRelatives(node, parent=True) or [None])[0]
            if parent == rig.rig_group:
                return
            self.assertIsNotNone(
                parent, f"{ctrl_name}'s group chain left the rig at {node}"
            )
            self.assertTrue(
                parent.startswith(f"{rig.rig_name}_") and parent.endswith("_GRP"),
                f"{ctrl_name} nests under non-rig group {parent}",
            )
            node, hops = parent, hops + 1
        self.fail(f"{ctrl_name}'s group chain never reached {rig.rig_group}")

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


class TestRingCyclicOrder(unittest.TestCase):
    """A ring is a POLYGON BOUNDARY to Newell's method in ``get_end_normals``,
    so its vertices must come back in cyclic (connectivity) order.

    Sorting by vertex id only coincides with cyclic order on primitive-built
    meshes (polyCylinder). On a user-modeled or cleaned mesh with scrambled
    ids the "polygon" self-intersects, the Newell sum partially cancels, and
    the wrong-but-nonzero normal clears the magnitude guard — building end
    controls skew to the cap they are meant to plug into.

    ``order_cycle`` is pure over an edge list, so this runs without a mesh.
    """

    def test_orders_a_scrambled_ring_by_connectivity(self):
        # Ring 7-3-9-1 (cyclic); id order 1-3-7-9 is a DIFFERENT, crossing path.
        edges = [(7, 3), (3, 9), (9, 1), (1, 7)]
        ordered = TubePath.order_cycle(edges)
        self.assertEqual(len(ordered), 4)
        # Rotation/direction are free; adjacency is what must hold.
        for i, v in enumerate(ordered):
            pair = {v, ordered[(i + 1) % len(ordered)]}
            self.assertIn(
                pair,
                [set(e) for e in edges],
                f"{pair} is not an edge — the walk crossed the ring",
            )

    def test_sequential_ids_are_unchanged_in_effect(self):
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        self.assertEqual(TubePath.order_cycle(edges), [0, 1, 2, 3])

    def test_rejects_open_path(self):
        self.assertEqual(TubePath.order_cycle([(0, 1), (1, 2)]), [])

    def test_rejects_two_disjoint_cycles(self):
        """Both cycles pass the every-vertex-has-two-neighbours test; only a
        full traversal proves the edges form ONE ring."""
        edges = [(0, 1), (1, 2), (2, 0), (5, 6), (6, 7), (7, 5)]
        self.assertEqual(TubePath.order_cycle(edges), [])

    def test_rejects_empty_and_degenerate(self):
        self.assertEqual(TubePath.order_cycle([]), [])
        self.assertEqual(TubePath.order_cycle([(4, 4)]), [])


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
            self.assertLess(r, 0.35, f"centerline point off-axis by {r:.3f} (radius=1)")

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


def _make_hooked_tube(
    name="hookTube", tube_r=0.5, straight=14.0, hook_r=3.5, hook_deg=200.0, sx=12, sy=24
):
    """A J-shaped tube: a straight run into a tight hook.

    The kink this suite gates only shows on a tube whose bend is tight
    relative to its length — a straight cylinder hides it, because the
    driver handoff and the path direction coincide there.
    """
    hook_rad = math.radians(hook_deg)
    total = straight + hook_r * hook_rad
    binormal = om.MVector(0, 0, 1)

    def frame(s):
        if s <= straight:
            return om.MVector(s, 0, 0), om.MVector(1, 0, 0)
        th = (s - straight) / hook_r
        center = om.MVector(straight, hook_r, 0)
        pos = center + om.MVector(math.sin(th) * hook_r, -math.cos(th) * hook_r, 0)
        return pos, om.MVector(math.cos(th), math.sin(th), 0)

    tube = cmds.polyCylinder(
        r=tube_r,
        h=1.0,
        sx=sx,
        sy=sy,
        sz=0,
        ax=(0, 1, 0),
        rcp=0,
        cuv=3,
        ch=False,
        name=name,
    )[0]
    sel = om.MSelectionList()
    sel.add(tube)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    fn = om.MFnMesh(dag)
    bent = om.MPointArray()
    for p in fn.getPoints(om.MSpace.kWorld):
        if abs(p.x) < 1e-6 and abs(p.z) < 1e-6:  # cap pole
            bent.append(om.MPoint(frame(0.0 if p.y < 0 else total)[0]))
            continue
        s = round((p.y + 0.5) * sy) / sy * total
        phi = math.atan2(p.z, p.x)
        pos, tangent = frame(s)
        normal = (binormal ^ tangent).normal()
        bent.append(
            om.MPoint(
                pos
                + normal * (math.cos(phi) * tube_r)
                + binormal * (math.sin(phi) * tube_r)
            )
        )
    fn.setPoints(bent, om.MSpace.kWorld)
    return tube, total


def _max_curve_curvature(curve, samples=300):
    """Peak discrete curvature (turn angle / segment length) along a curve.

    A kink is a local curvature spike, so this is the metric that separates
    "the hose bends" from "the hose creases".
    """
    sel = om.MSelectionList()
    sel.add(cmds.listRelatives(str(curve), s=True, f=True)[0])
    fn = om.MFnNurbsCurve(sel.getDagPath(0))
    lo, hi = fn.knotDomain
    pts = [
        fn.getPointAtParam(lo + (hi - lo) * i / (samples - 1), om.MSpace.kWorld)
        for i in range(samples)
    ]
    peak = 0.0
    for i in range(1, len(pts) - 1):
        v1 = om.MVector(pts[i]) - om.MVector(pts[i - 1])
        v2 = om.MVector(pts[i + 1]) - om.MVector(pts[i])
        seg = (v1.length() + v2.length()) / 2
        if seg > 1e-9:
            peak = max(peak, v1.angle(v2) / seg)
    return peak


class TestSplineCurveDeformationQuality(unittest.TestCase):
    """The IK logic curve gates the whole hose: spline IK re-poses every
    joint from it, so a kink in the curve reaches the mesh no matter how
    good the mesh weights are.

    Regression (reported 2026-08-12, BACKLOG 2026-08-02): the curve was
    bound to its driver joints with bare ``cmds.skinCluster`` defaults
    (closest-distance, maxInfluences=2). Weights handed off between drivers
    within one or two CVs and picked influences by Euclidean distance, so
    on a hooked tube CVs took a driver BEHIND them as their second
    influence. Pulling the end control then spiked curve curvature 0.385 ->
    0.588 at the start_tan -> mid handoff — a NEW kink far from the moved
    control.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _built(self, name):
        tube, _total = _make_hooked_tube(name=f"{name}Mesh")
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy="spline")
        return tube, rig

    def test_curve_skin_is_parametric_not_default(self):
        _tube, rig = self._built("CurveSkin")
        sc = SkinUtils.get_skin_cluster(rig.bundle.curve)
        self.assertTrue(sc, "the IK curve must be skinned")
        # The default bind leaves maxInfluences at 2 with the cap unenforced;
        # the parametric bind asks for degree + 1 and obeys it.
        self.assertEqual(cmds.getAttr(f"{sc}.maxInfluences"), 4)
        weights, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        dominant = []
        for v in range(len(weights) // n):
            row = weights[v * n : (v + 1) * n]
            self.assertAlmostEqual(sum(row), 1.0, places=5)
            dominant.append(max(range(n), key=lambda i: row[i]))
        for a, b in zip(dominant, dominant[1:]):
            self.assertLessEqual(
                a, b, f"CV dominance goes backward along the curve: {dominant}"
            )

    def test_end_pull_does_not_kink_the_curve(self):
        """Posing an end control must not create curvature the rest pose
        did not already have."""
        _tube, rig = self._built("NoKink")
        curve = rig.bundle.curve
        rest = _max_curve_curvature(curve)
        cmds.xform(rig.bundle.controls[-1], ws=True, r=True, t=(-2.5, 2.0, 0.0))
        posed = _max_curve_curvature(curve)
        self.assertLessEqual(
            posed,
            rest * 1.05,
            f"end pull kinked the curve: rest {rest:.3f} -> posed {posed:.3f}",
        )

    def test_bent_tube_rings_are_weight_uniform(self):
        """Every vertex of a cross-section must carry identical weights —
        on a BENT tube, not just a straight one.

        Regression: the solve stationed each vertex by its own projection
        onto the centerline. That is exact on a straight tube (which is all
        the original uniformity test covered) but skews on a bend — the
        inside of a ring projects short and the outside long — so a single
        cross-section carried a 9-18% weight spread and sheared under pose.
        Topological rings (TubePath.get_vertex_rings) fix it by construction.
        """
        tube, _total = _make_hooked_tube(name="RingUniform", hook_r=1.2, sy=24)
        rig = TubeRig(tube, rig_name="RingUniform")
        rig.build(strategy="spline")

        sc = SkinUtils.get_skin_cluster(tube)
        weights, influences = SkinUtils.get_weights(sc)
        n = len(influences)
        worst = 0.0
        for ring in TubePath.get_vertex_rings(tube):
            for i in range(n):
                column = [weights[v * n + i] for v in ring]
                worst = max(worst, max(column) - min(column))
        self.assertLess(worst, 1e-4, f"cross-section weight spread {worst:.3e}")

    def test_driver_stations_are_arc_even(self):
        """Tangent drivers sit at 20%/80% of ARC — not of the start-end
        chord, which on a curled tube bunched them to ~11%/89% and cost the
        weight basis its even support."""
        from mayatk.nurbs_utils._nurbs_utils import NurbsUtils

        _tube, rig = self._built("ArcStations")
        curve = rig.bundle.curve
        total = NurbsUtils.get_curve_length(curve)
        drivers = [
            cmds.ls(f"{rig.rig_name}_driver_{s}_jnt", long=True)[0]
            for s in ("start", "start_tan", "mid", "end_tan", "end")
        ]
        stations = [
            s / total
            for s in NurbsUtils.get_arc_lengths(
                curve, [cmds.xform(d, q=True, ws=True, t=True) for d in drivers]
            )
        ]
        for got, want in zip(stations, (0.0, 0.2, 0.5, 0.8, 1.0)):
            self.assertAlmostEqual(got, want, delta=0.03, msg=f"stations {stations}")


def _make_coil(
    name="coilTube", turns=2.0, coil_r=3.0, pitch=1.0, tube_r=0.4, sx=10, sy=12
):
    """A helix whose coils pass closer together than its rings are spaced.

    That inequality is the whole point: a nearest-neighbour walk over the
    ring centres hops to the neighbouring coil instead of the next ring.
    """
    total_ang = turns * 2 * math.pi

    def frame(t):
        a = t * total_ang
        pos = om.MVector(math.cos(a) * coil_r, t * pitch * turns, math.sin(a) * coil_r)
        tan = om.MVector(
            -math.sin(a) * coil_r, pitch * turns / total_ang, math.cos(a) * coil_r
        ).normal()
        return pos, tan

    tube = cmds.polyCylinder(
        r=tube_r,
        h=1.0,
        sx=sx,
        sy=sy,
        sz=0,
        ax=(0, 1, 0),
        rcp=0,
        cuv=3,
        ch=False,
        name=name,
    )[0]
    sel = om.MSelectionList()
    sel.add(tube)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    fn = om.MFnMesh(dag)
    out = om.MPointArray()
    for p in fn.getPoints(om.MSpace.kWorld):
        t = min(max(round((p.y + 0.5) * sy) / sy, 0.0), 1.0)
        pos, tan = frame(t)
        if abs(p.x) < 1e-6 and abs(p.z) < 1e-6:
            out.append(om.MPoint(pos))
            continue
        n = (om.MVector(0, 1, 0) ^ tan).normal()
        b = (tan ^ n).normal()
        phi = math.atan2(p.z, p.x)
        out.append(
            om.MPoint(pos + n * (math.cos(phi) * tube_r) + b * (math.sin(phi) * tube_r))
        )
    fn.setPoints(out, om.MSpace.kWorld)
    return tube, frame(0.0)[0], frame(1.0)[0]


class TestRigDeformationQuality(unittest.TestCase):
    """Numeric tolerances for how a built rig actually deforms.

    Rig quality was previously judged by eye, which let a 1.5-tube-radius
    axis drift ship: the mesh left its own skeleton whenever stretch was on,
    because dual quaternion skinning cannot represent a scaled influence
    unless ``dqsSupportNonRigid`` is set. Metrics live in
    ``test/rig_metrics.py`` and are scale-free (tube radii / degrees), so
    these tolerances hold for any asset size.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _built(self, name, strategy="spline"):
        tube, _total = _make_hooked_tube(name=f"{name}Mesh", sy=24)
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy=strategy)
        return tube, rig

    def test_mesh_stays_on_its_rig_when_posed(self):
        """The mesh must not leave the axis its own skeleton defines.

        Regression: with stretch/squash/volume on (the hose preset default)
        the posed mesh sat 1.5 tube radii off the IK curve while the joints
        stayed exactly on it — DQS silently mishandling the joint scale.
        """
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("Conform")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        for label, delta in (("lift", (0, r * 6, 0)), ("push", (0, 0, -r * 4))):
            cmds.xform(rig.bundle.controls[-1], ws=True, r=True, t=delta)
            cmds.refresh()
            conf = M.conformance(tube, rig.bundle.joints, rings, rig.bundle.curve, r)
            self.assertLess(
                conf["max"],
                0.25,
                f"{label}: mesh {conf['max']:.3f} tube radii off-axis",
            )
            cmds.xform(
                rig.bundle.controls[-1], ws=True, r=True, t=tuple(-d for d in delta)
            )

    def test_dqs_support_non_rigid_is_enabled(self):
        """The flag the above depends on — asserted directly so a regression
        names its own cause instead of surfacing as drift."""
        tube, _rig = self._built("NonRigid")
        sc = SkinUtils.get_skin_cluster(tube)
        self.assertTrue(cmds.getAttr(f"{sc}.dqsSupportNonRigid"))

    def test_cross_sections_keep_their_shape(self):
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("Integrity")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        cmds.xform(rig.bundle.controls[-1], ws=True, r=True, t=(0, r * 6, 0))
        cmds.refresh()
        integ = M.ring_integrity(tube, rest, rings, r)
        self.assertGreater(integ["radius_ratio_min"], 0.85, "cross-section collapsed")
        self.assertLess(integ["radius_ratio_max"], 1.15, "cross-section ballooned")
        self.assertLess(integ["roundness_max"], 0.15, "cross-section pinched/sheared")

    def test_posing_does_not_crease(self):
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("Crease")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.smoothness(tube, rings, r)["peak_curvature"]
        cmds.xform(rig.bundle.controls[-1], ws=True, r=True, t=(0, r * 6, 0))
        cmds.refresh()
        posed = M.smoothness(tube, rings, r)["peak_curvature"]
        self.assertLess(
            posed, rest * 1.25, f"mesh creased: curvature {rest:.3f} -> {posed:.3f}"
        )

    def test_end_stays_square_to_its_control(self):
        """The tube's end FACE must track its end control's axis.

        Built from the end cross-section's own normal rather than the
        centerline's last chord, so an angle-cut opening still gets a square
        control (rest alignment 0 instead of the cut angle).
        """
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("EndSquare")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        end_ctrl = rig.bundle.controls[-1]
        rest = M.end_alignment(tube, end_ctrl, rings=rings)
        self.assertLess(rest, 2.0, f"rest end alignment {rest:.1f} deg")
        for delta in ((0, r * 6, 0), (0, 0, -r * 4)):
            cmds.xform(end_ctrl, ws=True, r=True, t=delta)
            cmds.refresh()
            posed = M.end_alignment(tube, end_ctrl, rings=rings)
            self.assertLess(
                abs(posed - rest),
                5.0,
                f"end drifted {abs(posed - rest):.1f} deg out of square",
            )
            cmds.xform(end_ctrl, ws=True, r=True, t=tuple(-d for d in delta))

    def test_fk_bends_smoothly_from_a_few_controls(self):
        """A tentacle must be posable from a handful of keys.

        One control per joint is technically FK and practically unusable: an
        Auto build puts a joint on every edge loop, so a single curve costs
        twenty-odd keys in lockstep and any control left behind corners the
        tube. Each control now spreads its rotation across the joints it
        owns, so ONE key arcs its whole section.
        """
        from rig_metrics import TubeRigMetrics as M

        tube, _total = _make_hooked_tube(name="FkSpanMesh", sy=24)
        rig = TubeRig(tube, rig_name="FkSpan")
        rig.build(strategy="fk")
        ctrls = rig.bundle.controls
        self.assertLess(
            len(ctrls),
            len(rig.bundle.joints),
            "FK built one control per joint — nothing to animate with",
        )
        self.assertGreaterEqual(len(ctrls), 3, "too few controls to shape a tentacle")

        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.smoothness(tube, rings, r)["peak_curvature"]
        cmds.setAttr(f"{ctrls[len(ctrls) // 2]}.rotateZ", 40)
        cmds.refresh()
        posed = M.smoothness(tube, rings, r)["peak_curvature"]
        self.assertLess(
            posed,
            rest * 2.0,
            f"one key creased the tube: curvature {rest:.3f} -> {posed:.3f}",
        )

    def test_fk_one_control_per_joint_still_available(self):
        """``num_controls=-1`` keeps the classic chain, joint-constrained."""
        tube, _total = _make_hooked_tube(name="FkClassicMesh", sy=24)
        rig = TubeRig(tube, rig_name="FkClassic")
        rig.build(strategy="fk", num_controls=-1)
        self.assertEqual(len(rig.bundle.controls), len(rig.bundle.joints))

    def test_controls_do_not_swallow_each_other(self):
        """Every control must be individually pickable.

        Regression: an Auto FK build made one control per edge loop sized
        from the tube radius, so each was ~2.4x its gap to the next and the
        chain read as one blob — reported as "this rig has no controls".
        """
        from rig_metrics import TubeRigMetrics as M

        for strategy in ("spline", "fk"):
            cmds.file(new=True, force=True)
            tube, rig = self._built(f"Pick{strategy}", strategy=strategy)
            r = M.tube_radius(tube)
            usable = M.control_usability(rig.bundle.controls, r)
            self.assertTrue(usable["count"], f"{strategy}: no controls built")
            self.assertTrue(usable["all_visible"], f"{strategy}: controls hidden")
            self.assertTrue(
                usable["all_have_shapes"], f"{strategy}: controls have no shape"
            )
            self.assertLess(
                usable["size_vs_gap"],
                1.0,
                f"{strategy}: controls are {usable['size_vs_gap']:.2f}x their spacing",
            )


class TestCoiledTubeCenterline(unittest.TestCase):
    """A tube that passes near itself must still order end-to-end.

    Regression: the centerline was re-ordered geometrically with a greedy
    nearest-neighbour walk. Where a coil's gap is smaller than its ring
    spacing (a coarsely tessellated hose), that walk hops to the neighbouring
    pass, so the path's "end" lands mid-tube — and the rig builds its END
    control there.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_centerline_runs_end_to_end_on_a_tight_coil(self):
        tube, cap_a, cap_b = _make_coil()
        centerline, _n = TubePath.get_centerline(tube, num_joints=-1)
        pts = [om.MVector(float(p[0]), float(p[1]), float(p[2])) for p in centerline]
        first, last = pts[0], pts[-1]
        direct = max((first - cap_a).length(), (last - cap_b).length())
        flipped = max((first - cap_b).length(), (last - cap_a).length())
        self.assertLess(
            min(direct, flipped),
            0.4,
            "centerline does not span cap to cap (nearest-neighbour scramble)",
        )

    def test_end_control_lands_on_the_cap(self):
        tube, cap_a, cap_b = _make_coil()
        rig = TubeRig(tube, rig_name="Coil")
        rig.build(strategy="spline")
        c0 = om.MVector(*_ws(rig.bundle.controls[0]))
        c1 = om.MVector(*_ws(rig.bundle.controls[-1]))
        direct = max((c0 - cap_a).length(), (c1 - cap_b).length())
        flipped = max((c0 - cap_b).length(), (c1 - cap_a).length())
        self.assertLess(
            min(direct, flipped),
            0.4,
            "an end control was built away from the tube's cap",
        )


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
            self.assertTrue(cmds.ls(c, long=True), f"control {c} not resolvable")

    def test_fk_build_under_control_name_collision(self):
        rig = self._build("fk", 6)
        # Every FK joint must actually be DRIVEN by the control rig (proves
        # the re-derived control path was the real node, not a stale twin).
        # Asserted by effect rather than by mechanism: a joint that owns its
        # control is parent-constrained, while one inside a multi-joint span
        # takes a point constraint on the span's anchor plus a direct rotate
        # connection carrying its share of the control's rotation.
        for jnt in rig.bundle.joints:
            constrained = cmds.listRelatives(jnt, type="constraint")
            driven = cmds.listConnections(
                f"{jnt}.rotateX", source=True, destination=False
            )
            self.assertTrue(
                constrained or driven, f"{jnt} is not driven by any control"
            )

    def test_anchor_build_under_control_name_collision(self):
        rig = self._build("anchor", 2)
        for jnt in rig.bundle.joints:
            self.assertTrue(cmds.listRelatives(jnt, type="pointConstraint"))

    def test_teardown_spares_prefix_overlapping_sibling_rig(self):
        """``teardown`` must delete only ITS rig's nodes when a sibling rig's
        name extends this one's (``Dup`` next to ``Dup_02``).

        Regression: the stray sweep was a bare ``<rig>_*`` glob, which also
        matched every ``Dup_02_*`` DG node — tearing down ``Dup`` deleted the
        sibling's skinCluster (unbinding its mesh), curveInfo and control set.
        """
        mine = TubeRig(_make_tube(), rig_name="Dup")
        mine.build(strategy="spline", num_joints=6)
        sibling_tube = _make_tube()
        sibling = TubeRig(sibling_tube, rig_name="Dup_02")
        sibling.build(strategy="spline", num_joints=6)
        keep = [
            "Dup_02_GRP",
            "Dup_02_skinCluster",
            "Dup_02_curveInfo",
            "Dup_02_controls_SET",
        ]
        for n in keep:
            self.assertTrue(cmds.objExists(n), f"fixture: {n} missing")

        mine.teardown()

        self.assertFalse(cmds.objExists("Dup_GRP"))
        self.assertFalse(cmds.objExists("Dup_skinCluster"))
        self.assertFalse(cmds.objExists("Dup_curveInfo"))
        for n in keep:
            self.assertTrue(
                cmds.objExists(n), f"teardown of 'Dup' deleted sibling node {n}"
            )
        self.assertTrue(
            cmds.ls(cmds.listHistory(sibling_tube) or [], type="skinCluster"),
            "sibling mesh lost its bind",
        )


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

    def test_crossed_anchor_is_reassigned_to_its_own_end(self):
        """A crossed call must anchor the end the anchor actually sits at.

        Found 2026-08-25 in VDATS_DA: tube6/tube7 were built with the two
        anchors swapped, so ``<rig>_anchor_start_jnt`` sat at the FAR end and
        drove ``start_CTRL``. Pulling the plug then dragged the wrong end of
        the cable and tore the mesh off both sockets (measured: 25.0 units of
        gap growth vs 0.05 on the correctly built tubes).

        Only ``b004`` un-crossed the selection, so any direct caller of this
        public method could build the broken wiring. The primitive now owns
        the check, so every caller is covered.
        """
        tube, rig, a1, a2 = self._rigged_tube("spline", -1)
        joints = [str(j) for j in rig.bundle.joints]
        # a2 is the +X anchor, handed to the START index -- crossed.
        made = rig.constrain_end_with_falloff(joints, a2, falloff=2.0, joint_index=0)
        self.assertIsNotNone(made)
        # It must serve the +X end: named for it, and placed at it.
        self.assertTrue(
            made.endswith("_anchor_end_jnt"),
            f"crossed anchor kept the wrong end's name: {made}",
        )
        self.assertGreater(
            _ws(made)[0], 0.0, "anchor joint was not placed at the +X end"
        )
        # ...and it must drive the +X control, not the -X one.
        end_ctrl = str(rig.bundle.controls[-1])
        self.assertTrue(
            cmds.listRelatives(end_ctrl, type="parentConstraint"),
            "crossed anchor did not route through the +X end control",
        )
        self.assertFalse(
            cmds.listRelatives(str(rig.bundle.controls[0]), type="parentConstraint"),
            "crossed anchor wrongly drove the -X (start) control",
        )
        # The un-crossed end is still free for its own anchor.
        other = rig.constrain_end_with_falloff(joints, a1, falloff=2.0, joint_index=-1)
        self.assertTrue(
            other.endswith("_anchor_start_jnt"),
            f"second crossed anchor kept the wrong end's name: {other}",
        )
        self.assertLess(_ws(other)[0], 0.0)

    def test_middle_index_anchor_is_never_reassigned(self):
        """The un-cross guard must apply ONLY to the two end indices — a
        mid-chain anchor has no 'other end' to be crossed with."""
        tube, rig, a1, a2 = self._rigged_tube("spline", -1)
        joints = [str(j) for j in rig.bundle.joints]
        mid = len(joints) // 2
        made = rig.constrain_end_with_falloff(joints, a2, falloff=2.0, joint_index=mid)
        self.assertIsNotNone(made)
        self.assertTrue(
            made.endswith(f"_anchor_{mid}_jnt"),
            f"mid-chain anchor was reassigned: {made}",
        )

    def test_rerun_replaces_previous_end_anchor(self):
        """Re-anchoring an end must REPLACE its previous anchor, matching the
        rerun semantics every other step advertises.

        Pre-fix (probe-verified) the second run stacked: the end control's
        parentConstraint carried BOTH anchor joints as targets — so the end
        followed their midpoint — and the discarded anchor joint stayed on
        the skinCluster as a dead influence.
        """
        # a2 is the +X anchor — the end this test re-anchors.
        tube, rig, _, a2 = self._rigged_tube("spline", -1)
        joints = [str(j) for j in rig.bundle.joints]
        rig.constrain_end_with_falloff(joints, a2, falloff=2.0, joint_index=-1)

        a3 = cmds.polyCylinder(r=0.4, h=0.8)[0]
        cmds.xform(a3, ws=True, t=(5, 0, 0))
        rig.constrain_end_with_falloff(joints, a3, falloff=2.0, joint_index=-1)

        skin = rig.skin_cluster
        anchors = [
            i for i in cmds.skinCluster(skin, q=True, influence=True) if "_anchor_" in i
        ]
        self.assertEqual(
            len(anchors), 1, f"stale anchor influences left behind: {anchors}"
        )

        end_ctrl = rig._end_control(-1)
        constraints = set(
            cmds.listRelatives(end_ctrl, type="parentConstraint", fullPath=True) or []
        )
        self.assertEqual(
            len(constraints), 1, f"end control carries {len(constraints)} constraints"
        )
        targets = cmds.parentConstraint(constraints.pop(), q=True, targetList=True)
        self.assertEqual(len(targets), 1, f"constraint stacked targets: {targets}")

        # Behavioral proof: the end tracks the LATEST anchor, not a midpoint.
        before = _all_vertex_positions(tube)
        cmds.xform(a3, ws=True, t=(5, 4, 0))
        cmds.refresh()
        after = _all_vertex_positions(tube)
        end_i = max(range(len(before)), key=lambda i: before[i][0])
        dy = after[end_i][1] - before[end_i][1]
        self.assertGreater(
            dy, 3.0, f"end followed a blend of both anchors (dy={dy:.2f})"
        )

    def test_rerun_end_anchor_restores_weights(self):
        """Replacing an end anchor must not accumulate weight drift.

        ``_clear_end_anchor`` removes the old influence before deleting it, so
        the rows its falloff had redistributed renormalize back to their
        original proportions — re-anchoring N times must read the same as
        anchoring once.
        """

        def chain_weights(skin):
            """{influence leaf -> per-vertex weights} for the chain joints."""
            weights, influences = SkinUtils.get_weights(skin)
            n = len(influences)
            return {
                name.split("|")[-1]: weights[i::n]
                for i, name in enumerate(influences)
                if "_anchor_" not in name
            }

        # Both anchors sit at the SAME spot on the +X end, so the two runs
        # paint an identical region — any delta is replace-path drift.
        tube, rig, _, a2 = self._rigged_tube("spline", -1)
        joints = [str(j) for j in rig.bundle.joints]
        rig.constrain_end_with_falloff(joints, a2, falloff=2.0, joint_index=-1)
        first = chain_weights(rig.skin_cluster)

        a3 = cmds.polyCylinder(r=0.4, h=0.8)[0]
        cmds.xform(a3, ws=True, t=(5, 0, 0))
        rig.constrain_end_with_falloff(joints, a3, falloff=2.0, joint_index=-1)
        second = chain_weights(rig.skin_cluster)

        self.assertEqual(sorted(first), sorted(second), "chain influences changed")
        drift = max(
            abs(a - b) for name in first for a, b in zip(first[name], second[name])
        )
        self.assertLess(drift, 1e-4, f"weights drifted on re-anchor (max {drift:.6f})")


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
        cmds.xform(end_ctrl, ws=True, t=[e[i] + d[i] / length * 4.0 for i in range(3)])
        cmds.refresh()
        max_perp = max(math.hypot(p[0], p[2]) for p in _all_vertex_positions(tube))
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


# ======================================================================
# Real-world fixtures: swept-tube generator
# ======================================================================


def _pt_frames(ring_frames):
    """Parallel-transport (pos, tan, normal, binormal) per ring — a stable
    frame along a 3D path (a fixed world-up frame flips on bends that pass
    vertical, which would corkscrew the mesh)."""
    out = []
    prev_n = None
    for pos, tan in ring_frames:
        tan = om.MVector(tan).normal()
        if prev_n is None:
            up = om.MVector(0, 1, 0)
            if abs(tan * up) > 0.9:
                up = om.MVector(0, 0, 1)
            n = (up ^ tan).normal()
        else:
            n = (prev_n - tan * (prev_n * tan)).normal()
        b = (tan ^ n).normal()
        out.append((om.MVector(pos), tan, n, b))
        prev_n = n
    return out


def _make_swept_tube(
    name,
    ring_frames,
    radii,
    profile=None,
    sx=14,
    cap_start=True,
    cap_end=True,
    scramble_seed=None,
):
    """Tube mesh from explicit ring stations — the generator behind the
    real-world fixtures (corrugated duct, fitted hydraulic hose, molded
    radiator hose). polyCylinder-derived fixtures can't reach these shapes:
    per-ring radii and profiles (ribs, hex), non-uniform stations, open
    ends, and — via *scramble_seed* — the arbitrary vertex numbering of
    imported/merged production meshes (a deterministic Fisher-Yates
    permutation of every vertex id).
    """
    frames = _pt_frames(ring_frames)
    nr = len(frames)
    pts = []
    for i, (pos, tan, n, b) in enumerate(frames):
        r = radii[i]
        for k in range(sx):
            phi = 2 * math.pi * k / sx
            m = profile(i, phi) if profile else 1.0
            pts.append(
                om.MPoint(
                    pos + n * (math.cos(phi) * r * m) + b * (math.sin(phi) * r * m)
                )
            )
    counts, connects = [], []

    def vid(i, k):
        return i * sx + (k % sx)

    for i in range(nr - 1):
        for k in range(sx):
            counts.append(4)
            connects += [vid(i, k), vid(i, k + 1), vid(i + 1, k + 1), vid(i + 1, k)]
    if cap_start:
        c0 = len(pts)
        pts.append(om.MPoint(frames[0][0]))
        for k in range(sx):
            counts.append(3)
            connects += [c0, vid(0, k + 1), vid(0, k)]
    if cap_end:
        c1 = len(pts)
        pts.append(om.MPoint(frames[-1][0]))
        for k in range(sx):
            counts.append(3)
            connects += [c1, vid(nr - 1, k), vid(nr - 1, k + 1)]

    if scramble_seed is not None:
        perm = list(range(len(pts)))
        s = scramble_seed
        for i in range(len(perm) - 1, 0, -1):  # Fisher-Yates over an LCG
            s = (s * 1103515245 + 12345) & 0x7FFFFFFF
            j = s % (i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        new_pts = [None] * len(pts)
        for old, new in enumerate(perm):
            new_pts[new] = pts[old]
        pts = new_pts
        connects = [perm[c] for c in connects]

    fnm = om.MFnMesh()
    mobj = fnm.create(pts, counts, connects)
    dag = om.MFnDagNode(mobj)
    dag.setName(name)
    om.MFnDagNode(dag.child(0)).setName(f"{name}Shape")
    cmds.sets(name, e=True, forceElement="initialShadingGroup")
    return name


def _make_corrugated_duct(name="corrDuct"):
    """Flex duct: dense alternating-radius ribs between smooth cuffs, OPEN
    ends — 73 rings, far more than any sane joint count, so joints land
    between ribs, never on them."""
    L, r0, amp, periods, cuff = 20.0, 1.0, 0.28, 8, 0.12
    body_n = periods * 8

    def rib(t):
        if t < cuff or t > 1 - cuff:
            return 0.0
        return math.sin(2 * math.pi * periods * (t - cuff) / (1 - 2 * cuff))

    st = (
        [cuff * i / 4 for i in range(4)]
        + [cuff + (1 - 2 * cuff) * i / body_n for i in range(body_n + 1)]
        + [1 - cuff + cuff * (i + 1) / 4 for i in range(4)]
    )
    st = sorted(set(round(t, 9) for t in st))
    frames = [(om.MVector(t * L, 0, 0), om.MVector(1, 0, 0)) for t in st]
    radii = [r0 * (1 + amp * rib(t)) for t in st]
    tube = _make_swept_tube(name, frames, radii, sx=14, cap_start=False, cap_end=False)
    return tube, {"L": L, "r": r0, "n_rings": len(st)}


def _make_fitted_hose(name="fittedHose"):
    """Hydraulic hose with crimped metal ends: capped stub, HEX nut, ferrule,
    with hard radius steps between zones — and SCRAMBLED vertex ids, the
    topology a combined/imported mesh actually has."""
    L, r_body = 16.0, 0.5
    APOTHEM = math.cos(math.pi / 6)

    def hexm(phi):
        a = phi % (math.pi / 3)
        return APOTHEM / math.cos(a - math.pi / 6)

    def zone(d):  # (radius, is_hex) by distance from the nearer end
        if d < 0.8:
            return 0.35, False  # stub
        if d < 1.8:
            return 0.62, True  # nut
        if d < 3.2:
            return 0.66, False  # ferrule
        return r_body, False

    end_d = [0.0, 0.3, 0.6, 0.79, 0.81, 1.1, 1.5, 1.79, 1.81, 2.2, 2.8, 3.19, 3.21]
    body = [3.21 + (L - 2 * 3.21) * i / 14 for i in range(1, 14)]
    ds = sorted(
        set(round(d, 6) for d in end_d + body + [L - d for d in reversed(end_d)])
    )
    frames = [(om.MVector(d, 0, 0), om.MVector(1, 0, 0)) for d in ds]
    radii = [zone(min(d, L - d))[0] for d in ds]

    def profile(i, phi):
        return hexm(phi) if zone(min(ds[i], L - ds[i]))[1] else 1.0

    tube = _make_swept_tube(
        name,
        frames,
        radii,
        profile=profile,
        sx=24,
        cap_start=True,
        cap_end=True,
        scramble_seed=1234,
    )
    return tube, {"L": L, "r": r_body, "n_rings": len(ds), "fit_d": 3.2}


def _make_radiator_hose(name="radHose"):
    """Molded radiator hose: pre-bent 3D S-path AT REST (90 deg then 45 deg
    in different planes), tapered, rings dense on the bends and sparse on
    the straights, OPEN clamp-on ends."""
    pts = []
    state = {"pos": om.MVector(0, 0, 0), "d": om.MVector(1, 0, 0)}

    def straight(seg_len, n):
        p0, d = om.MVector(state["pos"]), state["d"]
        for i in range(n):
            pts.append((p0 + d * (seg_len * i / n), om.MVector(d)))
        state["pos"] = p0 + d * seg_len

    def bend(R, ang, axis, n):
        a = om.MVector(axis).normal()
        d = state["d"]
        center = state["pos"] + (a ^ d).normal() * R
        r0 = state["pos"] - center
        for i in range(1, n + 1):
            q = om.MQuaternion(ang * i / n, a)
            pts.append((center + om.MVector(r0).rotateBy(q), d.rotateBy(q)))
        state["pos"] = center + om.MVector(r0).rotateBy(om.MQuaternion(ang, a))
        state["d"] = d.rotateBy(om.MQuaternion(ang, a))

    straight(6.0, 5)
    bend(3.0, math.pi / 2, om.MVector(0, 1, 0), 10)
    straight(4.0, 4)
    bend(2.5, math.pi / 4, om.MVector(1, 0, 0), 6)
    straight(5.0, 5)
    pts.append((om.MVector(state["pos"]), om.MVector(state["d"])))

    dedup = [pts[0]]  # a bend ends exactly where the next straight begins
    for p, t in pts[1:]:
        if (p - dedup[-1][0]).length() > 1e-6:
            dedup.append((p, t))
    pts = dedup

    arc = [0.0]
    for (a, _), (b, _) in zip(pts, pts[1:]):
        arc.append(arc[-1] + (b - a).length())
    radii = [1.0 - 0.25 * (s / arc[-1]) for s in arc]  # taper 1.0 -> 0.75
    tube = _make_swept_tube(name, pts, radii, sx=16, cap_start=False, cap_end=False)
    return tube, {
        "L": arc[-1],
        "r": (1.0 + 0.75) / 2,
        "n_rings": len(pts),
        "start": om.MVector(pts[0][0]),
        "end": om.MVector(pts[-1][0]),
    }


def _outward_tangent(joints, ctrl):
    """Unit tangent pointing OUT of the tube at whichever chain end *ctrl*
    sits on (the ring-walk direction is arbitrary — never assume it)."""
    ps = [om.MVector(*_ws(j)) for j in joints]
    c = om.MVector(*_ws(ctrl))
    if (c - ps[0]).length() < (c - ps[-1]).length():
        return (ps[0] - ps[1]).normal()
    return (ps[-1] - ps[-2]).normal()


class TestCorrugatedDuct(unittest.TestCase):
    """Ribbed flex duct: the surface detail must ride the rig untouched.

    The trap: 73 rings of alternating radius with only 12 joints, so no
    joint sits where a rib does. Weights that leak between neighboring
    rings would shear ribs into cones — per-ring integrity vs rest is the
    metric that sees it (centerline curvature cannot: rib centres stay
    on-axis no matter how mangled the ribs are).
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_extraction_handles_dense_ribs(self):
        tube, meta = _make_corrugated_duct()
        centerline, _n = TubePath.get_centerline(tube, num_joints=-1)
        self.assertEqual(len(centerline), meta["n_rings"])
        first = om.MVector(*[centerline[0][k] for k in range(3)])
        last = om.MVector(*[centerline[-1][k] for k in range(3)])
        span = sorted([first.x, last.x])
        self.assertLess(abs(span[0] - 0.0), 0.3, "start rim centre off-axis")
        self.assertLess(abs(span[1] - meta["L"]), 0.3, "end rim centre off-axis")

    def test_ribs_survive_bending(self):
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_corrugated_duct()
        rig = TubeRig(tube, rig_name="CorrBend")
        rig.build(strategy="spline", num_joints=12)
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        end_ctrl = rig.bundle.controls[-1]
        cmds.xform(end_ctrl, ws=True, r=True, t=(0, r * 5, 0))
        cmds.refresh()
        integ = M.ring_integrity(tube, rest, rings, r)
        self.assertLess(
            integ["shape_error_max"],
            0.02,
            f"ribs sheared: shape error {integ['shape_error_max']:.3f}",
        )
        self.assertLess(
            integ["radius_ratio_max"] - integ["radius_ratio_min"],
            0.02,
            "rib amplitude no longer uniform along the tube",
        )
        conf = M.conformance(tube, rig.bundle.joints, rings, rig.bundle.curve, r)
        self.assertLess(conf["max"], 0.15, f"off-axis {conf['max']:.3f}r")

    def test_stretch_distributes_uniformly(self):
        """Pulling the end must scale every ring gap by the same factor —
        stretch that bunches into one span reads as rubbery smearing."""
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_corrugated_duct()
        rig = TubeRig(tube, rig_name="CorrStretch")
        rig.build(strategy="spline", num_joints=12)
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        end_ctrl = rig.bundle.controls[-1]
        out = _outward_tangent(rig.bundle.joints, end_ctrl)
        cmds.xform(
            end_ctrl, ws=True, r=True, t=tuple(0.15 * meta["L"] * v for v in out)
        )
        cmds.refresh()
        self.assertLess(
            M.spacing_uniformity(tube, rest, rings),
            0.05,
            "stretch bunched instead of distributing",
        )
        integ = M.ring_integrity(tube, rest, rings, r)
        self.assertLess(integ["shape_error_max"], 0.02)
        self.assertLess(
            integ["radius_ratio_max"] - integ["radius_ratio_min"],
            0.02,
            "volume response uneven across rings",
        )


class TestFittedHose(unittest.TestCase):
    """Crimped-fitting hydraulic hose, with SCRAMBLED vertex ids.

    Two things production meshes do that polyCylinder fixtures never did:
    cross-sections that are not circles (hex nut, stepped ferrule), and
    vertex numbering with no relation to topology (imported/merged mesh).
    Extraction is topological, so the scramble must change nothing.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _zone_indices(self, tube, rings, meta):
        """Ring indices inside the two fitting zones, by rest centroid X
        (the fixture runs along +X, so position IS the arc station)."""
        from rig_metrics import TubeRigMetrics as M

        pts = M.snapshot_points(tube)
        near, far = [], []
        for i, ring in enumerate(rings):
            x = sum(pts[v].x for v in ring) / len(ring)
            if x < meta["fit_d"]:
                near.append(i)
            elif x > meta["L"] - meta["fit_d"]:
                far.append(i)
        return near, far

    def test_extraction_survives_scrambled_ids(self):
        tube, meta = _make_fitted_hose()
        rings = TubePath.get_vertex_rings(tube)
        self.assertEqual(len(rings), meta["n_rings"])
        self.assertTrue(all(len(ring) == 24 for ring in rings))
        centerline, _n = TubePath.get_centerline(tube, num_joints=-1)
        first = om.MVector(*[centerline[0][k] for k in range(3)])
        last = om.MVector(*[centerline[-1][k] for k in range(3)])
        span = sorted([first.x, last.x])
        self.assertLess(abs(span[0] - 0.0), 0.3)
        self.assertLess(abs(span[1] - meta["L"]), 0.3)
        for p in (first, last):
            self.assertLess(math.hypot(p.y, p.z), 0.05, "cap centre off-axis")

    def test_shape_error_is_the_hex_safe_metric(self):
        """Roundness reads ~0.15 on a hex ring AT REST — it can only ever
        gate circular tubes. shape_error compares each ring against its own
        rest section, so hex reads 0 at rest and stays 0 under pose."""
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_fitted_hose()
        rig = TubeRig(tube, rig_name="FitShape")
        rig.build(strategy="spline")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        at_rest = M.ring_integrity(tube, rest, rings, r)
        self.assertGreater(
            at_rest["roundness_max"],
            0.10,
            "hex fixture should defeat roundness by design",
        )
        self.assertLess(at_rest["shape_error_max"], 1e-4)
        end_ctrl = rig.bundle.controls[-1]
        cmds.xform(end_ctrl, ws=True, r=True, t=(0, r * 5, 0))
        cmds.refresh()
        posed = M.ring_integrity(tube, rest, rings, r)
        self.assertLess(
            posed["shape_error_max"],
            0.02,
            f"fitting sections sheared: {posed['shape_error_max']:.3f}",
        )

    def test_fittings_stay_quasi_rigid_under_bend(self):
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_fitted_hose()
        rest_pts = M.snapshot_points(tube)
        rig = TubeRig(tube, rig_name="FitRigid")
        rig.build(strategy="spline")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        near, far = self._zone_indices(tube, rings, meta)
        self.assertTrue(near and far, "fitting zones unresolved")
        end_ctrl = rig.bundle.controls[-1]
        cmds.xform(end_ctrl, ws=True, r=True, t=(0, r * 5, 0))
        cmds.refresh()
        for label, zone in (("near", near), ("far", far)):
            drift = M.rigidity_drift(tube, rings, zone, rest_pts, r)
            self.assertLess(
                drift, 0.35, f"{label} fitting flexed {drift:.3f}r under an end bend"
            )

    def test_stretch_factor_zero_holds_fittings_rigid(self):
        """The animator's lever for metal ends: stretchFactor=0 keeps the
        fittings EXACTLY rigid under an axial pull. With stretch on, the
        whole tube (fittings included) scales — that is the volume system
        working as built, gated here as documented behavior so any future
        per-zone stretch immunity shows up as this assertion flipping."""
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_fitted_hose()
        rest_pts = M.snapshot_points(tube)
        rig = TubeRig(tube, rig_name="FitStretch")
        rig.build(strategy="spline")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        near, far = self._zone_indices(tube, rings, meta)
        end_ctrl = rig.bundle.controls[-1]
        main = rig.bundle.controls[0]
        out = _outward_tangent(rig.bundle.joints, end_ctrl)
        delta = tuple(0.15 * meta["L"] * v for v in out)

        cmds.xform(end_ctrl, ws=True, r=True, t=delta)
        cmds.refresh()
        with_stretch = M.rigidity_drift(tube, rings, near, rest_pts, r)
        self.assertGreater(with_stretch, 0.4, "documented: stretch scales fittings too")

        cmds.setAttr(f"{main}.stretchFactor", 0)
        cmds.refresh()
        held = max(
            M.rigidity_drift(tube, rings, near, rest_pts, r),
            M.rigidity_drift(tube, rings, far, rest_pts, r),
        )
        self.assertLess(
            held, 0.02, f"stretchFactor=0 left fittings drifting {held:.3f}r"
        )


class TestRadiatorHose(unittest.TestCase):
    """Molded pre-bent hose: the rest pose is already curved, tapered, and
    unevenly tessellated, with open clamp-on ends — the shape most of the
    suite's straight-cylinder assumptions would hide behind."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def _built(self, name):
        tube, meta = _make_radiator_hose(name=f"{name}Mesh")
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy="spline")
        return tube, meta, rig

    def test_extraction_spans_open_prebent_tube(self):
        tube, meta = _make_radiator_hose()
        centerline, _n = TubePath.get_centerline(tube, num_joints=-1)
        first = om.MVector(*[centerline[0][k] for k in range(3)])
        last = om.MVector(*[centerline[-1][k] for k in range(3)])
        direct = max((first - meta["start"]).length(), (last - meta["end"]).length())
        flipped = max((first - meta["end"]).length(), (last - meta["start"]).length())
        self.assertLess(
            min(direct, flipped), 0.3, "centerline does not span rim to rim"
        )

    def test_bind_preserves_rest_shape(self):
        """Binding a pre-bent tube must be a no-op on the rest pose — any
        pop means the weights and the rest transforms disagree."""
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_radiator_hose(name="RadBindMesh")
        rest_pts = M.snapshot_points(tube)
        rig = TubeRig(tube, rig_name="RadBind")
        rig.build(strategy="spline")
        cmds.refresh()
        shift = M.max_displacement(tube, rest_pts, meta["r"])
        self.assertLess(shift, 1e-3, f"bind popped the mesh {shift:.4f}r")

    def test_posed_quality_on_curved_rest(self):
        from rig_metrics import TubeRigMetrics as M

        tube, meta, rig = self._built("RadPose")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        rest_curv = M.smoothness(tube, rings, r)["peak_curvature"]
        end_ctrl = rig.bundle.controls[-1]
        rest_align = M.end_alignment(tube, end_ctrl, rings=rings)
        self.assertLess(rest_align, 1.0)
        out = _outward_tangent(rig.bundle.joints, end_ctrl)
        for label, delta in (
            ("lift", (0, r * 5, 0)),
            ("pull", tuple(0.15 * meta["L"] * v for v in out)),
        ):
            cmds.xform(end_ctrl, ws=True, r=True, t=delta)
            cmds.refresh()
            conf = M.conformance(tube, rig.bundle.joints, rings, rig.bundle.curve, r)
            self.assertLess(conf["max"], 0.15, f"{label}: off-axis {conf['max']:.3f}r")
            integ = M.ring_integrity(tube, rest, rings, r)
            self.assertLess(integ["shape_error_max"], 0.02, f"{label}: sheared")
            curv = M.smoothness(tube, rings, r)["peak_curvature"]
            self.assertLess(
                curv, rest_curv * 1.6, f"{label}: creased {rest_curv:.3f} -> {curv:.3f}"
            )
            self.assertLess(
                M.spacing_uniformity(tube, rest, rings), 0.05, f"{label}: rings bunched"
            )
            align = M.end_alignment(tube, end_ctrl, rings=rings)
            self.assertLess(
                abs(align - rest_align),
                5.0,
                f"{label}: end drifted {align:.1f} deg out of square",
            )
            cmds.xform(end_ctrl, ws=True, r=True, t=tuple(-d for d in delta))

    def test_roll_does_not_candy_wrap(self):
        from rig_metrics import TubeRigMetrics as M

        tube, meta, rig = self._built("RadRoll")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        end_ctrl = rig.bundle.controls[-1]
        cmds.setAttr(f"{end_ctrl}.roll", 60)
        cmds.refresh()
        integ = M.ring_integrity(tube, rest, rings, r)
        self.assertGreater(integ["radius_ratio_min"], 0.97, "twist pinched the tube")
        self.assertLess(integ["shape_error_max"], 0.01)


class TestFkSpanBoneIntegrity(unittest.TestCase):
    """Rotation-only FK posing must never stretch a bone.

    Regression: distributed-span FK nested each control under the previous
    one, so a control ORBITED rigidly while its span's joints arced
    gradually — and the span anchor's pointConstraint dragged the joint to
    the control's rigid-orbit position, stretching the boundary bones (159%
    on a 50-degree root key on a pre-bent hose). Centerline curvature reads
    almost nothing (the rings just space out); bone length is the metric
    that sees it.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_fk_rotation_preserves_bone_lengths(self):
        from rig_metrics import TubeRigMetrics as M

        tube, meta = _make_radiator_hose(name="RadFkMesh")
        rig = TubeRig(tube, rig_name="RadFk")
        rig.build(strategy="fk")
        ctrls = rig.bundle.controls
        self.assertGreaterEqual(len(ctrls), 3)
        rest_bones = M.bone_lengths(rig.bundle.joints)
        rest_tail = om.MVector(*_ws(rig.bundle.joints[-1]))
        for label, keys in (
            ("root", [(0, 50)]),
            ("mid", [(len(ctrls) // 2, 40)]),
            ("compound", [(0, 35), (len(ctrls) // 2, 30), (-1, 20)]),
        ):
            for idx, deg in keys:
                cmds.setAttr(f"{ctrls[idx]}.rotateZ", deg)
            cmds.refresh()
            bones = M.bone_lengths(rig.bundle.joints)
            drift = max(abs(b / a - 1.0) for a, b in zip(rest_bones, bones))
            self.assertLess(
                drift, 0.02, f"{label}: rotation stretched a bone {drift * 100:.1f}%"
            )
            # Guard the guard: a rig frozen solid also preserves bone
            # lengths — the keys above must actually carry the tail.
            tail_move = (om.MVector(*_ws(rig.bundle.joints[-1])) - rest_tail).length()
            self.assertGreater(
                tail_move, meta["r"], f"{label}: controls no longer drive the chain"
            )
            for idx, _deg in keys:
                cmds.setAttr(f"{ctrls[idx]}.rotateZ", 0)


class TestAnimatorHandoff(unittest.TestCase):
    """The handoff contract an experienced animator audits first.

    Channel hygiene (nothing keyable that the rig doesn't support), a
    pick-walkable control chain, a selection set, a settings control with
    proxy attrs, and a reference-locked mesh. Snapshot-style so none of it
    can regress silently.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _built(self, strategy, name):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy=strategy)
        return tube, rig

    def _set_members(self, rig):
        set_name = f"{rig.rig_name}_controls_SET"
        self.assertTrue(cmds.objExists(set_name), "controls selection set missing")
        return [
            (cmds.ls(m, long=True) or [m])[0]
            for m in (cmds.sets(set_name, query=True) or [])
        ]

    def test_channel_policy_every_strategy(self):
        """Scale locked+hidden, visibility non-keyable, T/R keyable on every
        control; the settings control exposes ONLY its custom attrs."""
        for strategy in ("spline", "anchor", "fk"):
            cmds.file(new=True, force=True)
            tube, rig = self._built(strategy, f"Hyg{strategy}")
            settings = f"{rig.rig_name}_settings_CTRL"
            self.assertTrue(cmds.objExists(settings), f"{strategy}: no settings ctrl")
            for ctrl in self._set_members(rig):
                is_settings = ctrl.endswith("_settings_CTRL")
                for axis in "xyz":
                    self.assertTrue(
                        cmds.getAttr(f"{ctrl}.s{axis}", lock=True),
                        f"{strategy}: {ctrl}.s{axis} not locked",
                    )
                    self.assertFalse(
                        cmds.getAttr(f"{ctrl}.s{axis}", keyable=True),
                        f"{strategy}: {ctrl}.s{axis} keyable",
                    )
                    t_keyable = cmds.getAttr(f"{ctrl}.t{axis}", keyable=True)
                    self.assertEqual(
                        t_keyable,
                        not is_settings,
                        f"{strategy}: {ctrl}.t{axis} keyable={t_keyable}",
                    )
                self.assertFalse(
                    cmds.getAttr(f"{ctrl}.v", keyable=True),
                    f"{strategy}: {ctrl}.v keyable",
                )
                # The animator-facing claim itself: keying scale sets nothing.
                self.assertFalse(
                    cmds.setKeyframe(ctrl, attribute="sx"),
                    f"{strategy}: {ctrl}.sx accepted a key",
                )

    def test_pickwalk_tags_are_chained(self):
        _tube, rig = self._built("spline", "Walk")
        start, mid, end = (
            rig.bundle.controls[0],
            rig.bundle.controls[1],
            rig.bundle.controls[-1],
        )
        for child, parent in ((mid, start), (end, mid)):
            got = cmds.controller(str(child), q=True, parent=True)
            if isinstance(got, (list, tuple)):
                got = got[0] if got else None
            # The query may answer with the parent's TAG node — resolve it
            # back to the tagged transform before comparing.
            if got and cmds.objExists(got) and cmds.nodeType(got) == "controller":
                objs = (
                    cmds.listConnections(
                        f"{got}.controllerObject", source=True, destination=False
                    )
                    or []
                )
                got = objs[0] if objs else got
            self.assertEqual(
                _leaf(got) if got else None,
                _leaf(parent),
                f"pick-walk parent of {child} is {got}",
            )

    def test_selection_set_contains_the_rig_controls(self):
        _tube, rig = self._built("spline", "SetRig")
        members = {_leaf(m) for m in self._set_members(rig)}
        for ctrl in rig.bundle.controls:
            self.assertIn(_leaf(ctrl), members)
        self.assertIn(f"{rig.rig_name}_settings_CTRL", members)

    def test_settings_proxies_mirror_masters(self):
        _tube, rig = self._built("spline", "Proxy")
        settings = f"{rig.rig_name}_settings_CTRL"
        start, end = rig.bundle.controls[0], rig.bundle.controls[-1]
        for attr, master in (("stretchFactor", start), ("roll", end)):
            self.assertTrue(
                cmds.attributeQuery(attr, node=settings, exists=True),
                f"settings missing proxy {attr}",
            )
        cmds.setAttr(f"{settings}.stretchFactor", 0.0)
        self.assertAlmostEqual(
            cmds.getAttr(f"{str(start)}.stretchFactor"), 0.0, places=6
        )
        cmds.setAttr(f"{str(end)}.roll", 25.0)
        self.assertAlmostEqual(cmds.getAttr(f"{settings}.roll"), 25.0, places=6)

    def test_mesh_display_locked_and_switchable(self):
        tube, rig = self._built("spline", "MeshLock")
        shape = cmds.listRelatives(tube, shapes=True, fullPath=True)[0]
        self.assertEqual(cmds.getAttr(f"{shape}.overrideEnabled"), 1)
        self.assertEqual(cmds.getAttr(f"{shape}.overrideDisplayType"), 2)
        settings = f"{rig.rig_name}_settings_CTRL"
        cmds.setAttr(f"{settings}.meshDisplay", 0)
        self.assertEqual(cmds.getAttr(f"{shape}.overrideDisplayType"), 0)
        rig.teardown()
        self.assertEqual(
            cmds.getAttr(f"{shape}.overrideEnabled"),
            0,
            "teardown left the mesh display-locked",
        )
        self.assertFalse(cmds.objExists(f"{rig.rig_name}_controls_SET"))

    def test_vis_toggles_drive_controls_and_joints(self):
        _tube, rig = self._built("spline", "Vis")
        settings = f"{rig.rig_name}_settings_CTRL"
        cmds.setAttr(f"{settings}.controlsVis", 0)
        self.assertEqual(cmds.getAttr(f"{str(rig.bundle.controls[0])}.v"), 0)
        cmds.setAttr(f"{settings}.controlsVis", 1)
        cmds.setAttr(f"{settings}.jointsVis", 0)
        self.assertEqual(cmds.getAttr(f"{str(rig.bundle.joints[0])}.v"), 0)


def _leaf(node):
    return str(node).split("|")[-1].split(":")[-1]


class TestTweakLayer(unittest.TestCase):
    """The FK-on-IK finesse layer: dual chain, tweaks riding the proxy
    solver chain, bind joints following through offsetParentMatrix wires.

    The load-bearing claims: enabling the layer changes NOTHING until a
    tweak is touched (the proxy chain clones the bind chain under the same
    solver/stretch/twist), a tweak's effect is LOCAL, rotation never
    stretches bones, and the bind chain stays the untouched export skeleton.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _built(self, name, **kwargs):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy="spline", **kwargs)
        return tube, rig

    def _proxy(self, rig, i):
        return cmds.ls(f"{rig.rig_name}_proxy_jnt_{i + 1}", long=True)[0]

    def test_layer_is_a_noop_until_touched(self):
        from rig_metrics import TubeRigMetrics as M

        tube = _make_tube()
        rest_pts = M.snapshot_points(tube)
        rig = TubeRig(tube, rig_name="TwNoop")
        rig.build(strategy="spline")
        self.assertTrue(rig.bundle.tweak_controls, "tweak layer missing")
        cmds.refresh()
        r = M.tube_radius(tube)
        self.assertLess(
            M.max_displacement(tube, rest_pts, r),
            1e-4,
            "enabling the tweak layer moved the mesh at rest",
        )
        for i, jnt in enumerate(rig.bundle.joints):
            jw = cmds.xform(str(jnt), q=True, ws=True, t=True)
            pw = cmds.xform(self._proxy(rig, i), q=True, ws=True, t=True)
            for a, b in zip(jw, pw):
                self.assertAlmostEqual(
                    a, b, places=4, msg=f"bind joint {i} left its proxy at rest"
                )

    def test_tweaks_ride_the_primary_pose(self):
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("TwRide")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        end = str(rig.bundle.controls[-1])
        cmds.xform(end, ws=True, r=True, t=(0, r * 5, 0))
        cmds.setAttr(f"{end}.rotateZ", 20)
        cmds.refresh()
        for i, tweak in enumerate(rig.bundle.tweak_controls):
            tw = cmds.xform(str(tweak), q=True, ws=True, t=True)
            pw = cmds.xform(self._proxy(rig, i), q=True, ws=True, t=True)
            for a, b in zip(tw, pw):
                self.assertAlmostEqual(
                    a, b, places=3, msg=f"tweak {i} fought the driver pose"
                )
        conf = M.conformance(tube, rig.bundle.joints, rings, rig.bundle.curve, r)
        self.assertLess(conf["max"], 0.25, f"posed conformance {conf['max']:.3f}r")

    def test_tweak_rotation_preserves_bone_lengths(self):
        from rig_metrics import TubeRigMetrics as M

        _tube, rig = self._built("TwBones")
        rest = M.bone_lengths(rig.bundle.joints)
        tweaks = rig.bundle.tweak_controls
        for idx, axis, deg in (
            (len(tweaks) // 3, "X", 30),
            (len(tweaks) // 2, "Z", 30),
            (-2, "Y", -30),
        ):
            cmds.setAttr(f"{tweaks[idx]}.rotate{axis}", deg)
        cmds.refresh()
        bones = M.bone_lengths(rig.bundle.joints)
        drift = max(abs(b / a - 1.0) for a, b in zip(rest, bones))
        self.assertLess(drift, 0.02, f"tweak rotation stretched a bone {drift:.1%}")

    def test_tweak_translation_is_local(self):
        from rig_metrics import TubeRigMetrics as M

        # Long tube: locality needs rings well outside the tweak's 4-joint
        # cubic weight support on both sides.
        tube = _make_tube(h=20.0, sy=24)
        rig = TubeRig(tube, rig_name="TwLocal")
        rig.build(strategy="spline")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        tweaks = rig.bundle.tweak_controls
        k = len(tweaks) // 2
        cmds.xform(str(tweaks[k]), ws=True, r=True, t=(0, r, 0))
        cmds.refresh()
        frames = M._ring_frames(TubePath._resolve_mesh_shape(tube), rings)
        moved = [(now[0] - was[0]).length() / r for now, was in zip(frames, rest)]
        peak = max(range(len(moved)), key=lambda i: moved[i])
        self.assertGreater(moved[peak], 0.5, "tweak did not reach the mesh")
        far = [d for i, d in enumerate(moved) if abs(i - peak) > 8]
        self.assertTrue(far, "fixture too short to judge locality")
        self.assertLess(
            max(far), 0.05, "a single tweak displaced rings far along the tube"
        )

    def test_tweak_twist_is_local_and_clean(self):
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("TwTwist")
        rings = M.rings(tube)
        r = M.tube_radius(tube, rings)
        rest = M.rest_frames(tube, rings)
        tweaks = rig.bundle.tweak_controls
        cmds.setAttr(f"{tweaks[len(tweaks) // 2]}.rotateX", 45)
        cmds.refresh()
        integ = M.ring_integrity(tube, rest, rings, r)
        self.assertGreater(
            integ["radius_ratio_min"], 0.9, "local twist pinched the tube"
        )
        self.assertLess(
            integ["shape_error_max"], 0.06, "local twist sheared cross-sections"
        )

    def test_tweaks_survive_stretch(self):
        tube, rig = self._built("TwStretch")
        end = str(rig.bundle.controls[-1])
        out = _outward_tangent(rig.bundle.joints, end)
        cmds.xform(end, ws=True, r=True, t=tuple(3.0 * v for v in out))
        cmds.refresh()
        for i, tweak in enumerate(rig.bundle.tweak_controls):
            tw = cmds.xform(str(tweak), q=True, ws=True, t=True)
            pw = cmds.xform(self._proxy(rig, i), q=True, ws=True, t=True)
            for a, b in zip(tw, pw):
                self.assertAlmostEqual(
                    a, b, places=3, msg=f"tweak {i} lost its proxy under stretch"
                )

    def test_no_evaluation_cycles(self):
        _tube, _rig = self._built("TwCycle")
        self.assertFalse(
            cmds.cycleCheck(all=True, list=True) or [],
            "tweak layer created an evaluation cycle",
        )

    def test_export_skeleton_unchanged(self):
        from mayatk.rig_utils.skinning import SkinUtils

        def chain_spec(rig):
            spec = []
            for j in rig.bundle.joints:
                j = str(j)
                spec.append(
                    (
                        _leaf(j),
                        _leaf((cmds.listRelatives(j, parent=True) or [""])[0]),
                        tuple(round(v, 5) for v in cmds.getAttr(f"{j}.jointOrient")[0]),
                    )
                )
            return spec

        tube, rig = self._built("TwExpA", enable_tweaks=False)
        plain = chain_spec(rig)
        cmds.file(new=True, force=True)
        tube2, rig2 = self._built("TwExpA")  # same rig name, tweaks on
        self.assertEqual(
            chain_spec(rig2), plain, "tweak layer altered the export skeleton"
        )
        sc = SkinUtils.get_skin_cluster(tube2)
        influences = {_leaf(i) for i in SkinUtils.get_influences(sc)}
        self.assertEqual(
            influences,
            {_leaf(j) for j in rig2.bundle.joints},
            "mesh skin influences must stay the bind chain only",
        )

    def test_tweaks_pickable_and_toggleable(self):
        from rig_metrics import TubeRigMetrics as M

        tube, rig = self._built("TwPick")
        r = M.tube_radius(tube)
        usable = M.control_usability(rig.bundle.tweak_controls, r)
        self.assertLess(usable["size_vs_gap"], 1.0, "tweak controls swallow each other")
        settings = f"{rig.rig_name}_settings_CTRL"
        cmds.setAttr(f"{settings}.tweakCtrlsVis", 0)
        grp = cmds.ls(f"{rig.rig_name}_tweak_GRP", long=True)[0]
        self.assertEqual(cmds.getAttr(f"{grp}.visibility"), 0)

    def test_teardown_and_step_rerun_sweep_the_layer(self):
        tube, rig = self._built("TwSweep")
        self.assertTrue(cmds.ls(f"{rig.rig_name}_proxy_*"))
        # Step-1 rerun must not strand the layer against a fresh chain.
        centerline, n = rig.resolve_centerline(-1)
        rig.generate_joint_chain(centerline, num_joints=n, radius=0.5)
        self.assertFalse(
            cmds.ls(f"{rig.rig_name}_proxy_*"), "Step-1 rerun stranded the proxy chain"
        )
        self.assertFalse(cmds.ls(f"{rig.rig_name}_tweak_*"))
        rig.teardown()
        self.assertFalse(cmds.ls(f"{rig.rig_name}_proxy_*", f"{rig.rig_name}_tweak_*"))


class TestSpaceSwitching(unittest.TestCase):
    """local / world / custom spaces on the posable controls.

    Local must be a bit-exact no-op (identity offsetParentMatrix via the
    passthrough default — a constraint-based switch can never claim that),
    world must pin the control against anything happening above its space
    group (rig-group motion, follow drape, auto-bend), and the custom slot
    must be assignable at runtime without constraint surgery.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _built(self, name, **kwargs):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy="spline", **kwargs)
        return tube, rig

    def _identity(self, m, tol=1e-6):
        ident = [1.0 if i % 5 == 0 else 0.0 for i in range(16)]
        return all(abs(a - b) < tol for a, b in zip(m, ident))

    def test_default_is_bitexact_noop(self):
        _tube, rig = self._built("SpDef")
        end = str(rig.bundle.controls[-1])
        self.assertTrue(cmds.attributeQuery("space", node=end, exists=True))
        self.assertEqual(cmds.getAttr(f"{end}.space"), 0)
        grp = cmds.ls(f"{rig.rig_name}_end_space_GRP", long=True)[0]
        self.assertTrue(
            self._identity(cmds.getAttr(f"{grp}.offsetParentMatrix")),
            "local mode must contribute nothing",
        )

    def test_world_space_pins_end_under_group_motion(self):
        _tube, rig = self._built("SpWorld")
        end = str(rig.bundle.controls[-1])
        before = cmds.xform(end, q=True, ws=True, t=True)
        cmds.setAttr(f"{end}.space", 1)
        after_switch = cmds.xform(end, q=True, ws=True, t=True)
        for a, b in zip(before, after_switch):
            self.assertAlmostEqual(a, b, places=4, msg="switching at rest popped")
        cmds.xform(rig.rig_group, r=True, t=(3, 4, 5))
        cmds.refresh()
        pinned = cmds.xform(end, q=True, ws=True, t=True)
        for a, b in zip(before, pinned):
            self.assertAlmostEqual(
                a, b, places=3, msg="world space did not pin the end control"
            )

    def test_mid_world_space_stops_following_the_ends(self):
        _tube, rig = self._built("SpMid")
        mid = str(rig.bundle.controls[1])
        start, end = str(rig.bundle.controls[0]), str(rig.bundle.controls[-1])
        # local: the follow drape carries the mid with the ends
        before = cmds.xform(mid, q=True, ws=True, t=True)
        for c in (start, end):
            cmds.xform(c, ws=True, r=True, t=(0, 2, 0))
        cmds.refresh()
        draped = cmds.xform(mid, q=True, ws=True, t=True)
        self.assertGreater(draped[1] - before[1], 1.0, "follow drape inactive")
        for c in (start, end):
            cmds.xform(c, ws=True, r=True, t=(0, -2, 0))
        # world: the mid holds while the ends move
        cmds.setAttr(f"{mid}.space", 1)
        held_before = cmds.xform(mid, q=True, ws=True, t=True)
        for c in (start, end):
            cmds.xform(c, ws=True, r=True, t=(0, 2, 0))
        cmds.refresh()
        held_after = cmds.xform(mid, q=True, ws=True, t=True)
        for a, b in zip(held_before, held_after):
            self.assertAlmostEqual(
                a, b, places=3, msg="world-pinned mid still follows the ends"
            )

    def test_custom_space_assign_and_clear(self):
        _tube, rig = self._built("SpCust")
        end = str(rig.bundle.controls[-1])
        before = cmds.xform(end, q=True, ws=True, t=True)
        # unassigned custom is safe
        cmds.setAttr(f"{end}.space", 2)
        cmds.refresh()
        after = cmds.xform(end, q=True, ws=True, t=True)
        for a, b in zip(before, after):
            self.assertAlmostEqual(a, b, places=4, msg="unassigned custom moved it")
        # assigned: stationary at assign, follows the target after
        loc = cmds.spaceLocator(name="socket_LOC")[0]
        cmds.xform(loc, ws=True, t=(2, 7, -3))
        rig.set_custom_space(end, loc)
        after_assign = cmds.xform(end, q=True, ws=True, t=True)
        for a, b in zip(before, after_assign):
            self.assertAlmostEqual(a, b, places=4, msg="assignment popped")
        cmds.xform(loc, ws=True, r=True, t=(0, 3, 0))
        cmds.refresh()
        followed = cmds.xform(end, q=True, ws=True, t=True)
        self.assertAlmostEqual(followed[1] - before[1], 3.0, places=3)
        # clear: back to safe
        rig.set_custom_space(end, None)
        cmds.refresh()
        cleared = cmds.xform(end, q=True, ws=True, t=True)
        for a, b in zip(before, cleared):
            self.assertAlmostEqual(a, b, places=3, msg="clearing did not restore")

    def test_anchor_ends_get_spaces(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="SpAnch")
        rig.build(strategy="anchor")
        for ctrl in rig.bundle.controls:
            self.assertTrue(
                cmds.attributeQuery("space", node=str(ctrl), exists=True),
                f"{ctrl} has no space attr",
            )

    def test_space_nodes_teardown_clean(self):
        _tube, rig = self._built("SpTear")
        rig.teardown()
        self.assertEqual(cmds.ls(f"{rig.rig_name}_*space*"), [])


class TestRigHardening(unittest.TestCase):
    """Studio-pipeline realities: whole-rig scaling on every strategy, and
    the rig surviving file referencing (how shots actually consume it)."""

    def setUp(self):
        cmds.file(new=True, force=True)

    def test_fk_scale_rig_group(self):
        """Mirror of the spline/anchor group-scale guards for FK."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="FkScale")
        rig.build(strategy="fk")
        for axis in "XYZ":
            cmds.setAttr(f"{rig.rig_group}.scale{axis}", 2.0)
        cmds.refresh()
        joints = rig.bundle.joints
        ws = cmds.xform(str(joints[len(joints) // 2]), q=True, ws=True, s=True)
        self.assertAlmostEqual(ws[0], 2.0, places=3, msg="FK joint double-transforms")

    def test_referenced_rig_is_animatable(self):
        """Build, save, reference into a fresh scene: channel contract
        intact, controls posable, spaces switch, settings proxies live."""
        import os
        import pythontk as ptk

        tube = _make_tube()
        rig = TubeRig(tube, rig_name="RefRig")
        rig.build(strategy="spline")
        scene = ptk.TempArtifacts("tube_rig_reftest").path(".ma", name="refrig")
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii", force=True)

        cmds.file(new=True, force=True)
        cmds.file(scene, reference=True, namespace="hose")
        try:
            end = "hose:RefRig_end_CTRL"
            self.assertTrue(cmds.objExists(end), "referenced end control missing")
            # channel contract survived the reference
            self.assertTrue(cmds.getAttr(f"{end}.sx", lock=True))
            self.assertTrue(cmds.getAttr(f"{end}.tx", keyable=True))
            # posable: the mesh follows the referenced control
            mesh = "hose:" + _leaf(tube)
            before = cmds.xform(f"{mesh}.vtx[0]", q=True, ws=True, t=True)
            cmds.xform(end, ws=True, r=True, t=(0, 2, 0))
            cmds.refresh()
            after = cmds.xform(f"{mesh}.vtx[0]", q=True, ws=True, t=True)
            self.assertGreater(
                abs(after[1] - before[1]) + abs(after[0] - before[0]),
                1e-3,
                "referenced rig does not deform its mesh",
            )
            cmds.xform(end, ws=True, r=True, t=(0, -2, 0))
            # spaces switch on the referenced rig
            pos = cmds.xform(end, q=True, ws=True, t=True)
            cmds.setAttr(f"{end}.space", 1)
            cmds.refresh()
            pos2 = cmds.xform(end, q=True, ws=True, t=True)
            for a, b in zip(pos, pos2):
                self.assertAlmostEqual(
                    a, b, places=3, msg="switch popped when referenced"
                )
            # settings proxy writes through to the referenced master
            settings = "hose:RefRig_settings_CTRL"
            cmds.setAttr(f"{settings}.stretchFactor", 0.25)
            self.assertAlmostEqual(
                cmds.getAttr("hose:RefRig_start_CTRL.stretchFactor"), 0.25, places=6
            )
        finally:
            cmds.file(new=True, force=True)
            try:
                os.remove(scene)
            except OSError:
                pass


class TestSceneRecord(unittest.TestCase):
    """The rig's identity lives in the scene, not only in the session:
    ``tubeRigData`` on the rig group lets ``for_node`` resolve a rig after a
    restart (empty registry), and ``rename`` moves every node with it."""

    def setUp(self):
        cmds.file(new=True, force=True)

    @staticmethod
    def _restart():
        TubeRig._instances.clear()

    def test_stamp_records_build_settings(self):
        tube = _make_tube()
        TubeRig(tube, rig_name="Rec").build(
            strategy="spline", num_joints=6, enable_auto_bend=True
        )
        data = TubeRig.scene_data("Rec_GRP")
        self.assertEqual(data["name"], "Rec")
        self.assertEqual(data["strategy"], "spline")
        self.assertEqual(data["num_joints"], 6)
        self.assertTrue(data["enable_auto_bend"])
        self.assertEqual(
            cmds.ls(data["mesh_uuid"], long=True), cmds.ls(tube, long=True)
        )

    def test_for_node_resolves_after_restart(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Rec")
        rig.build(strategy="spline", num_joints=6)
        joint, ctrl = rig.bundle.joints[-1], rig.bundle.controls[0]
        self._restart()
        for node in (joint, ctrl, tube, "Rec_GRP"):
            found = TubeRig.for_node(node)
            self.assertIsNotNone(found, f"no rig resolved from {node}")
            self.assertIsNot(found, rig)
            self.assertEqual(found.rig_name, "Rec")
            self.assertEqual(cmds.ls(found.mesh, long=True), cmds.ls(tube, long=True))
        self.assertEqual(len(TubeRig.for_node(joint).joints), 6)
        # The recovered handle acts on the rig.
        TubeRig.for_node(joint).teardown()
        self.assertFalse(cmds.objExists("Rec_GRP"))
        self.assertFalse(cmds.ls(cmds.listHistory(tube) or [], type="skinCluster"))

    def test_for_node_falls_back_to_naming_without_stamp(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Rec")
        rig.build(strategy="fk", num_joints=5)
        cmds.deleteAttr(f"Rec_GRP.{TubeRig.DATA_ATTR}")
        joint = rig.bundle.joints[2]
        self._restart()
        found = TubeRig.for_node(joint)
        self.assertIsNotNone(found)
        self.assertEqual(found.rig_name, "Rec")
        self.assertEqual(cmds.ls(found.mesh, long=True), cmds.ls(tube, long=True))

    def test_unrigged_nodes_resolve_to_nothing(self):
        tube = _make_tube()
        self.assertIsNone(TubeRig.for_node(tube))
        self.assertIsNone(TubeRig.for_node(cmds.group(empty=True, name="loose_GRP")))

    def test_a_duplicated_rig_does_not_resolve_to_the_original(self):
        """Ctrl+D copies the rig record verbatim, mesh UUID and all.

        ``cmds.duplicate`` copies the ``tubeRigData`` string attribute as-is, so
        the copy's record still named the ORIGINAL's mesh. ``from_scene`` trusted
        it, and Remove Rig on the copy deleted the ORIGINAL's skinCluster and
        control set while reporting the copy's name.
        """
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="hose")
        rig.build(strategy="spline", num_joints=6)
        original_mesh = cmds.ls(tube, long=True)[0]

        copy_grp = cmds.duplicate("hose_GRP", name="hose_GRP_copy")[0]
        copy_grp = cmds.ls(copy_grp, long=True)[0]

        # the copy's record still carries the original's mesh_uuid
        self.assertTrue(
            cmds.attributeQuery(TubeRig.DATA_ATTR, node=copy_grp, exists=True),
            "fixture: the duplicate should carry the copied record",
        )

        found = TubeRig.from_scene(copy_grp)
        self.assertIsNotNone(found)
        resolved = cmds.ls(found.mesh, long=True) if found.mesh else []
        self.assertNotEqual(
            resolved[:1],
            [original_mesh],
            "the copy resolved to the ORIGINAL's mesh; tearing it down would "
            "unbind the original",
        )

    def test_a_rig_still_resolves_to_its_own_mesh(self):
        """The identity check must not cost the normal path."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="ownmesh")
        rig.build(strategy="spline", num_joints=6)

        found = TubeRig.from_scene("ownmesh_GRP")
        self.assertIsNotNone(found)
        self.assertEqual(cmds.ls(found.mesh, long=True), cmds.ls(tube, long=True))

    def test_rename_and_teardown_spare_unrelated_nodes_sharing_the_stem(self):
        """A ``<rig>_*`` name glob is not ownership.

        An artist naming a rig after its asset (``cable``) leaves every other
        ``cable_*`` node in the scene matching the sweep: unrigged sibling meshes
        and, because a shader is a DG node, the material itself. Rename turned
        ``cable_B`` into ``hose_B`` and ``cable_rubber_MAT`` into
        ``hose_rubber_MAT``; teardown DELETED the shader outright.
        """
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="cable")
        rig.build(strategy="spline", num_joints=6)

        # bystanders: same stem, nothing to do with the rig
        bystander_mesh = cmds.polyCube(name="cable_B")[0]
        bystander_grp = cmds.group(empty=True, name="cable_spare_GRP")
        bystander_mat = cmds.shadingNode(
            "lambert", asShader=True, name="cable_rubber_MAT"
        )

        self.assertEqual(rig.rename("hose"), "hose")
        for node in (bystander_mesh, bystander_grp, bystander_mat):
            with self.subTest(node=node):
                self.assertTrue(
                    cmds.objExists(node),
                    f"rename renamed an unrelated node: {node}",
                )
        # ...and the rig itself really did move
        self.assertTrue(cmds.objExists("hose_GRP"))
        self.assertFalse(cmds.objExists("cable_GRP"))

        rig.teardown()
        for node in (bystander_mesh, bystander_grp, bystander_mat):
            with self.subTest(node=node, phase="teardown"):
                self.assertTrue(
                    cmds.objExists(node),
                    f"teardown deleted an unrelated node: {node}",
                )
        self.assertFalse(cmds.objExists("hose_GRP"))

    def test_rename_moves_every_node_and_spares_siblings(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="Old")
        rig.build(strategy="spline", num_joints=6)
        sibling = TubeRig(_make_tube(), rig_name="Old_02")
        sibling.build(strategy="spline", num_joints=6)

        self.assertEqual(rig.rename("New"), "New")

        self.assertEqual(rig.rig_name, "New")
        leftovers = [n for n in cmds.ls("Old_*") if not _leaf(n).startswith("Old_02_")]
        self.assertEqual(leftovers, [], "nodes kept the old prefix")
        for n in (
            "New_GRP",
            "New_skinCluster",
            "New_controls_SET",
            "New_curveInfo",
            "New_end_CTRL",
            "New_settings_CTRL",
            "Old_02_GRP",
            "Old_02_skinCluster",
        ):
            self.assertTrue(cmds.objExists(n), f"{n} missing")
        for p in [
            *rig.bundle.joints,
            *rig.bundle.controls,
            rig.bundle.rig_group,
            rig.skin_cluster,
            rig.ik_handle,
        ]:
            self.assertTrue(cmds.objExists(str(p)), f"recorded path stale: {p}")
        self.assertEqual(TubeRig.scene_data("New_GRP")["name"], "New")
        self.assertIs(TubeRig.for_node(rig.bundle.joints[0]), rig)
        # Still a working rig: posing the end control deforms the mesh.
        before = cmds.xform(f"{tube}.vtx[0]", q=True, ws=True, t=True)
        cmds.xform("New_end_CTRL", ws=True, r=True, t=(0, 2, 0))
        cmds.refresh()
        after = cmds.xform(f"{tube}.vtx[0]", q=True, ws=True, t=True)
        self.assertGreater(abs(after[1] - before[1]), 1e-3)
        # Teardown under the new name is complete and leaves the sibling alone.
        rig.teardown()
        self.assertEqual(cmds.ls("New_*"), [])
        self.assertTrue(cmds.objExists("Old_02_skinCluster"))

    def test_rename_onto_a_prefix_a_sibling_extends(self):
        """'Base_02' -> 'Base' is legal beside 'Base_03': the glob overlap is
        handled by the foreign-prefix logic, not refused as a clash."""
        rig = TubeRig(_make_tube(), rig_name="Base_02")
        rig.build(strategy="fk", num_joints=4)
        TubeRig(_make_tube(), rig_name="Base_03").build(strategy="fk", num_joints=4)
        self.assertEqual(rig.rename("Base"), "Base")
        self.assertTrue(cmds.objExists("Base_GRP"))
        self.assertTrue(cmds.objExists("Base_03_GRP"))
        self.assertTrue(cmds.objExists("Base_03_skinCluster"))
        self.assertEqual(cmds.ls("Base_02_*"), [])
        # And tearing 'Base' down still spares 'Base_03'.
        rig.teardown()
        self.assertTrue(cmds.objExists("Base_03_skinCluster"))

    def test_unstamped_user_hierarchy_is_not_a_rig(self):
        """A hand-built '<X>_GRP' + '<X>_jnt_1' must NOT resolve as a tube
        rig — Remove Rig would otherwise delete a user's group."""
        grp = cmds.group(empty=True, name="Arm_GRP")
        cmds.createNode("joint", name="Arm_jnt_1", parent=grp)
        self.assertIsNone(TubeRig.for_node("Arm_jnt_1"))
        self.assertIsNone(TubeRig.for_node(grp))

    def test_rename_refuses_a_name_in_use(self):
        rig = TubeRig(_make_tube(), rig_name="Old")
        rig.build(strategy="fk", num_joints=4)
        TubeRig(_make_tube(), rig_name="Other").build(strategy="fk", num_joints=4)
        with self.assertRaises(ValueError):
            rig.rename("Other")
        self.assertEqual(rig.rig_name, "Old")
        self.assertTrue(cmds.objExists("Old_GRP"))


class TestRebindSkin(unittest.TestCase):
    """``TubeRig.rebind_skin`` — recovery for a rig whose bind was destroyed.

    A UV round-trip, a Delete History, or a Bake Non-Deformer History run on
    the tube removes the skinCluster and leaves the joints, controls, IK and
    rig group untouched — the mesh simply stops following. The weights are
    solved analytically from the centerline, so nothing has to be stored:
    re-solving reproduces the original bind exactly.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    def _rig(self, name="hose", num_joints=8):
        tube = _make_tube(axis=(0, 1, 0), h=12.0, sy=20, sx=16)
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy="spline", num_joints=num_joints, num_controls=3)
        return rig, tube

    @staticmethod
    def _sample(mesh):
        flat = cmds.xform(f"{mesh}.vtx[*]", q=True, ws=True, t=True) or []
        return flat

    def test_rebind_reproduces_the_original_bind(self):
        rig, tube = self._rig()
        ctrl = rig.bundle.controls[1]

        cmds.setAttr(f"{ctrl}.translateX", 3)
        reference = self._sample(tube)
        cmds.setAttr(f"{ctrl}.translateX", 0)

        # Destroy the bind the way a UV/history op does.
        cmds.delete(tube, constructionHistory=True)
        self.assertIsNone(SkinUtils.get_skin_cluster(tube))

        recovered = TubeRig.from_scene(tube)
        self.assertIsNotNone(recovered)
        self.assertIsNotNone(recovered.rebind_skin())

        cmds.setAttr(f"{ctrl}.translateX", 3)
        after = self._sample(tube)
        self.assertEqual(len(reference), len(after))
        self.assertLess(
            max(abs(a - b) for a, b in zip(reference, after)),
            1e-4,
            "rebind did not reproduce the original deformation",
        )

    def test_rebind_replaces_a_live_skin_cluster(self):
        """Re-running on a healthy rig re-binds rather than erroring."""
        rig, tube = self._rig()
        self.assertIsNotNone(rig.skin_cluster)

        self.assertIsNotNone(rig.rebind_skin())

        self.assertIsNotNone(SkinUtils.get_skin_cluster(tube))
        # No accumulation: one cluster on the mesh, not two.
        shape = cmds.listRelatives(tube, shapes=True, ni=True, fullPath=True)[0]
        clusters = cmds.ls(cmds.listHistory(shape) or [], type="skinCluster")
        self.assertEqual(len(clusters), 1, clusters)

    def test_rebind_restores_the_display_lock(self):
        """The tube must go back to being marquee-proof after recovery."""
        rig, tube = self._rig()
        cmds.delete(tube, constructionHistory=True)
        shape = cmds.listRelatives(tube, shapes=True, ni=True, fullPath=True)[0]
        cmds.setAttr(f"{shape}.overrideEnabled", 0)

        TubeRig.from_scene(tube).rebind_skin()

        self.assertTrue(cmds.getAttr(f"{shape}.overrideEnabled"))

    def _make_legacy(self, rig):
        """Strip the scene record, leaving only the pre-DATA_ATTR markers.

        Rigs built before ``DATA_ATTR`` existed (the VDATS production scene is
        full of them) resolve ONLY through the skinCluster's first influence,
        so destroying the bind cuts the mesh off from its rig entirely.
        """
        grp = f"{rig.rig_name}_GRP"
        cmds.deleteAttr(f"{grp}.{TubeRig.DATA_ATTR}")
        return grp

    def test_legacy_rig_loses_every_route_from_the_mesh(self):
        """Pins the real-scene finding this rescue path exists for."""
        rig, tube = self._rig(name="legacyA")
        self._make_legacy(rig)
        self.assertIsNotNone(
            TubeRig._rig_group_of(tube), "legacy marker should resolve while bound"
        )

        cmds.delete(tube, constructionHistory=True)

        self.assertIsNone(TubeRig._rig_group_of(tube))
        self.assertIsNone(TubeRig.from_scene(tube))

    def test_legacy_rig_rebinds_when_the_mesh_is_paired_explicitly(self):
        rig, tube = self._rig(name="legacyB")
        ctrl = rig.bundle.controls[1]
        cmds.setAttr(f"{ctrl}.translateX", 3)
        reference = self._sample(tube)
        cmds.setAttr(f"{ctrl}.translateX", 0)

        self._make_legacy(rig)
        cmds.delete(tube, constructionHistory=True)

        # The only surviving route: a joint / control of the rig.
        recovered = TubeRig.from_scene(f"{rig.rig_name}_jnt_1")
        self.assertIsNotNone(recovered)
        self.assertIsNotNone(recovered.rebind_skin(mesh=tube))

        cmds.setAttr(f"{ctrl}.translateX", 3)
        self.assertLess(
            max(abs(a - b) for a, b in zip(reference, self._sample(tube))), 1e-4
        )

    def test_rebind_upgrades_a_legacy_rig_so_the_mesh_resolves_next_time(self):
        """One rescue is enough — the rig stops being legacy."""
        rig, tube = self._rig(name="legacyC")
        self._make_legacy(rig)
        cmds.delete(tube, constructionHistory=True)

        TubeRig.from_scene(f"{rig.rig_name}_jnt_1").rebind_skin(mesh=tube)

        cmds.delete(tube, constructionHistory=True)  # break it again
        self.assertIsNotNone(
            TubeRig.from_scene(tube),
            "rebind should have stamped the scene record",
        )

    def test_rebind_refuses_a_mesh_that_is_not_a_mesh(self):
        rig, _tube = self._rig(name="legacyD")
        with self.assertRaises(ValueError):
            rig.rebind_skin(mesh=f"{rig.rig_name}_GRP")

    def test_rebind_without_joints_raises(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="unbuilt")
        with self.assertRaises(ValueError):
            rig.rebind_skin()

    def test_rebind_without_a_mesh_raises(self):
        rig, tube = self._rig()
        cmds.delete(tube)
        recovered = TubeRig.from_scene(f"{rig.rig_name}_GRP")
        self.assertIsNotNone(recovered)
        with self.assertRaises(ValueError):
            recovered.rebind_skin()


class TestRigUnderAnimatedParent(unittest.TestCase):
    """The rig must live in the mesh's own parent space — VDATS wire looms,
    2026-08-30: each loom sits under an animated module locator (locked,
    non-identity local transform) and one end is constrained to a plug that
    rides the same locator.

    A skinCluster deforms in the mesh's BIND-TIME world frame (``geomMatrix``),
    so a mesh whose transform also follows its animated parent is transformed
    twice as soon as any joint follows that parent (an anchored end): the plug
    end of the loom moved 2.01x the module's motion, the free end 0.99x, the
    middle 1.6x (measured on the production scene). With the rig group at
    world root the controls were also left behind, and the nearest-end guard
    in ``constrain_end_with_falloff`` compared stale joint positions against
    plugs that had moved with the module — so the WRONG end followed the plug.

    The fix: the rig group is created beside the mesh (under its parent) so
    every joint and control rides whatever animates the mesh, and the mesh's
    world matrix is pinned (``Matrices.pin_world_matrix``: the parent's world
    matrix folded into ``offsetParentMatrix``, ``inheritsTransform`` off) so
    the skin alone carries it — the classic "deformed geometry must not
    inherit" idiom, without touching the asset's locked channels.
    """

    def setUp(self):
        cmds.file(new=True, force=True)

    LOCKABLE = ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz")

    def _module(self):
        """A tube nested under an offset module hierarchy with locked channels,
        plus a plug locator at its +X end riding the same module."""
        root = cmds.group(empty=True, name="module_GRP")
        cmds.xform(root, ws=True, t=(3, 4, 5), ro=(0, 30, 0))
        module = cmds.group(empty=True, name="module_LOC", parent=root)
        holder = cmds.group(empty=True, name="looms_GRP", parent=module)
        cmds.xform(holder, ws=True, t=(-1, 2, 0))
        tube = _make_tube()  # spans x in [-5, 5] at the world origin
        tube = cmds.ls(cmds.parent(tube, holder)[0], long=True)[0]
        for ch in self.LOCKABLE:
            cmds.setAttr(f"{tube}.{ch}", lock=True)
        plug = cmds.spaceLocator(name="plug_LOC")[0]
        cmds.xform(plug, ws=True, t=(5, 0, 0))
        plug = cmds.ls(cmds.parent(plug, module)[0], long=True)[0]
        return module, cmds.ls(holder, long=True)[0], tube, plug

    @staticmethod
    def _world(node):
        return om.MMatrix(cmds.xform(node, q=True, ws=True, m=True))

    def _move_module(self, module, t=(10.0, -5.0, 2.0), r=(0.0, 40.0, 0.0)):
        """Move the module; return the rigid world delta it applied."""
        before = self._world(module)
        cmds.setAttr(f"{module}.translate", *t, type="double3")
        cmds.setAttr(f"{module}.rotate", *r, type="double3")
        return before.inverse() * self._world(module)

    def _assert_rigid(self, before, after, delta, msg, tol=1e-3):
        """Every vertex moved exactly with the module (ratio 1, not 0 or 2)."""
        worst = 0.0
        for p0, p1 in zip(before, after):
            expected = om.MPoint(*p0) * delta
            worst = max(worst, (om.MVector(expected) - om.MVector(*p1)).length())
        self.assertLess(worst, tol, f"{msg}: max deviation {worst:.3f}")

    def _build(self, tube, name="loom"):
        rig = TubeRig(tube, rig_name=name)
        rig.build(strategy="spline", num_joints=-1)
        return rig

    def test_rig_group_lives_beside_the_mesh(self):
        _, holder, tube, _ = self._module()
        rig = self._build(tube)
        grp_parent = cmds.listRelatives(rig.rig_group, parent=True, fullPath=True)
        self.assertEqual(
            grp_parent, [holder], "rig group must be created under the mesh's parent"
        )
        # The mesh itself stays exactly where the asset put it.
        self.assertEqual(cmds.listRelatives(tube, parent=True, fullPath=True), [holder])
        for ch in self.LOCKABLE:
            self.assertTrue(cmds.getAttr(f"{tube}.{ch}", lock=True), f"{ch} unlocked")

    def test_build_leaves_the_rest_pose_alone(self):
        _, _, tube, _ = self._module()
        world_before = self._world(tube)
        verts_before = _all_vertex_positions(tube)
        self._build(tube)
        self.assertTrue(
            world_before.isEquivalent(self._world(tube), 1e-6),
            "pinning the mesh must not move it",
        )
        self._assert_rigid(
            verts_before,
            _all_vertex_positions(tube),
            om.MMatrix(),
            "build moved the rest pose",
        )

    def test_anchored_rig_rides_the_module_without_double_transform(self):
        module, _, tube, plug = self._module()
        rig = self._build(tube)
        joints = [str(j) for j in rig.bundle.joints]
        rig.constrain_end_with_falloff(joints, plug, falloff=2.0, joint_index=-1)
        verts = _all_vertex_positions(tube)
        end_ctrl = self._world(rig.bundle.controls[-1])
        joint_0 = self._world(joints[0])

        delta = self._move_module(module)

        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "loom did not move rigidly with its module (double transform)",
        )
        # The whole rig came along — controls and joints alike.
        self.assertTrue(
            (end_ctrl * delta).isEquivalent(self._world(rig.bundle.controls[-1]), 1e-3),
            "end control was left behind",
        )
        self.assertTrue(
            (joint_0 * delta).isEquivalent(self._world(joints[0]), 1e-3),
            "root joint was left behind",
        )

    def test_step_bind_keeps_the_mesh_in_its_hierarchy(self):
        """Step 3 (bind_joint_chain) used to unparent the mesh to world root and
        unlock its channels — destroying the module's export hierarchy."""
        module, holder, tube, plug = self._module()
        rig = TubeRig(tube, rig_name="stepLoom")
        centerline, n = rig.resolve_centerline(-1)
        joint_radius, size = rig.resolve_sizes(centerline, -1.0)
        joints = rig.generate_joint_chain(centerline, num_joints=n, radius=joint_radius)
        rig.create_spline_controls(joints, centerline=centerline, size=size)
        verts = _all_vertex_positions(tube)

        self.assertTrue(rig.bind_joint_chain(tube, joints))

        self.assertTrue(cmds.objExists(tube), "mesh path changed (reparented)")
        self.assertEqual(cmds.listRelatives(tube, parent=True, fullPath=True), [holder])
        for ch in self.LOCKABLE:
            self.assertTrue(cmds.getAttr(f"{tube}.{ch}", lock=True), f"{ch} unlocked")
        self.assertEqual(
            cmds.listRelatives(rig.rig_group, parent=True, fullPath=True), [holder]
        )
        delta = self._move_module(module)
        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "step-built loom did not ride its module",
        )

    def test_teardown_restores_parent_inheritance(self):
        module, _, tube, _ = self._module()
        rig = self._build(tube)
        verts = _all_vertex_positions(tube)
        rig.teardown()
        self.assertTrue(cmds.getAttr(f"{tube}.inheritsTransform"))
        self.assertTrue(
            om.MMatrix(cmds.getAttr(f"{tube}.offsetParentMatrix")).isEquivalent(
                om.MMatrix(), 1e-9
            ),
            "offsetParentMatrix not reset",
        )
        delta = self._move_module(module)
        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "unrigged mesh must follow its parent again",
        )

    def test_plug_end_follows_the_plug_after_the_module_moved(self):
        """The reported symptom: constrain a loom end to its plug AFTER the
        module has moved away from where the rig was built, move the plug —
        and the wrong end of the loom follows. The nearest-end guard read
        joints left at the build position against a plug that had moved with
        the module; a rig that rides the module never sees stale distances."""
        module, _, tube, plug = self._module()
        rig = self._build(tube)
        joints = [str(j) for j in rig.bundle.joints]
        # Far enough that the plug lands nearer the OLD position of the start
        # joint than the end joint's (the tube spans 10 units).
        self._move_module(module, t=(-20.0, 0.0, 0.0), r=(0.0, 0.0, 0.0))

        rig.constrain_end_with_falloff(joints, plug, falloff=2.0, joint_index=-1)

        before = _all_vertex_positions(tube)
        p_plug = om.MVector(*cmds.xform(plug, q=True, ws=True, t=True))
        by_distance = sorted(
            range(len(before)), key=lambda i: (om.MVector(*before[i]) - p_plug).length()
        )
        tenth = max(1, len(before) // 10)
        plug_end, far_end = by_distance[:tenth], by_distance[-tenth:]
        cmds.setAttr(
            f"{plug}.translate",
            *[
                v + d
                for v, d in zip(cmds.getAttr(f"{plug}.translate")[0], (0.0, 3.0, 0.0))
            ],
            type="double3",
        )
        after = _all_vertex_positions(tube)

        def moved(idx):
            return sum(
                (om.MVector(*after[i]) - om.MVector(*before[i])).length() for i in idx
            ) / len(idx)

        self.assertGreater(moved(plug_end), 2.0, "plug end did not follow the plug")
        self.assertLess(moved(far_end), 0.3, "the opposite end moved instead")

    def test_anchoring_at_a_moved_pose_does_not_move_the_mesh(self):
        """Adding the end anchor while the module is away from the build pose
        must not move a single vertex: Maya registers a new influence's bind
        pose where it stands NOW, while the mesh's skin frame is pinned at the
        build pose — so the vertices the anchor drives snapped back to the
        rest frame the moment they were weighted (10 units, measured)."""
        module, _, tube, plug = self._module()
        rig = self._build(tube)
        joints = [str(j) for j in rig.bundle.joints]
        self._move_module(module, t=(-20.0, 0.0, 0.0), r=(0.0, 0.0, 0.0))
        verts = _all_vertex_positions(tube)

        rig.constrain_end_with_falloff(joints, plug, falloff=2.0, joint_index=-1)

        self._assert_rigid(
            verts, _all_vertex_positions(tube), om.MMatrix(), "anchoring moved the mesh"
        )
        # And the anchored end still rides the module afterwards.
        delta = self._move_module(module, t=(4.0, 6.0, -3.0), r=(0.0, 25.0, 0.0))
        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "anchored loom did not ride its module",
        )

    def test_custom_rig_group_elsewhere_leaves_the_mesh_inheriting(self):
        """The pin is only right while the rig rides the mesh's parent. A rig
        built into a caller-supplied group that does NOT (world root here —
        also the layout every rig built before this change has) keeps the
        mesh inheriting: freezing it would stop the loom following its module
        at all, which is worse than the double transform it had."""
        module, _, tube, _ = self._module()
        grp = cmds.group(empty=True, name="elsewhere_GRP")
        rig = TubeRig(tube, rig_name="legacy", rig_group=grp)
        rig.build(strategy="spline", num_joints=-1)
        self.assertTrue(cmds.objExists(grp), "build deleted the caller's group")
        self.assertTrue(
            cmds.listRelatives(grp, children=True), "rig not built into the group"
        )
        self.assertIsNone(cmds.listRelatives(grp, parent=True))
        self.assertTrue(cmds.getAttr(f"{tube}.inheritsTransform"), "mesh was pinned")
        self.assertFalse((TubeRig.scene_data(grp) or {}).get("mesh_pinned"))
        verts = _all_vertex_positions(tube)
        delta = self._move_module(module)
        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "an unpinned mesh must still follow its module",
        )
        # A rebuild goes into the same group; a teardown hands it back empty.
        rig.build(strategy="spline", num_joints=-1)
        self.assertTrue(cmds.objExists(grp), "rebuild deleted the caller's group")
        self.assertTrue(cmds.listRelatives(grp, children=True))
        rig.teardown()
        self.assertTrue(cmds.objExists(grp), "teardown deleted the caller's group")
        self.assertFalse(cmds.listRelatives(grp, children=True))
        self.assertIsNone(TubeRig.scene_data(grp))

    def test_teardown_after_the_mesh_was_renamed(self):
        """A rename must strand neither the pin nor the display lock: the rig
        resolves its mesh by UUID, not by the path it recorded at build."""
        module, _, tube, _ = self._module()
        rig = self._build(tube)
        renamed = cmds.ls(cmds.rename(tube, "renamed_loom"), long=True)[0]

        rig.teardown()

        self.assertTrue(
            cmds.getAttr(f"{renamed}.inheritsTransform"), "pin not released"
        )
        self.assertFalse(
            cmds.ls(cmds.listHistory(renamed) or [], type="skinCluster"),
            "skin not removed",
        )
        shape = cmds.listRelatives(renamed, shapes=True, fullPath=True)[0]
        self.assertEqual(
            cmds.getAttr(f"{shape}.overrideEnabled"), 0, "display lock not restored"
        )

    def test_rebind_at_a_moved_pose_keeps_the_mesh_on_the_module(self):
        """A rebind re-pins the mesh where the module is NOW (its joints are
        there), rather than leaving it frozen at the original bind pose."""
        module, _, tube, _ = self._module()
        rig = self._build(tube)
        verts = _all_vertex_positions(tube)
        delta = self._move_module(module)
        rig.rebind_skin()
        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "rebound mesh is not on its module",
        )
        # And it keeps riding the module afterwards.
        verts = _all_vertex_positions(tube)
        delta = self._move_module(module, t=(0.0, 8.0, 0.0), r=(0.0, -20.0, 0.0))
        self._assert_rigid(
            verts,
            _all_vertex_positions(tube),
            delta,
            "rebound mesh stopped following its module",
        )


class TestRigOperationUndo(unittest.TestCase):
    """Every rig operation is ONE entry on Maya's undo queue.

    Regression (2026-09-01): ``rename`` and ``rebind_skin`` were decorated
    ``@CoreUtils.undoable`` but ``build`` and ``teardown`` were not -- only the
    UI slots chunked them. So a rig built or removed from a script (or from a
    tentacle slot that does not wrap the call itself) left one undo entry PER
    ``cmds`` call: Ctrl+Z peeled a rig apart node by node, and *Remove Rig* was
    unrecoverable in practice.
    """

    #: Maya's IK solvers are scene singletons created on demand; they are not
    #: part of any rig and Maya does not remove them on undo.
    _SOLVERS = {"ikRPsolver", "ikSCsolver", "ikSplineSolver", "hikSolver"}

    def setUp(self):
        cmds.file(new=True, force=True)
        # Batch/standalone Maya starts with undo recording OFF -- without this
        # every assertion below would pass vacuously.
        cmds.undoInfo(state=True, infinity=True)

    def _census(self):
        return {n for n in cmds.ls(long=True) if n.split("|")[-1] not in self._SOLVERS}

    def test_build_is_a_single_undo(self):
        tube = _make_tube()
        before = self._census()

        rig = TubeRig(tube, rig_name="UndoBuild")
        rig.build(strategy="spline", num_joints=10, num_controls=3)
        self.assertGreater(len(self._census() - before), 50, "build created nothing")
        self.assertEqual(cmds.undoInfo(q=True, undoName=True), "Tube Rig: Build")

        cmds.undo()
        self.assertEqual(
            self._census() - before, set(), "one undo did not revert the whole build"
        )

    def test_teardown_is_a_single_undo(self):
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="UndoRemove")
        rig.build(strategy="spline", num_joints=10, num_controls=3)
        rigged = self._census()

        TubeRig.from_scene(tube).teardown()
        self.assertGreater(len(rigged - self._census()), 50, "teardown removed nothing")
        self.assertEqual(cmds.undoInfo(q=True, undoName=True), "Tube Rig: Remove")

        cmds.undo()
        self.assertEqual(
            rigged - self._census(), set(), "one undo did not restore the removed rig"
        )
        # The bind is the part a user cannot rebuild by hand -- prove it is back.
        self.assertTrue(cmds.ls(type="skinCluster"))

    def test_rebuild_over_a_rig_is_a_single_undo(self):
        """``build`` tears the previous rig down first -- both halves, one undo."""
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="UndoRebuild")
        rig.build(strategy="spline", num_joints=10, num_controls=3)
        first = self._census()

        TubeRig.from_scene(tube).build(strategy="spline", num_joints=6, num_controls=3)
        cmds.undo()
        self.assertEqual(
            self._census() ^ first,
            set(),
            "one undo did not revert a rebuild (nested teardown escaped the chunk)",
        )

    def test_build_and_teardown_report_phases(self):
        """The progress hook is what tells a user a suspended viewport is working."""
        tube = _make_tube()
        seen = []

        rig = TubeRig(tube, rig_name="UndoPhases")
        rig.build(
            strategy="spline",
            num_joints=10,
            num_controls=3,
            progress=lambda current, total, message: seen.append(
                (current, total, message)
            ),
        )
        self.assertGreaterEqual(len(seen), 4, f"too few build phases: {seen}")
        # Indeterminate shape: a rig's phase count varies with strategy/options,
        # so a value/total would be a fiction -- the message is the signal.
        for current, total, message in seen:
            self.assertIsNone(current)
            self.assertEqual(total, 0)
            self.assertTrue(message)

        seen.clear()
        rig.teardown(progress=lambda c, t, m: seen.append(m))
        self.assertGreaterEqual(len(seen), 3, f"too few teardown phases: {seen}")

    def test_rebuild_reports_the_nested_teardown_phases(self):
        """A bare nested call inherits the caller's hook.

        ``build`` over an existing rig calls ``teardown()`` with no hook of its
        own; ``_reporting(None)`` used to MASK the build's hook there, so the
        teardown went silent exactly when a rebuild was running.
        """
        tube = _make_tube()
        rig = TubeRig(tube, rig_name="UndoInherit")
        rig.build(strategy="spline", num_joints=8, num_controls=3)

        seen = []
        rig.build(
            strategy="spline",
            num_joints=6,
            num_controls=3,
            progress=lambda c, t, m: seen.append(m),
        )
        self.assertTrue(
            any(m.startswith("Removing rig") for m in seen),
            f"nested teardown phases were masked: {seen}",
        )
        # And the hook did not leak past the operation.
        self.assertIsNone(rig._progress)

    def test_a_raising_progress_hook_cannot_abort_a_build(self):
        """Feedback failing is never a reason to leave a half-built rig behind."""
        tube = _make_tube()

        def boom(*args, **kwargs):
            raise RuntimeError("hook down")

        rig = TubeRig(tube, rig_name="UndoHookFail")
        rig.build(strategy="spline", num_joints=8, num_controls=3, progress=boom)
        self.assertTrue(rig.bundle and rig.bundle.joints)


if __name__ == "__main__":
    unittest.main()
