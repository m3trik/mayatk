# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.render_opacity module

Tests for the non-animating Channels-based implementation.
"""

import unittest
from unittest.mock import patch
import maya.cmds as cmds
import pythontk as ptk
from mayatk.mat_utils.render_opacity.render_effects import RenderEffects
from base_test import MayaTkTestCase


def _get_assigned_mat(transform):
    """Test helper: get the surface shader material assigned to a transform via shape.SG.surfaceShader."""
    shapes = cmds.listRelatives(str(transform), shapes=True, ni=True) or []
    if not shapes:
        return None
    sgs = cmds.listConnections(shapes[0], type="shadingEngine") or []
    if not sgs:
        return None
    mats = (
        cmds.listConnections(f"{sgs[0]}.surfaceShader", source=True, destination=False)
        or []
    )
    return mats[0] if mats else None


class TestOpacityAttributeMode(MayaTkTestCase):
    """Tests for mode='attribute' (Game Engine workflow)."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="test_cube")[0]

    def test_create_adds_fade_attribute(self):
        """create(mode='attribute') adds the 'opacity' float attribute."""
        RenderEffects.create(objects=[self.cube], mode="attribute")

        self.assertTrue(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True),
            "Attribute 'opacity' was not created",
        )
        attr = f"{self.cube}.opacity"
        self.assertEqual(cmds.getAttr(attr), 1.0, "Default value should be 1.0")
        self.assertEqual(
            cmds.attributeQuery("opacity", node=str(self.cube), minimum=True)[0],
            0.0,
            "Min value should be 0.0",
        )
        self.assertEqual(
            cmds.attributeQuery("opacity", node=str(self.cube), maximum=True)[0],
            1.0,
            "Max value should be 1.0",
        )
        self.assertTrue(cmds.getAttr(attr, keyable=True), "Attribute should be keyable")

    def test_create_does_not_add_keys(self):
        """create(mode='attribute') should NOT add animation keys."""
        RenderEffects.create(objects=[self.cube], mode="attribute")

        anim = cmds.listConnections(self.cube, type="animCurve")
        self.assertFalse(anim, "Attribute mode should not create animation curves")

    def test_remove_deletes_attribute(self):
        """remove(mode='attribute') deletes the attribute."""
        RenderEffects.create(objects=[self.cube], mode="attribute")
        self.assertTrue(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True)
        )

        RenderEffects.remove(objects=[self.cube], mode="attribute")
        self.assertFalse(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True),
            "Attribute should be removed",
        )


class TestLegacyMaterialModeCleanup(MayaTkTestCase):
    """The viewport material mode is retired (2026-09-05); what remains is the
    heal for a scene saved with it on: the object goes back onto its authored
    material, the duplicate and its shading group go, the record is forgotten."""

    def _legacy_scene(self):
        """Hand-build what the old mode left: ``Skin_Highlight`` on the cube,
        ``highlight`` driving its emission, and the binding record."""
        import pythontk as ptk

        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.node_utils.data_nodes import DataNodes

        cube = cmds.polyCube(name="legacy_cube")[0]
        skin = cmds.shadingNode("standardSurface", asShader=True, name="Skin")
        MatUtils.assign_mat([cube], skin)
        # The channel first: create() heals legacy leftovers before it adds
        # the attribute, so the duplicate has to be built AFTER it.
        RenderEffects.create([cube], channel="highlight")
        dup = cmds.shadingNode("standardSurface", asShader=True, name="Skin_Highlight")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="SkinSG_Copy"
        )
        cmds.connectAttr(f"{dup}.outColor", f"{sg}.surfaceShader")
        cmds.sets(cube, edit=True, forceElement=sg)
        cmds.connectAttr(f"{cube}.highlight", f"{dup}.emission", force=True)
        ptk.SceneRecords.RENDER_EFFECTS_BINDINGS.save(
            DataNodes,
            {
                "Skin_Highlight:highlight": {
                    "material": "Skin_Highlight",
                    "object": cube,
                    "channel": "highlight",
                    "restore": {f"{dup}.emission": 0.25},
                }
            },
        )
        return cube, skin, dup

    def test_remove_puts_the_object_back_on_its_authored_material(self):
        cube, skin, dup = self._legacy_scene()
        self.assertEqual(_get_assigned_mat(cube), dup)

        RenderEffects.remove([cube], channel="highlight")

        self.assertEqual(_get_assigned_mat(cube), skin)
        self.assertFalse(cmds.objExists(dup), "the orphaned duplicate is deleted")
        self.assertFalse(cmds.objExists("SkinSG_Copy"))
        self.assertFalse(cmds.attributeQuery("highlight", node=cube, exists=True))

    def test_remove_reports_the_healed_material_and_forgets_the_record(self):
        from mayatk.mat_utils.render_opacity.material_mode import OpacityMaterialMode

        cube, _skin, _dup = self._legacy_scene()
        touched = OpacityMaterialMode.remove([cube])
        self.assertEqual(touched, ["Skin_Highlight"])
        self.assertEqual(OpacityMaterialMode._bindings(), {}, "record forgotten")

    def test_remove_restores_the_recorded_authored_value_on_an_in_place_binding(self):
        """The old mode bound an EXCLUSIVE material in place (no duplicate), so
        the heal has to put the authored value back: disconnect first -- a
        driven plug is not settable -- then write the record's value."""
        import pythontk as ptk

        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.node_utils.data_nodes import DataNodes

        cube = cmds.polyCube(name="inplace_cube")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="Own")
        cmds.setAttr(f"{mat}.emission", 0.25)
        MatUtils.assign_mat([cube], mat)
        RenderEffects.create([cube], channel="highlight")
        cmds.connectAttr(f"{cube}.highlight", f"{mat}.emission", force=True)
        ptk.SceneRecords.RENDER_EFFECTS_BINDINGS.save(
            DataNodes,
            {
                "Own:highlight": {
                    "material": "Own",
                    "object": cube,
                    "channel": "highlight",
                    "restore": {f"{mat}.emission": 0.25},
                }
            },
        )
        cmds.setAttr(f"{cube}.highlight", 1.0)
        self.assertEqual(cmds.getAttr(f"{mat}.emission"), 1.0, "driven before")

        RenderEffects.remove([cube], channel="highlight")

        self.assertEqual(_get_assigned_mat(cube), mat, "never a duplicate to leave")
        self.assertFalse(
            cmds.listConnections(f"{mat}.emission", source=True, destination=False)
        )
        self.assertEqual(cmds.getAttr(f"{mat}.emission"), 0.25, "authored value back")

    def test_the_retired_material_mode_is_refused_before_touching_anything(self):
        """``mode="material"`` was the attribute mode with a warning from
        2026-09-05; it was retired 2026-09-21 with no caller, together with
        ``preview()`` and the ``preview=`` keyword. It is an unknown mode now,
        and an unknown mode is refused BEFORE ``create`` clears the objects'
        existing channel -- the old order logged "Unknown mode" only after
        removing it, so a typo wiped what it was pointed at."""
        from mayatk.mat_utils._mat_utils import MatUtils

        cube = cmds.polyCube(name="plain_cube")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="Plain")
        MatUtils.assign_mat([cube], mat)
        RenderEffects.create([cube], mode="attribute", channel="highlight")

        with self.assertLogs(RenderEffects.logger, level="ERROR"):
            result = RenderEffects.create([cube], mode="material", channel="highlight")

        self.assertEqual(result, {})
        self.assertTrue(
            cmds.attributeQuery("highlight", node=cube, exists=True),
            "the refused call must leave the existing channel alone",
        )
        self.assertEqual(_get_assigned_mat(cube), mat)
        self.assertFalse(
            cmds.listConnections(f"{mat}.emission", source=True, destination=False)
        )
        self.assertFalse(hasattr(RenderEffects, "preview"))
        with self.assertRaises(TypeError):
            RenderEffects.key_fade([cube], start=1, end=10, preview=True)


