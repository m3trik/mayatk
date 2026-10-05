# !/usr/bin/python
# coding=utf-8
"""ArticulatedRig -- the engine, the end control, the grab and the record, in Maya.

The fixture is the production magnifier's shape, built from primitives under
a scaled asset node the way the production prop sits (``ASSET`` x2 > ``ARM``
> six part groups): a base plate and post, a pole on a knob swivel, an elbow
knob, an upper tube with a collar knob, an outer rod with an inner rod running
inside it, and a head on a small ball. Every part is a GROUP of shells, as a
modelled prop's often are.

Pinned here: the proposal, a build that moves nothing at rest and a teardown
that puts everything back exactly, every control on its own joint and the
telescope's arrow clear of its tubes, Maya evaluating the joints exactly as
``ptk.ArticulationModel`` does (what the runtimes play), the end control (the
head follows it, turns with it, plays its keys back, hands over to FK without
a jump, and moves in a scene opened anywhere -- its solve is a MEL port of the
model, held to the model's conformance cases), the grab (the ball
head taking a world turn is the quaternion-order check), limits, give, undo,
and the post-rig edits carrying animation across -- rebuild, insert (the
telescope added after the fact), fold, retype, the pivots adjusted by hand --
curves, layers and a moved rig group alike.
"""

import math
import os
import random
import re
import unittest

import maya.api.OpenMaya as om
import maya.cmds as cmds

import pythontk as ptk
from base_test import MayaTkTestCase

from mayatk.rig_utils.articulated_rig import ArticulatedRig, ArticulatedRigGrab
from mayatk.rig_utils.articulated_rig._solver_expression import (
    SolverExpression,
    _MelText,
)

SCALE = 2.0
R = (0.966, 0.259, 0.0)


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _add(p, d, s=1.0):
    return tuple(a + b * s for a, b in zip(p, d))


def _cyl(name, p0, p1, radius, sides=12):
    axis = tuple(b - a for a, b in zip(p0, p1))
    height = math.sqrt(sum(c * c for c in axis))
    node = cmds.polyCylinder(
        name=name, r=radius, h=height, ax=axis, sx=sides, sy=2, ch=False
    )[0]
    cmds.move(*[(a + b) / 2 for a, b in zip(p0, p1)], node)
    return node


def _cube(name, centre, size):
    node = cmds.polyCube(name=name, w=size[0], h=size[1], d=size[2], ch=False)[0]
    cmds.move(*centre, node)
    return node


def _sphere(name, centre, radius):
    node = cmds.polySphere(name=name, r=radius, sx=8, sy=6, ch=False)[0]
    cmds.move(*centre, node)
    return node


ELBOW = (0.0, 32.0, 0.0)
COLLAR = _add(ELBOW, (0.8, 0.6, 0.0), 25)
ROD_END = _add(COLLAR, _norm(R), 32)
INNER_0 = _add(COLLAR, _norm(R), 20)
INNER_1 = _add(COLLAR, _norm(R), 40)
BALL = _add(INNER_1, _norm(R), 0.7)


def build_arm():
    """The fixture; returns the ``ARM`` group (its six part groups under it)."""
    r = _norm(R)
    parts = {
        "BASE": [
            _cube("plate", (0, 0.45, 0), (14, 0.9, 14)),
            _cyl("post", (0, 0.9, 0), (0, 7, 0), 2.0),
        ],
        "LEG_1": [
            _cyl("housing", (0, 4, 0), (0, 10, 0), 2.5),
            _cyl("pole", (0, 10, 0), (0, 31, 0), 2.47),
            _cube("tilt_knob", (0, 8, 3.4), (6, 2, 1)),
        ],
        "LEG_2": [
            _cyl("tube", ELBOW, COLLAR, 2.47),
            _cube("elbow_knob", (ELBOW[0], ELBOW[1], 3.4), (6, 2, 1)),
            _cube("collar_knob", (COLLAR[0], COLLAR[1], -3.4), (6, 2, 1)),
        ],
        "LEG_3": [_cyl("rod", COLLAR, ROD_END, 2.0)],
        "LEG_4": [_cyl("inner", INNER_0, INNER_1, 1.1)],
        "HEAD": [
            _sphere("ball", BALL, 0.7),
            _cyl("ring", _add(BALL, r, 12), _add(BALL, r, 12.3), 11.0, sides=24),
            _cyl("stub", BALL, _add(BALL, r, 1.5), 0.35),
        ],
    }
    groups = [cmds.group(shells, name=name) for name, shells in parts.items()]
    arm = cmds.group(groups, name="ARM")
    asset = cmds.group(arm, name="ASSET")
    cmds.setAttr(f"{asset}.scale", SCALE, SCALE, SCALE)
    return f"|{asset}|{arm}"


def _world(node):
    return om.MMatrix(cmds.getAttr(f"{node}.worldMatrix[0]"))


def _w(point):
    """A fixture point (built in the arm's own space) in world space: the
    asset scales about ITS pivot -- the bounding-box centre ``cmds.group``
    gave it -- so this is not just ``point * SCALE``."""
    p = om.MPoint(*point) * _world("ARM")
    return [p.x, p.y, p.z]


def _mdiff(a, b):
    return max(abs(a[i] - b[i]) for i in range(16))


def _rotation(node):
    return om.MTransformationMatrix(_world(node)).rotation(asQuaternion=True)


def _same_turn(a, b, tol=1e-4):
    return a.isEquivalent(b, tol) or a.isEquivalent(b.negate(), tol)


PARTS = ["BASE", "LEG_1", "LEG_2", "LEG_3", "LEG_4", "HEAD"]


