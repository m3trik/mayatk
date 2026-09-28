# !/usr/bin/python
# coding=utf-8
"""ArticulatedRig -- the engine, the grab and the record, in Maya.

The fixture is the production magnifier's shape, built from primitives under
a scaled asset node the way the production prop sits (``ASSET`` x2 > ``ARM``
> six part groups): a base plate and post, a pole on a knob swivel, an elbow
knob, an upper tube with a collar knob, an outer rod with an inner rod running
inside it, and a head on a small ball. Every part is a GROUP of shells, as a
modelled prop's often are.

Pinned here: the proposal, a build that moves nothing at rest and a teardown
that puts everything back exactly, Maya evaluating the joints exactly as
``ptk.ArticulationModel`` does (what the runtimes play), the grab (the ball
head taking a world turn is the quaternion-order check), limits, undo, and
the post-rig edits carrying animation across -- rebuild, insert (the
telescope added after the fact), fold, retype.
"""

import math
import random
import unittest

import maya.api.OpenMaya as om
import maya.cmds as cmds

import pythontk as ptk
from base_test import MayaTkTestCase

from mayatk.rig_utils.articulated_rig import ArticulatedRig, ArticulatedRigGrab

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

    def test_maya_poses_the_joints_exactly_as_the_model_does(self):
        rig = self.build()
        model = rig.model()
        group = _world(rig.group)
        rng = random.Random(3)
        worst = 0.0
        for _ in range(8):
            state = []
            for slot in range(len(model.channels)):
                lo, hi = model.limits(slot)
                state.append(
                    rng.uniform(-40 if lo is None else lo, 40 if hi is None else hi)
                )
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
        self.assertEqual(cmds.listRelatives("LEG_3", parent=True), ["ARM"])

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
        rig.teardown()
        self.assertAtRest()
        for part in PARTS:
            self.assertEqual(cmds.listRelatives(part, parent=True), ["ARM"])
            for attr, value in before[part].items():
                self.assertEqual(cmds.getAttr(f"{part}.{attr}"), value, (part, attr))
        leftovers = [n for n in cmds.ls() if n.startswith("arm_") or n.endswith("_RIG")]
        self.assertEqual(leftovers, [])
        self.assertIsNone(
            ptk.SceneRecords.ARTICULATION.load(__import__("mayatk").DataNodes)
        )


class TestGrab(ArticulatedRigCase):
    def test_a_grab_brings_the_held_point_to_its_target_inside_the_limits(self):
        rig = self.build()
        point = _w(_add(BALL, _norm(R), 12.15))
        hold = rig.grab_begin("ring", point)
        target = [point[0] - 6.0, point[1] + 8.0, point[2] + 4.0]
        state = rig.grab_to(hold, target, key=False)
        self.assertLess(math.dist(rig.held_point(hold), target), 0.05)
        self.assertEqual(state, hold["model"].clamp(state))

    def test_a_turned_hand_turns_the_ball_head_by_that_turn(self):
        rig = self.build()
        point = _w(_add(BALL, _norm(R), 12.15))
        hold = rig.grab_begin("ring", point)
        before = om.MTransformationMatrix(_world("HEAD")).rotation(asQuaternion=True)
        turn = om.MQuaternion(
            math.radians(20.0), om.MVector(0, 1, 0)
        )  # 20 degrees about world Y
        rig.grab_to(hold, point, turn=(turn.x, turn.y, turn.z, turn.w), key=False)
        after = om.MTransformationMatrix(_world("HEAD")).rotation(asQuaternion=True)
        # Maya's quaternion product applies the left one first: before, then the turn.
        want = before * turn
        self.assertTrue(
            after.isEquivalent(want, 1e-3) or after.isEquivalent(want.negate(), 1e-3)
        )
        self.assertLess(math.dist(rig.held_point(hold), point), 0.05)

    def test_pose_to_lands_a_part_on_a_target(self):
        rig = self.build()
        target = _w(_add(BALL, (-5, 6, 3)))
        left = rig.pose_to("HEAD", target, point=_w(BALL), key=False)
        self.assertLess(left, 0.05)

    def test_the_grab_tool_picks_drags_and_keys_on_release(self):
        rig = self.build()
        cmds.autoKeyframe(state=True)
        try:
            ring = _w(_add(BALL, _norm(R), 12.15))
            origin = [ring[0], ring[1], ring[2] + 200.0]
            tool = ArticulatedRigGrab()
            self.assertTrue(tool.press(origin, (0.0, 0.0, -1.0)))
            self.assertEqual(tool.rig.name, rig.name)
            moved = [origin[0] - 5.0, origin[1] + 5.0, origin[2]]
            tool.drag(moved, (0.0, 0.0, -1.0))
            tool.release()
            self.assertGreater(
                len(cmds.keyframe(rig.control("LEG_1"), query=True) or []), 0
            )
            cmds.undo()  # the whole drag, keys included
            self.assertEqual(cmds.keyframe(rig.control("LEG_1"), query=True), None)
        finally:
            cmds.autoKeyframe(state=False)

    def test_the_grab_tool_ignores_the_fixed_root(self):
        self.build()
        tool = ArticulatedRigGrab()
        plate = _w((0.0, 0.45, 0.0))
        self.assertFalse(tool.press((plate[0], plate[1], 100.0), (0.0, 0.0, -1.0)))


class TestLimits(ArticulatedRigCase):
    def test_limits_from_the_pose_bound_the_control_and_the_record(self):
        rig = self.build()
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


class TestPostRig(ArticulatedRigCase):
    def _key(self, rig, frames):
        """Key every control at *frames*; return each part's world matrix per frame."""
        rng = random.Random(9)
        model = rig.model()
        for frame in frames:
            cmds.currentTime(frame)
            state = []
            for slot in range(len(model.channels)):
                lo, hi = model.limits(slot)
                state.append(
                    rng.uniform(
                        -30 if lo is None else lo * 0.8, 30 if hi is None else hi * 0.8
                    )
                )
            rig.set_state(state, key=True)
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

    def test_a_telescope_added_after_the_fact_keeps_every_key(self):
        links = [[f"{self.arm}|{p}"] for p in PARTS]
        links[3] = [f"{self.arm}|LEG_3", f"{self.arm}|LEG_4"]  # the rods as one link
        del links[4]
        rig = self.build(links=links)
        self.assertEqual(len(rig.joint_ids()), 4)
        keyed = self._key(rig, [1, 12, 24])
        rig.insert_joint(
            ["LEG_4"]
        )  # its path changed: the build parented it under a joint
        spec = rig.spec
        added = next(j for j in spec["joints"] if j["id"] == "LEG_4")
        self.assertEqual(added["type"], "slide")
        # the head now hangs off the slide
        head = next(j for j in spec["joints"] if j["id"] == "HEAD")
        self.assertEqual(head["parent"], added["link"])
        self._same(keyed, self._sample([1, 12, 24]))
        # and it slides
        cmds.currentTime(12)
        before = _world("HEAD")
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


if __name__ == "__main__":
    unittest.main()