class TestOpacityVisibilityDriver(MayaTkTestCase):
    """Tests for the keyframe-mirroring visibility logic.

    Replaced the condition-node driver with direct keyframe mirroring
    (sync_visibility_from_opacity / behavior dual-keying) so that FBX
    export produces real visibility animation curves for game engines.
    """

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="vis_cube")[0]

    def test_no_condition_node_created(self):
        """create(mode='attribute') must NOT create a condition-node driver.

        The old condition-node approach broke FBX export because the
        DG graph doesn't survive the export round-trip.
        """
        RenderEffects.create(objects=[self.cube], mode="attribute")

        vis_inputs = cmds.listConnections(f"{self.cube}.visibility", source=True)
        conds = [
            n for n in (vis_inputs or []) if cmds.objectType(str(n)) == "condition"
        ]
        self.assertFalse(
            conds, "No condition node should drive visibility after create"
        )

    def test_sync_mirrors_opacity_to_visibility(self):
        """sync_visibility_from_opacity copies opacity keys to visibility."""
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Set opacity keyframes
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=15, value=1.0)

        # Sync
        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        # Verify visibility keyframes match (use full attr path to avoid
        # picking up the shape's visibility attribute in the query).
        vis_times = cmds.keyframe(f"{self.cube}.visibility", q=True, tc=True)
        vis_values = cmds.keyframe(f"{self.cube}.visibility", q=True, vc=True)
        self.assertEqual(vis_times, [1.0, 15.0])
        self.assertAlmostEqual(vis_values[0], 0.0)
        self.assertAlmostEqual(vis_values[1], 1.0)

    def test_sync_coerces_visibility_to_boolean(self):
        """Visibility mirror should use stepped 0/1 values, not raw opacity."""
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=0.7)
        cmds.setKeyframe(self.cube, attribute="opacity", time=10, value=0.0)

        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        vis_values = cmds.keyframe(f"{self.cube}.visibility", q=True, vc=True)
        self.assertAlmostEqual(vis_values[0], 1.0, msg="0.7 should coerce to 1")
        self.assertAlmostEqual(vis_values[1], 0.0, msg="0.0 should stay 0")

        out_tans = cmds.keyTangent(
            f"{self.cube}.visibility", q=True, outTangentType=True
        )
        self.assertTrue(
            all(t == "step" for t in out_tans),
            f"Visibility tangents should be stepped, got {out_tans}",
        )

    def test_sync_reads_any_nonzero_opacity_as_visible(self):
        """Sub-1.0 opacity (even 0.001) mirrors to visible -- only a literal
        0.0 marks the object hidden: the ``> 0`` rule the GLB's presence
        fallback (``ptk.MeshConvert._presence_keys``) applies too."""
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")
        for frame, value in ((1, 0.0), (10, 0.001), (20, 0.5), (30, 1.0)):
            cmds.setKeyframe(self.cube, attribute="opacity", time=frame, value=value)

        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        plug = f"{self.cube}.visibility"
        times = cmds.keyframe(plug, q=True, tc=True)
        values = cmds.keyframe(plug, q=True, vc=True)
        self.assertEqual(
            sorted(zip(times, values)), [(1, 0.0), (10, 1.0), (20, 1.0), (30, 1.0)]
        )

    def test_sync_does_not_key_shape_visibility(self):
        """Shape node visibility must not receive keyframes.

        Bug: cmds.setKeyframe(obj, attribute='visibility') propagated to
        both transform AND shape.  Fixed by using explicit attr path.
        Fixed: 2026-03-25
        """
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=15, value=1.0)

        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        shape = (cmds.listRelatives(str(self.cube), shapes=True, ni=True) or [None])[0]
        shape_vis_keys = cmds.keyframe(f"{shape}.visibility", q=True, tc=True)
        self.assertFalse(
            shape_vis_keys,
            f"Shape node should have 0 visibility keys, got {shape_vis_keys}",
        )

    def test_sync_is_idempotent(self):
        """Calling sync_visibility_from_opacity twice doesn't duplicate keys."""
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=15, value=1.0)

        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])
        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        vis_times = cmds.keyframe(f"{self.cube}.visibility", q=True, tc=True)
        self.assertEqual(len(vis_times), 2, "Should still be exactly 2 keys")

    # ------------------------------------------------------------------
    # Non-destructive sync / ensure_connections
    # ------------------------------------------------------------------

    def _vis_curve(self):
        """The transform's visibility animCurve node, or None."""
        plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        return (
            cmds.listConnections(plug, source=True, destination=False, type="animCurve")
            or [None]
        )[0]

    def _vis_curve_uuid(self):
        curve = self._vis_curve()
        return cmds.ls(curve, uuid=True)[0] if curve else None

    def test_sync_leaves_an_in_sync_curve_untouched(self):
        """An already-mirrored visibility curve must not be rebuilt.

        ``cutKey(clear=True)`` deletes the animCurve node and ``setKeyframe``
        creates a NEW one, so a no-op sync used to change node identity.
        """
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")
        RenderEffects.key_fade(objects=[self.cube], start=1, end=15, direction="in")

        before = self._vis_curve_uuid()
        self.assertIsNotNone(before, "key_fade must leave a visibility curve")

        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        self.assertEqual(
            self._vis_curve_uuid(),
            before,
            "An in-sync curve was deleted and recreated by a no-op sync",
        )

    def test_ensure_connections_preserves_selected_visibility_keys(self):
        """A SelectionChanged tick must not deselect Graph Editor keys.

        Bug: ``RenderEffectsSlots._update_fade_enabled`` (a SelectionChanged
        subscriber) deferred ``ensure_connections``, which rebuilt the
        ``visibility`` curve from scratch on EVERY selection change.  Keys
        picked in the Graph Editor belong to the destroyed animCurve node, so
        the user's key selection vanished on the next idle and stayed
        unselectable for the rest of the session.
        Fixed: 2026-09-03
        """
        RenderEffects.create(objects=[self.cube], mode="attribute")
        RenderEffects.key_fade(objects=[self.cube], start=1, end=15, direction="in")

        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        before = self._vis_curve_uuid()

        cmds.selectKey(vis_plug, time=(1, 1))
        self.assertEqual(
            cmds.keyframe(vis_plug, q=True, sl=True, tc=True),
            [1.0],
            "pre-condition: one visibility key selected",
        )

        RenderEffects.ensure_connections([self.cube])

        self.assertEqual(
            self._vis_curve_uuid(),
            before,
            "ensure_connections destroyed and recreated the visibility curve",
        )
        self.assertEqual(
            cmds.keyframe(vis_plug, q=True, sl=True, tc=True),
            [1.0],
            "Selected visibility keys must survive a selection-change tick",
        )

    def test_ensure_connections_preserves_hand_keyed_visibility(self):
        """Hand-authored visibility must not be overwritten from opacity.

        ``ensure_connections`` runs on every selection change; silently
        replacing an authored curve there also destroys the sparse
        ``windows=True`` encoding ShadowRig writes.
        """
        RenderEffects.create(objects=[self.cube], mode="attribute")
        RenderEffects.key_fade(objects=[self.cube], start=1, end=15, direction="in")

        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        cmds.keyframe(vis_plug, edit=True, time=(15, 15), timeChange=20)

        RenderEffects.ensure_connections([self.cube])

        self.assertEqual(
            cmds.keyframe(vis_plug, q=True, tc=True),
            [1.0, 20.0],
            "ensure_connections overwrote a hand-edited visibility curve",
        )

    def test_ensure_connections_mirrors_when_visibility_unkeyed(self):
        """The repair case still works: no visibility keys at all -> mirror."""
        RenderEffects.create(objects=[self.cube], mode="attribute")
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=15, value=1.0)

        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        self.assertFalse(
            cmds.keyframe(vis_plug, q=True, tc=True),
            "pre-condition: visibility unkeyed",
        )

        RenderEffects.ensure_connections([self.cube])

        self.assertEqual(cmds.keyframe(vis_plug, q=True, tc=True), [1.0, 15.0])

    def test_remove_restores_visibility(self):
        """Removing opacity should reset visibility to True with no drivers."""
        RenderEffects.create(objects=[self.cube], mode="attribute")

        RenderEffects.remove(objects=[self.cube], mode="attribute")

        self.assertTrue(
            cmds.getAttr(f"{self.cube}.visibility"), "Visibility should reset to True"
        )
        vis_inputs = cmds.listConnections(f"{self.cube}.visibility", source=True)
        self.assertFalse(
            vis_inputs,
            "Visibility should not be driven after remove",
        )

    def test_remove_deletes_the_visibility_mirror(self):
        """Removing opacity takes its stepped visibility mirror with it.

        Bug: remove() deleted the opacity curve but left the mirrored
        visibility keys behind -- the object kept blinking out, and a
        later create() refused it for having visibility keys.
        Fixed: 2026-10-06
        """
        RenderEffects.key_fade(objects=[self.cube], start=1, end=15, direction="out")
        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        self.assertTrue(
            cmds.keyframe(vis_plug, q=True, tc=True), "pre-condition: mirror keyed"
        )
        cmds.currentTime(20)  # past the fade-out: visibility evaluates off

        RenderEffects.remove(objects=[self.cube], mode="attribute")

        self.assertFalse(
            cmds.keyframe(vis_plug, q=True, tc=True),
            "visibility mirror keys left behind",
        )
        self.assertTrue(cmds.getAttr(vis_plug), "visibility should reset to on")
        self.assertEqual(
            RenderEffects.objects_with_visibility_keys([self.cube]),
            [],
            "a re-create would refuse the object",
        )

    def test_create_clears_the_keys_of_a_locked_visibility(self):
        """create(delete_visibility_keys=True) clears a locked visibility's
        keys (``cutKey`` ignores the lock) instead of raising on its reset.

        Bug: the reset ``setAttr`` raised RuntimeError on a locked plug, after
        the keys were already cut.
        Fixed: 2026-10-06
        """
        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        cmds.setKeyframe(vis_plug, time=1, value=0)
        cmds.setAttr(vis_plug, lock=True)

        RenderEffects.create(
            objects=[self.cube], mode="attribute", delete_visibility_keys=True
        )

        self.assertFalse(cmds.keyframe(vis_plug, q=True, tc=True))
        self.assertTrue(cmds.attributeQuery("opacity", node=self.cube, exists=True))

    def test_remove_highlight_keeps_the_visibility_mirror(self):
        """Only the presence channel owns the mirror: removing highlight
        leaves an opacity fade's visibility keys alone."""
        RenderEffects.key_fade(objects=[self.cube], start=1, end=15, direction="out")
        RenderEffects.key_fade(
            objects=[self.cube], start=1, end=15, direction="in", channel="highlight"
        )
        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"

        RenderEffects.remove(objects=[self.cube], channel="highlight")

        self.assertEqual(cmds.keyframe(vis_plug, q=True, tc=True), [1.0, 15.0])

    def test_legacy_condition_node_cleaned_on_create(self):
        """Creating opacity on an object with an old condition-node driver
        should remove the legacy node.

        Ensures backward compatibility with scenes that used the old
        condition-node approach.
        """
        # Simulate legacy state: create a condition node manually
        cond = cmds.createNode(
            "condition", name=f"{self.cube.split('|')[-1].split(':')[-1]}_VisDriver"
        )
        cmds.setAttr(f"{cond}.operation", 2)
        cmds.setAttr(f"{cond}.secondTerm", 0.0)
        cmds.setAttr(f"{cond}.colorIfTrueR", 1.0)
        cmds.setAttr(f"{cond}.colorIfFalseR", 0.0)
        cmds.connectAttr(f"{cond}.outColorR", f"{self.cube}.visibility", force=True)

        # Now create opacity (new code) — should clean up the legacy node
        RenderEffects.create(objects=[self.cube], mode="attribute")

        vis_inputs = cmds.listConnections(f"{self.cube}.visibility", source=True)
        conds = [
            n for n in (vis_inputs or []) if cmds.objectType(str(n)) == "condition"
        ]
        self.assertFalse(
            conds,
            "Legacy condition node should be removed on create",
        )

    def test_foreign_condition_not_removed(self):
        """A non-VisDriver condition driving visibility must not be touched."""
        foreign = cmds.createNode("condition", name="foreign_cond")
        cmds.setAttr(f"{foreign}.operation", 0)
        cmds.setAttr(f"{foreign}.colorIfTrueR", 1.0)
        cmds.setAttr(f"{foreign}.colorIfFalseR", 0.0)
        cmds.connectAttr(f"{foreign}.outColorR", f"{self.cube}.visibility", force=True)

        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Foreign condition should still be there
        inputs = cmds.listConnections(f"{self.cube}.visibility", source=True)
        self.assertTrue(inputs, "Visibility should still have a driver")
        self.assertEqual(
            inputs[0],
            "foreign_cond",
            "Foreign condition should not have been replaced",
        )

        # Cleanup
        RenderEffects.remove(objects=[self.cube], mode="attribute")
        if cmds.objExists(foreign):
            cmds.delete(foreign)

    def test_remove_handles_locked_visibility(self):
        """remove() must not crash when visibility is locked.

        Bug: visibility.set(True) threw RuntimeError when attr was locked.
        Fixed: 2026-02-20
        """
        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Lock visibility between create and remove
        cmds.setAttr(f"{self.cube}.visibility", lock=True)

        # Should not raise
        RenderEffects.remove(objects=[self.cube], mode="attribute")

        # Attribute should be gone regardless
        self.assertFalse(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True)
        )
        # Unlock for teardown
        cmds.setAttr(f"{self.cube}.visibility", lock=False)