class ArticulatedRigCase(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.arm = build_arm()
        self.rest = {p: _world(p) for p in PARTS + ["ball", "ring"]}

    def build(self, links=None, **kwargs):
        if links is None:
            plan = ArticulatedRig.analyze(self.arm)
            return ArticulatedRig.create(plan["links"], plan["joints"], **kwargs)
        return ArticulatedRig.create(links, **kwargs)

    def assertAtRest(self, tol=1e-6):
        worst = max(_mdiff(self.rest[p], _world(p)) for p in self.rest)
        self.assertLess(worst, tol, "a part moved")

    @staticmethod
    def random_state(model, rng, spread=40.0, fraction=1.0):
        state = []
        for slot in range(len(model.channels)):
            lo, hi = model.limits(slot)
            state.append(
                rng.uniform(
                    -spread if lo is None else lo * fraction,
                    spread if hi is None else hi * fraction,
                )
            )
        return state


class TestAnalyze(ArticulatedRigCase):
    def test_the_magnifier_shape_proposes_its_five_joints(self):
        plan = ArticulatedRig.analyze(self.arm)
        self.assertEqual(
            [p.split("|")[-1] for link in plan["links"] for p in link], PARTS
        )
        self.assertEqual(
            [j["type"] for j in plan["joints"]],
            ["universal", "hinge", "hinge", "slide", "ball"],
        )
        want = [(0, 8, 0), ELBOW, COLLAR, ROD_END, BALL]
        for joint, point in zip(plan["joints"], want):
            self.assertLess(
                math.dist(joint["position"], _w(point)),
                0.3 * SCALE,
                (joint["type"], joint["position"]),
            )
        lo, hi = plan["joints"][3]["limits"]["tx"]
        self.assertLess(lo, 0)
        self.assertAlmostEqual(hi / SCALE, 12 * 0.8, delta=0.5)

    def test_a_selection_order_is_taken_as_given(self):
        parts = [f"{self.arm}|{p}" for p in PARTS]
        plan = ArticulatedRig.analyze(parts, ordered=True)
        self.assertEqual([j["parent"] for j in plan["joints"]], [0, 1, 2, 3, 4])


class TestBuild(ArticulatedRigCase):
    def test_a_build_moves_nothing_and_every_joint_carries_a_part(self):
        rig = self.build()
        self.assertAtRest()
        self.assertEqual(len(rig.joint_ids()), 5)
        for joint_id in rig.joint_ids():
            joint = rig.joint(joint_id)
            below = cmds.listRelatives(joint, allDescendents=True, type="mesh") or []
            self.assertTrue(
                below, f"{joint_id} carries no part: the export would drop it"
            )
            self.assertFalse(cmds.getAttr(f"{joint}.segmentScaleCompensate"))
        # the rig group rides the parts' parent
        self.assertEqual(
            cmds.listRelatives(rig.group, parent=True, fullPath=True)[0], self.arm
        )

    def test_every_control_sits_on_its_joint(self):
        """Bug: each control sat on the joint ABOVE its own (``Controls``
        resolved a joint to its parent), so the telescope's arrow was buried
        in the collar hinge and a posed control swung about the wrong pivot.
        Fixed: 2026-10-03."""
        rig = self.build()
        for joint_id in rig.joint_ids():
            self.assertLess(
                _mdiff(_world(rig.control(joint_id)), _world(rig.joint(joint_id))),
                1e-5,
                joint_id,
            )
        # and they stay on them, posed
        rig.set_state(self.random_state(rig.model(), random.Random(5)), key=False)
        cmds.setAttr(f"{rig.end_control}.ikBlend", 0.0)
        for joint_id in rig.joint_ids():
            ctrl = om.MTransformationMatrix(_world(rig.control(joint_id)))
            joint = om.MTransformationMatrix(_world(rig.joint(joint_id)))
            self.assertLess(
                (
                    ctrl.translation(om.MSpace.kWorld)
                    - joint.translation(om.MSpace.kWorld)
                ).length(),
                1e-4,
                joint_id,
            )

    def test_the_telescope_arrow_floats_clear_of_its_tubes(self):
        """The slide's arrow rides above the two tubes, in the arm's plane:
        centred on the axis and sized from the inner rod, it sat inside the
        outer tube (87% of it, on the production magnifier)."""
        rig = self.build()
        ctrl, joint = rig.control("LEG_4"), rig.joint("LEG_4")
        cvs = [
            p
            for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True)
            for p in cmds.getAttr(f"{shape}.cv[*]")
        ]
        span = max(abs(p[0]) for p in cvs)
        to_joint = _world(joint).inverse()
        reach = max(
            math.hypot(q.y, q.z)
            for part in ("LEG_3", "LEG_4")
            for s in ArticulatedRig._shells(cmds.ls(part, long=True)[0])
            for q in (om.MPoint(*p) * to_joint for p in s)
            if abs(q.x) <= span
        )
        self.assertGreater(min(p[1] for p in cvs), reach)
        self.assertLess(max(abs(p[2]) for p in cvs), reach, "not in the arm's plane")
        for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True):
            self.assertTrue(cmds.getAttr(f"{shape}.alwaysDrawOnTop"))

    def test_maya_poses_the_joints_exactly_as_the_model_does(self):
        rig = self.build(end_control=False)
        model = rig.model()
        group = _world(rig.group)
        rng = random.Random(3)
        worst = 0.0
        for _ in range(8):
            state = self.random_state(model, rng)
            rig.set_state(state, key=False)
            for index, (p, q) in enumerate(model.world(state)):
                xf = om.MTransformationMatrix(om.MQuaternion(*q).asMatrix())
                xf.setTranslation(om.MVector(*p), om.MSpace.kTransform)
                worst = max(
                    worst,
                    _mdiff(
                        xf.asMatrix() * group, _world(rig.joint(rig.joint_ids()[index]))
                    ),
                )
        self.assertLess(worst, 1e-5)

    def test_the_controls_carry_only_their_joints_channels_with_limits(self):
        rig = self.build()
        slide = rig.control("LEG_4")
        self.assertEqual(cmds.listAttr(slide, keyable=True), ["translateX"])
        limits = cmds.transformLimits(slide, query=True, translationX=True)
        enabled = cmds.transformLimits(slide, query=True, enableTranslationX=True)
        self.assertEqual(enabled, [True, True])
        self.assertLess(limits[0], 0.0)
        self.assertGreater(limits[1], 0.0)
        base = rig.control("LEG_1")
        self.assertEqual(
            sorted(cmds.listAttr(base, keyable=True)), ["rotateX", "rotateZ"]
        )
        self.assertEqual(
            cmds.getAttr(f"{base}.rotateOrder"), 5
        )  # zyx: the swivel outermost

    def test_a_driven_part_is_refused(self):
        cmds.setKeyframe(f"{self.arm}|LEG_2", attribute="translateX")
        plan = ArticulatedRig.analyze(self.arm)
        with self.assertRaises(ValueError):
            ArticulatedRig.create(plan["links"], plan["joints"])

    def test_one_undo_takes_the_whole_build_back(self):
        self.build()
        cmds.undo()
        self.assertAtRest()
        self.assertFalse(cmds.ls("*_RIG"))
        self.assertFalse(cmds.ls(type=ArticulatedRig.SOLVER_TYPE))
        self.assertFalse(cmds.ls(type="addDoubleLinear"))
        self.assertEqual(cmds.listRelatives("LEG_3", parent=True), ["ARM"])

    def test_a_handle_outlives_a_rebuild_made_through_another(self):
        """A rebuild makes a new rig group: a handle the panel (or a script)
        did not rebuild through found its group gone and raised. It finds the
        rig again by the plan's name -- and a torn-down rig stays gone."""
        rig = self.build()
        other = ArticulatedRig.for_node(rig.control("LEG_2"))
        other.rebuild()
        self.assertEqual(rig.group, other.group)
        self.assertEqual(rig.joint_ids(), other.joint_ids())
        rig.teardown()
        with self.assertRaises(RuntimeError):
            _ = other.group

    def test_a_duplicated_rig_group_is_refused(self):
        rig = self.build()
        copy = cmds.duplicate(rig.group, name="copy_RIG")[0]
        with self.assertRaises(ValueError):
            ArticulatedRig(copy)
        self.assertEqual(len(ArticulatedRig.scene_rigs()), 1)

    def test_a_second_rig_of_the_same_name_is_numbered(self):
        first = self.build(name="arm")
        other = build_arm()
        plan = ArticulatedRig.analyze(other)
        second = ArticulatedRig.create(plan["links"], plan["joints"], name="arm")
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(len(ArticulatedRig.scene_rigs()), 2)


class TestTeardown(ArticulatedRigCase):
    def test_teardown_restores_every_part_exactly(self):
        before = {
            p: {a: cmds.getAttr(f"{p}.{a}") for a in ArticulatedRig.PART_ATTRS}
            for p in PARTS
        }
        rig = self.build()
        rig.set_state([10.0] * len(rig.state()), key=False)
        cmds.move(3, 4, 5, rig.end_control, relative=True)
        rig.teardown()
        self.assertAtRest()
        for part in PARTS:
            self.assertEqual(cmds.listRelatives(part, parent=True), ["ARM"])
            for attr, value in before[part].items():
                self.assertEqual(cmds.getAttr(f"{part}.{attr}"), value, (part, attr))
        leftovers = [n for n in cmds.ls() if n.startswith("arm_") or n.endswith("_RIG")]
        self.assertEqual(leftovers, [])
        for kind in (
            ArticulatedRig.SOLVER_TYPE,
            "multMatrix",
            "decomposeMatrix",
            "addDoubleLinear",
            "multiplyDivide",
            "plusMinusAverage",
            "unitConversion",
        ):
            self.assertFalse(cmds.ls(type=kind), kind)
        self.assertIsNone(
            ptk.SceneRecords.ARTICULATION.load(__import__("mayatk").DataNodes)
        )


