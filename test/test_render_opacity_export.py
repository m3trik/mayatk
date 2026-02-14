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
        self.fbx_path = os.path.join(r"O:/Cloud/Code/_scripts/mayatk/test", "debug_opacity.fbx")
        
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
        found_prop = 'P: "opacity", "Number"' in content or 'P: "opacity", "Double"' in content
        
        if not found_prop:
            # Fallback check: sometimes it's explicitly a User Property block
            # But standard custom attrs usually become top-level properties on the Model
            pass
            
        self.assertTrue(found_prop, f"Exported FBX missing 'opacity' property definition. Content sample: {content[:1000]}")

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
        pm.mel.eval("FBXExportBakeComplexAnimation -v false") # Export curves directly
        pm.mel.eval(f'FBXExport -f "{self.fbx_path.replace(os.sep, "/")}" -s')
        
        with open(self.fbx_path, "r") as f:
            content = f.read()
            
        # Check for AnimationCurveNode for "opacity"
        # Example: AnimationCurveNode: 2136056071056, "AnimCurveNode::opacity", ""
        self.assertIn('"AnimCurveNode::opacity"', content, "The opacity animation curve node should be present")
        
        # Also ensure the property exists
        self.assertTrue('P: "opacity", "Number"' in content or 'P: "opacity", "Double"' in content)

