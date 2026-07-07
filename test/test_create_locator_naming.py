# !/usr/bin/python
# coding=utf-8
"""Naming/rename robustness for ``RigUtils.create_locator_at_object``.

Regression: when the object being rigged has a *non-unique* leaf name, the
final ``cmds.parent(obj, loc)`` returns a partial DAG path (``loc|leaf``)
rather than a bare leaf.  The rename pass then renamed the LOCATOR first,
which changed that stored path's ancestor component out from under ``obj`` —
``cmds.rename`` blew up with ``RuntimeError: Invalid path 'locator1|...'``.

The fix resolves each node from its UUID immediately before renaming, so the
path is always current regardless of rename order or name collisions.
"""
import maya.cmds as cmds

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


if __name__ == "__main__":
    import unittest

    unittest.main()
