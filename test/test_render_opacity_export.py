# !/usr/bin/python
# coding=utf-8
import os
import unittest
import pymel.core as pm
import maya.cmds as cmds
from mayatk.mat_utils.render_opacity._render_opacity import RenderOpacity
from base_test import MayaTkTestCase


class TestRenderOpacityExport(MayaTkTestCase):
    """Verify that RenderOpacity attributes export correctly for Unity."""

    def setUp(self):
        super().setUp()
        self.cube = pm.polyCube(name="export_cube")[0]
        # Use workspace path for debugging visibility
        self.fbx_path = os.path.join(
            r"O:/Cloud/Code/_scripts/mayatk/test", "debug_opacity.fbx"
        )

        # Ensure FBX plugin is loaded
        if not pm.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                pm.loadPlugin("fbxmaya")
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
        pm.select(self.cube)

        # Configure FBX for ASCII export (human readable)
        # Note: Options string format depends on plugin version, but 'Ascii' is standard
        # -v 0: verbose off
        # -es 1: export selected
        # -type "FBX export"

        # Set FBX settings via MEL (most reliable method)
        pm.mel.eval("FBXExportInAscii -v true")
        pm.mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')

        self.assertTrue(os.path.exists(self.fbx_path), "FBX file was not created")

        # Read the file and check for the attribute
        with open(self.fbx_path, "r") as f:
            content = f.read()

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
        self.cube.opacity.set(1.0)
        pm.setKeyframe(self.cube, attribute="opacity", t=1)
        self.cube.opacity.set(0.0)
        pm.setKeyframe(self.cube, attribute="opacity", t=10)

        pm.select(self.cube)
        pm.mel.eval("FBXExportInAscii -v true")
        pm.mel.eval("FBXExportBakeComplexAnimation -v false")  # Export curves directly
        pm.mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')

        with open(self.fbx_path, "r") as f:
            content = f.read()

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
        self.fbx_path = os.path.join(
            r"O:/Cloud/Code/_scripts/mayatk/test", "debug_shared_mat.fbx"
        )

        # Ensure FBX plugin
        if not pm.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                pm.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

        # Create three objects sharing one lambert material and UV set
        self.mat = pm.shadingNode("lambert", asShader=True, name="shared_mat")
        self.sg = pm.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="shared_sg"
        )
        pm.connectAttr(self.mat.outColor, self.sg.surfaceShader)

        self.objects = []
        for i, name in enumerate(["piece_A", "piece_B", "piece_C"]):
            obj = pm.polyCube(name=name)[0]
            pm.move(obj, i * 3, 0, 0)
            pm.sets(self.sg, forceElement=obj)
            self.objects.append(obj)

    def tearDown(self):
        super().tearDown()

    def _export_fbx(self, objects, animate=False):
        """Apply opacity, optionally keyframe, export selected, return content."""
        RenderOpacity.create(objects=objects, mode="attribute")

        if animate:
            for i, obj in enumerate(objects):
                obj.opacity.set(1.0)
                pm.setKeyframe(obj, attribute="opacity", t=1)
                obj.opacity.set(0.0)
                pm.setKeyframe(obj, attribute="opacity", t=10 + i * 5)

        pm.select(objects)
        pm.mel.eval("FBXExportInAscii -v true")
        if animate:
            pm.mel.eval("FBXExportBakeComplexAnimation -v false")
        pm.mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')
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
                obj.nodeName(),
                content,
                f"Object '{obj.nodeName()}' missing from FBX",
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
            self.objects[2].nodeName(),
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
        self.fbx_path = os.path.join(
            r"O:/Cloud/Code/_scripts/mayatk/test", "debug_dual_key.fbx"
        )
        if not pm.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                pm.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")

        self.cube = pm.polyCube(name="dual_key_cube")[0]

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.fbx_path):
            try:
                os.remove(self.fbx_path)
            except OSError:
                pass

    def _export_ascii_fbx(self):
        pm.select(self.cube)
        pm.mel.eval("FBXExportInAscii -v true")
        pm.mel.eval("FBXExportBakeComplexAnimation -v false")
        pm.mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')
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
        pm.setKeyframe(self.cube, attribute="opacity", time=1, value=1.0)
        pm.setKeyframe(self.cube, attribute="opacity", time=15, value=0.0)
        pm.setKeyframe(self.cube, attribute="opacity", time=30, value=1.0)

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
        from mayatk.anim_utils.shots.behaviors import apply_behavior

        RenderOpacity.create(objects=[self.cube], mode="attribute")

        # Apply the fade_in behavior (template targets 'visibility')
        apply_behavior(self.cube.name(), "fade_in", start=1, end=30)

        # Verify both channels are keyed in Maya
        opacity_keys = pm.keyframe(self.cube, attribute="opacity", q=True, tc=True)
        vis_keys = pm.keyframe(self.cube, attribute="visibility", q=True, tc=True)
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

    def test_no_opacity_attr_falls_back_to_visibility_only(self):
        """Objects without opacity attribute get visibility-only keys (unchanged).

        This verifies we didn't break the standard path for non-opacity
        objects.
        """
        from mayatk.anim_utils.shots.behaviors import apply_behavior

        # No RenderOpacity.create — just plain object
        apply_behavior(self.cube.name(), "fade_in", start=1, end=30)

        vis_keys = pm.keyframe(self.cube, attribute="visibility", q=True, tc=True)
        self.assertTrue(vis_keys, "Visibility should have keyframes")

        # Opacity attribute should NOT exist
        self.assertFalse(
            self.cube.hasAttr("opacity"),
            "Object without opacity setup should not gain opacity attr",
        )

        content = self._export_ascii_fbx()
        self.assertIn(
            '"AnimCurveNode::Visibility"',
            content,
            "Visibility animation curve missing for non-opacity object",
        )
