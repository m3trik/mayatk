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
            snap[geo] = tuple(cmds.xform(f"{mesh[0]}.vtx[0]", q=True, ws=True, t=True))
        return snap

    def _assert_vtx0_world_unchanged(self, geo, label, delta=1e-3):
        before = self.pre_freeze_vtx0_world[geo]
        mesh = cmds.listRelatives(geo, shapes=True, type="mesh", fullPath=True)[0]
        after = tuple(cmds.xform(f"{mesh}.vtx[0]", q=True, ws=True, t=True))
        for axis_idx, (b, a) in enumerate(zip(before, after)):
            self.assertAlmostEqual(
                b,
                a,
                delta=delta,
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
                    b,
                    a,
                    delta=delta,
                    msg=f"[{label}] t={t} axis={'xyz'[axis_idx]}: before={b} after={a}",
                )
        cmds.currentTime(1, edit=True)

    # =================================================================== tests
    # Group A: pre-freeze sanity — confirms create_locator_at_object's layout

    def test_pre_freeze_static_rig_layout(self):
        """create_locator_at_object: GRP at world pivot, LOC and GEO at local identity."""
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateX"), 10.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateY"), 5.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateZ"), 0.0, places=4
        )
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
                v,
                0.0,
                places=4,
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
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.trans_anim_grp}.translateX"), -15.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.trans_anim_grp}.translateZ"), 5.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.rot_anim_grp}.translateY"), 10.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.rot_anim_grp}.translateZ"), -10.0, places=4
        )

    def test_freeze_static_world_position_lands_in_vertex_coords(self):
        """Static rig: after freeze, world position is in vertex local coords (GRP/LOC/GEO all identity)."""
        self._freeze_from_root()
        # GRP, LOC, GEO all identity local
        for node in (self.static_grp, self.static_loc, self.static_geo):
            t = tuple(cmds.getAttr(f"{node}.translate")[0])
            self.assertEqual(
                t, (0.0, 0.0, 0.0), msg=f"{node} expected identity translate, got {t}"
            )
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
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateX"), 10.0, delta=0.01
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateY"), 5.0, delta=0.01
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateZ"), 0.0, delta=0.01
        )

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
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.nested_grp}.translateX"), -5.0, delta=0.01
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.nested_grp}.translateY"), -8.0, delta=0.01
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.nested_grp}.translateZ"), -3.0, delta=0.01
        )

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
        self._assert_geo_world_unchanged(self.rot_anim_geo, "rotate_anim after restore")

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
                    v,
                    0.0,
                    delta=0.01,
                    msg=f"{label}.rotatePivot.{'xyz'[axis_idx]} expected 0, got {v}",
                )
            for axis_idx, v in enumerate(sp):
                self.assertAlmostEqual(
                    v,
                    0.0,
                    delta=0.01,
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
        self._assert_geo_world_unchanged(self.static_geo, "static after restore via rp")
        self._assert_vtx0_world_unchanged(
            self.static_geo, "static after restore via rp"
        )

    def test_restore_rig_anchors_rejects_invalid_pivot_source(self):
        """Invalid pivot_source raises ValueError up-front, before any side effects."""
        self._freeze_from_root()
        with self.assertRaises(ValueError):
            RigUtils.restore_rig_anchors(self.root, traverse=True, pivot_source="bogus")
        # State should be unchanged (raised before mutating anything).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.static_grp}.translateX"), 0.0, places=4
        )


