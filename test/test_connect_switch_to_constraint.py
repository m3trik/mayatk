# !/usr/bin/python
# coding=utf-8
"""``RigUtils.connect_switch_to_constraint`` — success signal + anchor wiring.

Two behaviours are pinned here:

* The tentacle ``rigging.tb001`` slot decides success by
  ``result.get("switch_attr")`` (the one key every success path sets and no
  failure path reaches), not by a truthy ``result``.

* An ``anchor`` is wired into the constraint as a REAL additional target (its
  own weight alias) and is only created after the pre-checks pass, so a failure
  can never orphan the helper locator.  (Previously the anchor was added to the
  target list but never constrained, so the weight/target counts never matched
  and the anchor helper was left orphaned in the scene.)
"""
import maya.cmds as cmds

from mayatk.rig_utils._rig_utils import RigUtils

from base_test import MayaTkTestCase


class TestConnectSwitchToConstraint(MayaTkTestCase):

    def _two_target_parent_constraint(self):
        a = cmds.spaceLocator(name="tgtA")[0]
        b = cmds.spaceLocator(name="tgtB")[0]
        driven = cmds.polyCube(name="driven")[0]
        con = cmds.parentConstraint(a, b, driven)[0]
        return con

    def test_success_sets_switch_attr(self):
        """A valid multi-target constraint yields a result containing switch_attr."""
        con = self._two_target_parent_constraint()
        result = RigUtils.connect_switch_to_constraint(
            con, attr_name="parent_switch", overwrite_existing=True
        )
        self.assertIn("switch_attr", result)
        self.assertTrue(cmds.objExists(result["switch_attr"]))

    def test_anchor_wired_as_real_constraint_target(self):
        """An anchor becomes a real 3rd target (weight + enum entry), no orphan."""
        con = self._two_target_parent_constraint()
        locs_before = set(cmds.ls(type="locator"))

        result = RigUtils.connect_switch_to_constraint(
            con, attr_name="parent_switch", overwrite_existing=True, anchor="world"
        )
        self.assertIn("switch_attr", result)
        self.assertIn("anchor_helper", result)

        # Real target + weight added to the constraint (not just a list entry).
        self.assertEqual(
            len(cmds.parentConstraint(con, q=True, targetList=True)), 3
        )
        self.assertEqual(
            len(cmds.parentConstraint(con, q=True, weightAliasList=True)), 3
        )

        # The switch enum exposes all three spaces, including the anchor.
        node = result["switch_attr"].split(".")[0]
        enum = cmds.attributeQuery("parent_switch", node=node, listEnum=True)[0]
        self.assertEqual(len(enum.split(":")), 3)
        self.assertIn("world", enum)

        # Exactly one new locator (the anchor) — nothing orphaned.
        new_locs = set(cmds.ls(type="locator")) - locs_before
        self.assertEqual(len(new_locs), 1)

    def test_precheck_failure_does_not_orphan_anchor(self):
        """A failure before the switch is built must not leave a stray anchor.

        The attribute already exists and ``overwrite_existing=False`` bails at
        the duplicate-attribute check — which runs *before* the anchor helper is
        created, so no ``world`` locator should appear.
        """
        con = self._two_target_parent_constraint()
        RigUtils.connect_switch_to_constraint(  # first call creates the attr
            con, attr_name="parent_switch", overwrite_existing=True
        )
        locs_before = set(cmds.ls(type="locator"))

        result = RigUtils.connect_switch_to_constraint(
            con, attr_name="parent_switch", overwrite_existing=False, anchor="world"
        )
        self.assertNotIn("switch_attr", result)
        self.assertNotIn("anchor_helper", result)
        self.assertEqual(set(cmds.ls(type="locator")), locs_before)


if __name__ == "__main__":
    import unittest

    unittest.main()