class TestEndControl(ArticulatedRigCase):
    """The end control: an IK target the stand follows, solved by a MEL
    expression of the same model the Grab Tool and the runtimes run."""

    def head_point(self, rig, pivot_at_rest):
        """Where the point that sat at the end control's pivot at rest is now,
        carried by the head."""
        return list(
            om.MPoint(*pivot_at_rest) * self.rest_head_inverse * _world("HEAD")
        )[:3]

    def setUp(self):
        super().setUp()
        self.rest_head_inverse = self.rest["HEAD"].inverse()

    def test_the_end_control_boxes_the_head_and_moves_nothing_at_rest(self):
        rig = self.build()
        end = rig.end_control
        self.assertIsNotNone(end)
        self.assertEqual(cmds.nodeType(rig.solver), ArticulatedRig.SOLVER_TYPE)
        self.assertAtRest()
        # aligned with the head's joint, centred on the head's geometry (the
        # lens) in that frame, the box around all of it
        frame = _world(rig.joint("HEAD"))
        self.assertTrue(_same_turn(_rotation(end), _rotation(rig.joint("HEAD"))))
        points = [
            om.MPoint(*p) * frame.inverse()
            for s in ArticulatedRig._shells("HEAD")
            for p in s
        ]
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        centre = om.MPoint(*[(a + b) / 2 for a, b in zip(lo, hi)]) * frame
        pivot = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        self.assertLess(math.dist(pivot, list(centre)[:3]), 1e-4)
        box = cmds.exactWorldBoundingBox(end)
        head = cmds.exactWorldBoundingBox("HEAD")
        for i in range(3):
            self.assertLessEqual(box[i], head[i] + 1e-6)
            self.assertGreaterEqual(box[i + 3], head[i + 3] - 1e-6)
        self.assertEqual(
            sorted(cmds.listAttr(end, keyable=True)),
            sorted(
                ["translateX", "translateY", "translateZ", "rotateX", "rotateY"]
                + ["rotateZ", "ikBlend", "followRotation"]
            ),
        )
        self.assertIn(end, rig.controls())

    def test_moving_the_end_control_brings_the_head_along(self):
        rig = self.build()
        end = rig.end_control
        pivot = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        cmds.move(-7.0, 9.0, 6.0, end, relative=True, worldSpace=True)
        target = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        self.assertLess(math.dist(self.head_point(rig, pivot), target), 0.05)
        # the head keeps its turn (the control did not rotate)
        self.assertTrue(
            _same_turn(
                _rotation("HEAD"),
                om.MTransformationMatrix(self.rest["HEAD"]).rotation(asQuaternion=True),
            )
        )
        self.assertEqual(rig.fk_state(), [0.0] * len(rig.fk_state()))
        self.assertNotEqual(rig.state(), rig.fk_state())

    def test_rotating_the_end_control_turns_the_ball_head(self):
        rig = self.build()
        end = rig.end_control
        before = _rotation("HEAD")
        control_before = _rotation(end)
        cmds.rotate(0.0, 25.0, 10.0, end, relative=True, objectSpace=True)
        turn = control_before.conjugate() * _rotation(end)
        self.assertTrue(_same_turn(_rotation("HEAD"), before * turn, 1e-3))

    def test_follow_rotation_off_keeps_the_heads_own_turn(self):
        rig = self.build()
        end = rig.end_control
        cmds.setAttr(f"{end}.followRotation", False)
        cmds.rotate(0.0, 40.0, 0.0, end, relative=True, objectSpace=True)
        cmds.move(-5.0, 6.0, 0.0, end, relative=True, worldSpace=True)
        head = rig.joint_ids()[-1]
        slots = [i for i, (c, _ch) in enumerate(rig._slots()) if c == rig.control(head)]
        state = rig.state()
        self.assertEqual([state[i] for i in slots], [0.0, 0.0, 0.0])

    def test_the_fk_controls_ride_their_joints_when_the_end_control_moves(self):
        """Seen in the viewport: the joints followed the end control while
        their controls stayed where FK left them, floating off the arm. Each
        control's IK group takes its joint's share of the solve."""
        rig = self.build()
        cmds.move(-7.0, 9.0, 6.0, rig.end_control, relative=True, worldSpace=True)
        for joint_id in rig.joint_ids():
            ctrl = om.MTransformationMatrix(_world(rig.control(joint_id)))
            joint = om.MTransformationMatrix(_world(rig.joint(joint_id)))
            self.assertLess(
                (
                    ctrl.translation(om.MSpace.kWorld)
                    - joint.translation(om.MSpace.kWorld)
                ).length(),
                1e-4,
                joint_id,
            )
            self.assertTrue(
                _same_turn(
                    ctrl.rotation(asQuaternion=True), joint.rotation(asQuaternion=True)
                ),
                joint_id,
            )

    def test_ik_blend_zero_is_the_fk_pose(self):
        rig = self.build()
        end = rig.end_control
        cmds.move(-7.0, 9.0, 6.0, end, relative=True, worldSpace=True)
        cmds.setAttr(f"{end}.ikBlend", 0.0)
        self.assertAtRest()
        self.assertEqual(rig.state(), rig.fk_state())

    def test_the_end_controls_keys_play_back(self):
        rig = self.build()
        end = rig.end_control
        pivot = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        targets = {}
        for frame, offset in ((1, (0, 0, 0)), (10, (-6.0, 8.0, 4.0))):
            cmds.currentTime(frame)
            cmds.setAttr(f"{end}.translate", *offset, type="double3")
            cmds.setKeyframe(end, attribute="translate")
            targets[frame] = cmds.xform(
                end, query=True, worldSpace=True, rotatePivot=True
            )
        for frame in (10, 1, 10):
            cmds.currentTime(frame)
            self.assertLess(
                math.dist(self.head_point(rig, pivot), targets[frame]), 0.05, frame
            )

    def test_fk_shapes_the_arm_under_a_pinned_end(self):
        rig = self.build()
        end = rig.end_control
        pivot = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        cmds.move(-4.0, 5.0, 0.0, end, relative=True, worldSpace=True)
        target = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        elbow = _world("LEG_2")
        cmds.setAttr(f"{rig.control('LEG_2')}.rz", 25.0)  # the seed moves...
        self.assertGreater(_mdiff(elbow, _world("LEG_2")), 1e-3)  # ...the arm too
        self.assertLess(math.dist(self.head_point(rig, pivot), target), 0.05)

    def test_switching_to_fk_and_back_never_jumps(self):
        rig = self.build()
        end = rig.end_control
        cmds.move(-6.0, 7.0, 5.0, end, relative=True, worldSpace=True)
        posed = {p: _world(p) for p in PARTS}
        self.assertFalse(rig.switch_ik(key=False))
        self.assertEqual(rig.ik_blend(), 0.0)
        for part in PARTS:
            self.assertLess(_mdiff(posed[part], _world(part)), 1e-4, part)
        rig.set_state(
            self.random_state(rig.model(), random.Random(2), spread=20), key=False
        )
        fk = {p: _world(p) for p in PARTS}
        self.assertTrue(rig.switch_ik(key=False))
        for part in PARTS:
            self.assertLess(_mdiff(fk[part], _world(part)), 1e-4, part)

    def test_an_inert_end_control_still_switches_both_ways(self):
        """Bug: the switch read the EFFECTIVE blend, 0 whenever the solver is
        not live, so an inert end control flipped to IK on every press and
        never back. It reads its own setting. Fixed: 2026-10-03."""
        rig = self.build()
        cmds.delete(rig.solver)  # a rig whose solve is gone
        self.assertIsNone(rig.solver)
        self.assertEqual(
            [rig.switch_ik(key=False) for _ in range(3)], [False, True, False]
        )

    def test_switching_keys_with_auto_key(self):
        rig = self.build()
        end = rig.end_control
        cmds.autoKeyframe(state=True)
        try:
            rig.switch_ik(False)
            self.assertTrue(cmds.keyframe(f"{end}.ikBlend", query=True))
            self.assertTrue(cmds.keyframe(rig.control("LEG_2"), query=True))
        finally:
            cmds.autoKeyframe(state=False)

    def test_rest_pose_puts_every_control_back(self):
        rig = self.build()
        cmds.move(-6.0, 7.0, 5.0, rig.end_control, relative=True, worldSpace=True)
        rig.set_state([5.0] * len(rig.fk_state()), key=False)
        rig.reset_pose(key=False)
        self.assertAtRest()

    def test_a_rebuild_carries_the_end_controls_animation(self):
        rig = self.build()
        end = rig.end_control
        cmds.setKeyframe(end, attribute="translateY", time=1, value=0.0)
        cmds.setKeyframe(end, attribute="translateY", time=10, value=6.0)
        cmds.setKeyframe(end, attribute="ikBlend", time=10, value=0.5)
        cmds.currentTime(7)
        before = {p: _world(p) for p in PARTS}
        rig.rebuild()
        for part in PARTS:
            self.assertLess(_mdiff(before[part], _world(part)), 1e-5, part)
        self.assertEqual(
            cmds.keyframe(rig.end_control, attribute="translateY", query=True),
            [1.0, 10.0],
        )

    def test_the_end_control_drops_and_comes_back(self):
        rig = self.build()
        rig.set_end_control(False)
        self.assertIsNone(rig.end_control)
        self.assertFalse(cmds.ls(type=ArticulatedRig.SOLVER_TYPE))
        self.assertFalse(cmds.ls(type="addDoubleLinear"))
        self.assertAtRest()
        rig.set_end_control(True, part="LEG_3")
        self.assertIsNotNone(rig.end_control)
        self.assertAtRest()
        cmds.move(0.0, 5.0, 0.0, rig.end_control, relative=True, worldSpace=True)
        self.assertGreater(_mdiff(self.rest["LEG_3"], _world("LEG_3")), 1e-3)
        self.assertFalse(cmds.ls(type="decomposeMatrix")[1:], "one solve, not two")

    def test_give_reaches_the_record_and_the_solver(self):
        """The solver's text inlines the weights: a give changed in place
        (no rebuild) has to rewrite it, or the end control keeps the old."""
        rig = self.build()
        rig.set_weight("LEG_1", 0.25)
        record = rig.record()
        base = next(j for j in record["joints"] if j["name"].endswith("LEG_1_jnt"))
        self.assertEqual([c["weight"] for c in base["channels"]], [0.25, 0.25])
        rig.set_weight("LEG_1", 0.0)
        cmds.move(-6.0, 7.0, 5.0, rig.end_control, relative=True, worldSpace=True)
        slots = [
            i for i, (c, _ch) in enumerate(rig._slots()) if c == rig.control("LEG_1")
        ]
        self.assertEqual([rig.state()[i] for i in slots], [0.0, 0.0])
        rig.set_weight("LEG_1", 1.0)
        self.assertNotEqual([rig.state()[i] for i in slots], [0.0, 0.0])
        with self.assertRaises(ValueError):
            rig.set_weight("LEG_1", -1.0)

    def test_a_rig_whose_solve_is_missing_is_repaired_in_place(self):
        """A rig built by an earlier mayatk, its solve a plug-in node that
        never loaded (``unknown``, or no node at all): the end control is
        inert until the panel, the Grab Tool or ``repair_scene`` gives it its
        solve -- no rebuild, so its animation stays where it is."""
        rig = self.build()
        group = rig.group
        cmds.delete(rig.solver)
        stale = cmds.createNode("unknown", name="arm_old_ik_solver")
        cmds.connectAttr(f"{stale}.message", f"{group}.{rig.SOLVER_ATTR}")
        self.assertIsNone(rig.solver)
        cmds.move(-6.0, 7.0, 5.0, rig.end_control, relative=True, worldSpace=True)
        self.assertAtRest()  # inert
        self.assertEqual(ArticulatedRig.repair_scene(), 1)
        self.assertEqual(rig.group, group, "rebuilt")
        self.assertFalse(cmds.objExists(stale))
        self.assertEqual(cmds.nodeType(rig.solver), "expression")
        self.assertGreater(_mdiff(self.rest["HEAD"], _world("HEAD")), 1e-3)
        self.assertEqual(ArticulatedRig.repair_scene(), 0)

    def test_a_saved_scene_moves_on_the_end_control_with_nothing_installed(self):
        """The user's report: in their Maya the end control did nothing -- its
        solver was a plug-in their session could not load. The solve is a
        MEL expression now: a scene saved and opened again moves on it with
        no plug-in and no repair, and requires none."""
        rig = self.build()
        end = rig.end_control
        pivot = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        artifacts = ptk.TempArtifacts("articulated_rig_saved", policy="scoped")
        self.addCleanup(artifacts.cleanup)
        path = os.path.join(artifacts.dir_path(), "rig.ma")
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(path, open=True, force=True, prompt=False)
        self.addCleanup(cmds.file, new=True, force=True)
        with open(path, encoding="utf-8") as handle:
            requires = [line for line in handle if line.startswith("requires")]
        self.assertFalse([r for r in requires if "mtk" in r], requires)
        rig = ArticulatedRig.scene_rigs()[0]
        self.assertEqual(cmds.nodeType(rig.solver), "expression")
        end = rig.end_control
        cmds.move(-6.0, 7.0, 5.0, end, relative=True, worldSpace=True)
        target = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        self.assertLess(math.dist(self.head_point(rig, pivot), target), 0.05)


