# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.core_utils.undo_recorder -- OpenMaya edits on Maya's undo queue.

An OpenMaya write bypasses the undo queue, so the Ctrl+Z after it reverts whatever
the user did BEFORE it, against the edited scene (measured 2026-09-14: after
``optimize_keys``, one undo left the optimized curve as it was and reverted the
previous edit). ``UndoRecorder`` hands a block's undo objects to Maya's own
``ufeCmd``, so the block is one ordinary undo step.
"""

import os
import unittest

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds

import mayatk
from base_test import MayaTkTestCase
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.undo_recorder import UndoRecorder


class TestUndoRecorder(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        # Standalone Maya starts with recording OFF: without this every undo
        # assertion below would pass vacuously.
        cmds.undoInfo(state=True, infinity=True)
        self.cube = cmds.polyCube(name="recorder_cube")[0]
        cmds.setKeyframe(self.cube, attribute="translateX", t=1, v=0.0)
        cmds.setKeyframe(self.cube, attribute="translateX", t=10, v=5.0)
        self.curve = cmds.listConnections(self.cube + ".translateX", type="animCurve")[
            0
        ]

    def _curve_fn(self):
        selection = om2.MSelectionList()
        selection.add(self.curve)
        return oma2.MFnAnimCurve(selection.getDependNode(0))

    def _times(self):
        return cmds.keyframe(self.curve, query=True, timeChange=True)

    @staticmethod
    def _frame(value):
        return om2.MTime(value, om2.MTime.uiUnit())

    def test_an_openmaya_key_edit_is_one_undo_step(self):
        with UndoRecorder.record() as recorder:
            self._curve_fn().addKey(self._frame(5), 9.0, **recorder.anim)
        self.assertEqual(self._times(), [1.0, 5.0, 10.0])
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0])
        cmds.redo()
        self.assertEqual(self._times(), [1.0, 5.0, 10.0])

    def test_the_undo_after_a_recorded_edit_leaves_the_edit_before_it_alone(self):
        """The measured harm: an unrecorded edit sent Ctrl+Z to the one before it."""
        cmds.setAttr(self.cube + ".translateZ", 9.0)
        with UndoRecorder.record() as recorder:
            fn = self._curve_fn()
            fn.remove(fn.find(self._frame(10)), **recorder.anim)
        self.assertEqual(self._times(), [1.0])
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0], "the recorded edit is what undoes")
        self.assertEqual(cmds.getAttr(self.cube + ".translateZ"), 9.0)

    def test_recorded_and_cmds_edits_in_one_chunk_undo_together(self):
        with CoreUtils.undo_chunk("recorder chunk"):
            cmds.setAttr(self.cube + ".translateZ", 4.0)
            with UndoRecorder.record() as recorder:
                self._curve_fn().addKey(self._frame(5), 9.0, **recorder.anim)
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0])
        self.assertEqual(cmds.getAttr(self.cube + ".translateZ"), 0.0)

    def test_modifier_and_snapshot_records_undo_and_redo(self):
        shape = cmds.listRelatives(self.cube, shapes=True)[0]
        selection = om2.MSelectionList()
        selection.add(shape)
        mesh = om2.MFnMesh(selection.getDependNode(0))
        before = mesh.getPoints()
        after = om2.MPointArray([om2.MPoint(p.x + 2.0, p.y, p.z) for p in before])
        with UndoRecorder.record() as recorder:
            modifier = om2.MDGModifier()
            node = modifier.createNode("multiplyDivide")
            modifier.doIt()
            recorder.modifier(modifier)
            mesh.setPoints(after)
            recorder.snapshot(
                undo=lambda: mesh.setPoints(before), redo=lambda: mesh.setPoints(after)
            )
        name = om2.MFnDependencyNode(node).name()

        def vertex_x():
            return round(cmds.pointPosition(self.cube + ".vtx[0]", local=True)[0], 3)

        self.assertTrue(cmds.objExists(name))
        self.assertEqual(vertex_x(), 1.5)
        cmds.undo()
        self.assertFalse(cmds.objExists(name))
        self.assertEqual(vertex_x(), -0.5)
        cmds.redo()
        self.assertTrue(cmds.objExists(name))
        self.assertEqual(vertex_x(), 1.5)

    def test_normals_record_their_vectors_and_their_locks(self):
        """``recorder.normals`` puts back each normal AND whether it was locked.

        Vertex 0's normals start locked, pointing up; the block re-aims them and
        locks vertex 1's too. The undo must leave vertex 1 unlocked, following
        its faces again -- not frozen where it happened to point.
        """
        mesh = cmds.polyCube(name="normals_cube", constructionHistory=False)[0]
        fn = CoreUtils.get_mfn_mesh(mesh)
        fn.setVertexNormal(om2.MVector(0.0, 1.0, 0.0), 0)
        vtx0, vtx1 = mesh + ".vtx[0]", mesh + ".vtx[1]"

        def normals(vertex):
            flat = cmds.polyNormalPerVertex(vertex, query=True, xyz=True)
            return [round(value, 3) for value in flat]

        def locked(vertex):
            return cmds.polyNormalPerVertex(vertex, query=True, freezeNormal=True)

        free = normals(vtx1)
        with UndoRecorder.record() as recorder, recorder.normals(fn):
            fn.setVertexNormal(om2.MVector(0.0, 0.0, 1.0), 0)
            fn.setVertexNormal(om2.MVector(1.0, 0.0, 0.0), 1)
        self.assertEqual(normals(vtx1), [1.0, 0.0, 0.0] * 3)
        cmds.undo()
        self.assertEqual(normals(vtx0), [0.0, 1.0, 0.0] * 3)
        self.assertTrue(all(locked(vtx0)))
        self.assertFalse(any(locked(vtx1)))
        self.assertEqual(normals(vtx1), free)
        cmds.redo()
        self.assertEqual(normals(vtx0), [0.0, 0.0, 1.0] * 3)
        self.assertEqual(normals(vtx1), [1.0, 0.0, 0.0] * 3)
        self.assertTrue(all(locked(vtx1)))

    def test_nothing_is_recorded_while_the_queue_is_off(self):
        """No cost where nobody can undo -- the export path runs this way."""
        cmds.setAttr(self.cube + ".translateZ", 9.0)
        with CoreUtils.undo_disabled():
            with UndoRecorder.record() as recorder:
                self.assertEqual(recorder.anim, {})
                self._curve_fn().addKey(self._frame(5), 9.0, **recorder.anim)
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.cube + ".translateZ"), 0.0)
        self.assertEqual(self._times(), [1.0, 5.0, 10.0], "nothing recorded it")

    def test_a_block_that_raises_still_leaves_its_applied_edits_undoable(self):
        with self.assertRaises(RuntimeError):
            with UndoRecorder.record() as recorder:
                self._curve_fn().addKey(self._frame(5), 9.0, **recorder.anim)
                raise RuntimeError("mid-edit failure")
        self.assertEqual(self._times(), [1.0, 5.0, 10.0])
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0])

    def test_a_block_inside_another_keeps_the_queue_in_edit_order(self):
        """The inner block commits the outer block's edits first.

        Committed only as each block closed, the outer block's key add would
        sit AFTER the inner block's removal of that key on the queue, and the
        undo would take a key away before putting the removed one back.
        """
        with CoreUtils.undo_chunk("nested blocks"):
            with UndoRecorder.record() as outer:
                fn = self._curve_fn()
                fn.addKey(self._frame(5), 9.0, **outer.anim)
                with UndoRecorder.record() as inner:
                    fn.remove(fn.find(self._frame(5)), **inner.anim)
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0])
        self.assertEqual(
            cmds.keyframe(self.curve, query=True, valueChange=True), [0.0, 5.0]
        )
        cmds.redo()
        self.assertEqual(self._times(), [1.0, 10.0])

    def test_recording_loads_no_plugin_from_outside_mayas_install(self):
        """By default GUI Maya stops on an "Untrusted Plugin Loading" prompt for a
        plugin loaded from outside its trusted locations, and blocks until someone
        answers it (a plugin of mayatk's own did, 2026-09-14). Standalone never
        prompts, so the load itself is checked: the step rides the
        ``ufeSupport`` plugin Maya ships, and no plugin loads from mayatk.
        """
        with UndoRecorder.record() as recorder:
            self._curve_fn().addKey(self._frame(5), 9.0, **recorder.anim)

        def folded(path):
            return os.path.normcase(os.path.normpath(path))

        package = folded(os.path.dirname(os.path.abspath(mayatk.__file__)))
        for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
            path = folded(cmds.pluginInfo(name, query=True, path=True))
            self.assertFalse(
                path.startswith(package + os.sep), f"{name} loads from {path}"
            )
        self.assertTrue(cmds.pluginInfo("ufeSupport", query=True, loaded=True))
        carrier = folded(cmds.pluginInfo("ufeSupport", query=True, path=True))
        self.assertTrue(
            carrier.startswith(folded(os.environ["MAYA_LOCATION"]) + os.sep), carrier
        )
        cmds.undo()
        self.assertEqual(self._times(), [1.0, 10.0])


if __name__ == "__main__":
    unittest.main()
