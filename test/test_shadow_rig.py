# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.rig_utils.shadow_rig module

Tests for ShadowRig: rig build (source/contact/plane/material/texture +
keyable attrs), the expression's evaluated math (objectHeight-proportional
stretch, projected ground anchor, rise fade), world-space light reads
(parented light), orbit rotation, and bake-to-keyframes.

Reference values (2x2x2 cube at origin, light (5,10,5), G=0):
  plane_size = 2.2, objectHeight = 2, contact = (0,-1,0)
  sx = 1 + (objH * |Cx-Lx|/relH)/size = 1 + (2*0.5)/2.2 = 1.4545
  k  = (Ly-G)/(Ly-Cy) = 10/11 = 0.9091
  Sx = Lx + (Cx-Lx)*k = 5 - 5*0.9091 = 0.4545
  tx = Sx + 1.1*(1-sx) = 0.4545 - 0.5 = -0.0455
"""
import os
import unittest

import maya.cmds as cmds

try:
    import mayatk as mtk
    from mayatk.rig_utils.shadow_rig import ShadowRig
except ImportError:
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import mayatk as mtk
    from mayatk.rig_utils.shadow_rig import ShadowRig

from base_test import MayaTkTestCase


class TestShadowRig(MayaTkTestCase):
    """Tests for the ShadowRig projected-shadow rig."""

    def setUp(self):
        super().setUp()
        # 2x2x2 cube centered at the origin (spans -1..1 on every axis).
        self.cube = cmds.polyCube(name="Box", width=2, height=2, depth=2)[0]
        self._textures = []

    def tearDown(self):
        for path in self._textures:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def _make(self, mode="stretch", **kwargs):
        rig = ShadowRig.create(
            [self.cube], light_pos=(5, 10, 5), texture_res=64, mode=mode, **kwargs
        )
        if rig.texture_path:
            self._textures.append(rig.texture_path)
        return rig

    # ------------------------------------------------------------------ build
    def test_build_stretch(self):
        """Rig build: nodes, keyable attrs, measured constants, texture."""
        rig = self._make()
        self.assertNodeExists("shadow_source")
        self.assertNodeExists("Box_contact_loc")
        self.assertNodeExists("Box_shadow")
        self.assertNodeExists("Box_shadow_expr")
        self.assertNodeExists("Box_contact_dm")
        self.assertNodeExists("Box_light_dm")
        self.assertNodeExists("Box_shadow_grp")

        plane = rig.shadow_plane
        for attr, val in (
            ("shadowIntensity", 1.0),
            ("falloffPower", 1.2),
            ("scaleInfluence", 0.0),
            ("maxStretch", 4.0),
            ("fadeHeight", 4.0),  # 2 x objectHeight
            ("basePlaneSize", 2.2),
            ("objectHeight", 2.0),
        ):
            self.assertAlmostEqual(
                cmds.getAttr(f"{plane}.{attr}"), val, places=3, msg=attr
            )
        self.assertTrue(rig.texture_path and os.path.exists(rig.texture_path))

    def test_explicit_axis_texture(self):
        """Explicit world-axis silhouettes still build (top-down 'y')."""
        rig = self._make(axis="y")
        self.assertTrue(os.path.exists(rig.texture_path))

    # ------------------------------------------------------------------ evaluate
    def test_stretch_evaluation(self):
        """Grounded target: objectHeight-proportional stretch, heel at the
        projected anchor, plane on the ground."""
        rig = self._make()
        plane = rig.shadow_plane
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.scaleX"), 1.4545, places=2)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.scaleZ"), 1.4545, places=2)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.translateX"), -0.0455, places=2)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.translateZ"), -0.0455, places=2)
        self.assertAlmostEqual(
            cmds.getAttr(f"{plane}.translateY"), ShadowRig.GROUND_OFFSET, places=3
        )
        # Lowering the light grows the stretch (monotonic).
        cmds.setAttr("shadow_source.translateY", 3)
        self.assertGreater(cmds.getAttr(f"{plane}.scaleX"), 1.4545 + 1e-3)

    def test_projected_anchor_and_rise_fade(self):
        """Rising target: the shadow slides away from the light and fades.
        Cube at y=+3 -> contact y=2: k = 10/8 = 1.25 -> Sx = -1.25 ->
        tx = -1.75; riseFade = 1 - 2/fadeHeight(4) = 0.5."""
        rig = self._make()
        plane = rig.shadow_plane
        opacity_grounded = cmds.getAttr(f"{rig.opacity_mult}.input2X")
        self.assertGreater(opacity_grounded, 0.0)

        cmds.setAttr(f"{self.cube}.translateY", 3)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.translateX"), -1.75, places=2)
        opacity_risen = cmds.getAttr(f"{rig.opacity_mult}.input2X")
        self.assertAlmostEqual(opacity_risen, opacity_grounded * 0.5, places=2)

    def test_light_world_space(self):
        """The expression reads the light's WORLD position — moving a parent
        group of the light must warp the shadow (raw .translate is local)."""
        rig = self._make()
        plane = rig.shadow_plane
        grp = cmds.group("shadow_source", name="light_grp")
        cmds.setAttr(f"{grp}.translateX", 5)  # light world x: 5 -> 10
        # dx = -10 -> sx = 1 + (2*1.0)/2.2 = 1.909; Sx = 10 - 10*0.9091 = 0.909;
        # tx = 0.909 + 1.1*(1 - 1.909) = -0.0909.
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.scaleX"), 1.909, places=2)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.translateX"), -0.091, places=2)

    def test_orbit_evaluation(self):
        """Orbit mode: plane rotates to face away from the light; depth
        stretch is objectHeight-proportional; scaleX stays 1."""
        rig = self._make(mode="orbit")
        plane = rig.shadow_plane
        # angle = atan2(dx, dz) = atan2(-5, -5) = -135 deg.
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.rotateY"), -135.0, places=1)
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.scaleX"), 1.0, places=3)
        # dist2D = 7.071 -> ratio 0.7071 -> sz = 1 + (2*0.7071)/2.2 = 1.643.
        self.assertAlmostEqual(cmds.getAttr(f"{plane}.scaleZ"), 1.643, places=2)

    # ------------------------------------------------------------------ bake
    def test_bake(self):
        """bake() keys the driven channels over the range and removes the
        expression + decompose nodes; values survive the bake."""
        rig = self._make()
        plane = rig.shadow_plane
        driven_tx = cmds.getAttr(f"{plane}.translateX")

        cmds.playbackOptions(min=1, max=3)
        baked = rig.bake(1, 3)
        self.assertEqual(baked, [plane])
        self.assertFalse(cmds.objExists("Box_shadow_expr"))
        self.assertFalse(cmds.objExists("Box_contact_dm"))
        self.assertFalse(cmds.objExists("Box_light_dm"))
        self.assertEqual(cmds.keyframe(f"{plane}.translateX", q=True, keyframeCount=True), 3)
        self.assertAlmostEqual(
            cmds.getAttr(f"{plane}.translateX", time=2), driven_tx, places=3
        )
        # A second bake finds no live expression -> no-op.
        self.assertEqual(ShadowRig.bake_planes([plane]), [])

    def test_find_shadow_planes(self):
        """Planes are found by the stamped basePlaneSize attr, including via
        a selected ancestor (the *_shadow_grp)."""
        rig = self._make()
        self.assertIn(rig.shadow_plane, ShadowRig.find_shadow_planes())
        self.assertIn(rig.shadow_plane, ShadowRig.find_shadow_planes(["Box_shadow_grp"]))
        self.assertEqual(ShadowRig.find_shadow_planes([self.cube]), [])

    # ------------------------------------------------------------------ export metadata
    def test_export_metadata(self):
        """create()/bake() publish the shadow_metadata channel on the
        data_export carrier (the Scene Exporter hand-off contract); a
        plane-less refresh clears it."""
        import json

        from mayatk.node_utils.data_nodes import DataNodes

        rig = self._make()
        payload = json.loads(
            DataNodes.get_export_string(ShadowRig.SHADOW_METADATA)
        )
        self.assertEqual(payload["version"], 1)
        recs = {r["name"]: r for r in payload["planes"]}
        self.assertIn("Box_shadow", recs)
        self.assertEqual(recs["Box_shadow"]["texture"], "Box_shadow.png")
        self.assertAlmostEqual(recs["Box_shadow"]["intensity"], 1.0, places=3)
        # Classmethod texture resolution (feeds the record) works off the plane.
        tex = ShadowRig._plane_texture_path(rig.shadow_plane)
        self.assertTrue(tex and os.path.basename(tex) == "Box_shadow.png")

        # Bake re-refreshes the channel (still one record, expression gone).
        cmds.playbackOptions(min=1, max=3)
        rig.bake(1, 3)
        payload = json.loads(
            DataNodes.get_export_string(ShadowRig.SHADOW_METADATA)
        )
        self.assertEqual(len(payload["planes"]), 1)

        # Removing the rig clears the channel on the next refresh
        # (run_export_preparers does this via the known-producer registry).
        cmds.delete("Box_shadow_grp")
        ShadowRig.refresh_export_metadata()
        self.assertIsNone(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))

    def test_known_producer_registration(self):
        """The shadow producer is wired into FbxUtils._KNOWN_PRODUCERS, so
        run_export_preparers refreshes the channel for any export pipeline."""
        import json

        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        self.assertIn("shadow", FbxUtils._KNOWN_PRODUCERS)
        self._make()
        DataNodes.set_export_string(ShadowRig.SHADOW_METADATA, "")  # stale it
        FbxUtils.run_export_preparers()
        payload = json.loads(
            DataNodes.get_export_string(ShadowRig.SHADOW_METADATA)
        )
        self.assertEqual(len(payload["planes"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
