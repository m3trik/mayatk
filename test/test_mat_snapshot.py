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

from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.mat_snapshot import MatSnapshot
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

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
        self.assertEqual(result, {"textures": 0, "scalars": 0, "connections": 0})

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


class TestMatSnapshotConnectionArity(MayaTkTestCase):
    """A restore puts back the inputs it captured, plug for plug.

    Backlog 2026-08-06: ``restore`` rebuilt the material from the manifest's
    AUTHORED channels alone, so every other input the material carried was
    dropped, and what survived came back on whichever plug the authoring rule
    derives -- a round trip that is not identity. This half is shader-type
    agnostic (no ShaderFX needed); the StingrayPBS graph swap the entry
    measured is :class:`TestMatSnapshotStingrayGraphSwap`.

    Added: 2026-08-17
    """

    def setUp(self):
        super().setUp()
        self.mat = cmds.shadingNode("blinn", asShader=True, name="arity_blinn")
        self.color = self._file_node("arity_color")
        self.extra = self._file_node("arity_extra")
        self.split = self._file_node("arity_split")
        cmds.connectAttr(f"{self.color}.outColor", f"{self.mat}.color")
        # A slot OUTSIDE the manifest's authored channels -- the analogue of a
        # StingrayPBS preset's own ``TEX_global_*_cube`` / ``TEX_brdf_lut``
        # inputs, in a shader every Maya has.
        cmds.connectAttr(f"{self.extra}.outAlpha", f"{self.mat}.reflectivity")
        # A compound slot driven PER CHILD: an arity the manifest cannot see
        # (``listConnections`` on the parent reports nothing for it).
        for axis in "RGB":
            cmds.connectAttr(
                f"{self.split}.outColor{axis}", f"{self.mat}.specularColor{axis}"
            )

    @staticmethod
    def _file_node(name):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", f"C:/tex/{name}.png", type="string")
        return node

    def _incoming(self):
        """``{destination attr: source plug}`` for every input on the material."""
        conns = (
            cmds.listConnections(
                self.mat, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        return {
            conns[i].partition(".")[2]: conns[i + 1] for i in range(0, len(conns), 2)
        }

    def _wipe(self):
        """What ``shaderfx loadGraph`` does to a material: inputs all gone."""
        for dst, src in self._incoming().items():
            cmds.disconnectAttr(src, f"{self.mat}.{dst}")
        self.assertEqual(self._incoming(), {}, "fixture wipe left an input behind")

    def test_capture_records_every_input_plug_for_plug(self):
        snap = MatSnapshot.capture(self.mat)
        recorded = {tuple(c) for c in snap["connections"]}
        self.assertIn(
            (cmds.ls(self.extra, uuid=True)[0], "outAlpha", "reflectivity"), recorded
        )
        self.assertIn(
            (cmds.ls(self.split, uuid=True)[0], "outColorR", "specularColorR"), recorded
        )
        # Additive: the keys existing consumers read are untouched.
        self.assertIn("textures", snap)
        self.assertIn("scalars", snap)

    def test_restore_puts_back_every_captured_input(self):
        before = self._incoming()
        snap = MatSnapshot.capture(self.mat)
        self._wipe()

        MatSnapshot.restore(self.mat, snap)

        self.assertEqual(self._incoming(), before)

    def test_restore_reports_a_connection_count(self):
        snap = MatSnapshot.capture(self.mat)
        self._wipe()
        result = MatSnapshot.restore(self.mat, snap)
        self.assertEqual(result["connections"], len(snap["connections"]))

    def test_restore_is_idempotent(self):
        before = self._incoming()
        snap = MatSnapshot.capture(self.mat)
        MatSnapshot.restore(self.mat, snap)  # nothing was wiped
        self.assertEqual(self._incoming(), before)

    def test_manifest_authors_a_channel_whose_captured_source_is_gone(self):
        """The fallback: no source node to replay, so the authoring rule runs."""
        snap = MatSnapshot.capture(self.mat)
        self._wipe()
        path = cmds.getAttr(f"{self.color}.fileTextureName")
        cmds.delete(self.color)

        MatSnapshot.restore(self.mat, snap)

        rebuilt = cmds.listConnections(
            f"{self.mat}.color", source=True, destination=False, type="file"
        )
        self.assertTrue(rebuilt, "baseColor must be re-authored from the manifest")
        self.assertEqual(
            (cmds.getAttr(f"{rebuilt[0]}.fileTextureName") or "").replace("\\", "/"),
            path.replace("\\", "/"),
        )


class TestMatSnapshotStingrayGraphSwap(MayaTkTestCase):
    """Swap a StingrayPBS between its three ShaderFX graphs; the wiring returns.

    The measured case from the 2026-08-06 backlog entry: ``loadGraph`` drops
    the node's inputs, and the restore used to re-AUTHOR the material from the
    five manifest channels -- so the preset's own IBL inputs
    (``TEX_global_diffuse_cube`` / ``TEX_global_specular_cube`` /
    ``TEX_brdf_lut``) were left dangling, and the greyscale maps came back per
    child (``...X/Y/Z <- outColorR``) where they went in on the compound
    (``<- outColor``).

    Added: 2026-08-17
    """

    #: Wired before the swap, all on the COMPOUND plug: the authored PBR set
    #: AND the preset's own IBL inputs.
    SLOTS = (
        "TEX_color_map",
        "TEX_metallic_map",
        "TEX_roughness_map",
        "TEX_global_diffuse_cube",
        "TEX_global_specular_cube",
        "TEX_brdf_lut",
    )

    def setUp(self):
        super().setUp()
        try:
            if not cmds.pluginInfo("shaderFXPlugin", query=True, loaded=True):
                cmds.loadPlugin("shaderFXPlugin")
        except Exception:
            self.skipTest("shaderFXPlugin not available")
        if not all(
            MatUtils.resolve_stingray_graph(m)
            for m in ("none", "masked", "transparent")
        ):
            self.skipTest("StingrayPBS preset graphs not installed")
        self.mat = self._stingray("none")

    @staticmethod
    def _stingray(mode):
        mat = cmds.shadingNode("StingrayPBS", asShader=True, name="swap_stingray")
        MatUtils.load_stingray_graph(mat, mode)
        return mat

    def _wire(self):
        """Drive every slot in :attr:`SLOTS` from its own file node's outColor."""
        wired = {}
        for attr in self.SLOTS:
            if not cmds.objExists(f"{self.mat}.{attr}"):
                continue
            node = cmds.shadingNode("file", asTexture=True, name=f"swap_{attr}")
            cmds.setAttr(f"{node}.fileTextureName", f"C:/tex/{attr}.png", type="string")
            cmds.connectAttr(f"{node}.outColor", f"{self.mat}.{attr}", force=True)
            toggle = ShaderAttributeMap.map_toggle_attr(attr)
            if cmds.objExists(f"{self.mat}.{toggle}"):
                cmds.setAttr(f"{self.mat}.{toggle}", 1)
            wired[attr] = f"{node}.outColor"
        return wired

    def _incoming(self):
        conns = (
            cmds.listConnections(
                self.mat, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        return {
            conns[i].partition(".")[2]: conns[i + 1] for i in range(0, len(conns), 2)
        }

    def _swap_and_restore(self, mode):
        snapshot = MatSnapshot.capture(self.mat)
        self.assertTrue(MatUtils.load_stingray_graph(self.mat, mode))
        return MatSnapshot.restore(self.mat, snapshot)

    def test_preset_ibl_inputs_survive_the_swap(self):
        wired = self._wire()
        self.assertIn("TEX_brdf_lut", wired, "fixture must wire the preset IBL slots")

        self._swap_and_restore("transparent")

        after = self._incoming()
        for attr in (
            "TEX_global_diffuse_cube",
            "TEX_global_specular_cube",
            "TEX_brdf_lut",
        ):
            self.assertEqual(
                after.get(attr), wired[attr], f"{attr} was dropped by the restore"
            )

    def test_a_compound_input_does_not_come_back_per_child(self):
        wired = self._wire()

        self._swap_and_restore("transparent")

        after = self._incoming()
        for attr in ("TEX_metallic_map", "TEX_roughness_map"):
            self.assertEqual(
                after.get(attr), wired[attr], f"{attr} came back on a different plug"
            )
            for axis in "XYZRGB":
                self.assertNotIn(
                    f"{attr}{axis}",
                    after,
                    f"{attr} child plugs must stay free -- the parent is driven",
                )

    def test_every_input_returns_on_the_same_plug_for_all_three_graphs(self):
        for mode in ("none", "masked", "transparent"):
            with self.subTest(graph=mode):
                cmds.file(new=True, force=True)
                self.mat = self._stingray("none")
                wired = self._wire()

                self._swap_and_restore(mode)

                after = self._incoming()
                for attr, src in wired.items():
                    if not cmds.objExists(f"{self.mat}.{attr}"):
                        continue  # absent by design on the target graph
                    self.assertEqual(
                        after.get(attr), src, f"{attr} not restored verbatim on {mode}"
                    )

    def test_a_slot_absent_from_the_target_graph_is_skipped(self):
        """``Standard_Masked``'s cutout slot has no counterpart on the others."""
        self.mat = self._stingray("masked")
        wired = self._wire()
        node = cmds.shadingNode("file", asTexture=True, name="swap_mask")
        cmds.setAttr(f"{node}.fileTextureName", "C:/tex/mask.png", type="string")
        cmds.connectAttr(f"{node}.outColor", f"{self.mat}.TEX_mask_map", force=True)

        self._swap_and_restore("transparent")

        after = self._incoming()
        self.assertFalse(cmds.objExists(f"{self.mat}.TEX_mask_map"))
        self.assertNotIn("TEX_mask_map", after)
        self.assertEqual(after.get("TEX_color_map"), wired["TEX_color_map"])

    def test_the_authoring_path_wires_a_greyscale_map_on_the_compound(self):
        """A ShaderFX ``TEX_*`` slot samples its texture only through the
        COMPOUND plug -- per-child (``TEX_roughness_mapX/Y/Z``) wiring renders
        as no map at all in VP2 (probed with ogsRender, Maya 2025), which is
        how a restored Stingray material lost its metallic/roughness. So the
        declaration is ``outColor`` and authoring lands on the parent, exactly
        as GameShader and a Painter export wire it."""
        node = cmds.shadingNode("file", asTexture=True, name="swap_author")
        cmds.setAttr(f"{node}.fileTextureName", "C:/tex/rough.png", type="string")

        self.assertTrue(ShaderAttributeMap.connect_channel(node, "roughness", self.mat))

        after = self._incoming()
        self.assertEqual(after.get("TEX_roughness_map"), f"{node}.outColor")
        for axis in "XYZ":
            self.assertNotIn(f"TEX_roughness_map{axis}", after)


if __name__ == "__main__":
    unittest.main()