class TestSolverExpression(MayaTkTestCase):
    """The end control's MEL solve is a port of ``ptk.ArticulationModel``'s
    -- held to the conformance cases the Unity and WebXR ports are."""

    IDENTITY = (0.0, 0.0, 0.0, 1.0)

    def solver(self, rig, joint, pivot):
        """An expression solving *rig* for *joint*'s link held at *pivot*, on
        a node of plain attributes: the target, the control's rotation, the
        switches, and a seed and an offset per slot."""
        model = ptk.ArticulationModel(rig)
        io = cmds.createNode("transform", name="solve_io")
        for attr in ("gx", "gy", "gz", "qx", "qy", "qz", "qw", "blend"):
            cmds.addAttr(io, longName=attr, attributeType="double")
        cmds.setAttr(f"{io}.qw", 1.0)
        cmds.setAttr(f"{io}.blend", 1.0)
        cmds.addAttr(io, longName="follow", attributeType="bool", defaultValue=True)
        slots = range(len(model.channels))
        for slot in slots:
            cmds.addAttr(io, longName=f"seed{slot}", attributeType="double")
            cmds.addAttr(io, longName=f"out{slot}", attributeType="double")
        text = SolverExpression.text_for(
            rig,
            joint,
            pivot,
            self.IDENTITY,
            {
                "translate": [f"{io}.g{a}" for a in "xyz"],
                "quat": [f"{io}.q{a}" for a in "xyzw"],
                "blend": f"{io}.blend",
                "follow": f"{io}.follow",
                "seeds": [f"{io}.seed{slot}" for slot in slots],
                "outputs": [[f"{io}.out{slot}"] for slot in slots],
            },
        )
        expression = cmds.expression(string=text, alwaysEvaluate=False)
        self.addCleanup(lambda: cmds.objExists(io) and cmds.delete(expression, io))
        return model, io

    def offsets(self, io, seed, target, rotation=None, follow=True):
        for slot, value in enumerate(seed):
            cmds.setAttr(f"{io}.seed{slot}", value)
        for axis, value in zip("xyz", target):
            cmds.setAttr(f"{io}.g{axis}", value)
        for axis, value in zip("xyzw", rotation or self.IDENTITY):
            cmds.setAttr(f"{io}.q{axis}", value)
        cmds.setAttr(f"{io}.follow", follow)
        return [cmds.getAttr(f"{io}.out{slot}") for slot in range(len(seed))]

    def test_the_mel_solve_matches_every_conformance_case(self):
        doc = ptk.ArticulationConformance.cases()
        tolerance = doc["tolerance"]["solve"]
        checked = skipped = 0
        for index, case in enumerate(doc["cases"]):
            rig = doc["rigs"][case["rig"]]
            for solve in case["solve"]:
                joint = solve["joint"]
                model = ptk.ArticulationModel(rig)
                turns = {c for j, c in model.channels if j == joint}
                if {"rx", "ry", "rz"} <= turns and solve["rotation"] is None:
                    skipped += 1  # a ball keeping its own turn is carried (below)
                    continue
                model, io = self.solver(rig, joint, solve["local"])
                got = self.offsets(
                    io, solve["from"], solve["target"], solve["rotation"]
                )
                chain = model.chain(joint)
                want = [
                    expect - start if model.channels[slot][0] in chain else 0.0
                    for slot, (expect, start) in enumerate(
                        zip(solve["expect"], solve["from"])
                    )
                ]
                self.assertLess(
                    max(abs(g - w) for g, w in zip(got, want)),
                    tolerance,
                    f"case {index} ({case['rig']}), joint {joint}: {got} != {want}",
                )
                checked += 1
        self.assertEqual(checked + skipped, sum(len(c["solve"]) for c in doc["cases"]))
        self.assertGreater(checked, skipped)

    def test_an_out_of_reach_target_settles_as_the_model_does(self):
        """Past the reach a step is halved until it brings the held point
        nearer, and the solve stops when none does: the arm settles where it
        comes nearest. Every conformance target is a pose's own point -- in
        reach by construction -- so this is pinned here: targets 1.2 to 2
        reaches out from the chain's root, along a drag (each solve seeded
        with where the last one settled; a target held still settles until
        no step helps), for an arm held off its slide (position only) and its
        ball-mounted end following a hand's turn (the wrist split). Each drag
        is seeded where the stop decides the pose: a port that took the last
        halved step anyway lands 0.8 to 3 degrees off the model's."""
        tolerance = ptk.ArticulationConformance.TOLERANCE["solve"]
        rig = ptk.ArticulationConformance.rigs()["arm"]
        compared = 0
        for joint, turned, stream in ((3, False, 21), (4, True, 8)):
            rng = random.Random(stream)
            local = [rng.uniform(-1.5, 1.5) for _ in range(3)]
            model, io = self.solver(rig, joint, local)
            chain = model.chain(joint)
            reach = math.dist(local, (0.0, 0.0, 0.0)) + sum(
                math.dist(model.joints[j]["t"], (0.0, 0.0, 0.0)) for j in chain[1:]
            )
            # The chain's root joint turns in place: nothing moves its origin.
            root = model.world(model.rest_state())[chain[0]][0]
            way = [rng.gauss(0.0, 1.0) for _ in range(3)]
            way = [c / math.dist(way, (0.0, 0.0, 0.0)) for c in way]
            seed = ptk.ArticulationConformance._random_state(model, rng)
            hand = ptk.ArticulationConformance._random_state(model, rng)
            rotation = list(model.world(hand)[joint][1]) if turned else None
            for k in (1.2, 1.2, 1.6, 2.0, 2.0):
                target = [r + c * k * reach for r, c in zip(root, way)]
                want = model.solve(seed, joint, local, target, rotation)
                held = model.point(want, joint, local)
                self.assertGreater(
                    math.dist(held, target), 0.1 * reach, "the target is in reach"
                )
                got = self.offsets(io, seed, target, rotation)
                expected = [
                    w - s if model.channels[slot][0] in chain else 0.0
                    for slot, (w, s) in enumerate(zip(want, seed))
                ]
                self.assertLess(
                    max(abs(g - e) for g, e in zip(got, expected)),
                    tolerance,
                    f"joint {joint}, {k} reaches out: {got} != {expected}",
                )
                compared += 1
                seed = want
        self.assertEqual(compared, 10)

    def test_a_steps_deltas_are_written_by_the_step_alone(self):
        """``$d<slot>`` is a step's delta. The wrist's drift sums once shared
        those names (``$d1`` / ``$d2``, reset and summed between two position
        solves), harmless only because each step writes its deltas before it
        reads them -- so a name a slot's variable could spell is refused, both
        ways round."""
        rig = ptk.ArticulationConformance.rigs()["arm"]
        slots = range(len(ptk.ArticulationModel(rig).channels))
        text = SolverExpression.text_for(
            rig,
            4,  # the ball-mounted head: the wrist split, steps and drift sums
            (0.1, 0.2, 0.3),
            self.IDENTITY,
            {
                "translate": ["io.gx", "io.gy", "io.gz"],
                "quat": ["io.qx", "io.qy", "io.qz", "io.qw"],
                "blend": "io.blend",
                "follow": "io.follow",
                "seeds": [f"io.seed{slot}" for slot in slots],
                "outputs": [[f"io.out{slot}"] for slot in slots],
            },
        )
        writes = re.findall(r"^\s*\$d\d+ = (.*);$", text, re.MULTILINE)
        self.assertTrue(writes, "no step in the text")
        self.assertEqual([w for w in writes if not w.startswith("$W")], [])
        mel = _MelText()
        mel.indexed("d", 1)
        with self.assertRaises(ValueError):
            mel.var("d1")
        mel = _MelText()
        mel.var("d2")
        with self.assertRaises(ValueError):
            mel.indexed("d", 7)

    def test_a_ball_keeping_its_own_turn_is_carried_by_its_parents_link(self):
        """``followRotation`` off: the head keeps its FK turn and the chain
        above carries it -- the held point solved on the parent's link."""
        rigs = ptk.ArticulationConformance.rigs()
        rng = random.Random(5)
        for name in ("arm", "ball_zyx"):
            rig = rigs[name]
            model = ptk.ArticulationModel(rig)
            joint = len(model.joints) - 1
            parent = model.joints[joint]["parent"]
            pivot = [rng.uniform(-1.0, 1.0) for _ in range(3)]
            model, io = self.solver(rig, joint, pivot)
            for _ in range(6):
                seed = ptk.ArticulationConformance._random_state(model, rng)
                goal = model.point(
                    ptk.ArticulationConformance._random_state(model, rng), joint, pivot
                )
                held = model.to_local(seed, parent, model.point(seed, joint, pivot))
                want = model.solve(seed, parent, held, goal)
                got = self.offsets(io, seed, goal, follow=False)
                for slot, (g, w, start) in enumerate(zip(got, want, seed)):
                    self.assertAlmostEqual(g, w - start, places=3, msg=(name, slot))


