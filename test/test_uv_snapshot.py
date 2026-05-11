# !/usr/bin/python
# coding=utf-8
"""Tests for UvUtils snapshot/restore/discard helpers."""
import unittest

import maya.cmds as cmds

from mayatk.uv_utils._uv_utils import UvUtils

from base_test import MayaTkTestCase


def _all_uv_sets(obj):
    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or [obj]
    return cmds.polyUVSet(shapes[0], query=True, allUVSets=True) or []


def _current_uv_set(obj):
    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or [obj]
    return cmds.polyUVSet(shapes[0], query=True, currentUVSet=True)[0]


class TestUvSnapshot(MayaTkTestCase):
    """Snapshot must add a backup set; restore must revert mutation; discard cleans up."""

    def test_snapshot_creates_backup_set(self):
        cube = cmds.polyCube(name="snap_cube")[0]
        before = set(_all_uv_sets(cube))
        snapshots = UvUtils.snapshot_uv_sets([cube])
        after = set(_all_uv_sets(cube))
        self.assertEqual(len(snapshots), 1)
        self.assertGreater(
            len(after), len(before), "Snapshot did not add a UV set."
        )
        shape, orig, snap = snapshots[0]
        self.assertIn(orig, after)
        self.assertIn(snap, after)
        self.assertNotEqual(orig, snap)

    def test_restore_reverts_mutation(self):
        import maya.api.OpenMaya as om

        cube = cmds.polyCube(name="restore_cube")[0]
        snapshots = UvUtils.snapshot_uv_sets([cube])
        shape, orig, snap = snapshots[0]

        def _mesh():
            sel = om.MSelectionList()
            sel.add(shape)
            dag = sel.getDagPath(0)
            if not dag.hasFn(om.MFn.kMesh):
                dag.extendToShape()
            return om.MFnMesh(dag)

        def _uv_multiset(uv_set):
            us, vs = _mesh().getUVs(uvSet=uv_set)
            # Round to defeat float drift introduced by setUVs/polyCopyUV.
            return sorted(
                (round(u, 5), round(v, 5)) for u, v in zip(list(us), list(vs))
            )

        pre = _uv_multiset(orig)
        # Mutate via OpenMaya (cmds.polyEditUV is unreliable in standalone).
        mesh = _mesh()
        us, vs = mesh.getUVs()
        new_us = type(us)([u + 0.5 for u in us])
        new_vs = type(vs)([v + 0.5 for v in vs])
        mesh.setUVs(new_us, new_vs)
        mid = _uv_multiset(orig)
        self.assertNotEqual(pre, mid)

        UvUtils.restore_uv_snapshot(snapshots)
        restored = _uv_multiset(orig)
        # Compare as a sorted multiset: polyCopyUV preserves face-UV
        # connectivity but may renumber UV indices, so raw array order
        # is not a stable comparison.
        self.assertEqual(pre, restored)
        self.assertNotIn(snap, _all_uv_sets(cube))
        self.assertIn(orig, _all_uv_sets(cube))

    def test_discard_removes_snapshot_set(self):
        cube = cmds.polyCube(name="discard_cube")[0]
        before = set(_all_uv_sets(cube))
        snapshots = UvUtils.snapshot_uv_sets([cube])
        self.assertGreater(len(_all_uv_sets(cube)), len(before))

        UvUtils.discard_uv_snapshot(snapshots)
        self.assertEqual(set(_all_uv_sets(cube)), before)

    def test_snapshot_handles_multiple_objects(self):
        cubes = [cmds.polyCube(name=f"multi_{i}")[0] for i in range(3)]
        snapshots = UvUtils.snapshot_uv_sets(cubes)
        self.assertEqual(len(snapshots), 3)
        shapes_seen = {s[0] for s in snapshots}
        # Each shape gets its own snapshot entry.
        self.assertEqual(len(shapes_seen), 3)

    def test_repeated_snapshot_names_do_not_collide(self):
        cube = cmds.polyCube(name="collide_cube")[0]
        snap1 = UvUtils.snapshot_uv_sets([cube])
        snap2 = UvUtils.snapshot_uv_sets([cube])
        self.assertNotEqual(snap1[0][2], snap2[0][2])
        sets = _all_uv_sets(cube)
        self.assertIn(snap1[0][2], sets)
        self.assertIn(snap2[0][2], sets)

    def test_restore_is_undoable_via_chunk(self):
        cube = cmds.polyCube(name="undo_cube")[0]
        snapshots = UvUtils.snapshot_uv_sets([cube])
        pre_restore_sets = set(_all_uv_sets(cube))
        UvUtils.restore_uv_snapshot(snapshots)
        post_restore_sets = set(_all_uv_sets(cube))
        self.assertNotEqual(pre_restore_sets, post_restore_sets)
        cmds.undo()
        self.assertEqual(set(_all_uv_sets(cube)), pre_restore_sets)


if __name__ == "__main__":
    unittest.main()
