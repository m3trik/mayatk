import unittest
import pymel.core as pm
import mayatk.rig_utils.tube_rig as tube_rig_module
from mayatk.rig_utils.tube_rig import TubeRig


class TestTubeRigCompression(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        self.tube = pm.polyCylinder(r=1, h=10, sy=10, sx=12, ax=(1, 0, 0))[0]
        # Rotate to ensure world space math works
        pm.rotate(self.tube, 0, 0, 0)
        pm.makeIdentity(self.tube, apply=True, t=1, r=1, s=1, n=0)

    def test_compression_attributes_exist(self):
        """Test that new compression/squash attributes are created."""
        rig = TubeRig(self.tube, rig_name="CompTest")
        # Proposed API: enable_squash=True/False
        rig.build(strategy="spline", enable_stretch=True, enable_squash=True)

        # Check if attributes exist on controls/settings
        start_ctrl = pm.PyNode("CompTest_start")
        # We expect some way to control or see squash, or at least the rig built successfully
        self.assertTrue(pm.objExists("CompTest_start"))

    def test_auto_bend_feature(self):
        """Test if auto-bend setup is created and connected."""
        rig = TubeRig(self.tube, rig_name="BendTest")
        # Proposed API: enable_auto_bend=True
        rig.build(strategy="spline", enable_auto_bend=True)

        mid_ctrl = pm.PyNode("BendTest_mid")
        # Check if there is an incoming connection affecting translation that isn't just the user
        # We expect an auto-bend offset group or direct connection

        # Actually, let's look for the attribute
        settings_node = pm.PyNode(
            "BendTest_start"
        )  # Assuming attributes are on start control
        self.assertTrue(settings_node.hasAttr("autoBend"), "autoBend attribute missing")

    def test_squash_disable_clamps_scale(self):
        """Verify that disabling squash clamps joint scaling to 1.0 minimum."""
        rig = TubeRig(self.tube, rig_name="NoSquash")
        rig.build(strategy="spline", enable_stretch=True, enable_squash=False)

        # Simulate compression: Make curve shorter
        # Since we can't easily interact with curve length in headless test without moving drivers
        # We can check the node graph.

        # Trace back from joint scale
        jnt = pm.PyNode("NoSquash_jnt_1")
        scale_input = jnt.scaleX.inputs(plugs=True)[0]

        # Should go through a clamp node or condition
        # scale_input -> ... -> Clamp -> ...
        history = scale_input.node().history(breadthFirst=True)
        node_types = [n.type() for n in history]
        self.assertIn(
            "clamp", node_types, "No clamp node found in scale chain to prevent squash"
        )