class TestFadeWindows(MayaTkTestCase):
    """A DENSE opacity ramp reduces to the visibility keys that bound its
    fades — the opposite-value pairs Unity's importer rebuilds a fade from."""

    def test_windows_bound_each_zero_run(self):
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        # 1..3 full, 4..6 fading, 7..9 zero, 10..12 fading in, 13..15 full.
        keys = (
            [(t, 1.0) for t in (1, 2, 3)]
            + [(4, 0.7), (5, 0.4), (6, 0.1)]
            + [(t, 0.0) for t in (7, 8, 9)]
            + [(10, 0.3), (11, 0.6), (12, 0.9)]
            + [(t, 1.0) for t in (13, 14, 15)]
        )
        self.assertEqual(
            OpacityAttributeMode.fade_windows(keys),
            [(1.0, 1.0), (3.0, 1.0), (7.0, 0.0), (9.0, 0.0), (13.0, 1.0)],
        )

    def test_partial_ramp_keeps_one_visible_key(self):
        """A ramp that never reaches zero still leaves keyed visibility, so
        the GLB route publishes the ramp beside a track."""
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        keys = [(1, 1.0), (2, 0.5), (3, 0.2), (4, 0.6), (5, 1.0)]
        self.assertEqual(OpacityAttributeMode.fade_windows(keys), [(1.0, 1.0)])

    def test_sync_windows_keys_visibility_sparsely(self):
        """``sync_visibility_from_opacity(windows=True)`` on a per-frame
        ramp writes the boundary keys, not one per frame."""
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        loc = cmds.spaceLocator(name="fade_loc")[0]
        OpacityAttributeMode.create([loc])
        for t in range(1, 25):
            # A dip to zero from frame 9 to 15, full outside 5..19.
            if t <= 5 or t >= 19:
                value = 1.0
            elif 9 <= t <= 15:
                value = 0.0
            elif t < 9:
                value = (9 - t) / 4.0
            else:
                value = (t - 15) / 4.0
            cmds.setKeyframe(loc, attribute="opacity", t=t, v=value)
        OpacityAttributeMode.sync_visibility_from_opacity([loc], windows=True)
        times = cmds.keyframe(f"{loc}.visibility", q=True, timeChange=True)
        values = cmds.keyframe(f"{loc}.visibility", q=True, valueChange=True)
        self.assertEqual(
            list(zip(times, values)),
            [(1.0, 1.0), (5.0, 1.0), (9.0, 0.0), (15.0, 0.0), (19.0, 1.0)],
        )
        # The per-key mirror is unchanged: one visibility key per opacity key.
        OpacityAttributeMode.sync_visibility_from_opacity([loc])
        self.assertEqual(
            cmds.keyframe(f"{loc}.visibility", q=True, keyframeCount=True), 24
        )


