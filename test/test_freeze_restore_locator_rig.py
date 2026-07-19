# !/usr/bin/python
# coding=utf-8
"""Test scene exposing freeze_transforms behavior on complex locator-rig hierarchies.

Builds four rigs under a single root using ``RigUtils.create_locator_at_object``:
  - static (no animation)
  - translate-animated LOC
  - rotate-animated LOC
  - static rig nested inside an intermediate sub-group

then freezes from the root with ``freeze_children=True``. Documented findings
encoded as assertions:

  * ANIMATED rigs survive the freeze intact — ``freeze_transforms`` default
    ``connection_strategy='preserve'`` causes ``makeIdentity`` to skip a GRP
    whose descendant LOC has incoming animation curves on translate/rotate.
    GRP.translate retains its world-space value; animation curves untouched.

  * STATIC rigs are collapsed: GRP, LOC, and GEO all end up at local identity.
    The world position is pushed into VERTEX COORDINATES — vertex_local equals
    vertex_world, and the bbox center in world space equals the pre-freeze
    GRP.translate.

  * ``unfreeze_to_parent`` is a no-op on either case after freeze: animated
    rigs were never broken (nothing to do), and static rigs have an identity
    LOC matrix so there's nothing to lift. Two tests in Group D are marked
    ``@expectedFailure`` documenting this gap — a future ``restore_locator_rig``
    operation needs to read the geo's world bbox center, shift vertices by -P
    in geo local space, and set GRP.translate = P (in parent space).
"""
import unittest

import maya.cmds as cmds

from mayatk.rig_utils._rig_utils import RigUtils
from mayatk.xform_utils._xform_utils import XformUtils

from base_test import MayaTkTestCase


