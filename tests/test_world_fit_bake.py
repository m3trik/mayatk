# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.anim_utils.world_fit_bake module.

Covers ``WorldFitBake.non_inheriting``: the rule every FBX write's fold
(``SmartBake.fold_offsets``) and the Scene Exporter's flatten ask -- which
nodes ignore (``inheritsTransform`` off) a parent that leaves the origin. The
flatten and bake themselves are covered by test_smart_bake.py
(``TestShearedMatrixFlatten``, ``TestFoldOffsets``).
"""

import importlib
import unittest
from unittest.mock import patch

import maya.api.OpenMaya as om2
import maya.cmds as cmds

from base_test import MayaTkTestCase

from mayatk.anim_utils.world_fit_bake import WorldFitBake


class _CountingCmds:
    """``maya.cmds`` with every ``currentTime`` SET counted -- each one a
    scene evaluation; a query is free."""

    def __init__(self, real):
        self._real = real
        self.sets = 0

    def currentTime(self, *args, **kwargs):
        if not (kwargs.get("query") or kwargs.get("q")):
            self.sets += 1
        return self._real.currentTime(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestNonInheriting(MayaTkTestCase):
    """``non_inheriting`` names each node whose parent leaves the origin at the
    current frame or any frame asked, and walks the timeline only over the
    frames that parent's drivers can move it at: a parent nothing animates
    is settled at the current frame, one keyed (or constrained) over its keys'
    span, anything unprovable over every frame. Before, a still parent cost
    one ``currentTime`` (a whole-scene evaluation) per frame of every FBX
    write's range -- a skinned mesh under a GEO group, the usual setup.
    Added: 2026-10-19
    """

    FRAMES = list(range(1, 101))

    def setUp(self):
        super().setUp()
        cmds.playbackOptions(min=1, max=100, animationStartTime=1, animationEndTime=100)
        cmds.currentTime(1)

    def _loose(self, parent, node=None):
        """*node* (a new cube when None) under *parent*, ignoring it."""
        node = node or cmds.polyCube(name="loose")[0]
        node = cmds.ls(cmds.parent(node, parent)[0], long=True)[0]
        cmds.setAttr(f"{node}.inheritsTransform", False)
        return node

    def _judge(self, nodes):
        """``(non_inheriting(nodes, FRAMES), currentTime sets it made)``."""
        module = importlib.import_module("mayatk.anim_utils.world_fit_bake")
        counting = _CountingCmds(cmds)
        with patch.object(module, "cmds", counting):
            found = WorldFitBake.non_inheriting(nodes, self.FRAMES)
        self.assertEqual(cmds.currentTime(query=True), 1)
        return found, counting.sets

    def _walked(self, nodes):
        """The rule by brute force: every parent's world at the current frame
        and at every frame."""
        identity = om2.MMatrix()
        parents = {
            node: cmds.listRelatives(node, parent=True, fullPath=True)[0]
            for node in cmds.ls(nodes, long=True)
        }
        placed = {}
        for frame in [None] + self.FRAMES:
            if frame is not None:
                cmds.currentTime(frame)
            for node, parent in parents.items():
                world = om2.MMatrix(cmds.getAttr(f"{parent}.worldMatrix[0]"))
                if not world.isEquivalent(identity, 1e-6):
                    placed.setdefault(node, parent)
        cmds.currentTime(1)
        return dict(sorted(placed.items()))

    def _keyed_late(self, node, attr="translateX", value=4.0):
        """Still until frame 70, *value* by frame 80."""
        cmds.setKeyframe(node, attribute=attr, time=70, value=0)
        cmds.setKeyframe(node, attribute=attr, time=80, value=value)

    def test_a_still_parent_is_settled_without_walking_the_timeline(self):
        """A skinned mesh under a GEO group at the origin, its skeleton keyed
        beside it: nothing is named and no frame is evaluated."""
        geo = cmds.group(empty=True, name="GEO")
        rig = cmds.group(empty=True, name="RIG")
        cmds.select(clear=True)
        joints = [cmds.joint(position=(0, 5 * i, 0)) for i in range(3)]
        cmds.parent(joints[0], rig)
        self._keyed_late(joints[1], "rotateZ", 30)
        mesh = cmds.polyCylinder(height=10, subdivisionsHeight=4, name="body")[0]
        cmds.move(0, 5, 0, mesh)
        cmds.skinCluster(joints, mesh, toSelectedBones=True)
        mesh = self._loose(geo, node=mesh)

        found, sets = self._judge([mesh])

        self.assertEqual(found, {})
        self.assertEqual(found, self._walked([mesh]))
        self.assertEqual(sets, 0)

    def test_a_parent_off_the_origin_now_is_named_without_a_walk(self):
        group = cmds.group(empty=True, name="placed")
        cmds.xform(group, translation=(5, 0, 0))
        cube = self._loose(group)

        found, sets = self._judge([cube])

        self.assertEqual(found, {cube: cmds.ls(group, long=True)[0]})
        self.assertEqual(sets, 0)

    def test_a_keyed_parent_is_walked_over_its_keys_only(self):
        """Still until frame 70: judged over the keys' span (and one frame
        either side), not from frame 1."""
        group = cmds.group(empty=True, name="late")
        self._keyed_late(group)
        cube = self._loose(group)

        found, sets = self._judge([cube])

        self.assertEqual(found, {cube: cmds.ls(group, long=True)[0]})
        self.assertEqual(found, self._walked([cube]))
        self.assertLess(sets, 15)

    def test_every_kind_of_motion_still_names_the_node(self):
        """A parent at the origin at the current frame that a constraint, an
        expression (with or without time), a driven key, an offset drive, an
        animation layer or its own parent moves later -- each named, exactly
        as a frame-by-frame walk names it."""

        def constraint(parent):
            driver = cmds.spaceLocator(name="driver")[0]
            self._keyed_late(driver)
            cmds.pointConstraint(driver, parent)

        def expression(parent):
            driver = cmds.spaceLocator(name="driver")[0]
            self._keyed_late(driver, "translateY", 1.0)
            cmds.expression(string=f"{parent}.translateX = {driver}.translateY * 3;")

        def time_expression(parent):
            cmds.expression(string=f"{parent}.translateX = frame > 75 ? 2 : 0;")

        def driven_key(parent):
            driver = cmds.spaceLocator(name="driver")[0]
            for drive, value in ((0.0, 0.0), (1.0, 3.0)):
                cmds.setDrivenKeyframe(
                    f"{parent}.translateX",
                    currentDriver=f"{driver}.translateY",
                    driverValue=drive,
                    value=value,
                )
            self._keyed_late(driver, "translateY", 1.0)

        def offset_drive(parent):
            driver = cmds.spaceLocator(name="driver")[0]
            self._keyed_late(driver)
            cmds.connectAttr(f"{driver}.worldMatrix[0]", f"{parent}.offsetParentMatrix")

        def anim_layer(parent):
            cmds.select(parent)
            layer = cmds.animLayer("lateLayer", addSelectedObjects=True)
            cmds.setKeyframe(
                parent, attribute="translateX", time=70, value=0, animLayer=layer
            )
            cmds.setKeyframe(
                parent, attribute="translateX", time=80, value=4, animLayer=layer
            )
            cmds.select(clear=True)

        def grandparent(parent):
            grand = cmds.group(empty=True, name="grand")
            cmds.parent(parent, grand)
            self._keyed_late(grand)

        kinds = {
            "constraint": constraint,
            "expression": expression,
            "time expression": time_expression,
            "driven key": driven_key,
            "offset drive": offset_drive,
            "animation layer": anim_layer,
            "its own parent's motion": grandparent,
        }
        for kind, rig in kinds.items():
            with self.subTest(kind=kind):
                cmds.file(new=True, force=True)
                cmds.playbackOptions(min=1, max=100)
                cmds.currentTime(1)
                parent = cmds.group(empty=True, name="moved")
                rig(parent)
                cmds.currentTime(1)
                cube = self._loose(cmds.ls("moved", long=True)[0])
                parent = cmds.ls("moved", long=True)[0]

                found, _sets = self._judge([cube])

                self.assertEqual(found, {cube: parent})
                self.assertEqual(found, self._walked([cube]))

    def test_an_ik_solved_parent_is_named(self):
        """A joint an IK handle turns later: no wire on its rotate, the
        solver writes it."""
        cmds.select(clear=True)
        root = cmds.joint(position=(0, 0, 0), name="ik_root")
        cmds.joint(position=(0, 5, 0), name="ik_mid")
        end = cmds.joint(position=(0, 10, 0), name="ik_end")
        handle = cmds.ikHandle(startJoint=root, endEffector=end, solver="ikSCsolver")[0]
        self._keyed_late(handle, "translateX", 3.0)
        cmds.currentTime(1)
        cube = self._loose(cmds.ls(root, long=True)[0])

        found, _sets = self._judge([cube])

        self.assertEqual(found, self._walked([cube]))
        self.assertEqual(list(found), [cube])


if __name__ == "__main__":
    unittest.main(exit=False)