class TestChannelRecords(MayaTkTestCase):
    """``channel_records`` / ``apply_channel_records``: the hand-off's channel
    payload, read from and landed on real transforms."""

    def test_records_declared_channels_and_keyed_user_attributes_only(self):
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        cube = cmds.polyCube(name="rec_cube")[0]
        RenderEffects.create([cube], channel="highlight")
        cmds.setKeyframe(cube, attribute="highlight", t=1, v=0.0, outTangentType="step")
        cmds.addAttr(cube, longName="keyed", attributeType="double", keyable=True)
        cmds.setKeyframe(cube, attribute="keyed", t=3, v=1.0)
        cmds.addAttr(cube, longName="static", attributeType="double", keyable=True)
        cmds.addAttr(cube, longName="note", dataType="string")
        rec = RenderEffects.channel_records([cube])[cmds.ls(cube, long=True)[0]]
        self.assertEqual(rec["highlight"]["keys"], [[1.0, 0.0, "step"]])
        self.assertIn("highlightColorR", rec)  # a declared value, unkeyed
        self.assertNotIn("highlightColor", rec)  # the compound: its leaves travel
        self.assertEqual(rec["keyed"]["keys"], [[3.0, 1.0, "smooth"]])
        self.assertNotIn("static", rec)  # unkeyed and undeclared
        self.assertNotIn("note", rec)  # not numeric

    def test_apply_replaces_an_importer_shaped_compound(self):
        """Maya's FBX importer spells a Blender vector property as a double3
        with ``0/1/2`` leaves; the declared colour must still land."""
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        cube = cmds.polyCube(name="fbx_cube")[0]
        cmds.addAttr(cube, longName="highlight", attributeType="double")
        for stem in ("highlightColor", "highlightColorDim"):
            cmds.addAttr(cube, longName=stem, attributeType="double3")
            for i in range(3):
                cmds.addAttr(
                    cube, longName=f"{stem}{i}", attributeType="double", parent=stem
                )
        RenderEffects.apply_channel_records(
            cube,
            {
                "highlight": {
                    "value": 0.0,
                    "keys": [[1.0, 0.0, "step"], [10.0, 1.0, "smooth"]],
                },
                "highlightColorR": {"value": 1.0, "keys": []},
                "highlightColorG": {"value": 0.0, "keys": []},
                "highlightColorB": {"value": 0.0, "keys": []},
                "highlightColorDimR": {"value": 0.0, "keys": []},
                "highlightColorDimG": {"value": 0.5, "keys": []},
                "highlightColorDimB": {"value": 0.0, "keys": []},
                "wobble": {"value": 2.0, "keys": [[5.0, 2.0, "linear"]]},
            },
        )
        # Replacing the second foreign compound must not disturb the first.
        self.assertEqual(cmds.getAttr(f"{cube}.highlightColor")[0], (1.0, 0.0, 0.0))
        self.assertEqual(cmds.getAttr(f"{cube}.highlightColorDim")[0], (0.0, 0.5, 0.0))
        for stem in ("highlightColor", "highlightColorDim"):
            self.assertFalse(cmds.attributeQuery(f"{stem}0", node=cube, exists=True))
        self.assertTrue(cmds.getAttr(f"{cube}.highlight", keyable=True))
        self.assertEqual(
            cmds.keyTangent(f"{cube}.highlight", q=True, outTangentType=True),
            ["step", "auto"],
        )
        self.assertEqual(
            cmds.keyTangent(f"{cube}.wobble", q=True, outTangentType=True), ["linear"]
        )


class TestPrepareForExport(MayaTkTestCase):
    """prepare_for_export stages the curve-proxy transport and writes nothing the
    export reads back: presence is derived from the authored channels where the
    GLB is built (``ptk.MeshConvert._presence_keys``) and the proxy carries each
    object's curve to Unity, so an opacity keyed by hand gets no visibility
    mirror, and authored visibility is never touched."""

    def test_hand_keyed_opacity_gets_no_visibility_mirror(self):
        cube = cmds.polyCube(name="manual_keyed_cube")[0]
        RenderEffects.create(objects=[cube], mode="attribute")
        for frame, value in ((1, 0.0), (30, 1.0), (60, 0.0)):
            cmds.setKeyframe(cube, attribute="opacity", time=frame, value=value)
        vis_plug = f"{cmds.ls(str(cube), l=True)[0]}.visibility"

        try:
            self.assertEqual(RenderEffects.prepare_for_export(), [])
            self.assertEqual(cmds.keyframe(vis_plug, q=True, keyframeCount=True), 0)
            self.assertEqual(
                len(cmds.ls("manual_keyed_cube__opacity")), 1, "the proxy is staged"
            )
        finally:
            RenderEffects.finish_export()
        self.assertEqual(cmds.ls("manual_keyed_cube__opacity"), [], "and removed after")

    def test_preserves_manual_visibility_keys(self):
        """Authored visibility, however many keys, is left exactly as authored."""
        cube = cmds.polyCube(name="manual_vis_cube")[0]
        RenderEffects.create(objects=[cube], mode="attribute")
        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=100, value=1.0)
        # Long-name plug path: attribute= by kwarg also keys the shape.
        vis_plug = f"{cmds.ls(str(cube), l=True)[0]}.visibility"
        for t, v in [(1, 0), (25, 1), (50, 0), (100, 1)]:
            cmds.setKeyframe(vis_plug, time=t, value=v)

        try:
            self.assertEqual(RenderEffects.prepare_for_export(), [])
        finally:
            RenderEffects.finish_export()
        self.assertEqual(
            sorted(set(cmds.keyframe(vis_plug, q=True, tc=True))), [1, 25, 50, 100]
        )

    def test_prepare_after_key_fade_is_noop(self):
        """key_fade's authoring mirror survives prepare_for_export untouched
        (the canonical happy path)."""
        cube = cmds.polyCube(name="key_fade_cube")[0]
        RenderEffects.create(objects=[cube], mode="attribute")
        RenderEffects.key_fade(objects=[cube], start=1, end=30, direction="in")

        vis_before = cmds.keyframe(cube, attribute="visibility", q=True, tc=True)
        synced = RenderEffects.prepare_for_export()
        vis_after = cmds.keyframe(cube, attribute="visibility", q=True, tc=True)

        self.assertEqual(synced, [])
        self.assertEqual(vis_before, vis_after, "Visibility keys must be untouched")

    def test_fewer_visibility_keys_than_opacity_keys_are_left_as_authored(self):
        """A sparse visibility encoding survives the export untouched.

        Fewer visibility keys than opacity keys is the encoding ShadowRig
        writes on purpose (``windows=True``: fade boundaries over a dense
        ramp) and the shape an optimize pass leaves. The old
        ``vis_keys < opa_keys`` rule rebuilt both on every export -- the
        windows flattened to one key per frame, and the rebuild deleted the
        curve node out from under a production restore (2026-09-14)."""
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        loc = cmds.spaceLocator(name="windowed_loc")[0]
        OpacityAttributeMode.create([loc])
        for t in range(1, 25):
            value = 0.0 if 9 <= t <= 15 else 1.0
            cmds.setKeyframe(loc, attribute="opacity", t=t, v=value)
        OpacityAttributeMode.sync_visibility_from_opacity([loc], windows=True)
        plug = f"{loc}.visibility"
        authored = list(
            zip(
                cmds.keyframe(plug, q=True, timeChange=True),
                cmds.keyframe(plug, q=True, valueChange=True),
            )
        )
        self.assertLess(len(authored), 24, "the fixture must be the sparse encoding")
        uuid = cmds.ls(cmds.keyframe(plug, q=True, name=True)[0], uuid=True)[0]

        synced = RenderEffects.prepare_for_export()

        self.assertEqual(synced, [])
        self.assertEqual(
            list(
                zip(
                    cmds.keyframe(plug, q=True, timeChange=True),
                    cmds.keyframe(plug, q=True, valueChange=True),
                )
            ),
            authored,
        )
        curve = cmds.keyframe(plug, q=True, name=True)[0]
        self.assertEqual(cmds.ls(curve, uuid=True)[0], uuid, "the node was rebuilt")


