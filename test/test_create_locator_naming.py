# !/usr/bin/python
# coding=utf-8
"""Naming/rename robustness for ``RigUtils.create_locator_at_object``.

Also pins the child affix to the object's OWN type: the method rigs whatever
the user selected, so hard-coding the mesh affix named a camera ``_CAM``'s rig
``USER_POS_GEO``.  ``obj_suffix=None`` now resolves per object through
``Naming.affix_for`` (type key -> shared convention).

Regression: when the object being rigged has a *non-unique* leaf name, the
final ``cmds.parent(obj, loc)`` returns a partial DAG path (``loc|leaf``)
rather than a bare leaf.  The rename pass then renamed the LOCATOR first,
which changed that stored path's ancestor component out from under ``obj`` —
``cmds.rename`` blew up with ``RuntimeError: Invalid path 'locator1|...'``.

The fix resolves each node from its UUID immediately before renaming, so the
path is always current regardless of rename order or name collisions.
"""

import maya.cmds as cmds

import pythontk as ptk

from mayatk.rig_utils._rig_utils import RigUtils

from base_test import MayaTkTestCase


class TestCreateLocatorNaming(MayaTkTestCase):
    """create_locator_at_object must survive non-unique leaf names."""

    def test_non_unique_leaf_name_does_not_break_rename(self):
        """A rigged object whose leaf name is shared elsewhere still renames cleanly.

        Building a decoy sibling ``A|widget`` forces the second ``|widget`` to
        parent under the locator as the partial path ``locator1|widget`` — the
        exact shape that used to go stale when the LOC was renamed first.
        """
        # Decoy that makes the leaf "widget" non-unique across the scene.
        cmds.group(empty=True, name="A")
        decoy = cmds.polyCube(name="widget")[0]
        cmds.parent(decoy, "A")

        # The object we actually rig — same leaf name, at the root.
        target = cmds.polyCube(name="widget")[0]

        # Must not raise "Invalid path ...".
        RigUtils.create_locator_at_object(target)

        # Canonical layout is produced with the clean base name.
        self.assertTrue(cmds.objExists("widget_GRP"))
        self.assertTrue(cmds.objExists("widget_GRP|widget_LOC"))
        self.assertTrue(cmds.objExists("widget_GRP|widget_LOC|widget_GEO"))
        # Decoy is untouched.
        self.assertTrue(cmds.objExists("A|widget"))

    def test_rerigging_a_previous_loc_named_object(self):
        """Re-running on an object already named ``*_LOC`` strips + re-suffixes safely.

        This mirrors the reported case (``locator1|locator_LOC``): the object's
        leaf collides with the suffix the LOC is about to receive.
        """
        # A stray decoy so the leaf "gizmo_LOC" is non-unique when reparented.
        cmds.group(empty=True, name="B")
        decoy = cmds.polyCube(name="gizmo_LOC")[0]
        cmds.parent(decoy, "B")

        target = cmds.polyCube(name="gizmo_LOC")[0]

        RigUtils.create_locator_at_object(target)

        # base "gizmo" (the _LOC suffix is stripped before re-suffixing)
        self.assertTrue(cmds.objExists("gizmo_GRP"))
        self.assertTrue(cmds.objExists("gizmo_GRP|gizmo_LOC"))
        self.assertTrue(cmds.objExists("gizmo_GRP|gizmo_LOC|gizmo_GEO"))