class TestAdjust(ArticulatedRigCase):
    """Adjusting the pivots: a handle on every joint and on the end control,
    the rig rebuilt on them when the adjusting ends."""

    def test_the_handles_sit_on_the_pivots_and_change_nothing(self):
        rig = self.build()
        group = rig.group
        handles = rig.begin_adjust()
        self.assertTrue(rig.adjusting)
        self.assertEqual(set(handles), set(rig.joint_ids()) | {ArticulatedRig.END_KEY})
        for joint_id in rig.joint_ids():
            self.assertLess(
                _mdiff(_world(handles[joint_id]), _world(rig.joint(joint_id))),
                1e-6,
                joint_id,
            )
        self.assertLess(
            _mdiff(_world(handles[ArticulatedRig.END_KEY]), _world(rig.end_control)),
            1e-6,
        )
        self.assertAtRest()
        self.assertEqual(rig.begin_adjust(), handles, "a second call adds nothing")
        self.assertFalse(rig.end_adjust(), "nothing moved: no rebuild")
        self.assertEqual(rig.group, group)
        self.assertFalse(rig.adjusting)
        self.assertFalse(cmds.ls(f"*{ArticulatedRig.HANDLE_SUFFIX}*", recursive=True))
        self.assertFalse(cmds.ls(type="multMatrix")[1:], "only the solve's is left")

    def test_a_handle_rides_the_part_its_joint_hangs_off(self):
        rig = self.build(end_control=False)
        handles = rig.begin_adjust()
        cmds.setAttr(f"{rig.control('LEG_2')}.rz", 30.0)
        moved = om.MTransformationMatrix(_world(handles["LEG_3"]))
        joint = om.MTransformationMatrix(_world(rig.joint("LEG_3")))
        self.assertLess(
            (
                moved.translation(om.MSpace.kWorld)
                - joint.translation(om.MSpace.kWorld)
            ).length(),
            1e-6,
        )

    def test_a_moved_pivot_rebuilds_the_joint_there_with_its_keys(self):
        rig = self.build(end_control=False)
        ctrl = rig.control("LEG_2")
        cmds.setKeyframe(ctrl, attribute="rz", time=1, value=0.0)
        cmds.setKeyframe(ctrl, attribute="rz", time=10, value=35.0)
        cmds.currentTime(1)
        handles = rig.begin_adjust()
        cmds.move(2.0, 1.0, 0.0, handles["LEG_2"], relative=True, objectSpace=True)
        pivot = cmds.xform(
            handles["LEG_2"], query=True, worldSpace=True, translation=True
        )
        child = cmds.xform(
            rig.joint("LEG_3"), query=True, worldSpace=True, translation=True
        )
        self.assertTrue(rig.end_adjust())
        self.assertFalse(rig.adjusting)
        self.assertAtRest(tol=1e-5)  # a rebuild's rounding
        joint = rig.joint("LEG_2")
        self.assertLess(
            math.dist(
                cmds.xform(joint, query=True, worldSpace=True, translation=True), pivot
            ),
            1e-4,
        )
        self.assertLess(
            math.dist(
                cmds.xform(
                    rig.joint("LEG_3"), query=True, worldSpace=True, translation=True
                ),
                child,
            ),
            1e-4,
            "the child's pivot stays where it was",
        )
        self.assertEqual(
            cmds.keyframe(rig.control("LEG_2"), attribute="rz", query=True), [1.0, 10.0]
        )
        # the part now turns about its new pivot: the point there stays put
        rest = _world("LEG_2")
        on_pivot = om.MPoint(*pivot) * rest.inverse()
        cmds.currentTime(10)
        self.assertLess(math.dist(list(on_pivot * _world("LEG_2"))[:3], pivot), 1e-4)

    def test_a_turned_end_handle_reorients_the_end_control(self):
        rig = self.build()
        handles = rig.begin_adjust()
        handle = handles[ArticulatedRig.END_KEY]
        cmds.rotate(0.0, 0.0, 90.0, handle, relative=True, objectSpace=True)
        wanted = _world(handle)
        self.assertTrue(rig.end_adjust())
        self.assertAtRest(tol=1e-5)  # a rebuild's rounding
        end = rig.end_control
        self.assertLess(_mdiff(_world(end), wanted), 1e-5)
        box = cmds.exactWorldBoundingBox(end)
        head = cmds.exactWorldBoundingBox("HEAD")
        for i in range(3):  # refitted around the head in its new frame
            self.assertLessEqual(box[i], head[i] + 1e-6)
            self.assertGreaterEqual(box[i + 3], head[i + 3] - 1e-6)
        pivot = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        cmds.move(-6.0, 7.0, 5.0, end, relative=True, worldSpace=True)
        target = cmds.xform(end, query=True, worldSpace=True, rotatePivot=True)
        held = om.MPoint(*pivot) * self.rest["HEAD"].inverse() * _world("HEAD")
        self.assertLess(math.dist(list(held)[:3], target), 0.05)

    def test_an_adjust_dropped_or_undone_changes_nothing(self):
        rig = self.build()
        plan = rig.spec
        handles = rig.begin_adjust()
        cmds.move(3.0, 0.0, 0.0, handles["LEG_3"], relative=True, objectSpace=True)
        self.assertFalse(rig.end_adjust(apply=False))
        self.assertEqual(rig.spec, plan)
        self.assertFalse(rig.adjusting)
        handles = rig.begin_adjust()
        cmds.move(3.0, 0.0, 0.0, handles["LEG_3"], relative=True, objectSpace=True)
        self.assertTrue(rig.end_adjust())
        self.assertNotEqual(rig.spec["joints"], plan["joints"])
        cmds.undo()
        self.assertEqual(rig.spec["joints"], plan["joints"])