class TestPanelReads(MayaTkTestCase):
    """What the Render Effects panel (tentacle's ``slots/tools/render_effects.py``,
    whose own tests moved with it) reads through ``mtk.RenderEffects``: the
    selection its actions act on, which objects carry a channel, and the
    selection a Shot Manifest focus makes."""

    def setUp(self):
        super().setUp()
        self.lit = cmds.polyCube(name="reads_lit")[0]
        self.plain = cmds.polyCube(name="reads_plain")[0]
        RenderEffects.create([self.lit], mode="attribute", channel="highlight")

    def test_has_channel_answers_per_object_and_channel(self):
        self.assertTrue(RenderEffects.has_channel(self.lit, "highlight"))
        self.assertFalse(RenderEffects.has_channel(self.plain, "highlight"))
        self.assertFalse(RenderEffects.has_channel(self.lit, "opacity"))
        with self.assertRaises(KeyError):
            RenderEffects.has_channel(self.lit, "glow")

    def test_selected_objects_keeps_the_order_and_its_lead(self):
        cmds.select([self.plain, self.lit], replace=True)
        self.assertEqual(RenderEffects.selected_objects(), [self.plain, self.lit])
        self.assertEqual(RenderEffects.selected_objects(last_only=True), [self.lit])
        cmds.select(clear=True)
        self.assertEqual(RenderEffects.selected_objects(), [])
        self.assertEqual(RenderEffects.selected_objects(last_only=True), [])

    def test_select_objects_selects_what_resolves_as_a_view_not_an_edit(self):
        cmds.undoInfo(state=True)
        cmds.select(self.plain, replace=True)
        before = cmds.undoInfo(q=True, undoName=True)
        names = RenderEffects.select_objects([self.lit, "reads_gone"])
        self.assertEqual(names, ["|reads_lit"])
        self.assertEqual(cmds.ls(selection=True, long=True), ["|reads_lit"])
        self.assertEqual(
            cmds.undoInfo(q=True, undoName=True), before, "no undo step recorded"
        )

    def test_select_objects_of_nothing_leaves_the_selection_alone(self):
        """An empty ``cmds.ls`` lists every node: nothing named selects nothing."""
        cmds.select(self.plain, replace=True)
        self.assertEqual(RenderEffects.select_objects([]), [])
        self.assertEqual(RenderEffects.select_objects(["reads_gone"]), [])
        self.assertEqual(cmds.ls(selection=True), [self.plain])


