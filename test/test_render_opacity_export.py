# !/usr/bin/python
# coding=utf-8
import json
import os
import maya.cmds as cmds
import maya.mel as mel
import pythontk as ptk
from pythontk import MeshConvert
from mayatk.mat_utils.render_opacity._render_opacity import RenderOpacity
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.env_utils.fbx_utils import FbxUtils
from base_test import MayaTkTestCase


class TestRenderOpacityExport(MayaTkTestCase):
    """Verify that RenderOpacity attributes export correctly for Unity."""

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
        RenderOpacity.create(objects=[self.cube], mode="attribute")

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
        RenderOpacity.create(objects=[self.cube], mode="attribute")

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
        RenderOpacity.create(objects=objects, mode="attribute")

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

        RenderOpacity.create(objects=[self.cube], mode="attribute")

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

        RenderOpacity.create(objects=[self.cube], mode="attribute")

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

        # No RenderOpacity.create — start from a plain object
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
    """The ``visibility_tracks`` channel — the glTF route's only path in.

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

    def _publish_shots(self, takes, fps=30.0):
        """Publish the shot record as ``ShotStore.publish_export_view`` writes
        it: each clip carrying its own range (*takes* as ``{"name", "start",
        "end"}``), the rate on the envelope."""
        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {
                "fps": fps,
                "shots": [
                    {
                        "clip": t["name"],
                        "start": t["start"],
                        "end": t["end"],
                        "objects": [],
                    }
                    for t in takes
                ],
            },
        )

    def test_a_stepped_fade_publishes_both_channels(self):
        """``key_fade`` writes a linear opacity ramp and a stepped vis mirror."""
        RenderOpacity.key_fade([self.grp], start=8, end=23, direction="in")

        tracks = RenderOpacity.visibility_tracks()

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["node"], "GATE_LOC")
        self.assertEqual(tracks[0]["visibility"], [[8.0, 0.0], [23.0, 1.0]])
        self.assertEqual(tracks[0]["opacity"], [[8.0, 0.0], [23.0, 1.0]])

    def test_the_published_tracks_do_not_depend_on_the_export_preparer(self):
        """Producer order cannot matter when no preparer writes what the walk
        reads: the channel published before ``prepare_for_export`` must equal
        the one published after it and its ``finish_export``. A hand-keyed
        opacity is the case a visibility repair used to change."""
        RenderOpacity.create(objects=[self.grp], mode="attribute")
        cmds.setKeyframe(self.grp, attribute="opacity", time=8, value=0.0)
        cmds.setKeyframe(self.grp, attribute="opacity", time=23, value=1.0)

        RenderOpacity.refresh_export_metadata()
        before = self._carrier(RenderOpacity.DATA_CHANNEL)
        try:
            RenderOpacity.prepare_for_export()
        finally:
            RenderOpacity.finish_export()
        RenderOpacity.refresh_export_metadata()
        after = self._carrier(RenderOpacity.DATA_CHANNEL)

        self.assertTrue(before and before["tracks"], "a vacuous comparison")
        self.assertEqual(after, before)

    def test_the_channel_carries_the_rate_and_each_clip_zero(self):
        """A frame number is unitless, and a take is rebased on its first key.

        ``clip_span`` is the only place that zero can come from: the converter
        counts visibility keys when sizing a take but emits no channel for
        them, so the shipped clip cannot report where its own zero is.
        """
        RenderOpacity.key_fade([self.grp], start=8, end=23, direction="in")
        RenderOpacity.key_fade([self.grp], start=1000, end=1015, direction="out")
        self._publish_shots(
            [
                {"name": "Shot_1", "start": 7, "end": 100},
                {"name": "Shot_5", "start": 915, "end": 1015},
            ]
        )

        RenderOpacity.refresh_export_metadata()
        published = self._carrier(RenderOpacity.DATA_CHANNEL)

        self.assertEqual(published["version"], RenderOpacity.SCHEMA_VERSION)
        self.assertEqual(published["fps"], 30.0)
        # Shot_1's window opens at 7, but its first authored key is at 8.
        self.assertEqual(published["clip_span"]["Shot_1"], [8.0, 23.0])
        self.assertEqual(published["clip_span"]["Shot_5"], [1000.0, 1015.0])

    def test_a_legacy_take_list_still_sizes_the_clip_spans(self):
        """A scene published before the ranges moved onto the clips holds its
        take list in ``fbx_takes`` beside a range-less shot record; the spans
        come out the same (``ptk.SceneRecords.declared_takes`` falls back)."""
        RenderOpacity.key_fade([self.grp], start=8, end=23, direction="in")
        ptk.SceneRecords.SHOTS.save(DataNodes, {"fps": 30.0, "shots": []})
        ptk.SceneRecords.FBX_TAKES.save(
            DataNodes, [{"name": "Shot_1", "start": 7, "end": 100}]
        )

        RenderOpacity.refresh_export_metadata()

        self.assertEqual(
            self._carrier(RenderOpacity.DATA_CHANNEL)["clip_span"]["Shot_1"],
            [8.0, 23.0],
        )

    def test_a_curve_that_is_no_timeline_channel_does_not_size_a_take(self):
        """The spans mirror the converter's take sizing, which reads the
        channels the FBX carries. A curve driving nothing (an export
        snapshot's stash, a leftover) is no channel, yet its keys were read as
        key times and opened this take at 7.5 instead of 8 (2026-09-14). The
        driven key rides along because its inputs are driver values, not
        frames: a seek over the curves has to skip it, not misread it."""
        RenderOpacity.key_fade([self.grp], start=8, end=23, direction="in")
        holder = cmds.createNode("transform", name="span_holder")
        cmds.setKeyframe(holder, attribute="translateX", time=7.5, value=0)
        stash = cmds.keyframe(f"{holder}.translateX", query=True, name=True)[0]
        cmds.disconnectAttr(f"{stash}.output", f"{holder}.translateX")
        driver = cmds.spaceLocator(name="span_driver")[0]
        for driver_value, value in ((7.2, 0.0), (30.0, 1.0)):
            cmds.setDrivenKeyframe(
                f"{holder}.translateZ",
                currentDriver=f"{driver}.translateY",
                driverValue=driver_value,
                value=value,
            )
        self._publish_shots([{"name": "Shot_1", "start": 7, "end": 100}])

        RenderOpacity.refresh_export_metadata()

        self.assertEqual(
            self._carrier(RenderOpacity.DATA_CHANNEL)["clip_span"]["Shot_1"],
            [8.0, 23.0],
        )

    def test_the_whole_timeline_zero_is_the_range_that_ships(self):
        """``*`` is the source stack's zero, and the stack ships only the
        BAKED RANGE.

        ``set_bake_animation_range`` narrows the export to the takes' union
        before this publishes, so a key authored outside that range never
        reaches the FBX -- yet the scene's first key was still setting the
        whole-timeline zero. The converter rebases the stack onto its first
        SHIPPED key, so the two disagreed by exactly the range start and every
        clip cut from that stack slid by that many frames. Measured on the
        PROPS assembly: the first take started at 33 while the scene's first
        key sat at 0, and all eight shots played 33 frames early -- up to
        90 cm of apparent mesh 'distortion' with the geometry itself exact
        (a -33 frame offset restored a 0.0001 cm match).
        """
        RenderOpacity.key_fade([self.grp], start=0, end=4, direction="in")
        RenderOpacity.key_fade([self.grp], start=40, end=60, direction="out")
        self._publish_shots([{"name": "Shot_A", "start": 33, "end": 60}])
        # What set_bake_animation_range leaves behind before this publishes:
        # the FBX plugin's BAKE range, not the playback range (the playback
        # range still starts at 0 here, which is exactly the trap).
        if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 33")
        mel.eval("FBXExportBakeComplexEnd -v 60")
        self.addCleanup(mel.eval, "FBXResetExport")

        RenderOpacity.refresh_export_metadata()
        published = self._carrier(RenderOpacity.DATA_CHANNEL)

        self.assertEqual(
            published["clip_span"]["*"],
            [33.0, 60.0],
            "the whole-timeline zero must be the first frame that SHIPS, not "
            "a scene key the FBX never carries",
        )

    def test_the_measured_zero_is_an_input_so_a_republish_keeps_it(self):
        """The clip origin rides the export context, never a patch.

        ``*`` is the first frame the exported stack CARRIES, measured from the
        keys that ship -- NOT the bake range: the range bounds what the plugin
        re-bakes, while an authored curve is written whole (a curve keyed
        0-100 exports as 0-100 under a 20-80 range, measured on Maya 2025 /
        FBX 2020.3.6). The pipeline used to publish that measurement AFTER the
        producers, and the export bracket's second producer run discarded it:
        three PROPS exports shipped 18 shots cut 81 frames early, each logging
        the right number. Now the measurement is the context's ``clip_span``,
        the producer reads it, and producing the record again with the same
        decision yields the same record.
        """
        RenderOpacity.key_fade([self.grp], start=0, end=4, direction="in")
        RenderOpacity.key_fade([self.grp], start=40, end=60, direction="out")
        self._publish_shots([{"name": "Shot_A", "start": 33, "end": 60}])
        if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        mel.eval("FBXExportBakeComplexAnimation -v true")
        # The preset default, untouched -- what a publish without a
        # measurement seeds from.
        mel.eval("FBXExportBakeComplexStart -v 0")
        mel.eval("FBXExportBakeComplexEnd -v 10000")
        self.addCleanup(mel.eval, "FBXResetExport")

        RenderOpacity.refresh_export_metadata()
        self.assertEqual(
            self._carrier(RenderOpacity.DATA_CHANNEL)["clip_span"]["*"],
            [0.0, 10000.0],
            "precondition: with no measurement the seed is the preset's range",
        )

        # The task that owns the range runs, as it does last. "scene" mode,
        # because it resolves from the playback range alone.
        import logging

        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        cmds.playbackOptions(animationStartTime=33, animationEndTime=60)
        tm = TaskManager(logging.getLogger("test_clip_origin"))
        # The origin describes what SHIPS, so the pipeline needs the export
        # set: the measurement is over it.
        tm.objects = cmds.ls(self.grp, long=True)
        tm.set_bake_animation_range("scene")

        for attempt in ("the publish", "a second publish with the same decision"):
            tm._publish_scene_records(only=[ptk.SceneRecords.VISIBILITY])
            published = self._carrier(RenderOpacity.DATA_CHANNEL)
            self.assertEqual(
                published["clip_span"]["*"],
                [0.0, 60.0],
                f"{attempt}: the clip origin must be the first frame that "
                "SHIPS -- the fades are keyed from 0 and the FBX carries them, "
                "whatever the 33-60 bake range says",
            )
        self.assertEqual(
            (
                mel.eval("FBXExportBakeComplexStart -q"),
                mel.eval("FBXExportBakeComplexEnd -q"),
            ),
            (33, 60),
            "and the RANGE still follows the selected mode -- the two are "
            "different numbers, which is the whole point",
        )
        # Only the whole-timeline entry moves; a take's span is its own keys.
        self.assertEqual(published["clip_span"]["Shot_A"], [40.0, 60.0])

    def test_a_publish_with_no_keyed_visibility_clears_the_channel(self):
        """No carrier is made to hold an origin, and a stale channel does not
        outlive its curves, whatever span the exporter measured.
        """
        ctx = FbxUtils.export_context(clip_span=(33, 60))
        ptk.SceneRecords.VISIBILITY.clear(DataNodes)
        FbxUtils.publish(ctx, only=[ptk.SceneRecords.VISIBILITY])
        self.assertIsNone(DataNodes.get_export_node(create=False))

        stale = {"version": 1, "tracks": []}
        ptk.SceneRecords.VISIBILITY.save(DataNodes, stale)
        FbxUtils.publish(ctx, only=[ptk.SceneRecords.VISIBILITY])
        self.assertFalse(ptk.SceneRecords.VISIBILITY.is_present(DataNodes))

    def test_a_scene_with_no_keyed_visibility_leaves_no_channel(self):
        """An empty carrier is worse than no carrier."""
        self.assertIsNone(RenderOpacity.refresh_export_metadata())
        self.assertFalse(ptk.SceneRecords.VISIBILITY.is_present(DataNodes))

    def test_a_stepped_hold_is_published_as_a_hold_not_a_ramp(self):
        """The ramp is consumed by LINEAR interpolation, so a step has to be
        stated rather than left to be guessed.

        Measured on a production assembly: ``REPAIRED_CMPT_LOC.opacity`` reads
        ``linear, step, step, linear``, so Maya holds it at 1.0 from frame 23
        to 1983 and cuts. Publishing the four keys alone makes every consumer
        invent a fifteen-frame fade-out the scene does not have -- and the GLB
        then played that invented fade for seven frames of Shot_9.
        """
        plug = f"{self.grp}.opacity"
        RenderOpacity.create([self.grp], mode="attribute")
        for frame, value in ((8, 0.0), (23, 1.0), (1968, 1.0), (1983, 0.0)):
            cmds.setKeyframe(plug, time=frame, value=value)
        cmds.keyTangent(plug, edit=True, time=(23, 1968), outTangentType="step")

        ramp = RenderOpacity._linear_ramp(plug)

        by_frame = {round(f, 3): v for f, v in ramp}
        self.assertEqual(by_frame[8.0], 0.0)
        self.assertEqual(by_frame[23.0], 1.0)
        # The hold, stated: still 1.0 a hundredth of a frame before the cut.
        self.assertEqual(by_frame[1967.99], 1.0)
        self.assertEqual(by_frame[1982.99], 1.0)
        self.assertEqual(by_frame[1983.0], 0.0)
        # And it still reads as a fade, so it is gated and published as one.
        self.assertTrue(MeshConvert._is_fade([(f, v) for f, v in ramp]))

    def test_a_linear_ramp_is_published_unchanged(self):
        """Nothing is added where Maya already agrees with the consumer."""
        RenderOpacity.key_fade([self.grp], start=8, end=23, direction="in")
        self.assertEqual(
            RenderOpacity._linear_ramp(f"{self.grp}.opacity"),
            [[8.0, 0.0], [23.0, 1.0]],
        )

    def test_a_non_stepped_curve_is_read_as_maya_actually_evaluates_it(self):
        """Visibility is a BOOLEAN, and Maya does not interpolate booleans.

        Measured on Maya 2025: a LINEAR fade-out from 1 to 0 over frames 10-20
        holds fully visible until frame 20 and then switches — the tangents are
        accepted and ignored, so the curve steps whatever it claims. That is
        why this track is sampled from the evaluated plug rather than inferred
        from the tangent type: the published timeline has to be what the DCC
        draws, and two plausible readings of this curve (visible from 11, or a
        gradient) are both wrong.
        """
        plug = f"{self.grp}.visibility"
        cmds.setKeyframe(
            plug, time=10, value=1, inTangentType="linear", outTangentType="linear"
        )
        cmds.setKeyframe(
            plug, time=20, value=0, inTangentType="linear", outTangentType="linear"
        )

        track = RenderOpacity.visibility_tracks()[0]["visibility"]

        self.assertEqual(track, [[10.0, 1.0], [20.0, 0.0]])

    def test_the_export_hook_reaches_this_producer(self):
        """Registered in ``FbxUtils.PRODUCERS``, so an export's publish refreshes it."""
        RenderOpacity.key_fade([self.grp], start=8, end=23, direction="in")
        ptk.SceneRecords.VISIBILITY.clear(DataNodes)

        FbxUtils.publish()

        self.assertTrue(ptk.SceneRecords.VISIBILITY.is_present(DataNodes))


class TestVisibilityChannelFrameRate(MayaTkTestCase):
    """The visibility channel always carries a frame rate.

    A shot-less scene clears ``shot_metadata``, and the producer used to
    forward its (absent) rate: the GLB appliers then could not place the
    frames in time and dropped every track and ramp -- "carry no frame rate,
    not applied" (measured 2026-09-02). The scene's own rate is the fallback.
    """

    def test_shotless_scene_publishes_scene_rate(self):
        from mayatk.audio_utils._audio_utils import AudioUtils
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )

        loc = cmds.spaceLocator(name="fps_loc")[0]
        OpacityAttributeMode.create([loc])
        cmds.setKeyframe(loc, attribute="opacity", t=1, v=1.0)
        cmds.setKeyframe(loc, attribute="opacity", t=10, v=0.0)
        OpacityAttributeMode.sync_visibility_from_opacity([loc])
        self.assertIsNone(ptk.SceneRecords.SHOTS.read_text(DataNodes))

        raw = RenderOpacity.refresh_export_metadata()
        self.assertTrue(raw)
        channel = json.loads(raw)
        self.assertAlmostEqual(channel["fps"], AudioUtils.get_fps(), places=3)
        self.assertEqual(channel["tracks"][0]["node"], "fps_loc")


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
        RenderOpacity.key_pulse(
            [self.cube], start=8, end=108, period=50, color=(0.2, 0.5, 1.0)
        )
        tracks = RenderOpacity.visibility_tracks()
        self.assertEqual(len(tracks), 1)
        track = tracks[0]
        self.assertEqual(track["node"], "glow")
        self.assertNotIn("visibility", track)
        # Opens DIM: the pulse is bracketed so the consumer's backward hold is
        # "not highlighted" rather than a glow reaching back to frame one.
        self.assertEqual(track["highlight"][0], [8.0, 0.0])
        self.assertEqual(max(v for _f, v in track["highlight"]), 1.0)
        self.assertEqual(
            [round(c, 3) for c in track["highlight_color"]], [0.2, 0.5, 1.0]
        )

    def test_a_faded_and_highlighted_node_publishes_both_on_one_track(self):
        RenderOpacity.key_fade([self.cube], start=8, end=23, direction="in")
        RenderOpacity.key_pulse([self.cube], start=8, end=108, period=50)
        tracks = RenderOpacity.visibility_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(
            set(tracks[0]) >= {"node", "visibility", "opacity", "highlight"}, True
        )

    def test_the_export_bracket_stages_a_curve_proxy_that_reaches_the_fbx(self):
        """One child per keyed channel, marked, with the curve on scale.x -- and gone after."""
        RenderOpacity.key_pulse([self.cube], start=1, end=10, period=10)
        fbx = self.temp_path("render_effects_proxy.fbx")
        cmds.select(self.cube, replace=True)
        with FbxUtils.export_prepared():
            # The bracket restores the selection the preparers may have moved.
            self.assertEqual(cmds.ls(selection=True), [self.cube])
            proxies = cmds.ls("glow__highlight", long=True)
            self.assertEqual(len(proxies), 1)
            proxy = proxies[0]
            self.assertTrue(cmds.getAttr(f"{proxy}.{RenderOpacity.PROXY_MARKER}"))
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
        self.assertIn(f'P: "{RenderOpacity.PROXY_MARKER}"', text)

    def test_a_hand_keyed_opacity_is_gated_in_the_preview_and_never_mirrored(self):
        """Opacity keyed by hand, no visibility anywhere, through the WebXR
        preview's ONE export bracket: the GLB gates the node (presence comes
        from the ramp) and the scene's ``visibility`` stays unkeyed -- no
        export step writes a mirror."""
        import pythontk as ptk
        from mayatk.env_utils.webxr_preview import WebXrPreview

        RenderOpacity.create(objects=[self.cube], mode="attribute")
        for frame, value in ((1, 0.0), (15, 1.0), (40, 1.0), (55, 0.0)):
            cmds.setKeyframe(self.cube, attribute="opacity", time=frame, value=value)
        plug = f"{cmds.ls(self.cube, long=True)[0]}.visibility"
        self.assertFalse(cmds.keyframe(plug, q=True, keyframeCount=True))

        bridge = WebXrPreview()
        fbx = self.temp_path("hand_keyed_opacity.fbx")
        bridge._export_fbx([self.cube], fbx, dict(bridge.params_defaults()))
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
        RenderOpacity.key_pulse(
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
            overlay = RenderOpacity.preview_channels(
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
        bridge._export_fbx([self.cube], fbx, dict(bridge.params_defaults()))

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
        # FBX carries no standardSurface emission (the sidecar does, when
        # asked): the bases hold the format default, never a driven frame value.
        self.assertTrue(all(max(b) < 1e-6 for b in bases), f"no leaked value: {bases}")
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
            # The same envelope schema from the same readers, and the same
            # texture-policy keys -- the dials differ, the mechanism does not.
            self.assertEqual(
                build["sidecar"]["version"], ptk.MeshConvert.SIDECAR_VERSION
            )
            self.assertIn("emissive", build["sidecar"]["sections"])
            self.assertLessEqual(
                {"image_format", "max_size"}, set(build["texture_params"])
            )
