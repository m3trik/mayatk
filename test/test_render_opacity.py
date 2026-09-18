# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.render_opacity module

Tests for the non-animating Channels-based implementation.
"""

import unittest
from unittest.mock import patch
import maya.cmds as cmds
from mayatk.mat_utils.render_opacity._render_opacity import RenderOpacity
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
        RenderOpacity.create(objects=[self.cube], mode="attribute")

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
        RenderOpacity.create(objects=[self.cube], mode="attribute")

        anim = cmds.listConnections(self.cube, type="animCurve")
        self.assertFalse(anim, "Attribute mode should not create animation curves")

    def test_remove_deletes_attribute(self):
        """remove(mode='attribute') deletes the attribute."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        self.assertTrue(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True)
        )

        RenderOpacity.remove(objects=[self.cube], mode="attribute")
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
        import json

        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.render_opacity.material_mode import OpacityMaterialMode
        from mayatk.node_utils.data_nodes import DataNodes

        cube = cmds.polyCube(name="legacy_cube")[0]
        skin = cmds.shadingNode("standardSurface", asShader=True, name="Skin")
        MatUtils.assign_mat([cube], skin)
        # The channel first: create() heals legacy leftovers before it adds
        # the attribute, so the duplicate has to be built AFTER it.
        RenderOpacity.create([cube], channel="highlight")
        dup = cmds.shadingNode("standardSurface", asShader=True, name="Skin_Highlight")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="SkinSG_Copy"
        )
        cmds.connectAttr(f"{dup}.outColor", f"{sg}.surfaceShader")
        cmds.sets(cube, edit=True, forceElement=sg)
        cmds.connectAttr(f"{cube}.highlight", f"{dup}.emission", force=True)
        DataNodes.set_internal_string(
            OpacityMaterialMode.BINDINGS_CHANNEL,
            json.dumps(
                {
                    "Skin_Highlight:highlight": {
                        "material": "Skin_Highlight",
                        "object": cube,
                        "channel": "highlight",
                        "restore": {f"{dup}.emission": 0.25},
                    }
                }
            ),
        )
        return cube, skin, dup

    def test_remove_puts_the_object_back_on_its_authored_material(self):
        cube, skin, dup = self._legacy_scene()
        self.assertEqual(_get_assigned_mat(cube), dup)

        RenderOpacity.remove([cube], channel="highlight")

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
        import json

        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.render_opacity.material_mode import OpacityMaterialMode
        from mayatk.node_utils.data_nodes import DataNodes

        cube = cmds.polyCube(name="inplace_cube")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="Own")
        cmds.setAttr(f"{mat}.emission", 0.25)
        MatUtils.assign_mat([cube], mat)
        RenderOpacity.create([cube], channel="highlight")
        cmds.connectAttr(f"{cube}.highlight", f"{mat}.emission", force=True)
        DataNodes.set_internal_string(
            OpacityMaterialMode.BINDINGS_CHANNEL,
            json.dumps(
                {
                    "Own:highlight": {
                        "material": "Own",
                        "object": cube,
                        "channel": "highlight",
                        "restore": {f"{mat}.emission": 0.25},
                    }
                }
            ),
        )
        cmds.setAttr(f"{cube}.highlight", 1.0)
        self.assertEqual(cmds.getAttr(f"{mat}.emission"), 1.0, "driven before")

        RenderOpacity.remove([cube], channel="highlight")

        self.assertEqual(_get_assigned_mat(cube), mat, "never a duplicate to leave")
        self.assertFalse(
            cmds.listConnections(f"{mat}.emission", source=True, destination=False)
        )
        self.assertEqual(cmds.getAttr(f"{mat}.emission"), 0.25, "authored value back")

    def test_the_material_mode_is_the_attribute_mode_now(self):
        """``mode="material"`` (one release) creates the attribute and touches
        no material: the authored one stays assigned and unconnected."""
        from mayatk.mat_utils._mat_utils import MatUtils

        cube = cmds.polyCube(name="plain_cube")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="Plain")
        MatUtils.assign_mat([cube], mat)

        RenderOpacity._preview_warned = False  # the notice is once per session
        with self.assertLogs(RenderOpacity.logger, level="WARNING"):
            RenderOpacity.create([cube], mode="material", channel="highlight")

        self.assertTrue(cmds.attributeQuery("highlight", node=cube, exists=True))
        self.assertEqual(_get_assigned_mat(cube), mat)
        self.assertFalse(
            cmds.listConnections(f"{mat}.emission", source=True, destination=False)
        )
        self.assertEqual(cmds.ls("*_Highlight"), [])


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
        RenderOpacity.create(objects=[self.cube], mode="attribute")

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

        RenderOpacity.create(objects=[self.cube], mode="attribute")

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

        RenderOpacity.create(objects=[self.cube], mode="attribute")
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

        RenderOpacity.create(objects=[self.cube], mode="attribute")
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

        RenderOpacity.create(objects=[self.cube], mode="attribute")
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

        RenderOpacity.create(objects=[self.cube], mode="attribute")
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

        RenderOpacity.create(objects=[self.cube], mode="attribute")
        RenderOpacity.key_fade(objects=[self.cube], start=1, end=15, direction="in")

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
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        RenderOpacity.key_fade(objects=[self.cube], start=1, end=15, direction="in")

        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        before = self._vis_curve_uuid()

        cmds.selectKey(vis_plug, time=(1, 1))
        self.assertEqual(
            cmds.keyframe(vis_plug, q=True, sl=True, tc=True),
            [1.0],
            "pre-condition: one visibility key selected",
        )

        RenderOpacity.ensure_connections([self.cube])

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
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        RenderOpacity.key_fade(objects=[self.cube], start=1, end=15, direction="in")

        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        cmds.keyframe(vis_plug, edit=True, time=(15, 15), timeChange=20)

        RenderOpacity.ensure_connections([self.cube])

        self.assertEqual(
            cmds.keyframe(vis_plug, q=True, tc=True),
            [1.0, 20.0],
            "ensure_connections overwrote a hand-edited visibility curve",
        )

    def test_ensure_connections_mirrors_when_visibility_unkeyed(self):
        """The repair case still works: no visibility keys at all -> mirror."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=15, value=1.0)

        vis_plug = f"{(cmds.ls(self.cube, long=True) or [self.cube])[0]}.visibility"
        self.assertFalse(
            cmds.keyframe(vis_plug, q=True, tc=True),
            "pre-condition: visibility unkeyed",
        )

        RenderOpacity.ensure_connections([self.cube])

        self.assertEqual(cmds.keyframe(vis_plug, q=True, tc=True), [1.0, 15.0])

    def test_remove_restores_visibility(self):
        """Removing opacity should reset visibility to True with no drivers."""
        RenderOpacity.create(objects=[self.cube], mode="attribute")

        RenderOpacity.remove(objects=[self.cube], mode="attribute")

        self.assertTrue(
            cmds.getAttr(f"{self.cube}.visibility"), "Visibility should reset to True"
        )
        vis_inputs = cmds.listConnections(f"{self.cube}.visibility", source=True)
        self.assertFalse(
            vis_inputs,
            "Visibility should not be driven after remove",
        )

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
        RenderOpacity.create(objects=[self.cube], mode="attribute")

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

        RenderOpacity.create(objects=[self.cube], mode="attribute")

        # Foreign condition should still be there
        inputs = cmds.listConnections(f"{self.cube}.visibility", source=True)
        self.assertTrue(inputs, "Visibility should still have a driver")
        self.assertEqual(
            inputs[0],
            "foreign_cond",
            "Foreign condition should not have been replaced",
        )

        # Cleanup
        RenderOpacity.remove(objects=[self.cube], mode="attribute")
        if cmds.objExists(foreign):
            cmds.delete(foreign)

    def test_remove_handles_locked_visibility(self):
        """remove() must not crash when visibility is locked.

        Bug: visibility.set(True) threw RuntimeError when attr was locked.
        Fixed: 2026-02-20
        """
        RenderOpacity.create(objects=[self.cube], mode="attribute")

        # Lock visibility between create and remove
        cmds.setAttr(f"{self.cube}.visibility", lock=True)

        # Should not raise
        RenderOpacity.remove(objects=[self.cube], mode="attribute")

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
        RenderOpacity.create(objects=[cube], mode="attribute")
        for frame, value in ((1, 0.0), (30, 1.0), (60, 0.0)):
            cmds.setKeyframe(cube, attribute="opacity", time=frame, value=value)
        vis_plug = f"{cmds.ls(str(cube), l=True)[0]}.visibility"

        try:
            self.assertEqual(RenderOpacity.prepare_for_export(objects=[cube]), [])
            self.assertEqual(cmds.keyframe(vis_plug, q=True, keyframeCount=True), 0)
            self.assertEqual(
                len(cmds.ls("manual_keyed_cube__opacity")), 1, "the proxy is staged"
            )
        finally:
            RenderOpacity.finish_export()
        self.assertEqual(cmds.ls("manual_keyed_cube__opacity"), [], "and removed after")

    def test_preserves_manual_visibility_keys(self):
        """Authored visibility, however many keys, is left exactly as authored."""
        cube = cmds.polyCube(name="manual_vis_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")
        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=100, value=1.0)
        # Long-name plug path: attribute= by kwarg also keys the shape.
        vis_plug = f"{cmds.ls(str(cube), l=True)[0]}.visibility"
        for t, v in [(1, 0), (25, 1), (50, 0), (100, 1)]:
            cmds.setKeyframe(vis_plug, time=t, value=v)

        try:
            self.assertEqual(RenderOpacity.prepare_for_export(objects=[cube]), [])
        finally:
            RenderOpacity.finish_export()
        self.assertEqual(
            sorted(set(cmds.keyframe(vis_plug, q=True, tc=True))), [1, 25, 50, 100]
        )

    def test_prepare_after_key_fade_is_noop(self):
        """key_fade's authoring mirror survives prepare_for_export untouched
        (the canonical happy path)."""
        cube = cmds.polyCube(name="key_fade_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")
        RenderOpacity.key_fade(objects=[cube], start=1, end=30, direction="in")

        vis_before = cmds.keyframe(cube, attribute="visibility", q=True, tc=True)
        synced = RenderOpacity.prepare_for_export(objects=[cube])
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

        synced = RenderOpacity.prepare_for_export(objects=[loc])

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

    def test_object_without_opacity_attr_silently_skipped(self):
        """Plain objects (no opacity attr) must not trigger errors when
        passed to prepare_for_export — common case during scene-wide
        operations that pass mixed selections."""
        plain = cmds.polyCube(name="plain_cube")[0]
        opacity_obj = cmds.polyCube(name="opacity_cube")[0]
        RenderOpacity.create(objects=[opacity_obj], mode="attribute")
        cmds.setKeyframe(opacity_obj, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(opacity_obj, attribute="opacity", time=30, value=1.0)

        synced = RenderOpacity.prepare_for_export(objects=[plain, opacity_obj])

        self.assertEqual(synced, [])
        self.assertFalse(cmds.attributeQuery("opacity", node=str(plain), exists=True))


class TestRenderEffectsSlots(MayaTkTestCase):
    """The panel's two key tools create their channel on demand and each
    option box carries a remove action -- there is no separate Create /
    Manage section any more.

    Regression kept from 2026-05-07: the slot wrappers must accept the plain
    string node names ``cmds.ls(selection=True)`` returns (a PyMEL ``.name()``
    idiom once raised here).
    """

    def setUp(self):
        super().setUp()
        from mayatk.mat_utils.render_opacity import render_effects_slots as res
        from unittest.mock import MagicMock

        self.res = res
        self.cube = cmds.polyCube(name="slot_cube")[0]
        cmds.select(self.cube, replace=True)

        self.slot = res.RenderEffectsSlots.__new__(res.RenderEffectsSlots)
        self.slot.ui = MagicMock()
        self.slot.ui.header.menu.chk_last_selected.isChecked.return_value = False
        self.slot.ui.header.menu.chk_delete_vis_keys.isChecked.return_value = False
        self.slot.sb = MagicMock()
        self.slot._sel_token = None
        self.slot._pulse_menu = None
        self.slot._remove_actions = {}
        self.slot._mode_menus = {}
        self.slot._mode_fields = {}
        self.slot._mode_hooks = {}
        # The colour row is the tool's only colour editor now, so a slot under
        # test needs one. Stubbed rather than built: what belongs here is the
        # slot's READING of it, and the widget has its own suite in uitk.
        self.slot._pulse_ramp = MagicMock()
        self.slot._pulse_ramp.decided.return_value = ((0.2, 0.5, 1.0), (0.0, 0.0, 0.0))
        self.slot._on_selection_changed = MagicMock()

    def _set_mode(self, mode):
        """Put both option boxes in *mode*, without building either.

        The layout IS the mode now, so this states it where the tool reads
        it rather than stubbing a combo box the tool no longer asks.
        """
        from uitk import FieldVisibility

        for channel in ("opacity", "highlight"):
            fields = self.slot._mode_fields.setdefault(channel, FieldVisibility())
            fields.define(mode, ())
            fields.mode = mode

    def _fade_widget(self, frames=10, ends_at_cursor=False, direction="in"):
        from unittest.mock import MagicMock

        widget = MagicMock()
        widget.option_box.menu.s000.value.return_value = frames
        widget.option_box.menu.chk000.isChecked.return_value = ends_at_cursor
        widget.option_box.menu.cmb_direction.currentData.return_value = direction
        return widget

    def _pulse_widget(self, seconds=4.0, period=2.0, duty=50, gaps=(0.72, 0.72)):
        """The pulse box asks for SECONDS now -- every field in it does."""
        from unittest.mock import MagicMock

        widget = MagicMock()
        widget.option_box.menu.s001.value.return_value = seconds
        widget.option_box.menu.s002.value.return_value = period
        widget.option_box.menu.s003.value.return_value = duty
        widget.option_box.menu.s004.value.return_value = gaps[0]
        widget.option_box.menu.s005.value.return_value = gaps[1]
        widget.option_box.menu.chk001.isChecked.return_value = False
        return widget

    def test_option_box_init_registers_a_remove_action_per_tool(self):
        """Live-Maya regression (2026-09-05): the actions were keyed by the
        ChannelSpec, a frozen dataclass holding a dict -- unhashable -- so
        both option-box inits raised and the pulse tool never wired."""
        from unittest.mock import MagicMock

        fade, pulse = MagicMock(), MagicMock()
        self.slot.tb000_init(fade)
        self.slot.tb001_init(pulse)

        self.assertEqual(set(self.slot._remove_actions), {"opacity", "highlight"})
        for widget in (fade, pulse):
            kwargs = widget.option_box.set_action.call_args.kwargs
            self.assertEqual(kwargs["icon"], "circle_remove")

    def test_option_box_init_ends_each_box_with_a_preview_button(self):
        """A button INSIDE the box, last, rather than an eye action beside the
        remove one: the eye read as "show me the object's effect", and what
        this shows is the box's settings."""
        from unittest.mock import MagicMock

        for init in (self.slot.tb000_init, self.slot.tb001_init):
            widget = MagicMock()
            init(widget)
            calls = widget.option_box.menu.add.call_args_list
            buttons = [
                c.kwargs
                for c in calls
                if c.kwargs.get("setObjectName") == "btn_preview"
            ]
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0]["setText"], "Preview in WebXR")
            self.assertIs(calls[-1].kwargs, buttons[0], "under every field it reads")
            widget.option_box.add_action.assert_not_called()

    def _push_preview(self, spec, applied=True):
        """Run the preview action with the bridge stubbed; return its ``push``.

        *applied* is what the stubbed result says landed: the overlay's channel
        (a bridge that honoured the knob) or nothing (one that predates it).
        """
        from unittest.mock import patch

        import pythontk as ptk
        import mayatk as mtk

        with patch.object(mtk, "WebXrPreview") as bridge:
            bridge.return_value.push.return_value = {
                "version": 3,
                "url": "http://127.0.0.1:0/",
                "data_export": (
                    [
                        ptk.MeshConvert.VISIBILITY_TRACKS_KEY,
                        ptk.MeshConvert.FBX_TAKES_KEY,
                    ]
                    if applied
                    else []
                ),
            }
            self.slot._preview_webxr(spec)
        return bridge.return_value.push

    def test_the_webxr_preview_pushes_the_fade_as_set_and_writes_nothing(self):
        import pythontk as ptk
        import mayatk as mtk

        self.slot._fade_menu = self._fade_widget(
            frames=15, direction="auto"
        ).option_box.menu
        push = self._push_preview(self.res.OPACITY)

        self.slot.sb.message_box.assert_not_called()
        push.assert_called_once()
        kwargs = push.call_args.kwargs
        self.assertEqual(kwargs["objects"], [self.cube])
        overlay = kwargs["data_export"]
        tracks = overlay[ptk.MeshConvert.VISIBILITY_TRACKS_KEY]["tracks"]
        self.assertEqual([t["node"] for t in tracks], [self.cube])
        fps = float(mtk.AudioUtils.get_fps() or 30.0)
        expected = ptk.RampKeys.fade_loop(
            15, hold=self.slot.PREVIEW_HOLD_SECONDS * fps, direction="auto"
        )
        self.assertEqual(tracks[0]["opacity"], [[f, v] for f, v in expected])
        self.assertIsNone(overlay[ptk.MeshConvert.FBX_TAKES_KEY], "no shot cuts it")
        self.assertFalse(
            cmds.attributeQuery("opacity", node=self.cube, exists=True),
            "nothing created",
        )
        self.assertFalse(cmds.keyframe(self.cube, q=True, kc=True), "nothing keyed")

    def test_the_webxr_preview_shows_the_pulse_alone_over_an_existing_effect(self):
        """The preview has nothing to do with existing keys or effects: an object
        that already fades previews the pulse as the box is set, with no trace of
        its fade in the push, and its fade keys are exactly as they were."""
        import pythontk as ptk
        import mayatk as mtk

        mtk.RenderEffects.key_fade([self.cube], start=1, end=15)
        before = cmds.keyframe(f"{self.cube}.opacity", q=True, kc=True)
        self.slot._pulse_menu = self._pulse_widget(
            seconds=4.0, period=2.0, duty=50, gaps=(0.5, 0.25)
        ).option_box.menu
        self.slot._pulse_ramp.decided.return_value = ((1.0, 0.0, 0.0), None)
        push = self._push_preview(self.res.HIGHLIGHT)

        self.slot.sb.message_box.assert_not_called()
        overlay = push.call_args.kwargs["data_export"]
        (track,) = overlay[ptk.MeshConvert.VISIBILITY_TRACKS_KEY]["tracks"]
        fps = float(mtk.AudioUtils.get_fps() or 30.0)
        expected = ptk.RampKeys.pulse(
            0.0,
            4.0 * fps,
            period=2.0 * fps,
            bright_fraction=0.5,
            lead_in=0.5 * fps,
            lead_out=0.25 * fps,
        )
        self.assertEqual(track["highlight"], [[f, v] for f, v in expected])
        self.assertEqual(track["highlight_color"], [1.0, 0.0, 0.0])
        self.assertEqual(track["highlight_color_dim"], list(self.slot.DEFAULT_DIM))
        self.assertNotIn("opacity", track, "the object's own fade is left out")
        self.assertFalse(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.assertEqual(cmds.keyframe(f"{self.cube}.opacity", q=True, kc=True), before)

    def test_the_webxr_preview_needs_a_selection(self):
        cmds.select(clear=True)
        push = self._push_preview(self.res.OPACITY)
        push.assert_not_called()
        self.slot.sb.message_box.assert_called_once()

    def test_a_push_that_dropped_the_overlay_is_reported_not_claimed(self):
        """Live report (2026-09-13): every push looked the same whatever the
        box said. A bridge that predates the overlay knob sweeps it into the
        export bag and publishes the scene as it stands, with no error
        anywhere -- so the slot reads the result's ``data_export`` and says so
        rather than announcing a preview of settings the page never got."""
        self.slot._fade_menu = self._fade_widget().option_box.menu
        push = self._push_preview(self.res.OPACITY, applied=False)

        push.assert_called_once()
        self.slot.sb.message_box.assert_called_once()
        self.assertIn(
            "without the opacity overlay", self.slot.sb.message_box.call_args.args[0]
        )
        self.assertIn(
            "not the opacity settings", self.slot.ui.footer.setText.call_args.args[0]
        )

    def test_key_fade_creates_the_opacity_channel_on_demand(self):
        self.slot.tb000(self._fade_widget())

        self.assertTrue(cmds.attributeQuery("opacity", node=self.cube, exists=True))
        self.assertEqual(cmds.keyframe(f"{self.cube}.opacity", q=True, kc=True), 2)
        self.slot.sb.message_box.assert_not_called()

    def test_the_pulse_option_box_gaps_reach_the_keys(self):
        """The two gap fields are seconds; the slot hands them to the writer in
        frames. A hard cut on one side and a one-second lead on the other are
        both readable straight off the curve."""
        import mayatk as mtk

        fps = float(mtk.AudioUtils.get_fps() or 30.0)
        cmds.currentTime(10)
        self.slot.tb001(
            self._pulse_widget(seconds=200 / 30.0, period=2.0, gaps=(1.0, 0.0))
        )
        plug = f"{self.cube}.highlight"
        keys = list(
            zip(
                cmds.keyframe(plug, q=True, tc=True),
                cmds.keyframe(plug, q=True, vc=True),
            )
        )
        self.assertEqual(keys[0], (10.0, 0.0), "opens dim at the playhead")
        self.assertEqual(keys[1], (10.0 + fps, 1.0), "one second up")
        self.assertEqual(keys[-1][1], 0.0, "ends dim")
        self.assertEqual(
            keys[-1][0] - keys[-2][0], 1.0, "a hard cut on the way out: one frame"
        )

    def test_key_pulse_creates_the_highlight_channel_on_demand(self):
        self.slot._pulse_ramp.decided.return_value = ((1.0, 0.0, 0.0), None)
        self.slot.tb001(self._pulse_widget())

        self.assertTrue(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.assertGreater(cmds.keyframe(f"{self.cube}.highlight", q=True, kc=True), 4)
        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [1.0, 0.0, 0.0],
        )
        self.slot.sb.message_box.assert_not_called()

    def test_the_pulse_option_box_carries_one_colour_editor(self):
        """Regression: the tool had TWO colour sections -- a compact row in the
        option box and a fuller window behind an icon -- which disagreed about
        what they could show. The box is the only one now, and the window's
        launcher is gone with it."""
        from unittest.mock import MagicMock

        pulse = MagicMock()
        self.slot.tb001_init(pulse)

        icons = [
            c.kwargs.get("icon") for c in pulse.option_box.set_action.call_args_list
        ]
        self.assertNotIn("theme", icons, "the retired colour window still has a door")
        self.assertIn("circle_remove", icons, "the remove action was dropped")

    def test_the_header_offers_no_second_colour_entry_point(self):
        """The header button opened a window that no longer exists."""
        from unittest.mock import MagicMock

        header = MagicMock()
        self.slot.header_init(header)

        names = [c.kwargs.get("setObjectName") for c in header.menu.add.call_args_list]
        self.assertNotIn("b_highlight_color", names)

    def test_each_option_box_opens_on_create(self):
        """Create is the safe default: it acts on what is selected. Revise
        reaches objects the artist may not have selected, so it is chosen."""
        from unittest.mock import MagicMock

        for init in (self.slot.tb000_init, self.slot.tb001_init):
            widget = MagicMock()
            init(widget)
            # ``_add_mode`` keeps the combo ``menu.add`` returned, and every
            # other ``add`` on this mock menu returns that SAME object -- so
            # the fade box's direction items land on the same call list. The
            # mode row is added first, so its two are the first two.
            combo = widget.option_box.menu.add.return_value
            self.assertEqual(
                [c.args[1] for c in combo.addItem.call_args_list][:2],
                [self.res.CREATE, self.res.REVISE],
                "Create must be the first item, and so the opening one",
            )

    def test_revise_hides_the_fields_it_cannot_write(self):
        """A box offering to re-time a signed-off pulse under the word
        'revise' would be offering to re-key it."""
        from unittest.mock import MagicMock

        pulse = MagicMock()
        self.slot.tb001_init(pulse)
        self.addCleanup(self.slot._pulse_ramp.deleteLater)
        fields = self.slot._mode_fields["highlight"]

        fields.mode = self.res.REVISE
        self.assertNotIn("s001", fields.visible, "the cadence lives in keys")
        self.assertIn("pulse_colors", fields.visible)

        fields.mode = self.res.CREATE
        self.assertIn("s001", fields.visible, "Create keys, so it may re-time")

    def test_the_fade_box_hides_nothing_in_either_mode(self):
        """A fade IS its keys: there is no part of it Revise could restate
        without re-keying, so the mode narrows the target and hides nothing."""
        from unittest.mock import MagicMock

        widget = MagicMock()
        self.slot.tb000_init(widget)
        self.addCleanup(self.slot._fade_preview.deleteLater)
        fields = self.slot._mode_fields["opacity"]

        for mode in (self.res.CREATE, self.res.REVISE):
            fields.mode = mode
            self.assertEqual(
                fields.keys, (), f"{mode} gates a field the fade always needs"
            )

    def test_the_fade_box_previews_the_exporters_own_alpha(self):
        """The fade has no colour to choose, so its length and direction are
        the only things there are to get wrong -- and the preview runs the
        function that writes the deliverable, not a lookalike of it."""
        from unittest.mock import MagicMock
        from pythontk.file_utils.mesh_convert.glb_fades import CHANNELS as GLTF

        widget = MagicMock()
        self.slot.tb000_init(widget)
        preview = self.slot._fade_preview
        self.addCleanup(preview.deleteLater)

        self.assertEqual(preview._values, GLTF["opacity"].values)
        self.assertEqual(len(preview.composite(0.5)), 4, "alpha rides the fourth lane")

    def test_the_fade_preview_tracks_the_box(self):
        """A preview animating at some other length than the one being keyed
        gives away the only thing it is there to show."""
        from unittest.mock import MagicMock

        widget = MagicMock()
        self.slot.tb000_init(widget)
        self.addCleanup(self.slot._fade_preview.deleteLater)
        menu = self.slot._fade_menu
        import mayatk as mtk

        fps = float(mtk.AudioUtils.get_fps() or 30.0)

        menu.s000.value.return_value = 60
        menu.cmb_direction.currentData.return_value = "out"
        self.slot._sync_fade_shape()

        shape = self.slot._fade_preview.shape
        self.assertAlmostEqual(shape["duration"], 60 / fps, places=5)
        self.assertEqual(shape["direction"], "out")

    def test_revise_re_keys_only_objects_that_already_fade(self):
        """Opacity's Revise rewrites keys, which is the whole of what a fade
        is -- but it must not give the channel to anything new."""
        plain = cmds.polyCube(name="slot_never_faded")[0]
        RenderOpacity.create([self.cube], mode="attribute", channel="opacity")
        cmds.select([self.cube, plain], replace=True)
        self._set_mode(self.res.REVISE)

        self.slot.tb000(self._fade_widget(frames=20))

        self.assertGreater(cmds.keyframe(f"{self.cube}.opacity", q=True, kc=True), 0)
        self.assertFalse(
            cmds.attributeQuery("opacity", node=plain, exists=True),
            "Revise must not spread the channel to an object that lacked it",
        )

    def test_the_remove_action_is_gated_on_what_it_would_act_on(self):
        """Pre-existing: the action was gated on the RAW selection while
        ``_remove_channel`` operates on the Last-Selected-Only subset, so
        picking a highlighted object and then a plain one left the action live
        over a target with nothing to remove."""
        from unittest.mock import MagicMock

        plain = cmds.polyCube(name="slot_gate_plain")[0]
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select([self.cube, plain], replace=True)
        self.slot.ui.header.menu.chk_last_selected.isChecked.return_value = True
        action = MagicMock()
        self.slot._remove_actions = {"highlight": action}
        self.slot.ui.isVisible.return_value = True
        del self.slot._on_selection_changed  # use the real one, not the stub

        self.slot._on_selection_changed()

        action.widget.setEnabled.assert_called_with(False)

    def test_the_selection_is_read_once_per_change(self):
        """Four consumers asked the same question separately, so picking
        objects cost a scene read and a per-object query four times over."""
        from unittest.mock import MagicMock

        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select(self.cube, replace=True)
        self.slot._remove_actions = {"highlight": MagicMock()}
        self.slot._mode_menus = {"highlight": MagicMock()}
        self.slot.ui.isVisible.return_value = True
        del self.slot._on_selection_changed

        reads = []
        real = self.slot._get_selected
        self.slot._get_selected = lambda: (reads.append(1), real())[1]
        self.slot._on_selection_changed()

        self.assertEqual(len(reads), 1, "the selection was read more than once")

    def test_the_readout_says_what_apply_will_do(self):
        """The tool button's label cannot say WHO it is about to act on, and
        that is the whole difference between the two modes."""
        from unittest.mock import MagicMock

        menu = MagicMock()
        self.slot._mode_menus["highlight"] = menu
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        plain = cmds.polyCube(name="slot_plain")[0]
        cmds.select([self.cube, plain], replace=True)

        self._set_mode(self.res.CREATE)
        self.slot._update_apply_readout(self.res.HIGHLIGHT)
        self.assertIn("2 selected", menu.lbl_apply.setText.call_args.args[0])

        self._set_mode(self.res.REVISE)
        self.slot._update_apply_readout(self.res.HIGHLIGHT)
        said = menu.lbl_apply.setText.call_args.args[0]
        self.assertIn("1 of 2 selected", said, "only one of them carries it")
        self.assertIn("keys untouched", said)

    def test_the_fade_readout_admits_that_revise_re_keys(self):
        """Opacity's Revise rewrites keys and the highlight's does not. The
        difference is invisible until it has cost something."""
        from unittest.mock import MagicMock

        menu = MagicMock()
        self.slot._mode_menus["opacity"] = menu
        self._set_mode(self.res.REVISE)
        RenderOpacity.create([self.cube], mode="attribute", channel="opacity")
        cmds.select(self.cube, replace=True)

        self.slot._update_apply_readout(self.res.OPACITY)

        self.assertIn("keys are rewritten", menu.lbl_apply.setText.call_args.args[0])

    def test_revise_reaches_only_objects_that_already_carry_the_channel(self):
        """The point of the mode: change this, do not spread it."""
        plain = cmds.polyCube(name="slot_unkeyed")[0]
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select([self.cube, plain], replace=True)
        self._set_mode(self.res.REVISE)

        self.assertEqual(self.slot._targets(self.res.HIGHLIGHT), [self.cube])

    def test_create_reaches_the_whole_selection(self):
        plain = cmds.polyCube(name="slot_unkeyed2")[0]
        cmds.select([self.cube, plain], replace=True)
        self._set_mode(self.res.CREATE)

        self.assertEqual(
            sorted(self.slot._targets(self.res.HIGHLIGHT)), sorted([self.cube, plain])
        )

    def test_revise_seeds_the_row_from_what_is_authored(self):
        """Seeding from the authored value is what makes this a revision
        rather than a guess -- and the same read decides the before/after."""
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(0.02, 0.17, 0.43))
        cmds.select(self.cube, replace=True)
        self._set_mode(self.res.REVISE)

        self.slot._sync_highlight_mode()

        seeded = self.slot._pulse_ramp.set_colors.call_args.args[0]
        self.assertEqual([round(c, 2) for c in seeded[0]], [0.02, 0.17, 0.43])
        reference = self.slot._pulse_ramp.set_reference.call_args.args[0]
        self.assertIsNotNone(reference, "one agreed look is a look to hold up")

    def test_a_disagreement_shows_no_before(self):
        """Picking one of them to show would be a lie about what is there."""
        other = cmds.polyCube(name="slot_other3")[0]
        for node in (self.cube, other):
            RenderOpacity.create([node], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(1.0, 0.0, 0.0))
        RenderOpacity.set_channel_color([other], color=(0.0, 1.0, 0.0))
        cmds.select([self.cube, other], replace=True)
        self._set_mode(self.res.REVISE)

        self.slot._sync_highlight_mode()

        self.assertIsNone(self.slot._pulse_ramp.set_reference.call_args.args[0])

    def test_switching_into_revise_reads_the_scene_when_nothing_is_selected(self):
        """The hole this closes: with no selection the row showed colours
        nobody had read off these objects, and Apply -- whose scope here is
        every highlighted object -- would have written them over all of them."""
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(0.02, 0.17, 0.43))
        cmds.select(clear=True)
        self._set_mode(self.res.REVISE)

        self.slot._sync_highlight_mode(deep=True)

        seeded = self.slot._pulse_ramp.set_colors.call_args.args[0]
        self.assertEqual([round(c, 2) for c in seeded[0]], [0.02, 0.17, 0.43])

    def test_the_selection_path_never_scans_the_scene(self):
        """That scan is one pass over every object; on the selection-changed
        signal it would make picking objects cost a pass per click."""
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select(clear=True)
        self._set_mode(self.res.REVISE)

        self.slot._sync_highlight_mode()  # shallow: what the signal does

        self.slot._pulse_ramp.set_colors.assert_not_called()

    def test_create_does_not_reseed_the_row(self):
        """Those colours are what the next pulse will be keyed with. An artist
        who picked one must not have it replaced by clicking an object."""
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(1.0, 0.0, 0.0))
        cmds.select(self.cube, replace=True)
        self._set_mode(self.res.CREATE)
        self.slot._pulse_ramp.editors = ()

        self.slot._sync_highlight_mode()

        self.slot._pulse_ramp.set_colors.assert_not_called()
        self.assertIsNone(self.slot._pulse_ramp.set_reference.call_args.args[0])

    def test_revise_writes_the_selection(self):
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select(self.cube, replace=True)
        self._set_mode(self.res.REVISE)
        self.slot._pulse_ramp.decided.return_value = ((0.02, 0.17, 0.43), None)

        self.slot.tb001(self._pulse_widget())

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.02, 0.17, 0.43],
        )
        # A selection is unambiguous: it must not ask.
        self.slot.sb.message_box.assert_not_called()

    def test_revise_confirms_before_the_whole_scene(self):
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select(clear=True)
        self._set_mode(self.res.REVISE)
        self.slot._pulse_ramp.decided.return_value = ((1.0, 0.0, 0.0), None)
        self.slot.sb.message_box.return_value = "No"

        self.slot.tb001(self._pulse_widget())

        self.assertEqual(
            [round(c, 6) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.0, 0.088656, 0.723055],
            "declining the confirm must write nothing",
        )

        self.slot.sb.message_box.return_value = "Yes"
        self.slot.tb001(self._pulse_widget())

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [1.0, 0.0, 0.0],
        )

    def test_the_editor_is_seeded_from_the_authored_colour(self):
        """Opening on the last PICK rather than what the objects carry would make
        this a guess; the revision has to start from the authored value."""
        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(0.02, 0.17, 0.43))
        RenderOpacity.set_channel_color([self.cube], color=(0.5, 0.0, 0.0), stop="lo")

        (bright, dim), mixed = self.slot._authored_stops([self.cube])

        self.assertEqual([round(c, 2) for c in bright], [0.02, 0.17, 0.43])
        self.assertEqual([round(c, 2) for c in dim], [0.5, 0.0, 0.0])
        self.assertEqual(mixed, (False, False))

    def test_a_selection_that_disagrees_reports_mixed(self):
        """The old reader took ``next(iter(...))``, so a multi-object edit
        showed ONE object's colour and the first drag wrote it to all of them."""
        other = cmds.polyCube(name="slot_other")[0]
        for node in (self.cube, other):
            RenderOpacity.create([node], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(1.0, 0.0, 0.0))
        RenderOpacity.set_channel_color([other], color=(0.0, 1.0, 0.0))

        _seeds, mixed = self.slot._authored_stops([self.cube, other])

        self.assertTrue(mixed[0], "the bright end disagrees and must say so")
        self.assertFalse(mixed[1], "both dim ends are still the seeded black")

    def test_an_end_left_mixed_is_not_written(self):
        """An artist who only touched Bright must not flatten every Dim."""
        other = cmds.polyCube(name="slot_other2")[0]
        for node in (self.cube, other):
            RenderOpacity.create([node], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(0.1, 0.1, 0.1), stop="lo")
        cmds.select([self.cube, other], replace=True)
        self._set_mode(self.res.REVISE)
        self.slot._pulse_ramp.decided.return_value = ((1.0, 0.0, 0.0), None)

        self.slot.tb001(self._pulse_widget())

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColorDim")[0]],
            [0.1, 0.1, 0.1],
            "an unstated end must be left exactly as authored",
        )

    def test_the_colour_row_writes_nothing_on_its_own(self):
        """An editor that wrote as it was dragged is what made setting a look
        and changing one feel like two different acts -- and it wrote to
        whatever happened to be selected while the artist was only picking a
        colour for the NEXT pulse. The tool button is the only writer now."""
        from unittest.mock import MagicMock
        from qtpy import QtWidgets

        if QtWidgets.QApplication.instance() is None:
            self.skipTest("the colour row is a real widget; no QApplication here")

        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(0.9, 0.1, 0.1))
        cmds.select(self.cube, replace=True)

        pulse = MagicMock()
        self.slot.tb001_init(pulse)
        ramp = self.slot._pulse_ramp
        self.addCleanup(ramp.deleteLater)

        # Act on the editor the way an artist does, rather than reading its
        # wiring: what matters is that the scene does not move.
        ramp.editor(0).color = (0.0, 0.0, 1.0)

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.9, 0.1, 0.1],
            "the box stages a value; only the tool button writes",
        )

    def test_revise_with_nothing_decided_writes_nothing(self):
        """Both ends mixed means the artist decided nothing; writing would
        flatten a disagreement into whatever the row happened to show."""
        other = cmds.polyCube(name="slot_other4")[0]
        for node in (self.cube, other):
            RenderOpacity.create([node], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(1.0, 0.0, 0.0))
        RenderOpacity.set_channel_color([other], color=(0.0, 1.0, 0.0))
        cmds.select([self.cube, other], replace=True)
        self._set_mode(self.res.REVISE)
        self.slot._pulse_ramp.decided.return_value = (None, None)

        self.slot.tb001(self._pulse_widget())

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [1.0, 0.0, 0.0],
            "an undecided revision must write nothing",
        )
        self.slot.sb.message_box.assert_called()

    def test_the_cycle_readout_names_a_cut_tail(self):
        """A length that is not a whole multiple of the period ends mid-cycle.
        Nothing is wrong with that; it is invisible without a number."""
        from unittest.mock import MagicMock

        self.slot._cycle_readout = MagicMock()
        self.slot._pulse_menu = MagicMock()
        self.slot._pulse_menu.s001.value.return_value = 4.0
        self.slot._pulse_menu.s002.value.return_value = 2.0
        self.slot._update_cycle_readout()
        self.assertNotIn("cut", self.slot._cycle_readout.setText.call_args.args[0])

        self.slot._pulse_menu.s001.value.return_value = 5.0
        self.slot._update_cycle_readout()
        self.assertIn("cut", self.slot._cycle_readout.setText.call_args.args[0])

    def test_remove_action_strips_one_channel_and_leaves_the_other(self):
        self.slot.tb000(self._fade_widget())
        self.slot.tb001(self._pulse_widget())

        self.slot._remove_channel(self.res.OPACITY)

        self.assertFalse(cmds.attributeQuery("opacity", node=self.cube, exists=True))
        self.assertTrue(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.slot.sb.message_box.assert_not_called()


class TestHighlightChannel(MayaTkTestCase):
    """The second channel on the same transport: create, pulse, remove."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="hl_cube")[0]

    def test_create_adds_intensity_and_colour_attributes(self):
        RenderOpacity.create(objects=[self.cube], mode="attribute", channel="highlight")
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

    def test_the_panel_seeds_the_colour_create_actually_writes(self):
        """Two files state this colour: the attribute preset Create authors
        from, and the panel's seed -- what the row shows before anything is
        picked. Drift between them shows one colour and writes another, and
        nothing else would catch it."""
        from mayatk.mat_utils.render_opacity import render_effects_slots as res

        RenderOpacity.create(objects=[self.cube], mode="attribute", channel="highlight")

        for attr, seed in (
            ("highlightColor", res.RenderEffectsSlots.DEFAULT_BRIGHT),
            ("highlightColorDim", res.RenderEffectsSlots.DEFAULT_DIM),
        ):
            self.assertEqual(
                [round(c, 6) for c in cmds.getAttr(f"{self.cube}.{attr}")[0]],
                [round(float(c), 6) for c in seed],
                attr,
            )

    def test_key_pulse_writes_linear_holds_and_ramps_and_the_colour(self):
        """Four linear keys per cycle -- the published ramp is read linearly."""
        keyed = RenderOpacity.key_pulse(
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
        RenderOpacity.key_pulse(
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
        RenderOpacity.key_pulse([self.cube], start=10, end=400, **kwargs)
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
        RenderOpacity.key_pulse(
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
        RenderOpacity.key_pulse(
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
        RenderOpacity.key_pulse(
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
        RenderOpacity.key_fade([self.cube], start=0, end=10)
        self.assertEqual(cmds.ls(selection=True), [self.cube])
        RenderOpacity.key_pulse([self.cube], start=0, end=100, period=50)
        self.assertEqual(cmds.ls(selection=True), [self.cube])

    def test_key_fade_can_clear_visibility_keys_before_creating(self):
        cmds.setKeyframe(f"{self.cube}.visibility", time=5, value=0)
        cmds.setKeyframe(f"{self.cube}.visibility", time=50, value=1)

        RenderOpacity.key_fade(
            [self.cube], start=10, end=20, direction="in", delete_visibility_keys=True
        )

        vis_times = cmds.keyframe(f"{self.cube}.visibility", q=True, tc=True)
        self.assertEqual(
            vis_times, [10.0, 20.0], "old vis keys cleared, mirror written"
        )

    def test_a_pulse_does_not_touch_visibility(self):
        RenderOpacity.key_pulse([self.cube], start=0, end=100, period=50)
        self.assertFalse(cmds.keyframe(f"{self.cube}.visibility", q=True, kc=True))

    def test_remove_one_channel_leaves_the_other(self):
        RenderOpacity.create([self.cube], channel="opacity")
        RenderOpacity.create([self.cube], channel="highlight")
        RenderOpacity.remove([self.cube], channel="highlight")
        self.assertFalse(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.assertFalse(
            cmds.attributeQuery("highlightColor", node=self.cube, exists=True)
        )
        self.assertTrue(cmds.attributeQuery("opacity", node=self.cube, exists=True))


class TestChannelColourRevision(MayaTkTestCase):
    """Re-colouring an authored highlight after the pulses were keyed.

    The colour is its own attribute rather than part of the curve, which is
    what lets a signed-off look change without a re-key.
    """

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="revise_cube")[0]
        self.plain = cmds.polyCube(name="plain_cube")[0]
        RenderOpacity.create(objects=[self.cube], mode="attribute", channel="highlight")

    def test_set_channel_color_writes_the_named_objects(self):
        written = RenderOpacity.set_channel_color([self.cube], color=(0.045, 0.39, 1.0))
        self.assertEqual(written, ["revise_cube"])
        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.045, 0.39, 1.0],
        )

    def test_set_channel_color_leaves_the_keys_alone(self):
        RenderOpacity.key_pulse([self.cube], start=0, end=100)
        before = cmds.keyframe(
            f"{self.cube}.highlight", q=True, timeChange=True, valueChange=True
        )
        RenderOpacity.set_channel_color([self.cube], color=(1.0, 0.0, 0.0))
        after = cmds.keyframe(
            f"{self.cube}.highlight", q=True, timeChange=True, valueChange=True
        )
        self.assertEqual(before, after, "a recolour must not touch the pulse")

    def test_objects_without_the_channel_are_skipped(self):
        written = RenderOpacity.set_channel_color(
            [self.cube, self.plain], color=(1.0, 0.0, 0.0)
        )
        self.assertEqual(written, ["revise_cube"])
        self.assertFalse(
            cmds.attributeQuery("highlightColor", node=self.plain, exists=True)
        )

    def test_empty_selection_falls_back_to_every_object_with_the_channel(self):
        other = cmds.polyCube(name="revise_other")[0]
        RenderOpacity.create(objects=[other], mode="attribute", channel="highlight")
        cmds.select(clear=True)

        written = RenderOpacity.set_channel_color(color=(0.045, 0.39, 1.0))

        self.assertEqual(sorted(written), ["revise_cube", "revise_other"])
        for node in (self.cube, other):
            self.assertEqual(
                [round(c, 3) for c in cmds.getAttr(f"{node}.highlightColor")[0]],
                [0.045, 0.39, 1.0],
            )

    def test_objects_with_channel_finds_only_the_carriers(self):
        found = RenderOpacity.objects_with_channel("highlight")
        self.assertEqual([n.split("|")[-1] for n in found], ["revise_cube"])

    def test_channel_colors_reads_back_what_was_written(self):
        RenderOpacity.set_channel_color([self.cube], color=(0.045, 0.39, 1.0))
        colors = RenderOpacity.channel_colors()
        self.assertEqual(len(colors), 1)
        (rgb,) = colors.values()
        self.assertEqual([round(c, 3) for c in rgb], [0.045, 0.39, 1.0])

    def test_a_missing_colour_is_refused(self):
        with self.assertRaises(ValueError):
            RenderOpacity.set_channel_color([self.cube])

    def test_a_channel_without_a_colour_is_refused(self):
        with self.assertRaises(ValueError):
            RenderOpacity.set_channel_color(
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
        RenderOpacity.create(objects=[self.cube], mode="attribute", channel="highlight")

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
        RenderOpacity.set_channel_color([self.cube], color=(1.0, 0.0, 0.0), stop="lo")
        self.assertEqual(self._rgb("highlightColorDim"), [1.0, 0.0, 0.0])
        self.assertEqual(self._rgb("highlightColor"), [0.0, 0.088656, 0.723055])

    def test_the_default_end_is_still_the_bright_one(self):
        """Every call site written before the dim end existed passes no stop."""
        RenderOpacity.set_channel_color([self.cube], color=(0.9, 0.1, 0.1))
        self.assertEqual(self._rgb("highlightColor"), [0.9, 0.1, 0.1])
        self.assertEqual(self._rgb("highlightColorDim"), [0.0, 0.0, 0.0])

    def test_channel_color_stops_reads_both_ends_in_one_pass(self):
        RenderOpacity.set_channel_color([self.cube], color=(0.1, 0.2, 0.3))
        RenderOpacity.set_channel_color([self.cube], color=(0.4, 0.5, 0.6), stop="lo")
        stops = RenderOpacity.channel_color_stops([self.cube])
        (pair,) = stops.values()
        self.assertEqual(len(pair), 2)
        self.assertEqual([round(c, 3) for c in pair[0]], [0.1, 0.2, 0.3])
        self.assertEqual([round(c, 3) for c in pair[1]], [0.4, 0.5, 0.6])

    def test_key_pulse_writes_both_ends(self):
        RenderOpacity.key_pulse(
            [self.cube],
            start=0,
            end=100,
            color=(1.0, 0.0, 0.0),
            dim_color=(0.0, 0.0, 0.5),
        )
        self.assertEqual(self._rgb("highlightColor"), [1.0, 0.0, 0.0])
        self.assertEqual(self._rgb("highlightColorDim"), [0.0, 0.0, 0.5])

    def test_key_pulse_without_a_dim_colour_leaves_it_untouched(self):
        RenderOpacity.set_channel_color([self.cube], color=(0.0, 0.9, 0.0), stop="lo")
        RenderOpacity.key_pulse([self.cube], start=0, end=100, color=(1.0, 0.0, 0.0))
        self.assertEqual(
            self._rgb("highlightColorDim"),
            [0.0, 0.9, 0.0],
            "a pulse that states no dim colour must not erase one",
        )

    def test_remove_strips_both_ends(self):
        RenderOpacity.remove(objects=[self.cube], channel="highlight")
        for attr in ("highlightColor", "highlightColorDim"):
            self.assertFalse(
                cmds.attributeQuery(attr, node=self.cube, exists=True), attr
            )

    def test_both_ends_publish_under_their_own_track_keys(self):
        """The join the drift guard only checks by NAME, checked by value."""
        RenderOpacity.key_pulse(
            [self.cube],
            start=0,
            end=60,
            period=20,
            color=(0.2, 0.5, 1.0),
            dim_color=(0.4, 0.0, 0.0),
        )
        tracks = RenderOpacity.visibility_tracks()
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
        from pythontk.file_utils.mesh_convert.glb_fades import CHANNELS as GLTF

        RenderOpacity.key_pulse(
            [self.cube],
            start=0,
            end=60,
            period=20,
            color=(0.2, 0.5, 1.0),
            dim_color=(0.4, 0.0, 0.0),
        )
        tracks = RenderOpacity.visibility_tracks()
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
            RenderOpacity.set_channel_color(
                [self.cube], color=(1.0, 0.0, 0.0), stop="middle"
            )


class TestDefaultHighlightColour(unittest.TestCase):
    """The seed colours must be ones the colour editor can hand back.

    The editor is 8-bit sRGB and the attribute is linear light, so a seed the
    picker cannot represent comes BACK changed: opening the pulse box in
    Revise and applying would rewrite an authored colour nobody touched. Each
    default is therefore the linear value of its own 8-bit display colour,
    stated to the 6 decimals this tool compares colours to.

    No scene: this is arithmetic over a constant, and what it guards is the
    REASON those digits are what they are -- which is the part a later edit
    to the default would otherwise drop.
    """

    def test_the_default_colours_survive_the_editors_own_round_trip(self):
        import pythontk as ptk
        from mayatk.mat_utils.render_opacity import render_effects_slots as res

        for name in ("DEFAULT_BRIGHT", "DEFAULT_DIM"):
            seed = getattr(res.RenderEffectsSlots, name)
            shown = tuple(
                round(c * 255) / 255 for c in ptk.Color.srgb_from_linear(seed)[:3]
            )
            self.assertEqual(
                [round(c, 6) for c in ptk.Color.linear_from_srgb(shown)],
                [round(float(c), 6) for c in seed],
                name,
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
        from pythontk.file_utils.mesh_convert.glb_fades import CHANNELS as GLTF

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
        RenderOpacity.key_fade([self.cube], start=10.4, end=25.6, direction="in")

        self.assertEqual(self._times("opacity"), [10.0, 26.0])
        self.assertEqual(self._times("visibility"), [10.0, 26.0])

    def test_a_fractional_pulse_keys_whole_frames_only(self):
        RenderOpacity.key_pulse([self.cube], start=10.4, end=110.6, period=self.PERIOD)

        times = self._times()
        self.assertTrue(times, "the pulse keyed nothing")
        self.assertEqual([t for t in times if t != int(t)], [], "sub-frame keys")
        self.assertEqual((times[0], times[-1]), (10.0, 111.0), "the window snaps too")

    def test_the_snapped_train_does_not_drift_off_the_cadence(self):
        """Only each key snaps -- the cycle itself still advances by the exact
        period, so a long train stays within a frame of the asked-for cadence
        instead of accumulating the rounding error cycle by cycle."""
        RenderOpacity.key_pulse(
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
        RenderOpacity.key_pulse(
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