class TestGrab(ArticulatedRigCase):
    def test_a_grab_brings_the_held_point_to_its_target_inside_the_limits(self):
        rig = self.build(end_control=False)
        point = _w(_add(BALL, _norm(R), 12.15))
        hold = rig.grab_begin("ring", point)
        target = [point[0] - 6.0, point[1] + 8.0, point[2] + 4.0]
        state = rig.grab_to(hold, target, key=False)
        self.assertLess(math.dist(rig.held_point(hold), target), 0.05)
        self.assertEqual(state, hold["model"].clamp(state))

    def test_a_grab_on_the_head_drags_the_end_control(self):
        rig = self.build()
        point = _w(_add(BALL, _norm(R), 12.15))
        hold = rig.grab_begin("ring", point)
        self.assertIn("end", hold)
        target = [point[0] - 6.0, point[1] + 8.0, point[2] + 4.0]
        rig.grab_to(hold, target, key=False)
        self.assertLess(math.dist(rig.held_point(hold), target), 0.05)
        self.assertEqual(rig.fk_state(), [0.0] * len(rig.fk_state()))

    def _turned_hand(self, end_control):
        rig = self.build(end_control=end_control)
        point = _w(_add(BALL, _norm(R), 12.15))
        hold = rig.grab_begin("ring", point)
        before = _rotation("HEAD")
        turn = om.MQuaternion(math.radians(20.0), om.MVector(0, 1, 0))  # world Y
        rig.grab_to(hold, point, turn=(turn.x, turn.y, turn.z, turn.w), key=False)
        # Maya's quaternion product applies the left one first: before, then
        # the turn.
        self.assertTrue(_same_turn(_rotation("HEAD"), before * turn, 1e-3))
        self.assertLess(math.dist(rig.held_point(hold), point), 0.05)

    def test_a_turned_hand_turns_the_ball_head_by_that_turn(self):
        self._turned_hand(end_control=False)

    def test_a_turned_hand_turns_the_ball_head_through_the_end_control(self):
        self._turned_hand(end_control=True)

    def test_a_grab_off_a_hinged_ends_pivot_lands_on_the_cursor(self):
        """A hinged end link turns with the solve, so the end control carried
        rigidly with the grabbed point left that point off the cursor; and a
        hand's turn rotated a control whose rotation is locked. The grab
        settles the control until the point lands; a hinge takes no turn."""
        rig = self.build()
        rig.edit_joint("HEAD", type="hinge")
        end = rig.end_control
        point = _w(_add(BALL, _norm(R), 12.15))  # on the ring, off the pivot
        hold = rig.grab_begin("ring", point)
        self.assertIn("end", hold)
        target = [point[0] - 6.0, point[1] + 8.0, point[2]]
        turn = om.MQuaternion(math.radians(20.0), om.MVector(0, 1, 0))
        rig.grab_to(hold, target, turn=(turn.x, turn.y, turn.z, turn.w), key=False)
        self.assertLess(math.dist(rig.held_point(hold), target), 0.05)
        self.assertEqual(cmds.getAttr(f"{end}.rotate")[0], (0.0, 0.0, 0.0))

    def _pose_to(self, end_control):
        rig = self.build(end_control=end_control)
        target = _w(_add(BALL, (-5, 6, 3)))
        left = rig.pose_to("HEAD", target, point=_w(BALL), key=False)
        self.assertLess(left, 0.05)

    def test_pose_to_lands_a_part_on_a_target(self):
        self._pose_to(end_control=False)

    def test_pose_to_lands_a_part_on_a_target_through_the_end_control(self):
        self._pose_to(end_control=True)

    def _drag(self, rig, part_point):
        cmds.autoKeyframe(state=True)
        origin = [part_point[0], part_point[1], part_point[2] + 200.0]
        tool = ArticulatedRigGrab()
        self.assertTrue(tool.press(origin, (0.0, 0.0, -1.0)))
        self.assertEqual(tool.rig.name, rig.name)
        moved = [origin[0] - 5.0, origin[1] + 5.0, origin[2]]
        tool.drag(moved, (0.0, 0.0, -1.0))
        tool.release()

    def test_the_grab_tool_drags_the_end_control_and_keys_it_on_release(self):
        rig = self.build()
        try:
            self._drag(rig, _w(_add(BALL, _norm(R), 12.15)))
            self.assertTrue(cmds.keyframe(rig.end_control, query=True))
            self.assertIsNone(cmds.keyframe(rig.control("LEG_1"), query=True))
            cmds.undo()  # the whole drag, keys included
            self.assertIsNone(cmds.keyframe(rig.end_control, query=True))
            self.assertAtRest()
        finally:
            cmds.autoKeyframe(state=False)

    def test_the_grab_tool_poses_and_keys_fk_with_ik_off(self):
        rig = self.build()
        cmds.setAttr(f"{rig.end_control}.ikBlend", 0.0)
        try:
            self._drag(rig, _w(_add(BALL, _norm(R), 12.15)))
            self.assertGreater(
                len(cmds.keyframe(rig.control("LEG_1"), query=True) or []), 0
            )
            cmds.undo()
            self.assertEqual(cmds.keyframe(rig.control("LEG_1"), query=True), None)
        finally:
            cmds.autoKeyframe(state=False)

    def test_the_grab_tool_ignores_the_fixed_root(self):
        self.build()
        tool = ArticulatedRigGrab()
        plate = _w((0.0, 0.45, 0.0))
        self.assertFalse(tool.press((plate[0], plate[1], 100.0), (0.0, 0.0, -1.0)))


