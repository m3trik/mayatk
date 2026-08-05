# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.mat_utils.bake_sets.

The scene-stored bake-source set shared by the substance and marmoset
bridges. Regression coverage for the promotion out of ``substance_bridge``
and the source/target rename -- canonical set naming plus transparent
adoption of BOTH legacy sets older scenes carry
(``bakeBridge_highPoly``, ``substanceBridge_highPoly``).
"""

import unittest

import maya.cmds as cmds

from mayatk.mat_utils.bake_sets import BakeSourceSet

from base_test import MayaTkTestCase


class TestBakeSourceSet(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="bakeset_cube")[0]
        self.other = cmds.polyCube(name="bakeset_other")[0]

    def tearDown(self):
        BakeSourceSet.clear()
        super().tearDown()

    def test_define_creates_canonical_set(self):
        members = BakeSourceSet.define([self.cube])
        self.assertTrue(cmds.objExists(BakeSourceSet.SET_NAME))
        self.assertEqual([m.split("|")[-1] for m in members], [self.cube])

    def test_legacy_set_is_read_transparently(self):
        """A scene saved before the promotion still resolves its set."""
        legacy = BakeSourceSet.LEGACY_SET_NAMES[0]
        cmds.sets(self.cube, name=legacy)
        self.assertTrue(BakeSourceSet.exists())
        self.assertEqual(
            [m.split("|")[-1] for m in BakeSourceSet.members()], [self.cube]
        )

    def test_define_migrates_legacy_set(self):
        """Redefining replaces the legacy node with the canonical one."""
        legacy = BakeSourceSet.LEGACY_SET_NAMES[0]
        cmds.sets(self.cube, name=legacy)
        BakeSourceSet.define([self.other])
        self.assertFalse(cmds.objExists(legacy))
        self.assertTrue(cmds.objExists(BakeSourceSet.SET_NAME))
        self.assertEqual(
            [m.split("|")[-1] for m in BakeSourceSet.members()], [self.other]
        )

    def test_clear_removes_canonical_and_legacy(self):
        legacy = BakeSourceSet.LEGACY_SET_NAMES[0]
        cmds.sets(self.cube, name=legacy)
        BakeSourceSet.define([self.other])  # canonical now exists
        cmds.sets(self.cube, name=legacy)  # recreate a stray legacy node
        BakeSourceSet.clear()
        self.assertFalse(BakeSourceSet.exists())
        self.assertFalse(cmds.objExists(legacy))

    def test_all_legacy_names_are_read_transparently(self):
        """BOTH prior names resolve -- a scene from the highPoly era included."""
        for legacy in BakeSourceSet.LEGACY_SET_NAMES:
            cmds.sets(self.cube, name=legacy)
            self.assertTrue(BakeSourceSet.exists(), legacy)
            BakeSourceSet.clear()

    def test_backcompat_alias(self):
        """The one-release-old class name still resolves to the same object."""
        from mayatk.mat_utils.bake_sets import HighPolySet

        self.assertIs(HighPolySet, BakeSourceSet)

    def test_empty_define_clears(self):
        BakeSourceSet.define([self.cube])
        BakeSourceSet.define([])
        self.assertFalse(BakeSourceSet.exists())
        self.assertEqual(BakeSourceSet.members(), [])


if __name__ == "__main__":
    unittest.main()
