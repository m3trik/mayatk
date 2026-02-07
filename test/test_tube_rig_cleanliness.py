import unittest
import pymel.core as pm
from mayatk.rig_utils.tube_rig import TubeRig


class TestTubeRigCleanExport(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        # Create a simple tube mesh
        self.tube = pm.polyCylinder(r=1, h=10, sy=10, sx=12, ax=(1, 0, 0))[0]
        # Rotate it to be somewhat interesting
        pm.rotate(self.tube, 0, 45, 45)
        # Freeze transforms so bounding box logic works as expected (usually good practice)
        pm.makeIdentity(self.tube, apply=True, t=1, r=1, s=1, n=0, pn=1)

    def test_spline_mode_cleanliness(self):
        """Verify no empty groups are left at root after Spline rig build."""
        rig = TubeRig(self.tube, rig_name="SplineTest")
        rig.build(strategy="spline")

        root_nodes = pm.ls(assemblies=True)
        root_names = [n.name() for n in root_nodes]

        orphaned_groups = [
            n for n in root_names if n.endswith("_GRP") and n != rig.rig_group.name()
        ]

        self.assertEqual(
            orphaned_groups, [], f"Found orphaned groups at root: {orphaned_groups}"
        )

        # Also check internal parenting structure
        # Start control should be under start_GRP, which is under rig_group
        start_ctrl = pm.PyNode("SplineTest_start_CTRL")
        start_grp = start_ctrl.getParent()
        # controls.py logic usually appends _GRP to the control name for the offset group
        # Control: SplineTest_start_CTRL
        # Group: SplineTest_start_CTRL_GRP
        self.assertTrue(
            start_grp.name().endswith("_CTRL_GRP"),
            f"Control parent is {start_grp}, expected *_CTRL_GRP",
        )
        self.assertEqual(
            start_grp.getParent(),
            rig.rig_group,
            "Control Group should be parented to Rig Group",
        )

    def test_anchor_mode_cleanliness(self):
        """Verify no empty groups are left at root after Anchor rig build."""
        rig = TubeRig(self.tube, rig_name="AnchorTest")
        rig.build(strategy="anchor")

        root_nodes = pm.ls(assemblies=True)
        root_names = [n.name() for n in root_nodes]

        orphaned_groups = [
            n for n in root_names if n.endswith("_GRP") and n != rig.rig_group.name()
        ]

        self.assertEqual(
            orphaned_groups, [], f"Found orphaned groups at root: {orphaned_groups}"
        )

        # Check hierarchy
        start_ctrl = pm.PyNode("AnchorTest_start_CTRL")
        start_grp = start_ctrl.getParent()
        self.assertEqual(
            start_grp.getParent(),
            rig.rig_group,
            "Control Group should be parented to Rig Group",
        )

    def test_anchor_scale_rig_group(self):
        """Test if scaling the rig group causes double transforms on Anchor joints."""
        rig = TubeRig(self.tube, rig_name="ScaleTest")
        rig.build(strategy="anchor")

        # Scale the rig group
        rig.rig_group.setScale([2.0, 2.0, 2.0])

        # Check joints
        j1 = pm.PyNode("ScaleTest_start_jnt")
        ws = pm.xform(j1, q=True, ws=True, s=True)

        # We expect 2.0. If 4.0, we have double transform.
        self.assertAlmostEqual(
            ws[0],
            2.0,
            places=3,
            msg="Joint X scale incorrect (Double Transform scaling failure)",
        )
        self.assertAlmostEqual(ws[1], 2.0, places=3, msg="Joint Y scale incorrect")

    def test_spline_scale_rig_group(self):
        """Test if scaling the rig group causes double transforms on Spline joints."""
        rig = TubeRig(self.tube, rig_name="SplineScaleTest")
        rig.build(strategy="spline")

        # Scale the rig group
        rig.rig_group.setScale([2.0, 2.0, 2.0])

        # Check bind joints (not driver joints)
        joints = rig.bundle.joints
        # Pick middle joint
        jnt = joints[len(joints) // 2]

        ws = pm.xform(jnt, q=True, ws=True, s=True)

        self.assertAlmostEqual(
            ws[0], 2.0, places=3, msg="Spline Joint X scale incorrect"
        )


if __name__ == "__main__":
    unittest.main()