class TestLimits(ArticulatedRigCase):
    def test_limits_from_the_pose_bound_the_control_the_solver_and_the_record(self):
        rig = self.build(end_control=False)
        ctrl = rig.control("LEG_2")
        cmds.setAttr(f"{ctrl}.rz", 72.0)
        rig.set_limit_from_pose("LEG_2", "rz", "max")
        cmds.setAttr(f"{ctrl}.rz", -35.0)
        rig.set_limit_from_pose("LEG_2", "rz", "min")
        self.assertEqual(
            cmds.transformLimits(ctrl, query=True, rotationZ=True), [-35.0, 72.0]
        )
        record = rig.record()
        elbow = next(j for j in record["joints"] if j["name"].endswith("LEG_2_jnt"))
        self.assertEqual(
            (elbow["channels"][0]["min"], elbow["channels"][0]["max"]), (-35.0, 72.0)
        )
        rig.set_limits("LEG_2", "rz", None, None)
        self.assertEqual(
            cmds.transformLimits(ctrl, query=True, enableRotationZ=True), [False, False]
        )

    def test_limits_from_the_pose_read_the_pose_the_end_control_made(self):
        rig = self.build()
        cmds.move(-6.0, 7.0, 5.0, rig.end_control, relative=True, worldSpace=True)
        actual = rig.channel_value("LEG_2", "rz")
        self.assertNotAlmostEqual(actual, 0.0, places=2)
        taken = rig.set_limit_from_pose("LEG_2", "rz", "max" if actual > 0 else "min")
        self.assertAlmostEqual(taken, actual, places=6)
        self.assertIn(f"{taken}", cmds.expression(rig.solver, query=True, string=True))

    def test_the_end_control_never_leaves_a_limit(self):
        rig = self.build()
        rig.set_limits("LEG_2", "rz", -10.0, 10.0)
        cmds.move(-12.0, 14.0, 0.0, rig.end_control, relative=True, worldSpace=True)
        self.assertLessEqual(abs(rig.channel_value("LEG_2", "rz")), 10.0 + 1e-6)


