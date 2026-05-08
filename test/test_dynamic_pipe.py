# !/usr/bin/python
# coding=utf-8
"""Smoke tests for ``mayatk.edit_utils.dynamic_pipe.DynamicPipe``.

The previous implementation imported PyMEL idioms (``getTranslation``,
``shape_node.getKnotDomain``, attribute-proxy ``circle.overrideEnabled.set``)
and silently broke once PyMEL was removed. These tests build a tiny pipe end-
to-end so any reintroduction of the old patterns surfaces immediately.
"""
import unittest

import maya.cmds as cmds

from mayatk.edit_utils.dynamic_pipe import DynamicPipe
from base_test import MayaTkTestCase


class TestDynamicPipe(MayaTkTestCase):
    """Build a pipe from real locators and verify the resulting scene state."""

    def _make_locators(self, positions):
        locs = []
        for p in positions:
            loc = cmds.spaceLocator()[0]
            cmds.xform(loc, worldSpace=True, translation=p)
            locs.append(loc)
        return locs

    def test_requires_at_least_two_locators(self):
        with self.assertRaises(ValueError):
            DynamicPipe([cmds.spaceLocator()[0]])

    def test_basic_pipe_three_locators(self):
        locators = self._make_locators([(0, 0, 0), (5, 0, 0), (10, 0, 0)])
        pipe = DynamicPipe(locators)

        # One circle per locator, parented under the locator (so it follows).
        self.assertEqual(len(pipe.circles), 3)
        for loc, circle in zip(locators, pipe.circles):
            parents = cmds.listRelatives(circle, parent=True, fullPath=False)
            self.assertEqual(parents, [loc])

        # Cross-section circles are reference-display so the user can keep
        # picking the locators instead.
        for circle in pipe.circles:
            self.assertEqual(cmds.getAttr(f"{circle}.overrideEnabled"), 1)
            self.assertEqual(cmds.getAttr(f"{circle}.overrideDisplayType"), 2)

        # A driving curve was created.
        self.assertTrue(cmds.objExists(pipe.curve))

    def test_loft_creates_pipe_segments(self):
        locators = self._make_locators([(0, 0, 0), (5, 0, 0), (10, 0, 0)])
        pipe = DynamicPipe(locators)
        segments = pipe.create_pipe_geometry()

        # 3 circles -> 2 segments.
        self.assertEqual(len(segments), 2)
        for seg in segments:
            self.assertTrue(cmds.objExists(seg))
        self.assertEqual(pipe.pipe_segments, segments)

    def test_inbetweens_inserted_between_each_pair(self):
        locators = self._make_locators([(0, 0, 0), (10, 0, 0)])
        pipe = DynamicPipe(locators, num_inbetween=3)

        # 2 originals + 3 inserted between them = 5.
        self.assertEqual(len(pipe.locators), 5)
        # First and last should still be the originals.
        self.assertEqual(pipe.locators[0], locators[0])
        self.assertEqual(pipe.locators[-1], locators[-1])

        # Inserted positions should sit on the line between the originals.
        for loc in pipe.locators[1:-1]:
            x, y, z = cmds.xform(
                loc, query=True, worldSpace=True, translation=True
            )
            self.assertAlmostEqual(y, 0.0, places=4)
            self.assertAlmostEqual(z, 0.0, places=4)
            self.assertGreater(x, 0.0)
            self.assertLess(x, 10.0)

    def test_two_locators_uses_linear_curve(self):
        """Two-point input previously crashed because cubic degree > num pts.

        Verify the constructor falls back to a lower degree instead.
        """
        locators = self._make_locators([(0, 0, 0), (5, 0, 0)])
        pipe = DynamicPipe(locators)
        # Must succeed and produce a usable curve + 2 circles.
        self.assertTrue(cmds.objExists(pipe.curve))
        self.assertEqual(len(pipe.circles), 2)
        # Pipe should still loft into a single segment.
        segs = pipe.create_pipe_geometry()
        self.assertEqual(len(segs), 1)

    def test_segments_to_loft_filters_pairs(self):
        locators = self._make_locators(
            [(0, 0, 0), (3, 0, 0), (6, 0, 0), (9, 0, 0)]
        )
        pipe = DynamicPipe(locators)
        # Loft only the middle pair (circles[1]→circles[2]).
        segments = pipe.create_pipe_geometry([1])
        self.assertEqual(len(segments), 1)

    def test_segments_to_loft_rejects_non_int(self):
        locators = self._make_locators([(0, 0, 0), (5, 0, 0)])
        pipe = DynamicPipe(locators)
        with self.assertRaises(ValueError):
            pipe.create_pipe_geometry(["not-an-int"])


if __name__ == "__main__":
    unittest.main()