class TestRestoreRigAnchorsTransformedRig(MayaTkTestCase):
    """``restore_rig_anchors`` on a rig chain that carries rotation / non-unit scale.

    The fixtures above are all built at identity rotation and unit scale, and a
    default (all-channel) freeze bakes any transform away before the restore
    ever runs — so nothing there exercises the pivot walk-back against a node
    that still has a rotation or a scale on it.  A translate-only freeze (the
    tentacle option box ships with **Rotate unchecked**) leaves those channels
    in place, which is the shape every production rig actually has.

    Regression guarded here: the walk-back wrote ``rotatePivot`` / ``scalePivot``
    with a raw ``setAttr``.  Maya's local matrix is

        ``M = SP-1 . S . SH . SP . ST . RP-1 . RA . R . RP . RT . T``

    so the pivot terms only cancel while ``R`` is identity and ``S`` is 1.  On
    any other node, moving a pivot without the matching
    ``rotatePivotTranslate`` / ``scalePivotTranslate`` compensation drags the
    node with it — and a rotated GRP drags its whole subtree, so even an
    identity leaf ends up displaced.
    """

    GRP_ROT = (90.0, 90.0, 0.0)
    GRP_POS = (12.0, -7.0, 20.0)

    def setUp(self):
        super().setUp()
        self._drivers = []  # first: tearDown reads it even if setUp fails below
        self.root = cmds.group(empty=True, name="XF_ROOT", world=True)
        grp = cmds.group(empty=True, name="XF_GRP", parent=self.root)
        loc = cmds.spaceLocator(name="XF_LOC")[0]
        cmds.parent(loc, grp)

        # One leaf per shape seen in production: identity, scale-only, and
        # rotate+scale.  The identity leaf is not redundant — it is the one
        # that proves a rotated GRP displaces a subtree it never touched.
        specs = [
            ("geo_plain", (3, 1, -2), (0, 0, 0), (1, 1, 1)),
            ("geo_scaled", (-4, 2, 1), (0, 0, 0), (22.5, 22.5, 22.5)),
            ("geo_rot_scaled", (1, -3, 4), (90, -90, 0), (0.36, 0.224, 0.36)),
        ]
        for name, t, r, s in specs:
            geo = cmds.polyCube(name=name, w=2, h=2, d=2)[0]
            cmds.parent(geo, loc)
            cmds.setAttr(f"{geo}.translate", *t, type="double3")
            cmds.setAttr(f"{geo}.rotate", *r, type="double3")
            cmds.setAttr(f"{geo}.scale", *s, type="double3")

        # An instanced trio: three transforms, ONE shape. Its points cannot
        # absorb a per-instance offset, so the restore has to compensate these
        # on their channels rather than in vertex space -- mirrors the
        # RECEPT_A..D / KNOB_B..D sets in production scenes.
        inst = cmds.polyCube(name="geo_inst_a", w=2, h=2, d=2)[0]
        cmds.parent(inst, loc)
        inst_names = ["geo_inst_a"]
        for suffix, offset in (("b", (7, 4, 6)), ("c", (9, 4, 6))):
            # cmds.instance places the copy under the SOURCE's parent (measured),
            # so it already lands under the LOC.
            dup = cmds.instance(inst, name=f"geo_inst_{suffix}")[0]
            cmds.setAttr(f"{dup}.translate", *offset, type="double3")
            cmds.setAttr(f"{dup}.rotate", 90, -90, 0, type="double3")
            cmds.setAttr(f"{dup}.scale", 0.36, 0.224, 0.36, type="double3")
            inst_names.append(f"geo_inst_{suffix}")
        cmds.setAttr(f"{inst}.translate", 5, 4, 6, type="double3")
        cmds.setAttr(f"{inst}.rotate", 90, -90, 0, type="double3")
        cmds.setAttr(f"{inst}.scale", 0.36, 0.224, 0.36, type="double3")

        cmds.setAttr(f"{grp}.rotate", *self.GRP_ROT, type="double3")
        cmds.setAttr(f"{grp}.translate", *self.GRP_POS, type="double3")

        self.grp = f"|{self.root}|XF_GRP"
        self.loc = f"{self.grp}|XF_LOC"
        self.geos = [f"{self.loc}|{name}" for name, *_ in specs]
        self.instanced_geos = [f"{self.loc}|{name}" for name in inst_names]
        self.geos += self.instanced_geos

    def tearDown(self):
        for node in [self.root] + self._drivers:
            if cmds.objExists(node):
                cmds.delete(node)
        super().tearDown()

    # ------------------------------------------------------------------ helpers

    def _vtx_world(self, geo):
        """World positions of every vertex of *geo* -- the ground truth for
        "did this move", independent of pivots and channel values."""
        mesh = cmds.listRelatives(
            geo, shapes=True, type="mesh", noIntermediate=True, fullPath=True
        )[0]
        flat = cmds.xform(f"{mesh}.vtx[*]", q=True, ws=True, t=True)
        return [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]

    def _instanced_shape_ids(self):
        """UUIDs of the instanced trio's mesh shapes. Instances share one shape
        NODE reached through several DAG paths, so paths (and even leaf names)
        can't tell "shared" from "forked" — the uuid can."""
        return {
            cmds.ls(
                cmds.listRelatives(
                    g, shapes=True, type="mesh", noIntermediate=True, fullPath=True
                )[0],
                uuid=True,
            )[0]
            for g in self.instanced_geos
        }

    def _freeze_translate_only(self):
        """Freeze translate but not rotate/scale, so the chain still carries
        them when the restore runs (tentacle's default option-box state)."""
        XformUtils.freeze_transforms(
            self.root, freeze_children=True, t=True, r=False, s=False
        )

    def _drive_translate(self, node):
        """Wire a driver into *node*'s translate the way a constraint does --
        per-axis (measured: parentConstraint / pointConstraint / setKeyframe
        all drive the CHILD plugs, never the compound)."""
        driver = cmds.group(
            empty=True, name=f"DRV_{node.rsplit('|', 1)[-1]}", world=True
        )
        cmds.setAttr(f"{driver}.translate", 1, 2, 3, type="double3")
        for axis in "XYZ":
            cmds.connectAttr(f"{driver}.translate{axis}", f"{node}.translate{axis}")
        self._drivers.append(driver)
        return driver

    def _assert_geometry_unmoved(self, before, label, delta=1e-3):
        after = {geo: self._vtx_world(geo) for geo in before}
        for geo in before:
            name = geo.rsplit("|", 1)[-1]
            for i, (b, a) in enumerate(zip(before[geo], after[geo])):
                for axis, (bv, av) in enumerate(zip(b, a)):
                    self.assertAlmostEqual(
                        bv,
                        av,
                        delta=delta,
                        msg=f"[{label}] {name}.vtx[{i}] {'xyz'[axis]}: "
                        f"before={bv} after={av}",
                    )

    # ------------------------------------------------------------------ tests

    def test_freeze_translate_only_preserves_geometry(self):
        """Baseline: the freeze itself must not move anything."""
        before = {geo: self._vtx_world(geo) for geo in self.geos}
        self._freeze_translate_only()
        self._assert_geometry_unmoved(before, "after translate-only freeze")

    def test_fixture_retains_rotation_and_scale_through_the_freeze(self):
        """Guard the fixture: if the freeze ever flattens these channels the
        displacement tests below would pass vacuously."""
        self._freeze_translate_only()
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.grp}.rotateY"), self.GRP_ROT[1], delta=1e-3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.loc}|geo_scaled.scaleX"), 22.5, delta=1e-3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.loc}|geo_rot_scaled.rotateX"), 90.0, delta=1e-3
        )

    def test_restore_rig_anchors_preserves_geometry_on_transformed_chain(self):
        """The whole point of the restore: the GRP takes the world anchor and
        the geometry does not move -- on rotated / scaled nodes too."""
        self._freeze_translate_only()
        before = {geo: self._vtx_world(geo) for geo in self.geos}
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        # Not vacuous: the rig really was processed.
        self.assertIn("XF_GRP", restored)
        self._assert_geometry_unmoved(before, "after restore_rig_anchors")

    def test_restore_rig_anchors_keeps_world_pivots_put(self):
        """The pivot walk-back exists so world pivots stay where they were --
        a pivot that rides along with its node is the same bug seen from the
        other side."""
        self._freeze_translate_only()
        nodes = [self.grp, self.loc] + self.geos
        before = {
            n: (
                cmds.xform(n, q=True, ws=True, rp=True),
                cmds.xform(n, q=True, ws=True, sp=True),
            )
            for n in nodes
        }
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        for n in nodes:
            name = n.rsplit("|", 1)[-1]
            for idx, label in enumerate(("rotatePivot", "scalePivot")):
                after = cmds.xform(n, q=True, ws=True, rp=(idx == 0), sp=(idx == 1))
                for axis, (b, a) in enumerate(zip(before[n][idx], after)):
                    self.assertAlmostEqual(
                        b,
                        a,
                        delta=1e-3,
                        msg=f"{name} world {label}.{'xyz'[axis]}: before={b} after={a}",
                    )

    def test_fixture_geos_are_actually_instanced(self):
        """Guard the fixture: without a genuinely shared shape the instance
        tests below prove nothing."""
        shapes = self._instanced_shape_ids()
        self.assertEqual(
            len(shapes), 1, f"expected one shared shape, got {sorted(shapes)}"
        )

    def test_restore_rig_anchors_leaves_shared_geometry_untouched(self):
        """A shared shape must not be written at all — a vertex shift there
        lands once per instance transform, and leaks to instances outside the
        rig."""
        self._freeze_translate_only()
        mesh = cmds.listRelatives(
            self.instanced_geos[0],
            shapes=True,
            type="mesh",
            noIntermediate=True,
            fullPath=True,
        )[0]
        before = cmds.xform(f"{mesh}.vtx[*]", q=True, objectSpace=True, t=True)
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        after = cmds.xform(f"{mesh}.vtx[*]", q=True, objectSpace=True, t=True)
        for i, (b, a) in enumerate(zip(before, after)):
            self.assertAlmostEqual(
                b,
                a,
                delta=1e-4,
                msg=f"shared shape point {i} was rewritten: {b} -> {a}",
            )

    def test_restore_rig_anchors_keeps_instancing_intact(self):
        """Compensating on the channel must not fork the shape."""
        self._freeze_translate_only()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertEqual(
            len(self._instanced_shape_ids()), 1, "the shared shape was forked"
        )

    def test_restore_rig_anchors_is_idempotent_on_a_transformed_rig(self):
        """A second run must find nothing left to do. The instanced geos end
        with a non-zero translate, a state the static fixture never reaches."""
        self._freeze_translate_only()
        RigUtils.restore_rig_anchors(self.root, traverse=True)
        before = {geo: self._vtx_world(geo) for geo in self.geos}
        again = RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertEqual(again, [])
        self._assert_geometry_unmoved(before, "second restore")

    def test_driven_GRP_translate_is_refused_before_anything_moves(self):
        """The restore ENDS by writing `GRP.translate`; a driven plug makes that
        write raise, and by then the geo vertices have already been shifted --
        the rig is left half-restored. It has to be refused up front."""
        self._freeze_translate_only()
        self._drive_translate(self.grp)
        before = {geo: self._vtx_world(geo) for geo in self.geos}
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertNotIn("XF_GRP", restored)
        self._assert_geometry_unmoved(before, "driven GRP refused")

    def test_driven_instanced_geo_does_not_abort_the_restore(self):
        """An instanced geo is compensated on its channel, so a driven translate
        makes that write raise. It must be skipped, not allowed to take the
        whole rig down with it -- every other geo still lands correctly."""
        self._freeze_translate_only()
        driven_geo = self.instanced_geos[0]
        self._drive_translate(driven_geo)
        before = {geo: self._vtx_world(geo) for geo in self.geos if geo != driven_geo}
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertIn("XF_GRP", restored)
        self._assert_geometry_unmoved(before, "driven instanced geo skipped")