class TestPostRig(ArticulatedRigCase):
    def _key(self, rig, frames):
        """Key every control at *frames*; return each part's world matrix per frame."""
        rng = random.Random(9)
        model = rig.model()
        for frame in frames:
            cmds.currentTime(frame)
            rig.set_state(self.random_state(model, rng, 30, 0.8), key=True)
        return self._sample(frames)

    @staticmethod
    def _sample(frames):
        out = {}
        for frame in frames:
            cmds.currentTime(frame)
            out[frame] = {p: _world(p) for p in PARTS}
        return out

    def _same(self, a, b, tol=1e-4):
        for frame in a:
            for part in a[frame]:
                self.assertLess(
                    _mdiff(a[frame][part], b[frame][part]), tol, (frame, part)
                )

    def test_a_rebuild_carries_the_animation_across(self):
        rig = self.build()
        keyed = self._key(rig, [1, 12, 24])
        rig.rebuild()
        self._same(keyed, self._sample([1, 12, 24]))

    def test_a_moved_rig_group_stays_moved_through_a_rebuild(self):
        rig = self.build()
        cmds.move(0.0, 0.0, 15.0, rig.group, relative=True)
        moved = {p: _world(p) for p in PARTS[1:]}
        rig.rebuild()
        for part in PARTS[1:]:
            self.assertLess(_mdiff(moved[part], _world(part)), 1e-5, part)

    def test_layered_animation_survives_a_rebuild(self):
        """A control channel animated through an animation layer is driven by
        the layer's blend node, not a curve: it is carried across like one."""
        rig = self.build(end_control=False)
        ctrl = rig.control("LEG_2")
        cmds.setKeyframe(ctrl, attribute="rz", time=1, value=10.0)
        layer = cmds.animLayer("articulated_layer")
        cmds.animLayer(layer, edit=True, attribute=f"{ctrl}.rz")
        cmds.setKeyframe(ctrl, attribute="rz", time=1, value=5.0, animLayer=layer)
        cmds.currentTime(1)
        before = cmds.getAttr(f"{ctrl}.rz")
        rig.rebuild()
        ctrl = rig.control("LEG_2")
        cmds.currentTime(2)
        cmds.currentTime(1)
        self.assertAlmostEqual(cmds.getAttr(f"{ctrl}.rz"), before, places=4)
        self.assertIn(
            f"{cmds.ls(ctrl)[0]}.rotateZ".split("|")[-1],
            [
                a.split("|")[-1]
                for a in cmds.animLayer(layer, query=True, attribute=True) or []
            ],
        )

    def test_a_telescope_added_after_the_fact_keeps_every_key(self):
        links = [[f"{self.arm}|{p}"] for p in PARTS]
        links[3] = [f"{self.arm}|LEG_3", f"{self.arm}|LEG_4"]  # the rods as one link
        del links[4]
        rig = self.build(links=links)
        self.assertEqual(len(rig.joint_ids()), 4)
        keyed = self._key(rig, [1, 12, 24])
        cmds.setKeyframe(rig.end_control, attribute="translateY", time=12, value=4.0)
        keyed = self._sample([1, 12, 24])
        rig.insert_joint(
            ["LEG_4"]
        )  # its path changed: the build parented it under a joint
        spec = rig.spec
        added = next(j for j in spec["joints"] if j["id"] == "LEG_4")
        self.assertEqual(added["type"], "slide")
        # it joins the end control's solve at give 0: the IK pose holds too
        self.assertEqual(added["weights"], {"tx": 0.0})
        # the head now hangs off the slide
        head = next(j for j in spec["joints"] if j["id"] == "HEAD")
        self.assertEqual(head["parent"], added["link"])
        self._same(keyed, self._sample([1, 12, 24]))
        # and it slides
        cmds.currentTime(12)
        before = _world("HEAD")
        cmds.setAttr(f"{rig.end_control}.ikBlend", 0.0)
        cmds.setAttr(f"{rig.control('LEG_4')}.tx", 1.5)
        self.assertGreater(_mdiff(before, _world("HEAD")), 1e-3)

    def test_a_split_off_part_named_like_a_joint_leaves_that_joint_its_keys(self):
        """A production assembly repeats part names: the new joint takes a
        fresh id, never one a standing joint's parked keys are filed under."""
        wrapper = cmds.group(f"{self.arm}|LEG_4", name="TELESCOPE")
        twin = cmds.rename(f"|ASSET|ARM|{wrapper}|LEG_4", "LEG_2")
        twin_uuid = cmds.ls(twin, uuid=True)[0]
        links = [[f"{self.arm}|{p}"] for p in ("BASE", "LEG_1", "LEG_2")]
        links += [[f"{self.arm}|LEG_3", cmds.ls(twin_uuid, long=True)[0]]]
        links += [[f"{self.arm}|HEAD"]]
        rig = self.build(links=links)
        ctrl = rig.control("LEG_2")
        cmds.setKeyframe(ctrl, attribute="rz", time=1, value=10)
        cmds.setKeyframe(ctrl, attribute="rz", time=24, value=40)
        rig.insert_joint([cmds.ls(twin_uuid, long=True)[0]], joint_type="slide")
        ids = rig.joint_ids()
        self.assertEqual(len(set(ids)), 5, ids)
        self.assertIn("LEG_2", ids)
        self.assertEqual(
            cmds.keyframe(rig.control("LEG_2"), attribute="rz", query=True), [1.0, 24.0]
        )

    def test_folding_a_joint_back_keeps_the_others_animation(self):
        rig = self.build()
        self._key(rig, [1, 24])
        rig.remove_joint("LEG_4")
        self.assertEqual(len(rig.joint_ids()), 4)
        self.assertEqual(
            cmds.keyframe(rig.control("LEG_2"), attribute="rz", query=True), [1.0, 24.0]
        )
        self.assertIsNotNone(rig.end_control)

    def test_a_refused_edit_leaves_the_rig_and_its_keys_standing(self):
        rig = self.build()
        self._key(rig, [1, 24])
        group = rig.group
        joint = next(j for j in rig.spec["joints"] if j["id"] == "LEG_2")
        aim = list(om.MVector(*joint["aim"]) * _world(group))
        with self.assertRaises(ValueError):
            rig.edit_joint("LEG_2", normal=aim)  # a hinge axis along the link
        self.assertTrue(cmds.objExists(group))
        self.assertEqual(
            cmds.keyframe(rig.control("LEG_2"), attribute="rz", query=True), [1.0, 24.0]
        )
        self.assertEqual(
            cmds.keyframe(rig.control("LEG_4"), attribute="tx", query=True), [1.0, 24.0]
        )

    def test_a_rebuild_missing_a_part_names_it_and_leaves_the_rig_standing(self):
        rig = self.build()
        self._key(rig, [1, 24])
        group = rig.group
        cmds.delete("HEAD")
        with self.assertRaisesRegex(ValueError, "no longer in the scene.*HEAD"):
            rig.rebuild()
        self.assertTrue(cmds.objExists(group))
        self.assertEqual(
            cmds.keyframe(rig.control("LEG_2"), attribute="rz", query=True),
            [1.0, 24.0],
        )

    def test_a_retyped_joint_keeps_the_channels_both_types_share(self):
        rig = self.build()
        self._key(rig, [1, 24])
        rig.edit_joint("LEG_2", type="ball")
        ctrl = rig.control("LEG_2")
        self.assertEqual(
            sorted(cmds.listAttr(ctrl, keyable=True)), ["rotateX", "rotateY", "rotateZ"]
        )
        self.assertEqual(cmds.keyframe(ctrl, attribute="rz", query=True), [1.0, 24.0])


class TestRecord(ArticulatedRigCase):
    def test_the_record_is_the_model_the_runtimes_pose(self):
        rig = self.build()
        record = ArticulatedRig.export_record(None)
        self.assertIsNotNone(record)
        payload = record.payload
        self.assertEqual(payload["version"], ptk.SceneRecords.ARTICULATION.version)
        entry = payload["rigs"][0]
        self.assertEqual([j["parent"] for j in entry["joints"]], [None, 0, 1, 2, 3])
        self.assertEqual(sorted(g["node"] for g in entry["grab"]), sorted(PARTS[1:]))
        model = ptk.ArticulationModel(entry)
        self.assertEqual(len(model.channels), 2 + 1 + 1 + 1 + 3)
        # published at build for any export to carry
        stored = ptk.SceneRecords.ARTICULATION.load(__import__("mayatk").DataNodes)
        self.assertEqual(stored["rigs"][0]["name"], rig.name)

    def test_the_grab_names_a_part_renamed_after_the_build(self):
        """A runtime binds a grab by the part's name under its joint: the
        record carries the name the part has now, not the one it was built
        with."""
        rig = self.build()
        cmds.rename("HEAD", "LENS")
        grabbed = [g["node"] for g in rig.record()["grab"]]
        self.assertIn("LENS", grabbed)
        self.assertNotIn("HEAD", grabbed)

    def test_the_rest_in_the_record_is_the_rest_on_the_joints(self):
        rig = self.build()
        rig.set_state(
            [15.0] * len(rig.state()), key=False
        )  # posed: the record must not care
        entry = rig.record()
        for joint in entry["joints"]:
            node = rig.joint(joint["name"][len(rig.name) + 1 : -len("_jnt")])
            orient = om.MEulerRotation(
                *[math.radians(v) for v in cmds.getAttr(f"{node}.jointOrient")[0]]
            ).asQuaternion()
            q = om.MQuaternion(*joint["q"])
            self.assertTrue(
                q.isEquivalent(orient, 1e-6) or q.isEquivalent(orient.negate(), 1e-6)
            )

    def test_maya_shows_the_model_of_the_pose_the_end_control_made(self):
        """FK plus the solve, added by Maya's own nodes, is exactly the model
        of :meth:`state`: the pose a runtime would play back."""
        rig = self.build()
        cmds.move(-6.0, 7.0, 5.0, rig.end_control, relative=True, worldSpace=True)
        rig.set_state(
            self.random_state(rig.model(), random.Random(4), spread=15), key=False
        )
        model, group = rig.model(), _world(rig.group)
        for index, (p, q) in enumerate(model.world(rig.state())):
            xf = om.MTransformationMatrix(om.MQuaternion(*q).asMatrix())
            xf.setTranslation(om.MVector(*p), om.MSpace.kTransform)
            self.assertLess(
                _mdiff(
                    xf.asMatrix() * group, _world(rig.joint(rig.joint_ids()[index]))
                ),
                1e-5,
            )


if __name__ == "__main__":
    unittest.main()