class TestFreezeRestoreLocatorRig(MayaTkTestCase):
    """Builds a complex rig scene, freezes from root, verifies & attempts restoration."""

    KEY_TIMES = (1, 5, 10, 15, 20)

    def setUp(self):
        super().setUp()
        cmds.currentTime(1, edit=True)
        self.root = cmds.group(empty=True, name="RIG_ROOT", world=True)

        self.static_grp, self.static_loc, self.static_geo = self._build_static_rig()
        (
            self.trans_anim_grp,
            self.trans_anim_loc,
            self.trans_anim_geo,
        ) = self._build_translate_anim_rig()
        (
            self.rot_anim_grp,
            self.rot_anim_loc,
            self.rot_anim_geo,
        ) = self._build_rotate_anim_rig()
        (
            self.nested_grp,
            self.nested_loc,
            self.nested_geo,
            self.nested_container,
        ) = self._build_nested_rig()

        self.all_geos = [
            self.static_geo,
            self.trans_anim_geo,
            self.rot_anim_geo,
            self.nested_geo,
        ]
        self.pre_freeze_geo_world = self._snapshot_geo_world_at_keys(self.all_geos)
        self.pre_freeze_vtx0_world = self._snapshot_vtx0_world(self.all_geos)
        cmds.currentTime(1, edit=True)

    def tearDown(self):
        cmds.currentTime(1, edit=True)
        if cmds.objExists(self.root):
            cmds.delete(self.root)
        super().tearDown()

    # ------------------------------------------------------------------ builders

    def _rig_paths(self, base_name, container):
        """Return (grp_long, loc_long, geo_long) under *container* for create_locator_at_object's defaults."""
        grp = f"{container}|{base_name}_GRP"
        loc = f"{grp}|{base_name}_LOC"
        geo = f"{loc}|{base_name}_GEO"
        return grp, loc, geo

    def _build_static_rig(self):
        """Cube at world (10, 5, 0). No animation."""
        cube = cmds.polyCube(name="static_obj")[0]
        cmds.move(10, 5, 0, cube, absolute=True)
        RigUtils.create_locator_at_object(cube)
        cmds.parent("static_obj_GRP", self.root)
        return self._rig_paths("static_obj", f"|{self.root}")

    def _build_translate_anim_rig(self):
        """Cube at world (-15, 0, 5). LOC.translateY animated 0 -> 5 -> 0 over frames 1, 10, 20."""
        cube = cmds.polyCube(name="trans_anim_obj")[0]
        cmds.move(-15, 0, 5, cube, absolute=True)
        RigUtils.create_locator_at_object(cube)
        cmds.parent("trans_anim_obj_GRP", self.root)
        grp, loc, geo = self._rig_paths("trans_anim_obj", f"|{self.root}")
        cmds.setKeyframe(loc, attribute="translateY", value=0, time=1)
        cmds.setKeyframe(loc, attribute="translateY", value=5, time=10)
        cmds.setKeyframe(loc, attribute="translateY", value=0, time=20)
        return grp, loc, geo

    def _build_rotate_anim_rig(self):
        """Cube at world (0, 10, -10). LOC.rotateY animated 0 -> 90 -> 0 over frames 1, 10, 20."""
        cube = cmds.polyCube(name="rot_anim_obj")[0]
        cmds.move(0, 10, -10, cube, absolute=True)
        RigUtils.create_locator_at_object(cube)
        cmds.parent("rot_anim_obj_GRP", self.root)
        grp, loc, geo = self._rig_paths("rot_anim_obj", f"|{self.root}")
        cmds.setKeyframe(loc, attribute="rotateY", value=0, time=1)
        cmds.setKeyframe(loc, attribute="rotateY", value=90, time=10)
        cmds.setKeyframe(loc, attribute="rotateY", value=0, time=20)
        return grp, loc, geo

    def _build_nested_rig(self):
        """Cube under an intermediate container: RIG_ROOT > NESTED > GRP > LOC > GEO."""
        container = cmds.group(empty=True, name="NESTED", parent=self.root)
        cube = cmds.polyCube(name="nested_obj")[0]
        cmds.move(-5, -8, -3, cube, absolute=True)
        RigUtils.create_locator_at_object(cube)
        cmds.parent("nested_obj_GRP", container)
        grp, loc, geo = self._rig_paths("nested_obj", f"|{self.root}|NESTED")
        return grp, loc, geo, f"|{self.root}|NESTED"

    # ------------------------------------------------------------------ snapshots

    def _snapshot_geo_world_at_keys(self, geos):
        """{geo: {time: world_rotate_pivot}} sampled at KEY_TIMES."""
        snap = {}
        for geo in geos:
            per_time = {}
            for t in self.KEY_TIMES:
                cmds.currentTime(t, edit=True)
                per_time[t] = tuple(cmds.xform(geo, q=True, ws=True, rp=True))
            snap[geo] = per_time
        return snap

    def _snapshot_vtx0_world(self, geos):
        """{geo: world_pos_of_vertex_0} at the current time. Used to verify
        vertex positions survive freeze + restore (not just rotate-pivot)."""
        snap = {}
        for geo in geos:
            mesh = (
                cmds.listRelatives(geo, shapes=True, type="mesh", fullPath=True) or []
            )
            if not mesh:
                continue
            snap[geo] = tuple(
                cmds.xform(f"{mesh[0]}.vtx[0]", q=True, ws=True, t=True)
            )
        return snap

    def _assert_vtx0_world_unchanged(self, geo, label, delta=1e-3):
        before = self.pre_freeze_vtx0_world[geo]
        mesh = cmds.listRelatives(geo, shapes=True, type="mesh", fullPath=True)[0]
        after = tuple(cmds.xform(f"{mesh}.vtx[0]", q=True, ws=True, t=True))
        for axis_idx, (b, a) in enumerate(zip(before, after)):
            self.assertAlmostEqual(
                b, a, delta=delta,
                msg=f"[{label}] vtx[0] axis={'xyz'[axis_idx]}: before={b} after={a}",
            )

    def _freeze_from_root(self):
        XformUtils.freeze_transforms(self.root, freeze_children=True)

    def _assert_geo_world_unchanged(self, geo, label, delta=1e-3):
        """At each KEY_TIME, geo's world rotate pivot matches the pre-freeze snapshot."""
        for t in self.KEY_TIMES:
            cmds.currentTime(t, edit=True)
            after = tuple(cmds.xform(geo, q=True, ws=True, rp=True))
            before = self.pre_freeze_geo_world[geo][t]
            for axis_idx, (b, a) in enumerate(zip(before, after)):
                self.assertAlmostEqual(
                    b, a, delta=delta,
                    msg=f"[{label}] t={t} axis={'xyz'[axis_idx]}: before={b} after={a}",
                )
        cmds.currentTime(1, edit=True)

    # =================================================================== tests
    # Group A: pre-freeze sanity — confirms create_locator_at_object's layout

    def test_pre_freeze_static_rig_layout(self):
        """create_locator_at_object: GRP at world pivot, LOC and GEO at local identity."""
        self.assertAlmostEqual(cmds.getAttr(f"{self.static_grp}.translateX"), 10.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{self.static_grp}.translateY"), 5.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{self.static_grp}.translateZ"), 0.0, places=4)
        self.assertEqual(
            tuple(cmds.getAttr(f"{self.static_loc}.translate")[0]), (0.0, 0.0, 0.0)
        )
        self.assertEqual(
            tuple(cmds.getAttr(f"{self.static_geo}.translate")[0]), (0.0, 0.0, 0.0)
        )

    def test_pre_freeze_animation_curves_intact(self):
        """Animation on the LOC drives its world position across keys."""
        cmds.currentTime(10, edit=True)
        ty = cmds.getAttr(f"{self.trans_anim_loc}.translateY")
        self.assertAlmostEqual(ty, 5.0, places=4)
        cmds.currentTime(10, edit=True)
        ry = cmds.getAttr(f"{self.rot_anim_loc}.rotateY")
        self.assertAlmostEqual(ry, 90.0, places=4)

    # Group B: freeze behavior — what state are we left in?

    def test_freeze_preserves_static_geo_world_position(self):
        """A static (un-animated) rig's geo must stay visually fixed through the freeze."""
        self._freeze_from_root()
        self._assert_geo_world_unchanged(self.static_geo, "static")

    def test_freeze_preserves_nested_geo_world_position(self):
        """A rig under an intermediate sub-group survives the freeze visually."""
        self._freeze_from_root()
        self._assert_geo_world_unchanged(self.nested_geo, "nested")

    def test_freeze_preserves_translate_animated_geo_world_position(self):
        """A translate-animated LOC's geo must remain at the right world position at every key."""
        self._freeze_from_root()
        self._assert_geo_world_unchanged(self.trans_anim_geo, "translate_anim")

    def test_freeze_preserves_rotate_animated_geo_world_position(self):
        """A rotate-animated LOC's geo must remain at the right world position at every key."""
        self._freeze_from_root()
        self._assert_geo_world_unchanged(self.rot_anim_geo, "rotate_anim")

    def test_freeze_collapses_static_GRP_to_identity(self):
        """Static rig: freeze_children collapses GRP.translate (no animated descendants to block it)."""
        self._freeze_from_root()
        for axis in "XYZ":
            v = cmds.getAttr(f"{self.static_grp}.translate{axis}")
            self.assertAlmostEqual(
                v, 0.0, places=4,
                msg=f"Expected static GRP.translate{axis} == 0 after freeze, got {v}",
            )

    def test_freeze_preserves_animated_GRP_translate(self):
        """Animated rig: freeze SKIPS the GRP because its descendants' connections block makeIdentity.

        Default connection_strategy='preserve' aborts the freeze on nodes whose children
        have incoming connections (animation curves on translate/rotate). The GRP retains
        its world translate value and the rig stays usable. This is critical: the user's
        scene relies on animated rigs surviving the cascade.
        """
        self._freeze_from_root()
        self.assertAlmostEqual(cmds.getAttr(f"{self.trans_anim_grp}.translateX"), -15.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{self.trans_anim_grp}.translateZ"), 5.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{self.rot_anim_grp}.translateY"), 10.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{self.rot_anim_grp}.translateZ"), -10.0, places=4)

    def test_freeze_static_world_position_lands_in_vertex_coords(self):
        """Static rig: after freeze, world position is in vertex local coords (GRP/LOC/GEO all identity)."""
        self._freeze_from_root()
        # GRP, LOC, GEO all identity local
        for node in (self.static_grp, self.static_loc, self.static_geo):
            t = tuple(cmds.getAttr(f"{node}.translate")[0])
            self.assertEqual(t, (0.0, 0.0, 0.0), msg=f"{node} expected identity translate, got {t}")
        # bbox center in world equals the original GRP position
        bb = cmds.exactWorldBoundingBox(self.static_geo)
        bb_center = ((bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2)
        self.assertAlmostEqual(bb_center[0], 10.0, delta=0.01)
        self.assertAlmostEqual(bb_center[1], 5.0, delta=0.01)
        self.assertAlmostEqual(bb_center[2], 0.0, delta=0.01)
        # Vertex 0 in object space equals its world position (since chain is identity)
        v0_ws = cmds.xform(f"{self.static_geo}.vtx[0]", q=True, ws=True, t=True)
        v0_os = cmds.xform(f"{self.static_geo}.vtx[0]", q=True, os=True, t=True)
        for ws, os_ in zip(v0_ws, v0_os):
            self.assertAlmostEqual(ws, os_, delta=1e-6)

    def test_freeze_preserves_animation_curve_values(self):
        """Animation curve values on the LOC are not disturbed by the freeze."""
        self._freeze_from_root()
        cmds.currentTime(10, edit=True)
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.trans_anim_loc}.translateY"), 5.0, places=3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.rot_anim_loc}.rotateY"), 90.0, places=3
        )
        cmds.currentTime(1, edit=True)

    # Group C: restoration via unfreeze_to_parent

    def test_unfreeze_to_parent_keeps_root_at_identity(self):
        """preserve_root=True default: input root stays at zero."""
        self._freeze_from_root()
        XformUtils.unfreeze_to_parent(self.root, traverse=True)
        for axis in "XYZ":
            v = cmds.getAttr(f"{self.root}.translate{axis}")
            self.assertAlmostEqual(v, 0.0, places=4)

    def test_unfreeze_to_parent_preserves_geo_world_position_static(self):
        """After freeze + unfreeze, static geo's world position is preserved."""
        self._freeze_from_root()
        XformUtils.unfreeze_to_parent(self.root, traverse=True)
        self._assert_geo_world_unchanged(self.static_geo, "static after unfreeze")

    def test_unfreeze_to_parent_preserves_translate_anim_world_position(self):
        """Translate-animated rig: world positions preserved across all keys after unfreeze.

        Animated rigs are no-ops for unfreeze_to_parent (LOC matrix is still identity at rest,
        nothing to lift). Visual position must remain stable across animation regardless.
        """
        self._freeze_from_root()
        XformUtils.unfreeze_to_parent(self.root, traverse=True)
        self._assert_geo_world_unchanged(
            self.trans_anim_geo, "translate_anim after unfreeze"
        )

    def test_unfreeze_to_parent_preserves_rotate_anim_world_position(self):
        """Rotate-animated rig: world positions preserved at all keys after unfreeze (no-op)."""
        self._freeze_from_root()
        XformUtils.unfreeze_to_parent(self.root, traverse=True)
        self._assert_geo_world_unchanged(
            self.rot_anim_geo, "rotate_anim after unfreeze"
        )

    # Group D: restoration via RigUtils.restore_rig_anchors

    def test_restore_rig_anchors_puts_static_GRP_back_at_world_translate(self):
        """After freeze, restore_rig_anchors lifts the world pivot from vertex coords onto the GRP."""
        self._freeze_from_root()
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertIn("static_obj_GRP", restored)
        self.assertAlmostEqual(cmds.getAttr(f"{self.static_grp}.translateX"), 10.0, delta=0.01)
        self.assertAlmostEqual(cmds.getAttr(f"{self.static_grp}.translateY"), 5.0, delta=0.01)
        self.assertAlmostEqual(cmds.getAttr(f"{self.static_grp}.translateZ"), 0.0, delta=0.01)

    def test_restore_rig_anchors_preserves_static_geo_world_position(self):
        """Vertex shift compensates for GRP move — geo's world position and vtx[0] unchanged."""
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        self._assert_geo_world_unchanged(self.static_geo, "static after restore")
        self._assert_vtx0_world_unchanged(self.static_geo, "static after restore")

    def test_restore_rig_anchors_puts_nested_GRP_back_at_world_translate(self):
        """Nested rig under an intermediate container is restored to its world pivot."""
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertAlmostEqual(cmds.getAttr(f"{self.nested_grp}.translateX"), -5.0, delta=0.01)
        self.assertAlmostEqual(cmds.getAttr(f"{self.nested_grp}.translateY"), -8.0, delta=0.01)
        self.assertAlmostEqual(cmds.getAttr(f"{self.nested_grp}.translateZ"), -3.0, delta=0.01)

    def test_restore_rig_anchors_preserves_nested_geo_world_position(self):
        """Nested geo's world position is preserved by the restore."""
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        self._assert_geo_world_unchanged(self.nested_geo, "nested after restore")

    def test_restore_rig_anchors_skips_animated_rigs(self):
        """skip_animated=True default: rigs whose LOC has anim curves are not modified."""
        self._freeze_from_root()
        before_grp_t = cmds.getAttr(f"{self.trans_anim_grp}.translate")[0]
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        after_grp_t = cmds.getAttr(f"{self.trans_anim_grp}.translate")[0]
        self.assertNotIn("trans_anim_obj_GRP", restored)
        self.assertNotIn("rot_anim_obj_GRP", restored)
        self.assertEqual(tuple(before_grp_t), tuple(after_grp_t))

    def test_restore_rig_anchors_preserves_animated_rig_world_position(self):
        """Animated rigs were never broken — their world positions stay correct after restore (no-op for them)."""
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        self._assert_geo_world_unchanged(
            self.trans_anim_geo, "translate_anim after restore"
        )
        self._assert_geo_world_unchanged(
            self.rot_anim_geo, "rotate_anim after restore"
        )

    def test_restore_rig_anchors_corrects_GRP_world_rotate_pivot(self):
        """GRP's world rotate pivot must land at the anchor, not at 2*anchor.

        The freeze cascade puts the translation delta into rotatePivot on every
        node in the chain. A previous version of restore_rig_anchors only fixed
        the GEO's pivot, leaving GRP.rp = delta and GRP.translate = delta. With
        ws_rp = local_rp * worldMatrix, the GRP's world rotate pivot ended up
        at 2*delta — rotations would happen at the wrong world location.
        """
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        ws_rp = cmds.xform(self.static_grp, q=True, ws=True, rp=True)
        self.assertAlmostEqual(ws_rp[0], 10.0, delta=0.01)
        self.assertAlmostEqual(ws_rp[1], 5.0, delta=0.01)
        self.assertAlmostEqual(ws_rp[2], 0.0, delta=0.01)

    def test_restore_rig_anchors_corrects_LOC_world_rotate_pivot(self):
        """LOC's world rotate pivot must match the rig anchor after restore.

        If the LOC is animated later, its rotation must happen at the GRP's
        position. With local_rp at zero (the natural state for a fresh rig),
        LOC.ws_rp = GRP.world.translation = anchor.
        """
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        ws_rp = cmds.xform(self.static_loc, q=True, ws=True, rp=True)
        self.assertAlmostEqual(ws_rp[0], 10.0, delta=0.01)
        self.assertAlmostEqual(ws_rp[1], 5.0, delta=0.01)
        self.assertAlmostEqual(ws_rp[2], 0.0, delta=0.01)

    def test_restore_rig_anchors_zeros_chain_local_pivots(self):
        """Restore should leave GRP/LOC/GEO with local rotatePivot at (0,0,0).

        Matches the canonical state right after create_locator_at_object — the
        freeze pushed pivot offsets into every node, and restore must undo all
        of them, not just the leaf's.
        """
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        for node, label in [
            (self.static_grp, "GRP"),
            (self.static_loc, "LOC"),
            (self.static_geo, "GEO"),
        ]:
            rp = cmds.getAttr(f"{node}.rotatePivot")[0]
            sp = cmds.getAttr(f"{node}.scalePivot")[0]
            for axis_idx, v in enumerate(rp):
                self.assertAlmostEqual(
                    v, 0.0, delta=0.01,
                    msg=f"{label}.rotatePivot.{'xyz'[axis_idx]} expected 0, got {v}",
                )
            for axis_idx, v in enumerate(sp):
                self.assertAlmostEqual(
                    v, 0.0, delta=0.01,
                    msg=f"{label}.scalePivot.{'xyz'[axis_idx]} expected 0, got {v}",
                )

    def test_restore_rig_anchors_idempotent_on_already_correct_rig(self):
        """Running restore twice leaves state unchanged after the first call."""
        self._freeze_from_root()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        after_first = cmds.getAttr(f"{self.static_grp}.translate")[0]
        restored_again = RigUtils.restore_rig_anchors(self.root, traverse=True)
        after_second = cmds.getAttr(f"{self.static_grp}.translate")[0]
        self.assertEqual(restored_again, [])  # nothing to restore the second time
        self.assertEqual(tuple(after_first), tuple(after_second))

    def test_restore_rig_anchors_with_pivot_source_rp(self):
        """pivot_source='rp' reads the geo's world rotate pivot rather than bbox center."""
        self._freeze_from_root()
        restored = RigUtils.restore_rig_anchors(
            self.root, traverse=True, pivot_source="rp"
        )
        self.assertIn("static_obj_GRP", restored)
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateX"), 10.0, delta=0.01
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateY"), 5.0, delta=0.01
        )
        self._assert_geo_world_unchanged(
            self.static_geo, "static after restore via rp"
        )
        self._assert_vtx0_world_unchanged(
            self.static_geo, "static after restore via rp"
        )

    def test_restore_rig_anchors_rejects_invalid_pivot_source(self):
        """Invalid pivot_source raises ValueError up-front, before any side effects."""
        self._freeze_from_root()
        with self.assertRaises(ValueError):
            RigUtils.restore_rig_anchors(
                self.root, traverse=True, pivot_source="bogus"
            )
        # State should be unchanged (raised before mutating anything).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateX"), 0.0, places=4
        )


if __name__ == "__main__":
    unittest.main()
