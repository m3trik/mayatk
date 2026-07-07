# !/usr/bin/python
# coding=utf-8
"""Tests for edit_utils/snap.py — Snap.snap_to_grid component handling.

The component branch used a PyMEL-era probe (``hasattr(obj, "getPosition")``)
that is always False for the strings ``cmds.ls(flatten=True)`` returns, so
components silently fell through to the transform branch: the delta was
computed from the OWNING TRANSFORM's pivot and applied to the component —
leaving it off-grid. These tests pin the documented behavior ("Components
snap their positions; transforms snap their pivots").
"""
import unittest

try:
    from base_test import MayaTkTestCase
except ImportError:
    from mayatk.test.base_test import MayaTkTestCase

import maya.cmds as cmds
from mayatk.edit_utils.snap import Snap


class TestSnapToGrid(MayaTkTestCase):
    def _make_offgrid_cube(self):
        cube = cmds.polyCube(name="snapCube", w=1, h=1, d=1, ch=False)[0]
        cmds.move(0.3, 0.7, -0.2, cube, absolute=True, worldSpace=True)
        return cube

    def _assert_on_grid(self, pos, msg=""):
        for v in pos:
            self.assertAlmostEqual(v, round(v), places=5, msg=msg or str(pos))

    def test_transform_pivot_snaps(self):
        cube = self._make_offgrid_cube()
        count = Snap.snap_to_grid([cube], grid_size=1.0)
        self.assertEqual(count, 1)
        self._assert_on_grid(cmds.xform(cube, q=True, ws=True, rp=True))

    def test_vertex_snaps_its_own_position(self):
        cube = self._make_offgrid_cube()
        vtx = f"{cube}.vtx[0]"
        before_other = cmds.pointPosition(f"{cube}.vtx[1]", world=True)

        count = Snap.snap_to_grid([vtx], grid_size=1.0)

        self.assertEqual(count, 1)
        self._assert_on_grid(
            cmds.pointPosition(vtx, world=True),
            "the vertex itself must land on the grid",
        )
        # Only the snapped vertex moves — never the whole object.
        after_other = cmds.pointPosition(f"{cube}.vtx[1]", world=True)
        for a, b in zip(before_other, after_other):
            self.assertAlmostEqual(a, b, places=5, msg="vtx[1] must not move")

    def test_edge_snaps_endpoint_vertices(self):
        cube = self._make_offgrid_cube()
        edge = f"{cube}.e[0]"

        Snap.snap_to_grid([edge], grid_size=1.0)

        verts = cmds.ls(
            cmds.polyListComponentConversion(edge, toVertex=True), flatten=True
        )
        self.assertGreaterEqual(len(verts), 2)
        for v in verts:
            self._assert_on_grid(
                cmds.pointPosition(v, world=True),
                f"{v} (edge endpoint) must land on the grid",
            )

    def test_axis_filter_only_snaps_named_axes(self):
        cube = self._make_offgrid_cube()
        vtx = f"{cube}.vtx[0]"
        before = cmds.pointPosition(vtx, world=True)

        Snap.snap_to_grid([vtx], grid_size=1.0, axes="y")

        after = cmds.pointPosition(vtx, world=True)
        self.assertAlmostEqual(after[1], round(after[1]), places=5)
        self.assertAlmostEqual(after[0], before[0], places=5)
        self.assertAlmostEqual(after[2], before[2], places=5)


if __name__ == "__main__":
    unittest.main()