class TestHighlightChannel(MayaTkTestCase):
    """The second channel on the same transport: create, pulse, remove."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="hl_cube")[0]

    def test_create_adds_intensity_and_colour_attributes(self):
        RenderEffects.create(objects=[self.cube], mode="attribute", channel="highlight")
        self.assertTrue(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.assertTrue(
            cmds.attributeQuery("highlightColor", node=self.cube, exists=True)
        )
        self.assertEqual(cmds.getAttr(f"{self.cube}.highlight"), 0.0)
        self.assertEqual(
            [round(c, 6) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.0, 0.088656, 0.723055],
        )
        self.assertTrue(cmds.getAttr(f"{self.cube}.highlight", keyable=True))
        # Not the presence channel: no visibility key is written by creating it.
        self.assertFalse(cmds.keyframe(f"{self.cube}.visibility", q=True, kc=True))

    def test_create_writes_the_recipes_default_colours(self):
        """Two files state this colour: the attribute preset Create authors
        from, and the effect recipe's defaults -- what the Render Effects
        panel's row shows before anything is picked (tentacle's panel test pins
        its seeds to the recipe). Drift between them shows one colour and
        writes another, and nothing else would catch it."""
        RenderEffects.create(objects=[self.cube], mode="attribute", channel="highlight")

        recipe = ptk.EffectRecipe()
        for attr, seed in (
            ("highlightColor", recipe.pulse_bright),
            ("highlightColorDim", recipe.pulse_dim),
        ):
            self.assertEqual(
                [round(c, 6) for c in cmds.getAttr(f"{self.cube}.{attr}")[0]],
                [round(float(c), 6) for c in seed],
                attr,
            )

    def test_key_pulse_writes_linear_holds_and_ramps_and_the_colour(self):
        """Four linear keys per cycle -- the published ramp is read linearly."""
        keyed = RenderEffects.key_pulse(
            [self.cube],
            start=0,
            end=200,
            period=100,
            bright_fraction=0.6,
            ramp_fraction=0.2,
            color=(1.0, 0.0, 0.0),
        )
        self.assertEqual(keyed, ["hl_cube"])
        plug = f"{self.cube}.highlight"
        times = cmds.keyframe(plug, q=True, tc=True)
        values = cmds.keyframe(plug, q=True, vc=True)
        # Bracketed by dim: the pulse opens at 0 and ramps up over the lead-in
        # (the cycle's own ramp, 20), so the first bright hold is 20..60 --
        # every bright hold, the first included, is preceded by one ramp.
        # Cycle 1 likewise; the trail-out lands dim at 200.
        self.assertEqual(times[:5], [0.0, 20.0, 60.0, 80.0, 100.0])
        self.assertEqual(values[:5], [0.0, 1.0, 1.0, 0.0, 0.0])
        self.assertEqual((times[-1], values[-1]), (200.0, 0.0))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in values))
        tangents = set(cmds.keyTangent(plug, q=True, outTangentType=True))
        self.assertEqual(tangents, {"linear"})
        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [1.0, 0.0, 0.0],
        )

    def test_a_pulse_is_dim_before_it_starts_and_after_it_ends(self):
        """Maya holds a curve's FIRST key value backwards forever, so a pulse
        whose first key is bright made the object glow for the whole timeline
        before it -- measured on a production board (frames 725-845 of a 3468
        frame scene): blue from frame 1. The pulse is bracketed by dim keys at
        both ends, so the hold in each direction is 'not highlighted'."""
        RenderEffects.key_pulse(
            [self.cube], start=100, end=300, period=50, bright_fraction=0.5
        )
        plug = f"{self.cube}.highlight"
        self.assertEqual(cmds.getAttr(plug, time=100), 0.0)
        self.assertEqual(cmds.getAttr(plug, time=300), 0.0)
        # The hold in both directions, well outside the authored window.
        self.assertEqual(cmds.getAttr(plug, time=1), 0.0)
        self.assertEqual(cmds.getAttr(plug, time=3468), 0.0)
        # ...and it really does pulse in between.
        inside = [cmds.getAttr(plug, time=t) for t in range(101, 300)]
        self.assertAlmostEqual(max(inside), 1.0, places=5)

    def test_key_pulse_keys_the_one_plan_the_preview_publishes(self):
        """The writer and the WebXR preview share ``ptk.RampKeys.pulse``: what
        lands on the curve is that plan, frame for frame, so a previewed pulse
        is the pulse the key tool writes."""
        import pythontk as ptk

        kwargs = dict(period=86, bright_fraction=0.59, lead_in=12, lead_out=30)
        RenderEffects.key_pulse([self.cube], start=10, end=400, **kwargs)
        plug = f"{self.cube}.highlight"
        keys = list(
            zip(
                cmds.keyframe(plug, q=True, tc=True),
                cmds.keyframe(plug, q=True, vc=True),
            )
        )
        self.assertEqual(keys, ptk.RampKeys.pulse(10, 400, **kwargs))

    def test_a_pulse_keyed_over_another_replaces_it(self):
        """An explicit keying replaces the pulse it lands on: the window's
        keys go first, so a re-keyed cadence is the new plan alone."""
        import pythontk as ptk

        RenderEffects.key_pulse([self.cube], start=10, end=400, period=50)
        kwargs = dict(period=86, bright_fraction=0.59, lead_in=12, lead_out=30)
        RenderEffects.key_pulse([self.cube], start=10, end=400, **kwargs)
        plug = f"{self.cube}.highlight"
        keys = list(
            zip(
                cmds.keyframe(plug, q=True, tc=True),
                cmds.keyframe(plug, q=True, vc=True),
            )
        )
        self.assertEqual(keys, ptk.RampKeys.pulse(10, 400, **kwargs))

    def test_the_pulse_gaps_are_the_cycles_own_ramp_by_default(self):
        """Default lead-in / lead-out: the ends are shaped exactly like every
        interior transition, so the first bright hold sits one ramp in and the
        pulse reads as periodic from its very first cycle."""
        RenderEffects.key_pulse(
            [self.cube],
            start=0,
            end=200,
            period=100,
            bright_fraction=0.6,
            ramp_fraction=0.2,
        )
        plug = f"{self.cube}.highlight"
        keys = list(
            zip(
                cmds.keyframe(plug, q=True, tc=True),
                cmds.keyframe(plug, q=True, vc=True),
            )
        )
        self.assertEqual(keys[:3], [(0.0, 0.0), (20.0, 1.0), (60.0, 1.0)])
        # The interior transition into cycle 1 takes the same 20 frames.
        self.assertEqual(keys[4:6], [(100.0, 0.0), (120.0, 1.0)])

    def test_the_two_pulse_gaps_can_be_set_apart(self):
        """The gaps are independent when the caller says so: a slow open and a
        hard cut are both askable for."""
        RenderEffects.key_pulse(
            [self.cube],
            start=0,
            end=200,
            period=100,
            bright_fraction=0.6,
            ramp_fraction=0.2,
            lead_in=40,
            lead_out=0,
        )
        plug = f"{self.cube}.highlight"
        keys = list(
            zip(
                cmds.keyframe(plug, q=True, tc=True),
                cmds.keyframe(plug, q=True, vc=True),
            )
        )
        self.assertEqual(keys[:2], [(0.0, 0.0), (40.0, 1.0)])
        # A zero gap still brackets -- the dim key is one frame before the end
        # (the tightest whole-frame bracket), so the cut is as instant as the
        # frame allows and the forward hold is still 'not highlighted'.
        self.assertEqual(keys[-1], (200.0, 0.0))
        self.assertEqual(keys[-2][0], 199.0)
        self.assertEqual(cmds.getAttr(plug, time=3468), 0.0)

    def test_pulse_gaps_that_cannot_fit_are_scaled_to_the_window(self):
        """Asked for more gap than there is pulse: the shape degrades, the keys
        stay inside the authored window and ordered."""
        RenderEffects.key_pulse(
            [self.cube], start=0, end=100, period=50, lead_in=400, lead_out=400
        )
        plug = f"{self.cube}.highlight"
        times = cmds.keyframe(plug, q=True, tc=True)
        self.assertEqual(times, sorted(times))
        self.assertGreaterEqual(times[0], 0.0)
        self.assertLessEqual(times[-1], 100.0)

    def test_keying_keeps_the_selection(self):
        """Live-Maya regression (2026-09-05): a key left ``data_internal`` (or a
        duplicated material) selected, so the user's next tool acted on it."""
        cmds.select(self.cube, replace=True)
        RenderEffects.key_fade([self.cube], start=0, end=10)
        self.assertEqual(cmds.ls(selection=True), [self.cube])
        RenderEffects.key_pulse([self.cube], start=0, end=100, period=50)
        self.assertEqual(cmds.ls(selection=True), [self.cube])

    def test_key_fade_can_clear_visibility_keys_before_creating(self):
        cmds.setKeyframe(f"{self.cube}.visibility", time=5, value=0)
        cmds.setKeyframe(f"{self.cube}.visibility", time=50, value=1)

        RenderEffects.key_fade(
            [self.cube], start=10, end=20, direction="in", delete_visibility_keys=True
        )

        vis_times = cmds.keyframe(f"{self.cube}.visibility", q=True, tc=True)
        self.assertEqual(
            vis_times, [10.0, 20.0], "old vis keys cleared, mirror written"
        )

    def test_a_pulse_does_not_touch_visibility(self):
        RenderEffects.key_pulse([self.cube], start=0, end=100, period=50)
        self.assertFalse(cmds.keyframe(f"{self.cube}.visibility", q=True, kc=True))

    def test_remove_one_channel_leaves_the_other(self):
        RenderEffects.create([self.cube], channel="opacity")
        RenderEffects.create([self.cube], channel="highlight")
        RenderEffects.remove([self.cube], channel="highlight")
        self.assertFalse(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.assertFalse(
            cmds.attributeQuery("highlightColor", node=self.cube, exists=True)
        )
        self.assertTrue(cmds.attributeQuery("opacity", node=self.cube, exists=True))


class TestEffectWriters(MayaTkTestCase):
    """``apply_effect`` -- the Shot Manifest's writer -- and the scene recipe
    the hand tools key with: one plan, one writer, nothing deleted."""

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="film")  # 24 fps
        self.cube = cmds.polyCube(name="fx_cube")[0]
        # The recipe lives on the shot store, which sits above mat_utils.
        self.store_cls = RenderEffects.scene_store()
        self.store_cls._active = None

    def tearDown(self):
        self.store_cls._active = None
        super().tearDown()

    def _keys(self, attr):
        plug = f"{self.cube}.{attr}"
        return list(
            zip(
                cmds.keyframe(plug, q=True, tc=True) or [],
                cmds.keyframe(plug, q=True, vc=True) or [],
            )
        )

    def _curve(self, attr):
        return cmds.keyframe(f"{self.cube}.{attr}", q=True, name=True)[0]

    def _rgb(self, attr):
        return [round(c, 6) for c in cmds.getAttr(f"{self.cube}.{attr}")[0]]

    def test_a_fade_is_the_recipes_length_where_it_is_placed(self):
        recipe = ptk.EffectRecipe(fade_frames=12)
        RenderEffects.apply_effect(self.cube, "fade_in", 10, 100, recipe, fps=24)
        RenderEffects.apply_effect(self.cube, "fade_out", 10, 100, recipe, fps=24)
        self.assertEqual(
            self._keys("opacity"),
            [(10.0, 0.0), (22.0, 1.0), (88.0, 1.0), (100.0, 0.0)],
        )

    def test_it_returns_every_key_it_wrote_the_mirror_included(self):
        recipe = ptk.EffectRecipe(fade_frames=10)
        written = RenderEffects.apply_effect(self.cube, "fade_in", 0, 50, recipe, 24)
        opacity, vis = self._curve("opacity"), self._curve("visibility")
        self.assertEqual(
            sorted(written),
            sorted([(opacity, 0.0), (opacity, 10.0), (vis, 0.0), (vis, 10.0)]),
        )

    def test_a_pulse_keys_the_recipes_plan_over_the_range(self):
        recipe = ptk.EffectRecipe(pulse_period=2.0, pulse_duty=0.5)
        RenderEffects.apply_effect(self.cube, "pulse", 0, 240, recipe, fps=24)
        self.assertEqual(self._keys("highlight"), recipe.plan("pulse", 0, 240, 24))

    def test_it_deletes_nothing(self):
        """The build releases its own keys first; a key it does not hold is
        the animator's and stays."""
        RenderEffects.create(objects=[self.cube], mode="attribute", channel="highlight")
        cmds.setKeyframe(f"{self.cube}.highlight", time=500, value=0.5)
        RenderEffects.apply_effect(self.cube, "pulse", 0, 240, fps=24)
        self.assertIn((500.0, 0.5), self._keys("highlight"))

    def test_a_channel_it_creates_takes_the_recipes_colours(self):
        recipe = ptk.EffectRecipe(pulse_bright=(1, 0, 0), pulse_dim=(0, 0, 0.5))
        RenderEffects.apply_effect(self.cube, "pulse", 0, 100, recipe, fps=24)
        self.assertEqual(self._rgb("highlightColor"), [1.0, 0.0, 0.0])
        self.assertEqual(self._rgb("highlightColorDim"), [0.0, 0.0, 0.5])

    def test_an_existing_channel_keeps_its_colours(self):
        """A revised object keeps its colours through a rebuild."""
        RenderEffects.create(objects=[self.cube], mode="attribute", channel="highlight")
        RenderEffects.set_channel_color([self.cube], (0.0, 1.0, 0.0))
        recipe = ptk.EffectRecipe(pulse_bright=(1, 0, 0))
        RenderEffects.apply_effect(self.cube, "pulse", 0, 100, recipe, fps=24)
        self.assertEqual(self._rgb("highlightColor"), [0.0, 1.0, 0.0])

    def test_an_effect_without_a_channel_says_so(self):
        with self.assertRaises(ValueError):
            RenderEffects.apply_effect(self.cube, "clip", 0, 10)

    def test_the_hand_tools_key_with_the_scene_recipe(self):
        """The panel's Key and the manifest's build key one recipe."""
        store = self.store_cls.active()
        store.update_effect_recipe(fade_frames=8, pulse_period=2.0, pulse_duty=0.5)
        RenderEffects.key_fade([self.cube], start=20)
        self.assertEqual(self._keys("opacity"), [(20.0, 0.0), (28.0, 1.0)])
        RenderEffects.key_pulse([self.cube], start=0, end=240)
        self.assertEqual(
            self._keys("highlight"), store.effect_recipe.plan("pulse", 0, 240, 24)
        )