class TestCreateLocatorTypeAffix(MayaTkTestCase):
    """The child's affix follows the child's own type, not a hard-coded "_GEO"."""

    def _affix(self, key):
        """The convention's spelling for *key* -- a studio may have changed it."""
        return ptk.NamingConvention.affix(key)

    def test_camera_child_takes_the_camera_affix(self):
        """The reported case: a camera rig came out ``*_GEO``."""
        cam = cmds.rename(cmds.camera()[0], "USER_POS")
        RigUtils.create_locator_at_object(cam)

        expected = f"USER_POS{self._affix('camera')}"
        self.assertTrue(
            cmds.objExists(f"USER_POS_GRP|USER_POS_LOC|{expected}"),
            f"expected {expected} under the rig; scene has "
            f"{cmds.listRelatives('USER_POS_GRP|USER_POS_LOC', children=True)}",
        )

    def test_mesh_child_is_unchanged(self):
        """The common case still lands on the mesh affix (no regression)."""
        RigUtils.create_locator_at_object(cmds.polyCube(name="widget")[0])
        self.assertTrue(
            cmds.objExists(f"widget_GRP|widget_LOC|widget{self._affix('mesh')}")
        )

    def test_wrong_affix_is_corrected_not_stacked(self):
        """A camera an earlier run mis-named ``_GEO`` re-rigs to ``_CAM``.

        The by-type path strips the whole convention vocabulary, not just the
        three affixes in play -- otherwise ``_GEO`` survives the strip and the
        camera comes out ``USER_POS_GEO_CAM``.
        """
        cam = cmds.rename(cmds.camera()[0], f"USER_POS{self._affix('mesh')}")
        RigUtils.create_locator_at_object(cam)

        expected = f"USER_POS{self._affix('camera')}"
        self.assertTrue(cmds.objExists(f"USER_POS_GRP|USER_POS_LOC|{expected}"))

    def test_explicit_affix_still_pins_one_spelling(self):
        """An explicit string opts out: every object gets that affix verbatim."""
        cam = cmds.rename(cmds.camera()[0], "USER_POS")
        RigUtils.create_locator_at_object(cam, obj_suffix="_XYZ")
        self.assertTrue(cmds.objExists("USER_POS_GRP|USER_POS_LOC|USER_POS_XYZ"))

    def test_an_explicit_affix_is_placed_as_the_picker_says(self):
        """The panel offers Auto/Suffix/Prefix, so the engine has to honour them."""
        cam = cmds.rename(cmds.camera()[0], "USER_POS")
        RigUtils.create_locator_at_object(
            cam, obj_suffix="XYZ_", obj_affix_mode="prefix"
        )
        self.assertTrue(cmds.objExists("USER_POS_GRP|USER_POS_LOC|XYZ_USER_POS"))

    def test_auto_placement_reads_the_delimiter(self):
        """ "GEO_" leads and "_GEO" trails -- the default, and what Auto means."""
        cube = cmds.polyCube(name="widget")[0]
        RigUtils.create_locator_at_object(cube, obj_suffix="XYZ_")
        self.assertTrue(cmds.objExists("widget_GRP|widget_LOC|XYZ_widget"))

    def test_group_and_locator_affixes_are_placed_as_the_picker_says(self):
        """The panel offers Auto/Suffix/Prefix on the GROUP and LOCATOR fields too.

        Both were pinned to "auto" in the engine, so a user who picked Prefix and
        typed a spelling auto reads as a suffix got a suffix anyway -- the same
        state-the-operation-ignores bug the child field was fixed for.
        """
        cube = cmds.polyCube(name="widget")[0]
        RigUtils.create_locator_at_object(
            cube,
            grp_suffix="GRP_",
            grp_affix_mode="prefix",
            loc_suffix="LOC_",
            loc_affix_mode="prefix",
            obj_suffix="_GEO",
        )
        self.assertTrue(cmds.objExists("GRP_widget|LOC_widget|widget_GEO"))

    def test_group_and_locator_affixes_still_default_to_auto(self):
        """The new modes are additive: an unqualified literal reads its delimiter."""
        cube = cmds.polyCube(name="widget")[0]
        RigUtils.create_locator_at_object(
            cube, grp_suffix="_G", loc_suffix="_L", obj_suffix="_GEO"
        )
        self.assertTrue(cmds.objExists("widget_G|widget_L|widget_GEO"))

    def test_empty_affix_still_means_none(self):
        """An empty string is not None: it asks for NO affix, not the type's."""
        cam = cmds.rename(cmds.camera()[0], "USER_POS")
        RigUtils.create_locator_at_object(cam, obj_suffix="")
        self.assertTrue(cmds.objExists("USER_POS_GRP|USER_POS_LOC|USER_POS"))


if __name__ == "__main__":
    import unittest

    unittest.main()
