# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.mat_utils.render_opacity module

Tests for the non-animating Channels-based implementation.
"""

import unittest
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


class TestPrepareForExport(MayaTkTestCase):
    """prepare_for_export must guarantee every animated opacity object also
    carries visibility keys, since the Unity importer reconstructs per-object
    fades from the visibility curves (animated custom properties bind to the
    root Animator with empty paths and can't be mapped per-object)."""

    def test_syncs_manually_keyed_opacity(self):
        cube = cmds.polyCube(name="manual_keyed_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")

        # Hand-author opacity keys WITHOUT going through key_fade / behaviors
        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=30, value=1.0)
        cmds.setKeyframe(cube, attribute="opacity", time=60, value=0.0)

        # Pre-condition: no visibility keys yet — would silently fail in Unity
        self.assertEqual(
            cmds.keyframe(cube, attribute="visibility", q=True, keyframeCount=True), 0
        )

        synced = RenderOpacity.prepare_for_export(objects=[cube])

        self.assertIn(cube, synced)
        vis_count = cmds.keyframe(
            cube, attribute="visibility", q=True, keyframeCount=True
        )
        self.assertGreaterEqual(
            vis_count,
            3,
            "Visibility must be keyed at every opacity transition",
        )

    def test_idempotent(self):
        cube = cmds.polyCube(name="already_synced_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")
        # key_fade dual-keys both channels already
        RenderOpacity.key_fade(objects=[cube], start=1, end=30, direction="in")

        synced = RenderOpacity.prepare_for_export(objects=[cube])
        self.assertEqual(synced, [], "Already-synced object must not be re-processed")

    def test_scene_wide_scan(self):
        c1 = cmds.polyCube(name="scan_a")[0]
        c2 = cmds.polyCube(name="scan_b")[0]
        c3 = cmds.polyCube(name="scan_c_no_anim")[0]
        RenderOpacity.create(objects=[c1, c2, c3], mode="attribute")

        # Only c1 and c2 get opacity animation (c3 has the attr but no keys)
        cmds.setKeyframe(c1, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(c1, attribute="opacity", time=30, value=1.0)
        cmds.setKeyframe(c2, attribute="opacity", time=10, value=1.0)
        cmds.setKeyframe(c2, attribute="opacity", time=40, value=0.0)

        # objects=None → scene-wide scan
        synced = RenderOpacity.prepare_for_export()

        self.assertIn(c1, synced)
        self.assertIn(c2, synced)
        self.assertNotIn(c3, synced, "Object without opacity keys must be skipped")

    def test_multi_segment_animation(self):
        """fade-in → hold → fade-out → hold → fade-in produces matching
        visibility keys at every opacity transition boundary."""
        cube = cmds.polyCube(name="multi_segment_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")

        # 5-segment opacity authoring
        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=20, value=1.0)
        cmds.setKeyframe(cube, attribute="opacity", time=50, value=1.0)
        cmds.setKeyframe(cube, attribute="opacity", time=70, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=100, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=120, value=1.0)

        RenderOpacity.prepare_for_export(objects=[cube])

        vis_times = cmds.keyframe(cube, attribute="visibility", q=True, tc=True)
        vis_vals = cmds.keyframe(cube, attribute="visibility", q=True, vc=True)
        self.assertEqual(
            sorted(vis_times),
            [1, 20, 50, 70, 100, 120],
            "Visibility must be keyed at every opacity transition boundary",
        )
        # Boolean coercion: any opacity > 0 → visibility 1
        expected = [0.0, 1.0, 1.0, 0.0, 0.0, 1.0]
        for t, v in sorted(zip(vis_times, vis_vals)):
            idx = sorted(vis_times).index(t)
            self.assertEqual(
                v, expected[idx], f"vis@{t} = {v}, expected {expected[idx]}"
            )

    def test_preserves_manual_visibility_keys(self):
        """When the user has authored more visibility keys than opacity keys,
        prepare_for_export must NOT clobber that manual authoring."""
        cube = cmds.polyCube(name="manual_vis_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")

        # 2 opacity keys, 4 manually-authored visibility keys.
        # Use long-name plug path to target only the transform — pm.setKeyframe
        # with attribute="visibility" hits the shape too and double-counts.
        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=100, value=1.0)
        vis_plug = f"{cmds.ls(str(cube), l=True)[0]}.visibility"
        for t, v in [(1, 0), (25, 1), (50, 0), (100, 1)]:
            cmds.setKeyframe(vis_plug, time=t, value=v)

        synced = RenderOpacity.prepare_for_export(objects=[cube])
        self.assertNotIn(
            cube, synced, "Should not resync — user has authored visibility"
        )

        vis_times = cmds.keyframe(vis_plug, q=True, tc=True)
        self.assertEqual(
            sorted(set(vis_times)),
            [1, 25, 50, 100],
            "Manual visibility keyframes must be preserved verbatim",
        )

    def test_partial_opacity_values_coerce_to_visible(self):
        """Sub-1.0 opacity (e.g. 0.3) must produce visibility=1 — only
        a literal 0.0 marks the object as fully hidden."""
        cube = cmds.polyCube(name="partial_opa_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")

        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(
            cube, attribute="opacity", time=10, value=0.001
        )  # epsilon-visible
        cmds.setKeyframe(cube, attribute="opacity", time=20, value=0.5)
        cmds.setKeyframe(cube, attribute="opacity", time=30, value=1.0)

        RenderOpacity.prepare_for_export(objects=[cube])

        vis_vals = sorted(
            zip(
                cmds.keyframe(cube, attribute="visibility", q=True, tc=True),
                cmds.keyframe(cube, attribute="visibility", q=True, vc=True),
            )
        )
        self.assertEqual(vis_vals, [(1, 0.0), (10, 1.0), (20, 1.0), (30, 1.0)])

    def test_hierarchy_opacity_on_parent_only(self):
        """Opacity attr lives on a parent group transform; child meshes
        carry the Renderers. The Unity importer descends to add controllers
        on child Renderers, so the Maya side must still produce a usable
        visibility curve on the parent."""
        parent = cmds.group(empty=True, name="opacity_parent_loc")
        child = cmds.polyCube(name="child_mesh")[0]
        cmds.parent(child, parent)

        RenderOpacity.create(objects=[parent], mode="attribute")
        cmds.setKeyframe(parent, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(parent, attribute="opacity", time=30, value=1.0)

        synced = RenderOpacity.prepare_for_export(objects=[parent])
        self.assertIn(parent, synced)

        # Visibility on parent gets keyed; child geometry inherits via Maya
        # transform vis. The Unity importer reads m_Enabled@Renderer on the
        # child; Maya FBX export propagates parent visibility to child
        # m_Enabled in the absence of overrides.
        self.assertGreater(
            cmds.keyframe(parent, attribute="visibility", q=True, keyframeCount=True),
            0,
            "Parent visibility must be keyed even when geometry lives on child",
        )

    def test_prepare_after_key_fade_is_noop(self):
        """key_fade already dual-keys; prepare_for_export must be a no-op
        on top of it (idempotency under the canonical happy path)."""
        cube = cmds.polyCube(name="key_fade_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")
        RenderOpacity.key_fade(objects=[cube], start=1, end=30, direction="in")

        vis_before = cmds.keyframe(cube, attribute="visibility", q=True, tc=True)
        synced = RenderOpacity.prepare_for_export(objects=[cube])
        vis_after = cmds.keyframe(cube, attribute="visibility", q=True, tc=True)

        self.assertEqual(synced, [])
        self.assertEqual(vis_before, vis_after, "Visibility keys must be untouched")

    def test_visibility_query_ignores_shape_keys(self):
        """Stray shape.visibility keys must not inflate the visibility
        count and falsely satisfy the resync trigger.

        Bug surface: ``cmds.keyframe(obj, attribute='visibility')`` queries
        BOTH transform.visibility and shape.visibility — if some other
        tool keyed shape.visibility, our count would exceed opacity_count
        and we'd skip resync even though transform.visibility is empty
        (which is what the FBX exporter actually reads)."""
        cube = cmds.polyCube(name="shape_keyed_cube")[0]
        RenderOpacity.create(objects=[cube], mode="attribute")

        # Hand-author opacity, but only key SHAPE visibility (transform
        # vis stays unkeyed — this is the silent-failure scenario)
        cmds.setKeyframe(cube, attribute="opacity", time=1, value=0.0)
        cmds.setKeyframe(cube, attribute="opacity", time=30, value=1.0)
        shape = (cmds.listRelatives(str(cube), shapes=True, ni=True) or [None])[0]
        if shape is not None:
            for t, v in [(1, 0), (10, 1), (20, 0), (30, 1)]:
                cmds.setKeyframe(
                    f"{cmds.ls(str(shape), l=True)[0]}.visibility", time=t, value=v
                )

        synced = RenderOpacity.prepare_for_export(objects=[cube])

        self.assertIn(
            cube,
            synced,
            "Must resync transform.visibility despite shape.visibility keys "
            "— FBX export reads transform vis, not shape",
        )

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

        self.assertEqual(synced, [opacity_obj])
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
        self.slot._pulse_color = None
        self.slot._remove_actions = {}
        self.slot._update_remove_enabled = MagicMock()

    def _fade_widget(self, frames=10, ends_at_cursor=False, direction="in"):
        from unittest.mock import MagicMock

        widget = MagicMock()
        widget.option_box.menu.s000.value.return_value = frames
        widget.option_box.menu.chk000.isChecked.return_value = ends_at_cursor
        widget.option_box.menu.cmb_direction.currentData.return_value = direction
        return widget

    def _pulse_widget(self, frames=120, period=2.0, bright=50, gaps=(0.72, 0.72)):
        from unittest.mock import MagicMock

        widget = MagicMock()
        widget.option_box.menu.s001.value.return_value = frames
        widget.option_box.menu.s002.value.return_value = period
        widget.option_box.menu.s003.value.return_value = bright
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
        self.slot.tb001(self._pulse_widget(frames=200, period=2.0, gaps=(1.0, 0.0)))
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
        self.slot._pulse_color = (1.0, 0.0, 0.0)
        self.slot.tb001(self._pulse_widget())

        self.assertTrue(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        self.assertGreater(cmds.keyframe(f"{self.cube}.highlight", q=True, kc=True), 4)
        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [1.0, 0.0, 0.0],
        )
        self.slot.sb.message_box.assert_not_called()

    def test_header_init_wires_the_highlight_colour_action(self):
        """The revision entry point must be reachable from the header, not only
        from inside the pulse option box where it was easy to miss."""
        from unittest.mock import MagicMock

        header = MagicMock()
        self.slot.header_init(header)

        names = [c.kwargs.get("setObjectName") for c in header.menu.add.call_args_list]
        self.assertIn("b_highlight_color", names)

    def test_revise_highlight_color_writes_the_selection(self):
        from unittest.mock import MagicMock

        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select(self.cube, replace=True)
        self.slot._ask_highlight_color = MagicMock(return_value=(0.02, 0.17, 0.43))

        self.slot._revise_highlight_color()

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.02, 0.17, 0.43],
        )
        # A selection is unambiguous: it must not ask.
        self.slot.sb.message_box.assert_not_called()

    def test_revise_highlight_color_confirms_before_the_whole_scene(self):
        from unittest.mock import MagicMock

        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        cmds.select(clear=True)
        self.slot._ask_highlight_color = MagicMock(return_value=(1.0, 0.0, 0.0))
        self.slot.sb.message_box.return_value = "No"

        self.slot._revise_highlight_color()

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.2, 0.5, 1.0],
            "declining the confirm must write nothing",
        )
        self.slot._ask_highlight_color.assert_not_called()

        self.slot.sb.message_box.return_value = "Yes"
        self.slot._revise_highlight_color()

        self.assertEqual(
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [1.0, 0.0, 0.0],
        )

    def test_the_colour_dialog_is_seeded_from_the_authored_colour(self):
        """Opening on the last PICK rather than what the objects carry would make
        this a guess; the revision has to start from the authored value."""
        try:
            from unittest.mock import patch
            import qtpy  # noqa: F401
        except ImportError:
            self.skipTest("qtpy unavailable in this interpreter")

        RenderOpacity.create([self.cube], mode="attribute", channel="highlight")
        RenderOpacity.set_channel_color([self.cube], color=(0.02, 0.17, 0.43))
        self.slot._pulse_color = (1.0, 0.0, 0.0)  # a stale pick that must lose

        with patch("qtpy.QtWidgets.QColorDialog.getColor") as get_color:
            get_color.return_value.isValid.return_value = False
            self.assertIsNone(self.slot._ask_highlight_color([self.cube]))

        seeded = get_color.call_args.args[0]
        self.assertEqual(
            [round(c, 2) for c in (seeded.redF(), seeded.greenF(), seeded.blueF())],
            [0.02, 0.17, 0.43],
        )

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
            [round(c, 3) for c in cmds.getAttr(f"{self.cube}.highlightColor")[0]],
            [0.2, 0.5, 1.0],
        )
        self.assertTrue(cmds.getAttr(f"{self.cube}.highlight", keyable=True))
        # Not the presence channel: no visibility key is written by creating it.
        self.assertFalse(cmds.keyframe(f"{self.cube}.visibility", q=True, kc=True))

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


if __name__ == "__main__":
    unittest.main()
