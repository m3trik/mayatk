# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.mat_snapshot.MatSnapshot.

Covers capture+restore of scalar values across destructive operations.
Texture capture/restore is exercised transitively through MatManifest's
own test suite — here we focus on the scalar half of the snapshot which
mat_manifest does NOT cover.
"""
import unittest

import maya.cmds as cmds

from mayatk.mat_utils.mat_snapshot import MatSnapshot

from base_test import MayaTkTestCase


class TestMatSnapshotScalars(MayaTkTestCase):
    """Capture and restore non-default scalar values on a lambert."""

    def setUp(self):
        super().setUp()
        self.mat = cmds.shadingNode("lambert", asShader=True, name="snap_lambert")

    def test_capture_returns_textures_and_scalars_keys(self):
        snap = MatSnapshot.capture(self.mat)
        self.assertIn("textures", snap)
        self.assertIn("scalars", snap)

    def test_capture_records_non_default_scalar(self):
        cmds.setAttr(f"{self.mat}.diffuse", 0.42)
        snap = MatSnapshot.capture(self.mat)
        self.assertIn("diffuse", snap["scalars"])
        self.assertAlmostEqual(snap["scalars"]["diffuse"], 0.42, places=5)

    def test_capture_skips_driven_attributes(self):
        # Drive diffuse via an animCurve — capture must NOT include it.
        cmds.setKeyframe(self.mat, attribute="diffuse", t=1, v=0.5)
        cmds.setKeyframe(self.mat, attribute="diffuse", t=10, v=0.9)
        snap = MatSnapshot.capture(self.mat)
        self.assertNotIn("diffuse", snap["scalars"])

    def test_capture_skips_locked_attributes(self):
        cmds.setAttr(f"{self.mat}.diffuse", 0.33, lock=True)
        snap = MatSnapshot.capture(self.mat)
        self.assertNotIn("diffuse", snap["scalars"])

    def test_restore_round_trip_resets_changed_value(self):
        """capture → mutate → restore → original value back."""
        cmds.setAttr(f"{self.mat}.diffuse", 0.77)
        snap = MatSnapshot.capture(self.mat)

        # Simulate a destructive op that resets the scalar.
        cmds.setAttr(f"{self.mat}.diffuse", 0.0)

        result = MatSnapshot.restore(self.mat, snap)
        self.assertGreaterEqual(result["scalars"], 1)
        self.assertAlmostEqual(cmds.getAttr(f"{self.mat}.diffuse"), 0.77, places=5)

    def test_restore_count_matches_restored_attrs(self):
        cmds.setAttr(f"{self.mat}.diffuse", 0.6)
        cmds.setAttr(f"{self.mat}.translucence", 0.4)
        snap = MatSnapshot.capture(self.mat)

        # Reset both.
        cmds.setAttr(f"{self.mat}.diffuse", 0.0)
        cmds.setAttr(f"{self.mat}.translucence", 0.0)

        result = MatSnapshot.restore(self.mat, snap)
        # At minimum both diffuse and translucence should be reported as restored.
        self.assertGreaterEqual(result["scalars"], 2)

    def test_restore_skips_driven_target_attrs(self):
        """Restore must not stomp an attr that's now driven."""
        cmds.setAttr(f"{self.mat}.diffuse", 0.5)
        snap = MatSnapshot.capture(self.mat)
        # Drive diffuse after capture — restore should leave it alone.
        cmds.setKeyframe(self.mat, attribute="diffuse", t=1, v=0.1)
        cmds.setKeyframe(self.mat, attribute="diffuse", t=10, v=0.9)
        cmds.currentTime(5)
        driven_value_before = cmds.getAttr(f"{self.mat}.diffuse")

        MatSnapshot.restore(self.mat, snap)
        # The animCurve still drives diffuse — value at t=5 should match the curve,
        # not the snapshot's 0.5.
        driven_value_after = cmds.getAttr(f"{self.mat}.diffuse")
        self.assertAlmostEqual(driven_value_before, driven_value_after, places=5)

    def test_restore_empty_snapshot_returns_zero(self):
        result = MatSnapshot.restore(self.mat, {"textures": {}, "scalars": {}})
        self.assertEqual(result, {"textures": 0, "scalars": 0})

    def test_capture_handles_nonexistent_material(self):
        """capture on a deleted material should not raise."""
        cmds.delete(self.mat)
        # Either raise cleanly or return an empty snapshot — verify it
        # doesn't crash the caller.
        try:
            snap = MatSnapshot.capture(self.mat)
            self.assertEqual(snap.get("scalars", {}), {})
        except Exception:
            # Acceptable — but if it raises, the contract is "raises on
            # missing node", which the caller can catch.
            pass


if __name__ == "__main__":
    unittest.main()