class TestRestoreRigAnchorsUncompensatedSubtrees(MayaTkTestCase):
    """Everything under the GRP that is neither the LOC nor one of the geos.

    The vertex compensation moves POINTS only -- the geo transform itself
    genuinely travels with the GRP -- so any transform parented into the chain
    is displaced by the full delta with nothing to cancel it. Three shapes
    reach it: a second LOC beside the first, a whole rig nested under a geo,
    and a plain non-mesh child. A zero-vertex mesh is a fourth case that does
    not displace so much as CRASH the call, on `.vtx[*]` of a shape with no
    vertices -- after the real geo's points have already been shifted.
    """

    GRP_ROT = (90.0, 90.0, 0.0)
    GRP_POS = (12.0, -7.0, 20.0)

    def setUp(self):
        super().setUp()
        self._extra = []  # first: tearDown reads it even if setUp fails below
        self.root = cmds.group(empty=True, name="U_ROOT", world=True)
        grp = cmds.group(empty=True, name="U_GRP", parent=self.root)
        loc = cmds.spaceLocator(name="U_LOC")[0]
        cmds.parent(loc, grp)
        geo = cmds.polyCube(name="U_GEO", w=2, h=2, d=2)[0]
        cmds.parent(geo, loc)
        cmds.setAttr(f"{geo}.translate", 3, 1, -2, type="double3")
        cmds.setAttr(f"{grp}.rotate", *self.GRP_ROT, type="double3")
        cmds.setAttr(f"{grp}.translate", *self.GRP_POS, type="double3")

        self.grp = f"|{self.root}|U_GRP"
        self.loc = f"{self.grp}|U_LOC"
        self.geo = f"{self.loc}|U_GEO"

    def tearDown(self):
        for node in [self.root] + self._extra:
            if cmds.objExists(node):
                cmds.delete(node)
        super().tearDown()

    # ------------------------------------------------------------------ helpers

    def _all_mesh_world(self):
        """{shape uuid: flat world vertex list} for every mesh in the scene.
        Empty shapes are skipped -- they have no `.vtx[*]` to query."""
        snap = {}
        for mesh in cmds.ls(type="mesh", noIntermediate=True, long=True) or []:
            if not cmds.polyEvaluate(mesh, vertex=True):
                continue
            uid = cmds.ls(mesh, uuid=True)[0]
            snap[uid] = cmds.xform(f"{mesh}.vtx[*]", q=True, ws=True, t=True)
        return snap

    def _freeze(self):
        XformUtils.freeze_transforms(
            self.root, freeze_children=True, t=True, r=False, s=False
        )

    def _restore_and_assert_nothing_moved(self, label, delta=1e-3):
        before = self._all_mesh_world()
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        self.assertIn("U_GRP", restored, f"[{label}] the rig was not processed")
        after = self._all_mesh_world()
        self.assertEqual(sorted(before), sorted(after), f"[{label}] a mesh disappeared")
        for uid in before:
            worst = max(
                (abs(b - a) for b, a in zip(before[uid], after[uid])), default=0.0
            )
            name = cmds.ls(uid, long=True)
            self.assertLess(
                worst,
                delta,
                msg=f"[{label}] {name[0] if name else uid} moved {worst:.4f}",
            )

    # ------------------------------------------------------------------ tests

    def test_second_LOC_under_the_GRP_holds_its_position(self):
        """`add_candidate` binds the FIRST locator child and ignores any other,
        so the second LOC's whole subtree rode the move uncompensated."""
        loc2 = cmds.spaceLocator(name="U_LOC2")[0]
        cmds.parent(loc2, self.grp)
        geo2 = cmds.polyCube(name="U_GEO2", w=2, h=2, d=2)[0]
        cmds.parent(geo2, loc2)
        cmds.setAttr(f"{geo2}.translate", -5, 2, 4, type="double3")
        self._freeze()
        self._restore_and_assert_nothing_moved("second LOC")

    def test_rig_nested_under_a_geo_holds_its_position(self):
        """The outer geo's POINTS absorb the delta but its transform still
        travels, so a rig parented under it is carried along."""
        inner_grp = cmds.group(empty=True, name="IN_GRP", parent=self.geo)
        inner_loc = cmds.spaceLocator(name="IN_LOC")[0]
        cmds.parent(inner_loc, inner_grp)
        inner_geo = cmds.polyCube(name="IN_GEO", w=1, h=1, d=1)[0]
        cmds.parent(inner_geo, inner_loc)
        cmds.setAttr(f"{inner_geo}.translate", 2, 2, 2, type="double3")
        cmds.setAttr(f"{inner_grp}.rotate", 0, 45, 0, type="double3")
        self._freeze()
        self._restore_and_assert_nothing_moved("nested rig")

    def test_non_mesh_child_of_the_LOC_holds_its_position(self):
        """A curve beside the mesh geo is not a candidate for the vertex
        compensation, so it has to be held on its channel instead."""
        curve = cmds.circle(name="U_CURVE", constructionHistory=False)[0]
        cmds.parent(curve, self.loc)
        cmds.setAttr(f"{curve}.translate", 4, -2, 6, type="double3")
        probe = cmds.polyCube(name="U_PROBE", w=1, h=1, d=1)[0]
        cmds.parent(probe, curve)  # a mesh under it, so the move is measurable
        self._freeze()
        self._restore_and_assert_nothing_moved("non-mesh sibling")

    def test_driven_passenger_is_skipped_not_raised(self):
        """A passenger whose translate is driven cannot be held -- the driver
        owns that channel, and writing it would raise. It has to be skipped
        with a warning; the rest of the rig still has to land."""
        loc2 = cmds.spaceLocator(name="U_LOC2")[0]
        cmds.parent(loc2, self.grp)
        geo2 = cmds.polyCube(name="U_GEO2", w=2, h=2, d=2)[0]
        cmds.parent(geo2, loc2)
        cmds.setAttr(f"{geo2}.translate", -5, 2, 4, type="double3")
        self._freeze()

        driver = cmds.group(empty=True, name="U_DRV", world=True)
        self._extra.append(driver)
        cmds.setAttr(f"{driver}.translate", 1, 2, 3, type="double3")
        for axis in "XYZ":
            cmds.connectAttr(
                f"{driver}.translate{axis}", f"{self.grp}|U_LOC2.translate{axis}"
            )

        own_shape = cmds.ls(
            cmds.listRelatives(
                self.geo, shapes=True, type="mesh", noIntermediate=True, fullPath=True
            )[0],
            uuid=True,
        )[0]
        before = self._all_mesh_world()
        restored = RigUtils.restore_rig_anchors(self.root, traverse=True)
        after = self._all_mesh_world()

        self.assertIn("U_GRP", restored, "the rig was taken down by the passenger")
        worst = max(
            (abs(b - a) for b, a in zip(before[own_shape], after[own_shape])),
            default=0.0,
        )
        self.assertLess(
            worst,
            1e-3,
            msg=f"the rig's own geo moved {worst:.4f} -- a driven passenger "
            "must not disturb the nodes that CAN be compensated",
        )

    def test_zero_vertex_mesh_geo_does_not_crash_the_restore(self):
        """A mesh shape with no vertices is still a `type='mesh'` child, so it
        was accepted as a geo -- and `cmds.move` on its `.vtx[*]` raised, out
        of the whole call, with the real geo's points already shifted."""
        empty = cmds.createNode("transform", name="U_EMPTY", parent=self.loc)
        cmds.createNode("mesh", name="U_EMPTYShape", parent=empty)
        self._freeze()
        self._restore_and_assert_nothing_moved("empty mesh")


if __name__ == "__main__":
    unittest.main()