class TestChannelColourRevision(MayaTkTestCase):
    """Re-colouring an authored highlight after the pulses were keyed.

    The colour is its own attribute rather than part of the curve, which is
    what lets a signed-off look change without a re-key.
    """

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="revise_cube")[0]
        self.plain = cmds.polyCube(name="plain_cube")[0]
        RenderEffects.create(objects=[self.cube], mode="attribute", channel="highlight")

    def test_set_channel_color_writes_the_named_objects(self):
        written = RenderEffects.set_channel_color([self.cube], color=(0.045, 0.39, 1.0))
        self.assertEqual(written, ["revise_cube"])
        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.045, 0.39, 1.0],
        )

    def test_set_channel_color_leaves_the_keys_alone(self):
        RenderEffects.key_pulse([self.cube], start=0, end=100)
        before = cmds.keyframe(
            f"{self.cube}.highlight", q=True, timeChange=True, valueChange=True
        )
        RenderEffects.set_channel_color([self.cube], color=(1.0, 0.0, 0.0))
        after = cmds.keyframe(
            f"{self.cube}.highlight", q=True, timeChange=True, valueChange=True
        )
        self.assertEqual(before, after, "a recolour must not touch the pulse")

    def test_objects_without_the_channel_are_skipped(self):
        written = RenderEffects.set_channel_color(
            [self.cube, self.plain], color=(1.0, 0.0, 0.0)
        )
        self.assertEqual(written, ["revise_cube"])
        self.assertFalse(
            cmds.attributeQuery("highlightColor", node=self.plain, exists=True)
        )

    def test_empty_selection_falls_back_to_every_object_with_the_channel(self):
        other = cmds.polyCube(name="revise_other")[0]
        RenderEffects.create(objects=[other], mode="attribute", channel="highlight")
        cmds.select(clear=True)

        written = RenderEffects.set_channel_color(color=(0.045, 0.39, 1.0))

        self.assertEqual(sorted(written), ["revise_cube", "revise_other"])
        for node in (self.cube, other):
            self.assertEqual(
                [round(c, 3) for c in cmds.getAttr(f"{node}.highlightColor")[0]],
                [0.045, 0.39, 1.0],
            )

    def test_objects_with_channel_finds_only_the_carriers(self):
        found = RenderEffects.objects_with_channel("highlight")
        self.assertEqual([n.split("|")[-1] for n in found], ["revise_cube"])

    def test_channel_colors_reads_back_what_was_written(self):
        RenderEffects.set_channel_color([self.cube], color=(0.045, 0.39, 1.0))
        colors = RenderEffects.channel_colors()
        self.assertEqual(len(colors), 1)
        (rgb,) = colors.values()
        self.assertEqual([round(c, 3) for c in rgb], [0.045, 0.39, 1.0])

    def test_a_missing_colour_is_refused(self):
        with self.assertRaises(ValueError):
            RenderEffects.set_channel_color([self.cube])

    def test_a_channel_without_a_colour_is_refused(self):
        with self.assertRaises(ValueError):
            RenderEffects.set_channel_color(
                [self.cube], color=(1.0, 0.0, 0.0), channel="opacity"
            )


class TestHighlightColourStops(MayaTkTestCase):
    """The highlight rides BETWEEN two authored colours.

    The bright end is what the object reads at intensity 1, the dim end at 0.
    Before the dim end existed the low half of a pulse was the ABSENCE of a
    colour, so an artist asking for the opposing half had nothing to set.
    """

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="stops_cube")[0]
        RenderEffects.create(objects=[self.cube], mode="attribute", channel="highlight")

    def _rgb(self, attr):
        # 6 decimals, the precision the tool states and compares colours to.
        # At 3 the seed reads as its own rounding and a real drift hides.
        return [round(c, 6) for c in cmds.getAttr(f"{self.cube}.{attr}")[0]]

    def test_create_seeds_both_ends(self):
        for attr in ("highlightColor", "highlightColorDim"):
            self.assertTrue(
                cmds.attributeQuery(attr, node=self.cube, exists=True), attr
            )

    def test_the_dim_end_seeds_black(self):
        """Which is what collapses the two-end ramp to the one-colour shape
        this channel had before the end existed."""
        self.assertEqual(self._rgb("highlightColorDim"), [0.0, 0.0, 0.0])

    def test_writing_one_end_leaves_the_other_alone(self):
        RenderEffects.set_channel_color([self.cube], color=(1.0, 0.0, 0.0), stop="lo")
        self.assertEqual(self._rgb("highlightColorDim"), [1.0, 0.0, 0.0])
        self.assertEqual(self._rgb("highlightColor"), [0.0, 0.088656, 0.723055])

    def test_the_default_end_is_still_the_bright_one(self):
        """Every call site written before the dim end existed passes no stop."""
        RenderEffects.set_channel_color([self.cube], color=(0.9, 0.1, 0.1))
        self.assertEqual(self._rgb("highlightColor"), [0.9, 0.1, 0.1])
        self.assertEqual(self._rgb("highlightColorDim"), [0.0, 0.0, 0.0])

    def test_channel_color_stops_reads_both_ends_in_one_pass(self):
        RenderEffects.set_channel_color([self.cube], color=(0.1, 0.2, 0.3))
        RenderEffects.set_channel_color([self.cube], color=(0.4, 0.5, 0.6), stop="lo")
        stops = RenderEffects.channel_color_stops([self.cube])
        (pair,) = stops.values()
        self.assertEqual(len(pair), 2)
        self.assertEqual([round(c, 3) for c in pair[0]], [0.1, 0.2, 0.3])
        self.assertEqual([round(c, 3) for c in pair[1]], [0.4, 0.5, 0.6])

    def test_key_pulse_writes_both_ends(self):
        RenderEffects.key_pulse(
            [self.cube],
            start=0,
            end=100,
            color=(1.0, 0.0, 0.0),
            dim_color=(0.0, 0.0, 0.5),
        )
        self.assertEqual(self._rgb("highlightColor"), [1.0, 0.0, 0.0])
        self.assertEqual(self._rgb("highlightColorDim"), [0.0, 0.0, 0.5])

    def test_key_pulse_without_a_dim_colour_leaves_it_untouched(self):
        RenderEffects.set_channel_color([self.cube], color=(0.0, 0.9, 0.0), stop="lo")
        RenderEffects.key_pulse([self.cube], start=0, end=100, color=(1.0, 0.0, 0.0))
        self.assertEqual(
            self._rgb("highlightColorDim"),
            [0.0, 0.9, 0.0],
            "a pulse that states no dim colour must not erase one",
        )

    def test_remove_strips_both_ends(self):
        RenderEffects.remove(objects=[self.cube], channel="highlight")
        for attr in ("highlightColor", "highlightColorDim"):
            self.assertFalse(
                cmds.attributeQuery(attr, node=self.cube, exists=True), attr
            )

    def test_both_ends_publish_under_their_own_track_keys(self):
        """The join the drift guard only checks by NAME, checked by value."""
        RenderEffects.key_pulse(
            [self.cube],
            start=0,
            end=60,
            period=20,
            color=(0.2, 0.5, 1.0),
            dim_color=(0.4, 0.0, 0.0),
        )
        tracks = RenderEffects.visibility_tracks()
        track = next(t for t in tracks if t.get("node", "").endswith("stops_cube"))
        self.assertEqual(
            [round(c, 3) for c in track["highlight_color"]], [0.2, 0.5, 1.0]
        )
        self.assertEqual(
            [round(c, 3) for c in track["highlight_color_dim"]], [0.4, 0.0, 0.0]
        )

    def test_the_published_track_reaches_the_gltf_values(self):
        """End to end across three packages: Maya attribute -> published key ->
        emissiveFactor. Each half was covered and the JOIN was not, which is
        exactly where a renamed key would have gone unnoticed."""
        from pythontk import GlbFades

        GLTF = GlbFades.CHANNELS

        RenderEffects.key_pulse(
            [self.cube],
            start=0,
            end=60,
            period=20,
            color=(0.2, 0.5, 1.0),
            dim_color=(0.4, 0.0, 0.0),
        )
        tracks = RenderEffects.visibility_tracks()
        track = next(t for t in tracks if t.get("node", "").endswith("stops_cube"))

        spec = GLTF["highlight"]
        stops = spec.color_stops.resolve([track.get(k) for k in spec.color_stops.keys])
        base = [0.0, 0.0, 0.0]
        self.assertEqual(
            [round(c, 3) for c in spec.values(base, 0.0, stops)], [0.4, 0.0, 0.0]
        )
        self.assertEqual(
            [round(c, 3) for c in spec.values(base, 1.0, stops)], [0.2, 0.5, 1.0]
        )

    def test_an_unknown_stop_is_refused(self):
        with self.assertRaises(ValueError):
            RenderEffects.set_channel_color(
                [self.cube], color=(1.0, 0.0, 0.0), stop="middle"
            )


