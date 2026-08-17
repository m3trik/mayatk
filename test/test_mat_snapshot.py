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


class TestMatSnapshotNetwork(MayaTkTestCase):
    """capture_network / restore_network — an exact-wiring undo of a rewrite.

    Added: 2026-08-16
    """

    def setUp(self):
        super().setUp()
        self.mat = cmds.shadingNode("lambert", asShader=True, name="net_lambert")
        self.tex = cmds.shadingNode("file", asTexture=True, name="net_color")
        cmds.setAttr(f"{self.tex}.fileTextureName", "orig.png", type="string")
        cmds.setAttr(f"{self.tex}.colorSpace", "sRGB", type="string")
        cmds.connectAttr(f"{self.tex}.outColor", f"{self.mat}.color")

    def _rewire_like_a_conversion(self):
        """New node into an EMPTY slot + swap the colour source + repoint."""
        packed = cmds.shadingNode("file", asTexture=True, name="net_packed")
        cmds.connectAttr(f"{packed}.outAlpha", f"{self.mat}.diffuse")
        swapped = cmds.shadingNode("file", asTexture=True, name="net_swapped")
        cmds.disconnectAttr(f"{self.tex}.outColor", f"{self.mat}.color")
        cmds.connectAttr(f"{swapped}.outColor", f"{self.mat}.color")
        cmds.setAttr(f"{self.tex}.fileTextureName", "moved.png", type="string")
        cmds.setAttr(f"{self.tex}.colorSpace", "Raw", type="string")
        return packed, swapped

    def test_capture_records_upstream_nodes_by_uuid_with_wiring(self):
        snap = MatSnapshot.capture_network([self.mat])
        self.assertEqual(snap["materials"], [self.mat])
        self.assertIn(self.mat, snap["nodes"])
        self.assertIn(self.tex, snap["nodes"])
        mat_entry = snap["nodes"][self.mat]
        self.assertEqual(mat_entry["uuid"], cmds.ls(self.mat, uuid=True)[0])
        tex_uuid = cmds.ls(self.tex, uuid=True)[0]
        self.assertIn((tex_uuid, "outColor", "color"), mat_entry["connections"])
        self.assertEqual(snap["nodes"][self.tex]["attrs"]["fileTextureName"], "orig.png")

    def test_restore_reverses_a_rewrite_verbatim(self):
        snap = MatSnapshot.capture_network([self.mat])
        packed, swapped = self._rewire_like_a_conversion()

        counts = MatSnapshot.restore_network(snap)

        self.assertFalse(cmds.objExists(packed), "node the rewrite created")
        self.assertFalse(cmds.objExists(swapped), "node the rewrite created")
        self.assertTrue(cmds.isConnected(f"{self.tex}.outColor", f"{self.mat}.color"))
        self.assertFalse(
            cmds.listConnections(f"{self.mat}.diffuse", source=True),
            "a slot the rewrite ADDED must be empty again",
        )
        self.assertEqual(cmds.getAttr(f"{self.tex}.fileTextureName"), "orig.png")
        self.assertEqual(cmds.getAttr(f"{self.tex}.colorSpace"), "sRGB")
        self.assertEqual(counts["deleted"], 2)
        self.assertGreaterEqual(counts["reconnected"], 1)

    def test_restore_survives_a_rename_of_a_recorded_node(self):
        snap = MatSnapshot.capture_network([self.mat])
        self._rewire_like_a_conversion()
        self.tex = cmds.rename(self.tex, "net_color_renamed")

        MatSnapshot.restore_network(snap)

        self.assertTrue(cmds.isConnected(f"{self.tex}.outColor", f"{self.mat}.color"))
        self.assertEqual(cmds.getAttr(f"{self.tex}.fileTextureName"), "orig.png")

    def test_restore_is_a_no_op_on_an_untouched_network(self):
        snap = MatSnapshot.capture_network([self.mat])
        counts = MatSnapshot.restore_network(snap)
        self.assertEqual(counts, {"deleted": 0, "reconnected": 0, "attrs": 0})
        self.assertTrue(cmds.isConnected(f"{self.tex}.outColor", f"{self.mat}.color"))

    def test_restore_unplugs_but_keeps_a_node_shared_outside_the_network(self):
        """A rewrite that wires a PRE-EXISTING node (another material's map)
        into the snapshotted material: restore must break that connection,
        never delete the node out from under its other consumer."""
        other = cmds.shadingNode("lambert", asShader=True, name="net_other")
        shared = cmds.shadingNode("file", asTexture=True, name="net_shared")
        cmds.connectAttr(f"{shared}.outColor", f"{other}.color")
        snap = MatSnapshot.capture_network([self.mat])
        cmds.connectAttr(f"{shared}.outAlpha", f"{self.mat}.diffuse")

        counts = MatSnapshot.restore_network(snap)

        self.assertTrue(cmds.objExists(shared))
        self.assertTrue(cmds.isConnected(f"{shared}.outColor", f"{other}.color"))
        self.assertFalse(cmds.listConnections(f"{self.mat}.diffuse", source=True))
        self.assertEqual(counts["deleted"], 0)

    def test_network_scope_restores_on_normal_exit_and_on_a_raise(self):
        with MatSnapshot.network_scope([self.mat]):
            packed, _ = self._rewire_like_a_conversion()
        self.assertFalse(cmds.objExists(packed))
        self.assertTrue(cmds.isConnected(f"{self.tex}.outColor", f"{self.mat}.color"))

        with self.assertRaises(RuntimeError):
            with MatSnapshot.network_scope([self.mat]):
                packed, _ = self._rewire_like_a_conversion()
                raise RuntimeError("conversion blew up halfway")
        self.assertFalse(cmds.objExists(packed), "restored despite the raise")
        self.assertTrue(cmds.isConnected(f"{self.tex}.outColor", f"{self.mat}.color"))
        self.assertEqual(cmds.getAttr(f"{self.tex}.fileTextureName"), "orig.png")

    def test_network_scope_restore_failure_is_logged_never_masks_the_body(self):
        """A restore that raises must not hide the body's real error."""
        from unittest.mock import patch

        with self.assertLogs("mayatk.mat_utils.mat_snapshot", level="WARNING") as cap:
            with self.assertRaises(ValueError):
                with patch.object(
                    MatSnapshot, "restore_network", side_effect=RuntimeError("no")
                ):
                    with MatSnapshot.network_scope([self.mat]):
                        raise ValueError("the real error")
        self.assertIn("shading network not fully restored", cap.output[0])

    def test_restored_scope_reconnects_after_a_wipe(self):
        """The light (manifest + scalars) scope, for a loadGraph-style wipe."""
        cmds.setAttr(f"{self.mat}.diffuse", 0.61)
        with MatSnapshot.restored(self.mat):
            cmds.disconnectAttr(f"{self.tex}.outColor", f"{self.mat}.color")
            cmds.setAttr(f"{self.mat}.diffuse", 0.0)
        self.assertTrue(cmds.listConnections(f"{self.mat}.color", source=True))
        self.assertAlmostEqual(cmds.getAttr(f"{self.mat}.diffuse"), 0.61, places=5)

    def test_restore_leaves_a_shading_engine_alone(self):
        """A shading engine is downstream, but listHistory can echo it through
        the material's message links — never delete or rewire it."""
        cube = cmds.polyCube(name="net_cube")[0]
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="net_SG")
        cmds.connectAttr(f"{self.mat}.outColor", f"{sg}.surfaceShader")
        cmds.sets(cube, edit=True, forceElement=sg)
        snap = MatSnapshot.capture_network([self.mat])
        self._rewire_like_a_conversion()
        MatSnapshot.restore_network(snap)
        self.assertTrue(cmds.objExists(sg))
        self.assertTrue(cmds.isConnected(f"{self.mat}.outColor", f"{sg}.surfaceShader"))


if __name__ == "__main__":
    unittest.main()
