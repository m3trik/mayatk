# !/usr/bin/python
# coding=utf-8
import json
import math
import os
import maya.cmds as cmds
import maya.mel as mel
import pythontk as ptk
from mayatk.mat_utils.render_opacity.render_effects import RenderEffects
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.env_utils.fbx_utils import FbxUtils
from base_test import MayaTkTestCase


class TestRenderOpacityExport(MayaTkTestCase):
    """Verify that RenderEffects attributes export correctly for Unity."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="export_cube")[0]
        # temp_tests/: kept for debugging visibility, but OUT of the tracked tree
        self.fbx_path = self.temp_path("debug_opacity.fbx")

        # Ensure FBX plugin is loaded
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

    def tearDown(self):
        super().tearDown()
        # Don't delete for now, so we can inspect it
        # if os.path.exists(self.fbx_path):
        #     try:
        #         os.remove(self.fbx_path)
        #     except OSError:
        #         pass

    def test_attribute_exports_to_fbx(self):
        """Verify 'opacity' attribute appears in FBX user properties."""
        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Select object to export
        cmds.select(self.cube)

        # Configure FBX for ASCII export (human readable)
        # Note: Options string format depends on plugin version, but 'Ascii' is standard
        # -v 0: verbose off
        # -es 1: export selected
        # -type "FBX export"

        # Set FBX settings via MEL (most reliable method)
        mel.eval("FBXExportInAscii -v true")
        mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')

        self.assertTrue(os.path.exists(self.fbx_path), "FBX file was not created")

        # Read the file and check for the attribute
        content = self.read_text_settled(self.fbx_path)

        # Success criteria:
        # 1. The custom attribute "opacity" must be defined.
        #    In ASCII FBX 2010+, it usually appears in the Model definition under "Properties70"
        #    Example: P: "opacity", "Double", "Number", "", 1
        #    Or verify via UserProperties.

        # Search for the property definition attached to the Model
        # We look for the exact string pattern roughly
        # ASCII FBX 2020: P: "opacity", "Number", "", "A+U",1,0,1
        found_prop = (
            'P: "opacity", "Number"' in content or 'P: "opacity", "Double"' in content
        )

        if not found_prop:
            # Fallback check: sometimes it's explicitly a User Property block
            # But standard custom attrs usually become top-level properties on the Model
            pass

        self.assertTrue(
            found_prop,
            f"Exported FBX missing 'opacity' property definition. Content sample: {content[:1000]}",
        )

    def test_animated_attribute_exports_curves(self):
        """Verify animated 'opacity' exports as animation curve."""
        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Keyframe it
        cmds.setAttr(f"{self.cube}.opacity", 1.0)
        cmds.setKeyframe(self.cube, attribute="opacity", t=1)
        cmds.setAttr(f"{self.cube}.opacity", 0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", t=10)

        cmds.select(self.cube)
        mel.eval("FBXExportInAscii -v true")
        mel.eval("FBXExportBakeComplexAnimation -v false")  # Export curves directly
        mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')

        content = self.read_text_settled(self.fbx_path)

        # Check for AnimationCurveNode for "opacity"
        # Example: AnimationCurveNode: 2136056071056, "AnimCurveNode::opacity", ""
        self.assertIn(
            '"AnimCurveNode::opacity"',
            content,
            "The opacity animation curve node should be present",
        )

        # Also ensure the property exists
        self.assertTrue(
            'P: "opacity", "Number"' in content or 'P: "opacity", "Double"' in content
        )


class TestSharedMaterialExport(MayaTkTestCase):
    """Verify opacity export when multiple objects share a material and UV map.

    This is the most common real-world scenario: several mesh pieces share
    one material (e.g. 'Body_Mat') and the default 'map1' UV set.  Each
    object must still get its own opacity attribute and animation curves
    in the exported FBX so that Unity's RenderOpacityController can drive
    them independently.
    """

    def setUp(self):
        super().setUp()
        self.fbx_path = self.temp_path("debug_shared_mat.fbx")

        # Ensure FBX plugin
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

        # Create three objects sharing one lambert material and UV set
        self.mat = cmds.shadingNode("lambert", asShader=True, name="shared_mat")
        self.sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="shared_sg"
        )
        cmds.connectAttr(f"{self.mat}.outColor", f"{self.sg}.surfaceShader")

        self.objects = []
        for i, name in enumerate(["piece_A", "piece_B", "piece_C"]):
            obj = cmds.polyCube(name=name)[0]
            cmds.move(i * 3, 0, 0, obj)
            cmds.sets(obj, edit=True, forceElement=self.sg)
            self.objects.append(obj)

    def tearDown(self):
        super().tearDown()

    def _export_fbx(self, objects, animate=False):
        """Apply opacity, optionally keyframe, export selected, return content."""
        RenderEffects.create(objects=objects, mode="attribute")

        if animate:
            for i, obj in enumerate(objects):
                cmds.setAttr(f"{obj}.opacity", 1.0)
                cmds.setKeyframe(obj, attribute="opacity", t=1)
                cmds.setAttr(f"{obj}.opacity", 0.0)
                cmds.setKeyframe(obj, attribute="opacity", t=10 + i * 5)

        cmds.select(objects)
        mel.eval("FBXExportInAscii -v true")
        if animate:
            mel.eval("FBXExportBakeComplexAnimation -v false")
        mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')
        self.assertTrue(os.path.exists(self.fbx_path), "FBX was not created")

        with open(self.fbx_path, "r") as f:
            return f.read()

    def _count_opacity_props(self, content):
        """Count how many Model nodes in the FBX contain an opacity property."""
        import re

        return len(re.findall(r'P: "opacity", "(?:Number|Double)"', content))

    # ------------------------------------------------------------------

    def test_shared_material_all_objects_get_opacity_prop(self):
        """Each object gets its own 'opacity' property even with shared material.

        Verifies that the FBX contains one opacity property definition per
        exported object, not just one for the shared material.
        """
        content = self._export_fbx(self.objects, animate=False)

        count = self._count_opacity_props(content)
        self.assertEqual(
            count,
            len(self.objects),
            f"Expected {len(self.objects)} opacity properties, found {count}",
        )

    def test_shared_material_animated_exports_per_object_curves(self):
        """Animated opacity on shared-material objects produces per-object curves.

        Each object must have its own AnimCurveNode so Unity can drive
        each RenderOpacityController independently.
        """
        content = self._export_fbx(self.objects, animate=True)

        # Each object should produce an AnimCurveNode
        import re

        curve_count = len(re.findall(r'"AnimCurveNode::opacity"', content))
        self.assertGreaterEqual(
            curve_count,
            len(self.objects),
            f"Expected >= {len(self.objects)} AnimCurveNode::opacity, got {curve_count}",
        )

    def test_shared_uv_set_preserved(self):
        """Objects sharing 'map1' UV set still export UV data correctly."""
        content = self._export_fbx(self.objects, animate=False)

        # Each mesh should reference a UV layer
        for obj in self.objects:
            # FBX Model names include the short node name
            self.assertIn(
                obj.split("|")[-1].split(":")[-1],
                content,
                f"Object '{obj.split('|')[-1].split(':')[-1]}' missing from FBX",
            )

        # At least one UV layer element should exist per mesh
        import re

        uv_layers = re.findall(r"LayerElementUV:", content)
        self.assertGreaterEqual(
            len(uv_layers),
            len(self.objects),
            f"Expected >= {len(self.objects)} UV layers, found {len(uv_layers)}",
        )

    def test_subset_export_preserves_shared_material(self):
        """Exporting a subset of objects sharing a material still works.

        Verifies that exporting only 2 of 3 objects doesn't break the
        opacity setup or produce incorrect FBX output.
        """
        subset = self.objects[:2]
        content = self._export_fbx(subset, animate=True)

        count = self._count_opacity_props(content)
        self.assertEqual(
            count,
            len(subset),
            f"Expected {len(subset)} opacity properties for subset export, got {count}",
        )

        # The third object should not appear
        self.assertNotIn(
            self.objects[2],
            content,
            "Non-selected object should not appear in exported FBX",
        )


class TestDualKeyVisibilityExport(MayaTkTestCase):
    """Verify that the opacity→visibility keyframe mirroring produces
    real animation curves on both channels in the exported FBX.

    This is the core fix: Unity reads the native 'Visibility' track from
    FBX. The old condition-node approach didn't export; the new dual-key
    system keys both opacity and visibility so FBX contains both curves.
    """

    def setUp(self):
        super().setUp()
        self.fbx_path = self.temp_path("debug_dual_key.fbx")
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

        self.cube = cmds.polyCube(name="dual_key_cube")[0]

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.fbx_path):
            try:
                os.remove(self.fbx_path)
            except OSError:
                pass

    def _export_ascii_fbx(self):
        cmds.select(self.cube)
        mel.eval("FBXExportInAscii -v true")
        mel.eval("FBXExportBakeComplexAnimation -v false")
        mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')
        with open(self.fbx_path, "r") as f:
            return f.read()

    def test_sync_produces_visibility_curve_in_fbx(self):
        """sync_visibility_from_opacity keys must appear as a real FBX
        Visibility animation track alongside the opacity track.

        Bug: Old condition-node driver was Maya-only and didn't export.
        Unity saw no visibility animation, objects stayed permanently
        visible or invisible.
        Fixed: 2026-03-21
        """
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Key opacity 1→0→1
        cmds.setKeyframe(self.cube, attribute="opacity", time=1, value=1.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=15, value=0.0)
        cmds.setKeyframe(self.cube, attribute="opacity", time=30, value=1.0)

        # Mirror to visibility
        OpacityAttributeMode.sync_visibility_from_opacity([self.cube])

        content = self._export_ascii_fbx()

        # Both channels must have animation curves
        self.assertIn(
            '"AnimCurveNode::opacity"',
            content,
            "Opacity animation curve missing from FBX",
        )
        self.assertIn(
            '"AnimCurveNode::Visibility"',
            content,
            "Visibility animation curve missing from FBX — "
            "sync_visibility_from_opacity didn't produce real keys",
        )

    def test_behavior_dual_keys_both_channels(self):
        """apply_behavior with a fade_in template on an opacity object
        must key both opacity and visibility in the FBX.

        This tests the behavior auto-redirect: the YAML targets
        'visibility' but the object has 'opacity', so the behavior
        system keys both channels.
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        RenderEffects.create(objects=[self.cube], mode="attribute")

        # Apply the fade_in behavior (template targets 'visibility')
        Behaviors.apply_behavior(self.cube, "fade_in", start=1, end=30)

        # Verify both channels are keyed in Maya
        opacity_keys = cmds.keyframe(self.cube, attribute="opacity", q=True, tc=True)
        vis_keys = cmds.keyframe(self.cube, attribute="visibility", q=True, tc=True)
        self.assertTrue(opacity_keys, "Opacity should have keyframes")
        self.assertTrue(vis_keys, "Visibility should have keyframes")
        # Visibility is boolean-typed; Maya may store keys differently
        # (e.g. step tangents create extra entries). Just verify both
        # channels were keyed, not exact count parity.
        self.assertGreaterEqual(
            len(vis_keys),
            len(opacity_keys),
            "Visibility should have at least as many keys as opacity",
        )

        content = self._export_ascii_fbx()

        self.assertIn(
            '"AnimCurveNode::opacity"',
            content,
            "Opacity animation curve missing from FBX",
        )
        self.assertIn(
            '"AnimCurveNode::Visibility"',
            content,
            "Visibility animation curve missing from FBX after behavior",
        )

    def test_visibility_template_auto_promotes_to_opacity(self):
        """A visibility-targeting behavior on a plain object auto-creates
        the opacity attribute and dual-keys both channels.

        Production design: every visibility fade flows through the opacity
        path so the FBX always carries both a smooth ``opacity`` curve
        (for the Unity ``RenderOpacityController``) and a stepped
        ``visibility`` curve (for engines that read visibility natively).
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        # No RenderEffects.create — start from a plain object
        self.assertFalse(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True)
        )

        Behaviors.apply_behavior(self.cube, "fade_in", start=1, end=30)

        # Auto-promotion must have occurred
        self.assertTrue(
            cmds.attributeQuery("opacity", node=str(self.cube), exists=True),
            "Visibility-targeting behavior must auto-create the opacity attr "
            "so the FBX gets a smooth opacity curve for the Unity controller",
        )

        opacity_keys = cmds.keyframe(self.cube, attribute="opacity", q=True, tc=True)
        vis_keys = cmds.keyframe(self.cube, attribute="visibility", q=True, tc=True)
        self.assertTrue(opacity_keys, "Opacity should have keyframes")
        self.assertTrue(vis_keys, "Visibility should have keyframes")

        content = self._export_ascii_fbx()
        self.assertIn(
            '"AnimCurveNode::opacity"',
            content,
            "Opacity animation curve missing — Unity controller can't drive fade",
        )
        self.assertIn(
            '"AnimCurveNode::Visibility"',
            content,
            "Visibility animation curve missing — engines without opacity "
            "support won't see the fade",
        )


class TestVisibilityTracksProducer(MayaTkTestCase):
    """The ``render_effects`` channel — the glTF route's only path in.

    An FBX carries a ``Visibility`` curve natively (the test above pins that),
    but glTF animates only translation/rotation/scale/weights, so the
    conversion drops it without a word: every gated object ships visible for
    the whole deliverable. This channel is what
    ``ptk.MeshConvert.apply_glb_visibility`` rebuilds them from.
    """

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="ntsc")
        self.grp = cmds.group(cmds.polyCube()[0], name="GATE_LOC")

    def _carrier(self, attr):
        raw = DataNodes.read(ptk.Scope.DELIVERABLE, attr)
        return json.loads(raw) if raw else None

    def _publish_shots(self, takes):
        """Declare the scene's shots (*takes* as ``{"name", "start", "end"}``)
        in its shot store: one take each."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        for t in takes:
            store.define_shot(t["name"], t["start"], t["end"])

    def test_a_fade_publishes_its_opacity_and_not_its_mirror(self):
        """``key_fade`` writes a linear opacity ramp and a stepped visibility
        mirror; the record names the opacity alone -- the mirror is the
        opacity's, and a reader hides the node where its opacity is 0."""
        RenderEffects.key_fade([self.grp], start=8, end=23, direction="in")
        self.assertTrue(cmds.keyframe(f"{self.grp}.visibility", q=True, kc=True))

        tracks = RenderEffects.visibility_tracks()

        self.assertEqual(tracks, [{"node": "GATE_LOC", "channels": ["opacity"]}])

    def test_the_published_tracks_do_not_depend_on_the_export_preparer(self):
        """Producer order cannot matter when no preparer writes what the walk
        reads: the channel published before ``prepare_for_export`` must equal
        the one published after it and its ``finish_export``. A hand-keyed
        opacity is the case a visibility repair used to change."""
        RenderEffects.create(objects=[self.grp], mode="attribute")
        cmds.setKeyframe(self.grp, attribute="opacity", time=8, value=0.0)
        cmds.setKeyframe(self.grp, attribute="opacity", time=23, value=1.0)

        RenderEffects.refresh_export_metadata()
        before = self._carrier(RenderEffects.DATA_CHANNEL)
        try:
            RenderEffects.prepare_for_export()
        finally:
            RenderEffects.finish_export()
        RenderEffects.refresh_export_metadata()
        after = self._carrier(RenderEffects.DATA_CHANNEL)

        self.assertTrue(before and before["tracks"], "a vacuous comparison")
        self.assertEqual(after, before)

    def test_the_record_holds_no_frame_and_no_rate(self):
        """Version 2: the record names channels, and the curves ride the
        takes on their proxies -- no ramp, no frame, no rate (2026-11-05)."""
        RenderEffects.key_fade([self.grp], start=8, end=23, direction="in")
        RenderEffects.key_fade([self.grp], start=1000, end=1015, direction="out")
        self._publish_shots(
            [
                {"name": "Shot_1", "start": 7, "end": 100},
                {"name": "Shot_5", "start": 915, "end": 1015},
            ]
        )

        RenderEffects.refresh_export_metadata()
        published = self._carrier(RenderEffects.DATA_CHANNEL)

        self.assertEqual(published["version"], RenderEffects.SCHEMA_VERSION)
        self.assertEqual(published["version"], 2)
        self.assertEqual(set(published), {"version", "tracks"})
        self.assertEqual(published["tracks"], [{"node": "GATE_LOC", "channels": ["opacity"]}])

    def test_a_scoped_write_states_the_tracks_of_what_it_ships(self):
        """Every export writes data for the objects it ships. Measured
        2026-10-31 (fresh mayapy, two cubes, one exported): the record named
        the other cube's tracks and the GLB build warned "2 keyed node(s) are
        not in this GLB". Added: 2026-10-31"""
        RenderEffects.key_fade([self.grp], start=8, end=23, direction="in")
        other = cmds.group(cmds.polyCube()[0], name="OTHER_LOC")
        RenderEffects.key_fade([other], start=3, end=40, direction="in")
        self._publish_shots([{"name": "Shot_1", "start": 1, "end": 100}])

        scoped = RenderEffects.export_record(
            FbxUtils.export_context(scope=tuple(cmds.ls(self.grp, long=True)))
        ).payload
        whole = RenderEffects.export_record(FbxUtils.export_context()).payload

        self.assertEqual([t["node"] for t in scoped["tracks"]], ["GATE_LOC"])
        self.assertEqual(
            sorted(t["node"] for t in whole["tracks"]), ["GATE_LOC", "OTHER_LOC"]
        )

    def test_a_publish_with_no_keyed_visibility_clears_the_channel(self):
        """No carrier is made for an empty channel, and a stale channel does
        not outlive its curves."""
        ctx = FbxUtils.export_context()
        ptk.SceneRecords.VISIBILITY.clear(DataNodes)
        FbxUtils.publish(ctx, only=[ptk.SceneRecords.VISIBILITY])
        self.assertIsNone(DataNodes.get_export_node(create=False))

        stale = {"version": 1, "tracks": []}
        ptk.SceneRecords.VISIBILITY.save(DataNodes, stale)
        FbxUtils.publish(ctx, only=[ptk.SceneRecords.VISIBILITY])
        self.assertFalse(ptk.SceneRecords.VISIBILITY.is_present(DataNodes))

    def test_a_scene_with_no_keyed_visibility_leaves_no_channel(self):
        """An empty carrier is worse than no carrier."""
        self.assertIsNone(RenderEffects.refresh_export_metadata())
        self.assertFalse(ptk.SceneRecords.VISIBILITY.is_present(DataNodes))

    def test_a_shot_scene_with_no_keyed_visibility_publishes_no_channel(self):
        """The channel used to carry the whole-timeline clip origin alone for
        a shot scene keying nothing, because the GLB cut its shots out of the
        whole-timeline take against it. The GLB's clips are the FBX's takes
        now, one per shot, so there is nothing to publish (2026-11-05)."""
        cube = cmds.polyCube(name="origin_mover")[0]
        cmds.setKeyframe(cube, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(cube, attribute="translateX", time=40, value=10)
        self._publish_shots(
            [
                {"name": "Shot_A", "start": 1, "end": 20},
                {"name": "Shot_B", "start": 21, "end": 40},
            ]
        )

        for mode in (
            ptk.ExportContext.PIPELINE,
            ptk.ExportContext.HANDOFF,
            ptk.ExportContext.AUTHORING,
        ):
            FbxUtils.publish(
                FbxUtils.export_context(mode=mode), only=[ptk.SceneRecords.VISIBILITY]
            )
            self.assertFalse(ptk.SceneRecords.VISIBILITY.is_present(DataNodes), mode)

    def test_a_non_stepped_curve_is_read_as_maya_actually_evaluates_it(self):
        """Visibility is a BOOLEAN, and Maya does not interpolate booleans.

        Measured on Maya 2025: a LINEAR fade-out from 1 to 0 over frames 10-20
        holds fully visible until frame 20 and then switches -- the tangents are
        accepted and ignored, so the curve steps whatever it claims. That is
        why the visibility proxy is keyed from the evaluated plug rather than
        copied from the curve: what ships has to be what the DCC draws, and two
        plausible readings of this curve (visible from 11, or a gradient) are
        both wrong.
        """
        plug = f"{self.grp}.visibility"
        cmds.setKeyframe(
            plug, time=10, value=1, inTangentType="linear", outTangentType="linear"
        )
        cmds.setKeyframe(
            plug, time=20, value=0, inTangentType="linear", outTangentType="linear"
        )

        self.assertEqual(
            RenderEffects.visibility_tracks(),
            [{"node": "GATE_LOC", "channels": ["visibility"]}],
        )
        try:
            (proxy,) = RenderEffects.stage_export_proxies()
            self.assertEqual(proxy.split("|")[-1], "GATE_LOC__visibility")
            self.assertEqual(cmds.keyframe(f"{proxy}.scaleX", q=True, tc=True), [10.0, 20.0])
            self.assertEqual(cmds.keyframe(f"{proxy}.scaleX", q=True, vc=True), [1.0, 0.0])
            self.assertEqual(
                cmds.keyTangent(f"{proxy}.scaleX", q=True, outTangentType=True),
                ["step", "step"],
            )
        finally:
            RenderEffects.remove_export_proxies()

    def test_the_export_hook_reaches_this_producer(self):
        """Registered in ``FbxUtils.PRODUCERS``, so an export's publish refreshes it."""
        RenderEffects.key_fade([self.grp], start=8, end=23, direction="in")
        ptk.SceneRecords.VISIBILITY.clear(DataNodes)

        FbxUtils.publish()

        self.assertTrue(ptk.SceneRecords.VISIBILITY.is_present(DataNodes))


class TestHandoffClipOrigin(MayaTkTestCase):
    """A hand-off GLB's clips open where their shots do.

    A hand-off publishes its records on entering the export bracket, before it
    arms its bake range, so a clip origin it published was whatever range the
    LAST export left in the FBX plugin -- and every shot was cut that many
    frames early (measured 2026-10-04: 23). The GLB's clips are the FBX's
    takes now, one per shot, each resampled across its window, so a clip
    opens on its shot's first frame and nothing is published to place it.
    """

    def test_shots_are_cut_where_the_file_s_take_opens(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from pythontk.file_utils.mesh_convert.glb.reader import GlbReader

        cmds.loadPlugin("fbxmaya", quiet=True)
        cmds.currentUnit(time="film")
        mover = cmds.polyCube(name="origin_mover")[0]
        cmds.setKeyframe(mover, attribute="translateX", time=10, value=0)
        cmds.setKeyframe(mover, attribute="translateX", time=200, value=190)
        cmds.keyTangent(
            mover,
            attribute="translateX",
            inTangentType="linear",
            outTangentType="linear",
        )
        blink = cmds.polyCube(name="origin_blink")[0]
        cmds.setKeyframe(blink, attribute="visibility", time=10, value=1)
        cmds.setKeyframe(blink, attribute="visibility", time=150, value=0)
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 20, 60, objects=[mover])
        store.define_shot("ShotB", 80, 120, objects=[mover])
        store.publish_export_view()
        # What an earlier export in the session leaves armed in the plugin.
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 33")
        mel.eval("FBXExportBakeComplexEnd -v 60")
        self.addCleanup(mel.eval, "FBXResetExport")

        artifacts = ptk.TempArtifacts("handoff_clip_origin", policy="scoped")
        self.addCleanup(artifacts.cleanup)
        out = artifacts.dir_path()
        fbx, glb = os.path.join(out, "handoff.fbx"), os.path.join(out, "handoff.glb")
        bridge = WebXrPreview()
        params = dict(bridge.params_defaults(), INCLUDE_ANIMATION=True)
        bridge._export_fbx([mover, blink], fbx, params)
        ptk.GlbPipeline.build(fbx, dst=glb)

        reader = GlbReader.load(glb)
        spans = reader.clip_spans(24.0)
        self.assertEqual(sorted(spans), ["ShotA", "ShotB"], "one clip per shot")
        for shot, start, end in (("ShotA", 20, 60), ("ShotB", 80, 120)):
            low, high, _ = spans[shot]
            v0 = reader.sample(shot, "origin_mover", "translation", low)
            v1 = reader.sample(shot, "origin_mover", "translation", high)
            slope = (v1[0] - v0[0]) / (end - start)  # GLB units per frame
            # mover's translateX is (frame - 10) scene units.
            self.assertAlmostEqual(
                10 + v0[0] / slope,
                start,
                delta=0.5,
                msg=f"{shot} opens on the wrong authored frame",
            )


class TestRenderEffectsExport(MayaTkTestCase):
    """The highlight channel's export legs: the GLB track and the FBX transport."""

    def setUp(self):
        super().setUp()
        cmds.currentUnit(time="ntsc")
        self.cube = cmds.polyCube(name="glow")[0]
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

    def test_a_highlight_only_node_publishes_its_ramp_and_colour(self):
        """No visibility keys at all -- highlighted but never hidden -- still ships."""
        RenderEffects.key_pulse(
            [self.cube], start=8, end=108, period=50, color=(0.2, 0.5, 1.0)
        )
        tracks = RenderEffects.visibility_tracks()
        self.assertEqual(len(tracks), 1)
        track = tracks[0]
        self.assertEqual(track["node"], "glow")
        self.assertEqual(track["channels"], ["highlight"])
        self.assertEqual(
            [round(c, 3) for c in track["highlight_color"]], [0.2, 0.5, 1.0]
        )

    def test_a_faded_and_highlighted_node_publishes_both_on_one_track(self):
        RenderEffects.key_fade([self.cube], start=8, end=23, direction="in")
        RenderEffects.key_pulse([self.cube], start=8, end=108, period=50)
        tracks = RenderEffects.visibility_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["channels"], ["highlight", "opacity"])

    def test_the_export_bracket_stages_a_curve_proxy_that_reaches_the_fbx(self):
        """One child per keyed channel, marked, with the curve on scale.x -- and gone after."""
        RenderEffects.key_pulse([self.cube], start=1, end=10, period=10)
        fbx = self.temp_path("render_effects_proxy.fbx")
        cmds.select(self.cube, replace=True)
        with FbxUtils.export_prepared():
            # The bracket restores the selection the preparers may have moved.
            self.assertEqual(cmds.ls(selection=True), [self.cube])
            proxies = cmds.ls("glow__highlight", long=True)
            self.assertEqual(len(proxies), 1)
            proxy = proxies[0]
            self.assertTrue(cmds.getAttr(f"{proxy}.{RenderEffects.PROXY_MARKER}"))
            self.assertEqual(
                cmds.keyframe(f"{proxy}.scaleX", q=True, kc=True),
                cmds.keyframe(f"{self.cube}.highlight", q=True, kc=True),
            )
            mel.eval("FBXResetExport")
            mel.eval("FBXExportInAscii -v true")
            mel.eval("FBXExportBakeComplexAnimation -v true")
            mel.eval("FBXExportBakeComplexStart -v 1")
            mel.eval("FBXExportBakeComplexEnd -v 10")
            mel.eval(f'FBXExport -f "{fbx.replace(chr(92), "/")}" -s')
        self.assertEqual(cmds.ls("glow__highlight"), [])
        with open(fbx, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        self.assertIn('"Model::glow__highlight"', text)
        self.assertIn(f'P: "{RenderEffects.PROXY_MARKER}"', text)

    def test_a_hand_keyed_opacity_is_gated_in_the_preview_and_never_mirrored(self):
        """Opacity keyed by hand, no visibility anywhere, through the WebXR
        preview's ONE export bracket, animation on: the GLB gates the node
        (opacity at 0 is presence) and the scene's ``visibility`` stays
        unkeyed -- no export step writes a mirror."""
        import pythontk as ptk
        from mayatk.env_utils.webxr_preview import WebXrPreview

        RenderEffects.create(objects=[self.cube], mode="attribute")
        for frame, value in ((1, 0.0), (15, 1.0), (40, 1.0), (55, 0.0)):
            cmds.setKeyframe(self.cube, attribute="opacity", time=frame, value=value)
        plug = f"{cmds.ls(self.cube, long=True)[0]}.visibility"
        self.assertFalse(cmds.keyframe(plug, q=True, keyframeCount=True))

        bridge = WebXrPreview()
        fbx = self.temp_path("hand_keyed_opacity.fbx")
        bridge._export_fbx(
            [self.cube], fbx, dict(bridge.params_defaults(), INCLUDE_ANIMATION=True)
        )
        self.assertFalse(
            cmds.keyframe(plug, q=True, keyframeCount=True),
            "the export wrote a visibility mirror into the scene",
        )

        glb = self.temp_path("hand_keyed_opacity.glb")
        ptk.MeshConvert.fbx_to_glb(
            fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False
        )
        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
        glow = {i for i, n in enumerate(gltf["nodes"]) if n.get("name") == "glow"}
        gates = [
            channel
            for animation in gltf.get("animations") or []
            for channel in animation.get("channels") or []
            if channel["target"].get("node") in glow
            and channel["target"].get("path") == "scale"
        ]
        self.assertTrue(glow, "the cube is not in the GLB")
        self.assertTrue(gates, "the hand-keyed opacity was not gated in the GLB")

    def _highlighted_cube_on_a_material(self):
        from mayatk.mat_utils._mat_utils import MatUtils

        mat = cmds.shadingNode("standardSurface", asShader=True, name="glowMat")
        cmds.setAttr(f"{mat}.emission", 0.5)  # an AUTHORED emission to preserve
        MatUtils.assign_mat([self.cube], mat)
        RenderEffects.key_pulse(
            [self.cube], start=1, end=100, period=50, color=(1.0, 0.0, 0.0)
        )
        # Keying never touches the material (the viewport binding that did was
        # retired): inside a bright hold the authored emission is still 0.5.
        cmds.currentTime(20)
        self.assertEqual(cmds.getAttr(f"{mat}.emission"), 0.5)
        self.assertFalse(
            cmds.listConnections(f"{mat}.emission", source=True, destination=False)
        )
        return mat

    @staticmethod
    def _glb_material_and_pointers(glb):
        import pythontk as ptk

        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
            bases = [m.get("emissiveFactor", [0, 0, 0]) for m in gltf["materials"]]
            pointers = [
                ch["target"]["extensions"]["KHR_animation_pointer"]["pointer"]
                for a in gltf.get("animations", [])
                for ch in a.get("channels", [])
                if ch.get("target", {}).get("path") == "pointer"
            ]
        return bases, pointers

    @staticmethod
    def _glb_emissive_samples(glb):
        """Every ``(r, g, b)`` the GLB's emissive pointer channels sample, any clip."""
        import struct

        import pythontk as ptk

        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf, blob = edit.gltf, edit.bin_data
        samples = []
        for animation in gltf.get("animations", []):
            for channel in animation.get("channels", []):
                target = channel.get("target", {})
                pointer = (
                    target.get("extensions", {})
                    .get("KHR_animation_pointer", {})
                    .get("pointer", "")
                )
                if target.get("path") != "pointer" or not pointer.endswith(
                    "/emissiveFactor"
                ):
                    continue
                acc = gltf["accessors"][
                    animation["samplers"][channel["sampler"]]["output"]
                ]
                view = gltf["bufferViews"][acc["bufferView"]]
                offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
                flat = struct.unpack_from(f"<{acc['count'] * 3}f", blob, offset)
                samples.extend(
                    tuple(round(c, 3) for c in flat[i : i + 3])
                    for i in range(0, len(flat), 3)
                )
        return samples

    def test_a_preview_overlay_carries_the_boxes_colours_into_the_glb(self):
        """The WebXR preview action's whole path short of the browser: a real
        selection export of an object with NO keys, overlaid with the panel's
        plan, builds a GLB whose emissive channel rides between the colours the
        box was set to -- and a second build with other colours differs. Live
        report (2026-09-13): every push looked the same whatever Bright and Dim
        were set to."""
        import pythontk as ptk
        from mayatk.env_utils.webxr_preview import WebXrPreview

        bridge = WebXrPreview()
        fbx = self.temp_path("preview_overlay.fbx")
        bridge._export_fbx([self.cube], fbx, dict(bridge.params_defaults()))
        self.assertFalse(cmds.attributeQuery("highlight", node=self.cube, exists=True))
        keys = ptk.RampKeys.pulse(0.0, 60.0, period=30.0)
        looks = {
            "red": ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            "green": ((0.0, 1.0, 0.0), (0.0, 0.0, 0.2)),
        }
        seen = {}
        for tag, colors in looks.items():
            overlay = RenderEffects.preview_channels(
                [self.cube], channel="highlight", keys=keys, colors=colors, fps=30.0
            )
            glb = self.temp_path(f"preview_overlay_{tag}.glb")
            ptk.MeshConvert.fbx_to_glb(
                fbx,
                dst=glb,
                overwrite=True,
                prompt=False,
                lightmaps=False,
                data_export=overlay,
            )
            seen[tag] = set(self._glb_emissive_samples(glb))
            self.assertTrue(seen[tag], f"{tag}: no emissive channel reached the GLB")
        for tag, (bright, dim) in looks.items():
            self.assertIn(bright, seen[tag], f"{tag}: bright end never sampled")
            self.assertIn(dim, seen[tag], f"{tag}: dim end never sampled")
        self.assertNotEqual(seen["red"], seen["green"], "the colours did not change")

    def test_the_export_reads_the_scene_as_authored_with_no_restore_step(self):
        """CODE_STANDARD s13's test: a keyed highlight leaves the material as
        authored before, during and after the write -- there is nothing for the
        export bracket to suspend -- and the highlight still reaches the GLB as
        the emissive pointer channel through the carrier."""
        import pythontk as ptk
        from mayatk.env_utils.webxr_preview import WebXrPreview

        self.assertIn("render_effects", WebXrPreview.export_stagers)
        self.assertTrue(
            WebXrPreview.include_data_export, "the channel rides the carrier"
        )
        mat = self._highlighted_cube_on_a_material()
        bridge = WebXrPreview()
        fbx = self.temp_path("transport_highlight.fbx")
        # Animation on: the highlight's curve rides the take, and a push with
        # no take carries none (render_effects v2).
        bridge._export_fbx(
            [self.cube], fbx, dict(bridge.params_defaults(), INCLUDE_ANIMATION=True)
        )

        self.assertFalse(
            cmds.listConnections(f"{mat}.emission", source=True, destination=False),
            "the authored material was never bound, so nothing was re-bound",
        )
        self.assertEqual(cmds.getAttr(f"{mat}.emission"), 0.5, "authored, untouched")
        self.assertEqual(cmds.ls("*__highlight"), [], "proxies removed")
        glb = self.temp_path("transport_highlight.glb")
        ptk.MeshConvert.fbx_to_glb(
            fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False
        )
        bases, pointers = self._glb_material_and_pointers(glb)
        # The materials record states the AUTHORED emission (0.5 x white): the
        # base holds that, never a driven frame value (the pulse is red).
        self.assertEqual(bases, [[0.5, 0.5, 0.5]], f"no leaked value: {bases}")
        self.assertTrue(any(p.endswith("/emissiveFactor") for p in pointers), pointers)

    def test_the_preview_and_the_exporter_build_the_glb_through_one_pipeline(self):
        """Same mechanism by construction: the Scene Exporter's GLB stage and the
        preview's deliverer both call ``pythontk.GlbPipeline.build`` -- so the GLB
        setup (sidecar, lightmaps, render effects, texture pass) cannot differ
        between what the page shows and what the target platform receives."""
        from unittest.mock import patch

        import pythontk as ptk
        from mayatk.env_utils.webxr_preview import WebXrPreview
        from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter

        builds = []

        def _build(src, dst=None, **kwargs):
            builds.append({"src": src, "dst": dst, **kwargs})
            out = dst or os.path.splitext(src)[0] + ".glb"
            with open(out, "wb") as fh:
                fh.write(b"glTF")
            return {
                "glb": out,
                "src": src,
                "scratch": [],
                "payload_textures": None,
                "sidecar": {},
                "lightmaps": None,
                "textures": {},
            }

        self._highlighted_cube_on_a_material()
        preview = WebXrPreview()
        request = ptk.HandoffRequest(params=dict(preview.params_defaults()))
        payload = preview._produce([self.cube], request)
        self.assertTrue(os.path.isfile(payload.primary))

        exporter = SceneExporter(log_level="WARNING")
        exporter.task_manager.objects = [self.cube]
        server = ptk.PreviewServer(root=self.temp_path("preview_root"), port=0).start()
        self.addCleanup(server.stop)
        deliverer = ptk.PreviewDeliverer(server=server, open_browser=False)
        with patch.object(ptk.GlbPipeline, "build", side_effect=_build):
            exported = exporter.task_manager.create_glb(fbx_path=payload.primary)
            delivered = deliverer.deliver(preview, payload, request)

        self.assertEqual(len(builds), 2, "both routes run the one build")
        self.assertTrue(exported and exported.endswith(".glb"))
        self.assertIsNotNone(delivered)
        by_exporter, by_preview = builds
        self.assertEqual(by_exporter["src"], by_preview["src"])
        for build in builds:
            # Neither hands the build an account of the scene beside the file
            # -- the GLB reads its materials and handoff off the FBX -- and the
            # same texture-policy keys: the dials differ, the mechanism does not.
            self.assertIsNone(build.get("sidecar"))
            self.assertLessEqual(
                {"image_format", "max_size"}, set(build["texture_params"])
            )


class TestRenderEffectsRealExport(MayaTkTestCase):
    """``render_effects`` v2 end to end on a real Maya export: two shots, one
    take each; a box fading out in the first and in again in the second, a
    sphere pulsing a highlight, a cube switched off and on by its keyed
    visibility. Read back with ``ptk.FbxFile`` (the record and the proxies'
    curves, per take) and in the GLB the conversion builds from them.
    Added: 2026-11-05"""

    FPS = 24.0

    def setUp(self):
        super().setUp()
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")
        cmds.currentUnit(time="film")
        cmds.playbackOptions(minTime=1, maxTime=48)
        self.artifacts = ptk.TempArtifacts("render_effects_v2", policy="scoped")
        self.addCleanup(self.artifacts.cleanup)
        self.out = self.artifacts.dir_path()

    def _scene(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.mat_utils._mat_utils import MatUtils

        fader = cmds.polyCube(name="fader")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="faderMat")
        MatUtils.assign_mat([fader], mat)
        RenderEffects.key_fade([fader], start=5, end=15, direction="out")
        RenderEffects.key_fade([fader], start=30, end=40, direction="in")
        glower = cmds.polySphere(name="glower")[0]
        RenderEffects.key_pulse([glower], start=1, end=48, period=24, color=(1.0, 0.5, 0.0))
        blinker = cmds.polyCube(name="blinker")[0]
        for frame, value in ((1, 1), (10, 0), (30, 1)):
            cmds.setKeyframe(blinker, attribute="visibility", time=frame, value=value)
        cmds.keyTangent(blinker, attribute="visibility", outTangentType="step")
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 1, 24, objects=[fader, glower, blinker])
        store.define_shot("ShotB", 25, 48, objects=[fader, glower, blinker])
        store.publish_export_view()
        return [fader, glower, blinker]

    def _export(self, objects, name="effects.fbx"):
        from mayatk.env_utils.webxr_preview import WebXrPreview

        fbx = os.path.join(self.out, name)
        bridge = WebXrPreview()
        bridge._export_fbx(
            objects, fbx, dict(bridge.params_defaults(), INCLUDE_ANIMATION=True)
        )
        self.addCleanup(mel.eval, "FBXResetExport")
        return fbx

    def test_the_fbx_carries_the_record_and_one_proxy_curve_per_channel_per_take(self):
        fbx = self._export(self._scene())
        file = ptk.FbxFile.load(fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
        self.assertEqual(sorted(file.take_names()), ["ShotA", "ShotB"])
        (raw,) = [
            v
            for owner, key, v in file.user_property_items()
            if key == ptk.SceneRecords.VISIBILITY.key
        ]
        record = json.loads(bytes(raw).decode("utf-8"))
        self.assertEqual(set(record), {"version", "tracks"}, "no frame, no rate")
        self.assertEqual(record["version"], 2)
        by_node = {t["node"]: t["channels"] for t in record["tracks"]}
        self.assertEqual(
            by_node,
            {"blinker": ["visibility"], "fader": ["opacity"], "glower": ["highlight"]},
        )
        curves = ptk.MeshConvert.render_effect_curves(file)
        for take in ("ShotA", "ShotB"):
            self.assertLessEqual(
                {("fader", "opacity"), ("glower", "highlight"), ("blinker", "visibility")},
                set(curves[take]),
                take,
            )
        # The fade-out reaches 0 at frame 15, measured from ShotA's clip t=0
        # (its first key, frame 1, floored to the converter's 1/30 s grid).
        zero = 1 / 30.0
        opacity = curves["ShotA"][("fader", "opacity")]
        at = {round((t + zero) * self.FPS, 2): v for t, v, _i in opacity}
        self.assertAlmostEqual(at[5.0], 1.0, places=4)
        self.assertAlmostEqual(at[15.0], 0.0, places=4)
        steps = curves["ShotA"][("blinker", "visibility")]
        self.assertEqual({round(v) for _t, v, _i in steps}, {0, 1})
        verdict = ptk.ExportVerifier(fbx=fbx, sidecar=None).run(["check_fbx_render_effects"])
        self.assertTrue(verdict.ok, verdict.summary())

    def test_the_glb_plays_the_files_own_curves_opaque_at_rest(self):
        fbx = self._export(self._scene())
        glb = os.path.join(self.out, "effects.glb")
        ptk.MeshConvert.fbx_to_glb(fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False)
        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
        self.assertEqual(
            {m.get("alphaMode", "OPAQUE") for m in gltf["materials"]}, {"OPAQUE"},
            "no material written BLEND",
        )
        self.assertEqual(gltf["extras"][ptk.SceneRecords.VISIBILITY.key]["version"], 2)
        names = {i: n.get("name") for i, n in enumerate(gltf["nodes"])}
        self.assertFalse([n for n in names.values() if n and "__" in n], "proxies stripped")
        played = {}
        for animation in gltf["animations"]:
            for channel in animation["channels"]:
                target = channel["target"]
                note = (channel.get("extras") or {}).get("render_effects")
                if target.get("path") == "pointer":
                    played.setdefault(animation["name"], set()).add((note["node"], note["channel"]))
                elif target.get("path") == "scale" and names.get(target.get("node")) in ("fader", "blinker"):
                    played.setdefault(animation["name"], set()).add((names[target["node"]], "gate"))
        for clip in ("ShotA", "ShotB"):
            self.assertLessEqual(
                {("fader", "opacity"), ("glower", "highlight"), ("blinker", "gate"), ("fader", "gate")},
                played[clip],
                clip,
            )

    def test_an_animation_off_push_plays_the_scenes_effects_on_its_shots_clips(self):
        """Bug: a WebXR push with Include Animation off (the default) played
        the scene's fades, highlights and keyed visibility until the curves
        moved into the takes (render_effects v2); such an FBX has no take, so
        nothing played (fixed 2026-11-05: the push states them in its build's
        overlay) -- and the fix gave a scene with shots one ``Take 001`` where
        HEAD gave one clip per declared take, its picker's shots and their
        sequence. Fixed: 2026-11-07 -- one clip per take an animated push
        splits, named as the take, each curve from its shot's start and
        spanning the shot, the record's descriptions and rate beside them. A
        real push, its build fed exactly what the push handed its deliverer."""
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.webxr_preview import WebXrPreview

        out = self.out

        class _Build(ptk.Deliverer):
            seen = {}

            def preflight(self, bridge, request):
                return True

            def deliver(self, bridge, payload, request):
                glb = os.path.join(out, "pushed.glb")
                overlay = request.get("data_export")
                type(self).seen.update(
                    takes=ptk.FbxFile.load(payload.primary).take_names(), overlay=overlay
                )
                ptk.MeshConvert.fbx_to_glb(
                    payload.primary, dst=glb, overwrite=True, prompt=False,
                    lightmaps=False, data_export=overlay,
                )
                return {"glb": glb}

        objects = self._scene()
        store = ShotStore.active()
        for shot, text in zip(store.sorted_shots(), ("the fade out", "the fade in")):
            shot.description = text
        store.publish_export_view()
        bridge = WebXrPreview()
        bridge.deliverer = _Build()
        self.addCleanup(mel.eval, "FBXResetExport")
        result = bridge.push(objects=objects, open_browser=False)
        self.assertIsNotNone(result, "the push failed")
        seen = _Build.seen
        self.assertFalse(seen["takes"], "an animation-off FBX carries no take")
        self.assertEqual(list(seen["overlay"]), [ptk.MeshConvert.EFFECT_PREVIEW_KEY])
        stated = seen["overlay"][ptk.MeshConvert.EFFECT_PREVIEW_KEY]["clips"]
        self.assertEqual([c["name"] for c in stated], ["ShotA", "ShotB"])
        for clip in stated:
            self.assertEqual(
                {(c["target"], c["channel"]) for c in clip["curves"]},
                {("fader", "opacity"), ("glower", "highlight"), ("blinker", "visibility")},
                clip["name"],
            )

        with ptk.MeshConvert.open_glb(result["glb"]) as edit:
            record = edit.gltf["extras"][ptk.SceneRecords.SHOTS.key]
        self.assertEqual(
            [(s["clip"], s.get("description")) for s in record["shots"]],
            [("ShotA", "the fade out"), ("ShotB", "the fade in")],
        )
        self.assertEqual(record["fps"], self.FPS)
        played = self._played(result["glb"])
        self.assertEqual(list(played), ["ShotA", "ShotB"], "one clip per shot, in order")
        for name, clip in played.items():
            self.assertLessEqual(
                {("fader", "opacity"), ("glower", "highlight"), ("fader", "gate"), ("blinker", "gate")},
                set(clip),
                name,
            )
            # Each clip spans its shot: 23 frames from its first to its last.
            end = max(times[-1] for times, _values in clip.values())
            self.assertAlmostEqual(end, 23 / self.FPS, places=3, msg=name)
        a, b = played["ShotA"], played["ShotB"]
        # From each shot's start: the fade-out reaches 0 at frame 15 (ShotA,
        # 14 frames in); ShotB opens at 0 and the fade-in leaves it at 30
        # (5 frames in).
        zero_a = [t for t, v in zip(*a[("fader", "opacity")]) if v[3] <= 1e-6]
        zero_b = [t for t, v in zip(*b[("fader", "opacity")]) if v[3] <= 1e-6]
        self.assertAlmostEqual(zero_a[0], 14 / self.FPS, places=3)
        self.assertAlmostEqual(zero_b[0], 0.0, places=3)
        self.assertAlmostEqual(zero_b[-1], 5 / self.FPS, places=3)
        # Blinker off at frame 10 (ShotA, 9 in), on again at frame 30 (ShotB,
        # 5 in), stepped.
        gate_a = [(round(t * self.FPS, 2), int(v[0] > 0)) for t, v in zip(*a[("blinker", "gate")])]
        gate_b = [(round(t * self.FPS, 2), int(v[0] > 0)) for t, v in zip(*b[("blinker", "gate")])]
        self.assertIn((9.0, 0), gate_a)
        self.assertEqual(gate_b[0], (0.0, 0))
        self.assertIn((5.0, 1), gate_b)
        # The pulse peaks in the colour it was keyed with.
        peak = max(a[("glower", "highlight")][1], key=sum)
        self.assertEqual(tuple(round(c, 3) for c in peak), (1.0, 0.5, 0.0))

    def test_a_still_shot_is_a_clip_on_an_animated_and_an_animation_off_push(self):
        """Bug: a shot in which nothing moves -- a hold, a title shot -- had no
        glTF animation (glTF forbids an empty one; FBX2glTF drops the take),
        so the page's picker and sequence lost it, on an animated push and
        on an animation-off one alike; at HEAD it was a window in the
        whole-timeline take. Fixed: 2026-11-07 -- the build makes every
        declared take, and every clip an overlay states, a clip spanning its
        shot, a still one by a hold. ShotC (frames 49-72) holds still; real
        pushes, each build fed what its push handed the deliverer."""
        from mayatk.anim_utils.shots._shots import ShotStore

        objects = self._scene()
        store = ShotStore.active()
        store.define_shot("ShotC", 49, 72, objects=objects, description="the title")
        store.publish_export_view()
        for animated in (True, False):
            with self.subTest(animated=animated):
                glb = self._pushed(objects, f"still_{animated}.glb", INCLUDE_ANIMATION=animated)
                with ptk.MeshConvert.open_glb(glb) as edit:
                    gltf = edit.gltf
                    spans = {
                        a["name"]: ptk.MeshConvert._animation_time_span(edit, a)[1]
                        for a in gltf["animations"]
                    }
                    record = gltf["extras"][ptk.SceneRecords.SHOTS.key]
                    effects = {
                        a["name"]: {
                            ((c.get("extras") or {}).get("render_effects") or {}).get("channel")
                            for c in a["channels"]
                            if c["target"].get("path") == "pointer"
                        }
                        for a in gltf["animations"]
                    }
                self.assertEqual(list(spans), ["ShotA", "ShotB", "ShotC"])
                # From the clip's t=0 to the shot's end: an animated take's
                # t=0 is its first key floored to the converter's 1/30 s grid
                # (frame 49 at 24 fps -> 61/30 s), where its curves are placed
                # too; an overlay's clip opens on the shot's first frame.
                zero = math.floor(49 / self.FPS * 30.0) / 30.0 if animated else 49 / self.FPS
                self.assertAlmostEqual(spans["ShotC"], 72 / self.FPS - zero, places=4)
                self.assertEqual(record["shots"][2], {"clip": "ShotC", "description": "the title"})
                for shot in ("ShotA", "ShotB"):
                    self.assertLessEqual({"opacity", "highlight"}, effects[shot], shot)

    def test_an_emissive_groups_weight_plays_on_an_animation_off_push_as_on_an_animated_one(self):
        """Bug: an emissive group's weight rides its copy's
        ``<material>__emission`` proxy, in the takes -- so a WebXR push with
        animation off (the default) played no group's glow. Fixed: 2026-11-07
        -- ``RenderEffects.effect_curves`` states each animated copy's weight
        (``EmissiveGroups.weight_curves``), and the push's overlay plays it on
        each shot's clip: the copy's emissiveFactor starts, ends and peaks
        where the animated push's does, in each shot. Real pushes."""
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        panel = cmds.polyCube(name="panel", subdivisionsX=2)[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="panel_mat")
        cmds.setAttr(f"{mat}.emission", 2.0)
        cmds.setAttr(f"{mat}.emissionColor", 0.2, 0.6, 1.0, type="double3")
        MatUtils.assign_mat([panel], mat)
        EmissiveGroups.add_group("signal", [f"{panel}.f[0:3]"])
        for frame, value in ((1, 0.0), (20, 1.0), (21, 1.0), (40, 0.0)):
            EmissiveGroups.key_weight("signal", value, frame=frame)
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 1, 20, objects=[panel])
        store.define_shot("ShotB", 21, 40, objects=[panel])
        store.publish_export_view()
        glow = {
            animated: self._emission_by_clip(
                self._pushed([panel], f"glow_{animated}.glb", INCLUDE_ANIMATION=animated)
            )
            for animated in (True, False)
        }
        self.assertEqual(sorted(glow[True]), ["ShotA", "ShotB"], glow[True])
        self.assertEqual(sorted(glow[False]), ["ShotA", "ShotB"], "the animation-off push plays no glow")
        for clip in ("ShotA", "ShotB"):
            on, off = glow[True][clip], glow[False][clip]
            self.assertEqual(off[0], on[0], f"{clip} starts as the animated push's")
            self.assertEqual(off[-1], on[-1], f"{clip} ends as the animated push's")
            self.assertEqual(max(off, key=sum), max(on, key=sum), f"{clip} peaks as it")

    @staticmethod
    def _emission_by_clip(glb):
        """``{clip: [(r, g, b) rounded to 3]}`` each clip's emissiveFactor
        pointer channel samples."""
        import struct

        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf, blob = edit.gltf, edit.bin_data
        out = {}
        for animation in gltf.get("animations") or []:
            for channel in animation["channels"]:
                target = channel["target"]
                pointer = (target.get("extensions") or {}).get("KHR_animation_pointer", {}).get("pointer", "")
                if target.get("path") != "pointer" or not pointer.endswith("/emissiveFactor"):
                    continue
                acc = gltf["accessors"][animation["samplers"][channel["sampler"]]["output"]]
                view = gltf["bufferViews"][acc["bufferView"]]
                offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
                flat = struct.unpack_from(f"<{acc['count'] * 3}f", blob, offset)
                out[animation["name"]] = [
                    tuple(round(c, 3) for c in flat[i : i + 3]) for i in range(0, len(flat), 3)
                ]
        return out

    def _pushed(self, objects, name, **params):
        """A real WebXR push of *objects* under *params*, its build fed what
        the push handed the deliverer (the overlay included); the GLB."""
        from mayatk.env_utils.webxr_preview import WebXrPreview

        glb = os.path.join(self.out, name)

        class _Build(ptk.Deliverer):
            def preflight(self, bridge, request):
                return True

            def deliver(self, bridge, payload, request):
                ptk.MeshConvert.fbx_to_glb(
                    payload.primary, dst=glb, overwrite=True, prompt=False,
                    lightmaps=False, data_export=request.get("data_export"),
                )
                return {"glb": glb}

        bridge = WebXrPreview()
        bridge.deliverer = _Build()
        self.addCleanup(mel.eval, "FBXResetExport")
        result = bridge.push(objects=objects, open_browser=False, **params)
        self.assertIsNotNone(result, "the push failed")
        return result["glb"]

    @staticmethod
    def _played(glb):
        """``{clip: {(node, channel | "gate"): (times, values)}}`` for every
        pointer channel and presence gate *glb* plays."""
        import struct

        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf, blob = edit.gltf, edit.bin_data

        def read(index):
            acc = gltf["accessors"][index]
            per = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[acc["type"]]
            view = gltf["bufferViews"][acc["bufferView"]]
            off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
            flat = struct.unpack_from(f"<{acc['count'] * per}f", blob, off)
            return [flat[i : i + per] for i in range(0, len(flat), per)]

        names = {i: n.get("name") for i, n in enumerate(gltf["nodes"])}
        out = {}
        for animation in gltf.get("animations") or []:
            clip = out.setdefault(animation["name"], {})
            for channel in animation["channels"]:
                target = channel["target"]
                sampler = animation["samplers"][channel["sampler"]]
                if target.get("path") == "pointer":
                    note = (channel.get("extras") or {}).get("render_effects") or {}
                    key = (note.get("node"), note.get("channel"))
                elif target.get("path") == "scale":
                    key = (names.get(target.get("node")), "gate")
                else:
                    continue
                clip.setdefault(
                    key, ([t[0] for t in read(sampler["input"])], read(sampler["output"]))
                )
        return out

    def test_a_material_proxy_on_the_carrier_rides_the_transport(self):
        """A material target (what the emissive groups write in A2): a
        ``<material>__emission`` proxy under the carrier is read off the FBX
        per take, the gate names it unclaimed until the record does, and with
        the record naming it the GLB animates that material's emission."""
        from mayatk.mat_utils._mat_utils import MatUtils

        objects = self._scene()
        screen = cmds.shadingNode("standardSurface", asShader=True, name="screenMat")
        cmds.setAttr(f"{screen}.emission", 1.0)
        MatUtils.assign_mat([objects[2]], screen)
        fbx = os.path.join(self.out, "material_target.fbx")
        # Inside the export bracket, as a stager stages it (the bracket's
        # pre-clean takes every marked proxy it did not stage).
        self.addCleanup(mel.eval, "FBXResetExport")
        with FbxUtils.export_prepared(FbxUtils.export_context()):
            carrier = DataNodes.get_export_node(create=True)
            proxy = cmds.createNode(
                "transform", name="screenMat__emission", parent=carrier, skipSelect=True
            )
            cmds.addAttr(proxy, longName=RenderEffects.PROXY_MARKER, attributeType="bool")
            cmds.setAttr(f"{proxy}.{RenderEffects.PROXY_MARKER}", True)
            for frame, value in ((1, 0.0), (24, 1.0), (25, 1.0), (48, 0.0)):
                cmds.setKeyframe(proxy, attribute="scaleX", time=frame, value=value)
            FbxUtils.apply_takes([("ShotA", 1, 24), ("ShotB", 25, 48)])
            try:
                cmds.select(objects + [carrier], replace=True)
                mel.eval("FBXExportInAscii -v false")
                mel.eval(f'FBXExport -f "{fbx.replace(chr(92), "/")}" -s')
            finally:
                FbxUtils.reset_takes()
                cmds.delete(proxy)

        curves = ptk.MeshConvert.render_effect_curves(fbx)
        self.assertIn(("screenMat", "emission"), curves["ShotA"])
        verdict = ptk.ExportVerifier(fbx=fbx, sidecar=None).run(["check_fbx_render_effects"])
        self.assertEqual(
            [row.detail for row in verdict.rows],
            ["a 'screenMat__emission' proxy the record does not name"],
        )
        record = ptk.MeshConvert.build_visibility_tracks(
            [{"node": "fader", "channels": ["opacity"]}],
            materials=[{"material": "screenMat", "channels": ["emission"], "emission_strength": 4.0}],
        )
        glb = os.path.join(self.out, "material_target.glb")
        ptk.MeshConvert.fbx_to_glb(
            fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False,
            data_export={ptk.SceneRecords.VISIBILITY.key: record},
        )
        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
        index = next(i for i, m in enumerate(gltf["materials"]) if m.get("name") == "screenMat")
        pointers = {
            animation["name"]: [
                c["target"]["extensions"]["KHR_animation_pointer"]["pointer"]
                for c in animation["channels"]
                if c["target"].get("path") == "pointer"
            ]
            for animation in gltf["animations"]
        }
        for clip in ("ShotA", "ShotB"):
            self.assertIn(f"/materials/{index}/emissiveFactor", pointers[clip], clip)
        self.assertEqual(
            gltf["materials"][index]["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"],
            4.0,
        )