class TestChannelTableJoin(unittest.TestCase):
    """mayatk's channel table and pythontk's must describe one channel.

    They are joined by NAME rather than by a shared constant -- Maya attribute
    names on one side, published track keys on the other -- so nothing but this
    guard stops a rename on either side from publishing a key no reader looks
    for. The old join held the same way and had no guard; adding a second stop
    doubled the surface it could go wrong on.
    """

    def test_the_published_stop_keys_agree(self):
        from mayatk.mat_utils.render_opacity.channels import CHANNELS as MAYA
        from pythontk import GlbFades

        GLTF = GlbFades.CHANNELS

        for name, spec in MAYA.items():
            row = GLTF.get(name)
            if row is None or spec.track_color_stops is None:
                continue
            with self.subTest(channel=name):
                self.assertIsNotNone(
                    row.color_stops, f"pythontk's {name} row states no colour"
                )
                self.assertEqual(
                    spec.track_color_stops.keys,
                    row.color_stops.keys,
                    "mayatk publishes keys pythontk does not read",
                )

    def test_attrs_covers_every_stop(self):
        """``attrs`` is what the shot system reads as content; a stop missing
        from it is a curve the shot system cannot see."""
        from mayatk.mat_utils.render_opacity.channels import HIGHLIGHT

        for attr in HIGHLIGHT.color_stops.keys:
            for leaf in "RGB":
                self.assertIn(f"{attr}{leaf}", HIGHLIGHT.attrs)


class TestWholeFrameKeys(MayaTkTestCase):
    """Render-effect keys land on whole frames.

    The pulse is authored in SECONDS, so its cadence is fractional in frames
    (2.86 s is 85.8 of them at 30 fps) and every key past the first used to sit
    between frames -- off the graph editor's grid and awkward to retime by
    hand. Whole frames are the default; the exact sub-frame cadence is still
    askable for.
    """

    PERIOD = 85.8  # 2.86 s at 30 fps -- fractional on purpose

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="whole_frame_cube")[0]

    def _times(self, attr="highlight"):
        return cmds.keyframe(f"{self.cube}.{attr}", q=True, tc=True) or []

    def test_a_fade_snaps_its_window_and_its_visibility_mirror(self):
        RenderEffects.key_fade([self.cube], start=10.4, end=25.6, direction="in")

        self.assertEqual(self._times("opacity"), [10.0, 26.0])
        self.assertEqual(self._times("visibility"), [10.0, 26.0])

    def test_a_fractional_pulse_keys_whole_frames_only(self):
        RenderEffects.key_pulse([self.cube], start=10.4, end=110.6, period=self.PERIOD)

        times = self._times()
        self.assertTrue(times, "the pulse keyed nothing")
        self.assertEqual([t for t in times if t != int(t)], [], "sub-frame keys")
        self.assertEqual((times[0], times[-1]), (10.0, 111.0), "the window snaps too")

    def test_the_snapped_train_does_not_drift_off_the_cadence(self):
        """Only each key snaps -- the cycle itself still advances by the exact
        period, so a long train stays within a frame of the asked-for cadence
        instead of accumulating the rounding error cycle by cycle."""
        RenderEffects.key_pulse(
            [self.cube], start=0, end=1800, period=self.PERIOD, bright_fraction=0.5
        )

        plug = f"{self.cube}.highlight"
        keys = list(zip(self._times(), cmds.keyframe(plug, q=True, vc=True)))
        rises = [
            t
            for i, (t, v) in enumerate(keys)
            if v == 1.0 and i and keys[i - 1][1] == 0.0
        ]
        self.assertGreater(len(rises), 15, "too few cycles to read a cadence")
        span = rises[-1] - rises[0]
        self.assertLessEqual(
            abs(span - (len(rises) - 1) * self.PERIOD),
            1.0,
            f"the train drifted: {span} over {len(rises) - 1} cycles",
        )

    def test_the_exact_cadence_is_still_askable_for(self):
        RenderEffects.key_pulse(
            [self.cube], start=0, end=400, period=self.PERIOD, whole_frames=False
        )

        self.assertTrue(
            any(t != int(t) for t in self._times()),
            "whole_frames=False must author the sub-frame cadence",
        )


class TestStepTrackEvaluation(MayaTkTestCase):
    """``_stepped_track`` evaluates the CURVE, not the plug (2026-09-14): a
    ``getAttr`` at a time is a DG pass per frame -- 11 tracks over a
    4,700-frame production timeline was most of the data-node task's 19 s.
    Maya reads the float into the bool as ``>= 0.5`` (measured on 2025:
    0.4999 is off, 0.5 is on, a spline's negative overshoot is off), so the
    curve path must agree with the plug frame for frame."""

    def _reference(self, plug, first, last):
        out = []
        for frame in range(first, last + 1):
            on = 1.0 if cmds.getAttr(plug, time=frame) else 0.0
            if not out or out[-1][1] != on:
                out.append([float(frame), on])
        return out

    def test_a_linear_ramp_and_a_spline_agree_with_maya(self):
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        linear = cmds.polyCube(name="vis_linear")[0]
        for t, v in ((1, 0.0), (11, 1.0)):
            cmds.setKeyframe(linear, attribute="visibility", time=t, value=v)
        cmds.keyTangent(
            f"{linear}.visibility",
            edit=True,
            inTangentType="linear",
            outTangentType="linear",
        )
        spline = cmds.polyCube(name="vis_spline")[0]
        for t, v in ((1, 0.0), (5, -0.4), (9, 0.4999), (13, 0.5), (17, 1.0)):
            cmds.setKeyframe(spline, attribute="visibility", time=t, value=v)
        cmds.keyTangent(
            f"{spline}.visibility",
            edit=True,
            inTangentType="spline",
            outTangentType="spline",
        )
        for node, last in ((linear, 11), (spline, 17)):
            plug = f"{node}.visibility"
            expected = self._reference(plug, 1, last)
            with patch(
                "maya.cmds.getAttr", side_effect=AssertionError("the plug was read")
            ):
                got = RenderEffects._stepped_track(plug)
            self.assertEqual(got, expected, node)
        self.assertEqual(got[0], [1.0, 0.0])
        self.assertGreater(len(got), 2, "the spline crosses the threshold twice")

    def test_the_curve_walk_is_one_query(self):
        """The visibility / channel walks asked ``listConnections`` once per
        scene curve; a scene mid-export carries thousands."""
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        a = cmds.polyCube(name="walk_a")[0]
        b = cmds.polyCube(name="walk_b")[0]
        # By plug: keying the transform by attribute keys its shape's too.
        cmds.setKeyframe(f"{a}.visibility", time=1, value=1)
        cmds.setKeyframe(f"{b}.translateX", time=1, value=1)
        with patch("maya.cmds.listConnections", wraps=cmds.listConnections) as walk:
            found = RenderEffects._visibility_curves()
        self.assertEqual(walk.call_count, 1)
        self.assertEqual(
            list(found.values()), [f"{cmds.ls(a, long=True)[0]}.visibility"]
        )


if __name__ == "__main__":
    unittest.main()
