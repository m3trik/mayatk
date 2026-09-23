# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.mat_utils.bake_sets.

The scene-stored bake sets. :class:`BakeSourceSet` is shared by the substance
and marmoset bridges: regression coverage for the promotion out of
``substance_bridge`` and the source/target rename -- canonical set naming plus
transparent adoption of BOTH legacy sets older scenes carry
(``bakeBridge_highPoly``, ``substanceBridge_highPoly``).
:class:`LightmapExcludeSet` is the lightmap baker's Exclude set.
"""

import unittest

import maya.cmds as cmds

from mayatk.mat_utils.bake_sets import BakeSet, BakeSourceSet, LightmapExcludeSet

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

    def test_retired_class_alias_stays_removed(self):
        """``HighPolySet`` aliased the class from 2026-08-05 through 27 releases
        with no caller, and was retired 2026-09-21 -- together with the
        ``substance_bridge`` re-export and both bridges' ``high_poly_path_for``.
        Scenes saved under the old SET names still resolve (above); only the
        Python names are gone."""
        from mayatk.mat_utils import bake_sets
        from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import MarmosetBridge
        from mayatk.mat_utils.substance_bridge import _substance_bridge

        self.assertFalse(hasattr(bake_sets, "HighPolySet"))
        self.assertFalse(hasattr(_substance_bridge, "HighPolySet"))
        self.assertFalse(
            hasattr(_substance_bridge.SubstanceBridge, "high_poly_path_for")
        )
        self.assertFalse(hasattr(MarmosetBridge, "high_poly_path_for"))

    def test_empty_define_clears(self):
        BakeSourceSet.define([self.cube])
        BakeSourceSet.define([])
        self.assertFalse(BakeSourceSet.exists())
        self.assertEqual(BakeSourceSet.members(), [])


class TestLightmapExcludeSet(MayaTkTestCase):
    """The lightmap Exclude set: a second :class:`BakeSet` with its own node.

    What it adds over the storage is :meth:`BakeSet.meshes` -- the members as
    the bake reads them, a group standing for every mesh under it.
    """

    def tearDown(self):
        LightmapExcludeSet.clear()
        BakeSourceSet.clear()
        super().tearDown()

    @staticmethod
    def _cube(name):
        return cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]

    def test_each_set_keeps_its_own_node(self):
        a = self._cube("exOwnA")
        b = self._cube("exOwnB")
        LightmapExcludeSet.define([a])
        BakeSourceSet.define([b])
        self.assertTrue(cmds.objExists(LightmapExcludeSet.SET_NAME))
        self.assertEqual(LightmapExcludeSet.members(), [a])
        self.assertEqual(BakeSourceSet.members(), [b])
        LightmapExcludeSet.clear()
        self.assertEqual(BakeSourceSet.members(), [b])  # untouched
        self.assertTrue(issubclass(LightmapExcludeSet, BakeSet))

    def test_meshes_expand_a_group_and_skip_what_is_not_a_mesh(self):
        a = self._cube("exMeshA")
        b = self._cube("exMeshB")
        group = cmds.group(a, b, name="exMeshGroup")
        locator = cmds.spaceLocator(name="exMeshLoc")[0]
        LightmapExcludeSet.define([group, locator])
        self.assertEqual(
            sorted(LightmapExcludeSet.meshes()),
            sorted(cmds.ls(["exMeshA", "exMeshB"], long=True)),
        )

    def test_faces_stand_for_their_mesh_alone(self):
        parent = self._cube("exFaceParent")
        cmds.parent(self._cube("exFaceChild"), parent)
        LightmapExcludeSet.define([f"{parent}.f[0:1]"])
        self.assertEqual(LightmapExcludeSet.meshes(), [parent])

    def test_define_is_one_undo_step(self):
        """Cleared and rebuilt as two steps, one Ctrl+Z after "Set From
        Selection" restored nothing and left NO set -- and the next bake
        re-baked whatever the old set had excluded."""
        was = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(state=True, infinity=True)
        self.addCleanup(cmds.undoInfo, state=was)
        a = self._cube("exUndoA")
        b = self._cube("exUndoB")
        LightmapExcludeSet.define([a])
        LightmapExcludeSet.define([b])
        cmds.undo()
        self.assertEqual(LightmapExcludeSet.members(), [a])

    def test_no_set_means_no_meshes(self):
        self.assertFalse(LightmapExcludeSet.exists())
        self.assertEqual(LightmapExcludeSet.meshes(), [])


if __name__ == "__main__":
    unittest.main()
