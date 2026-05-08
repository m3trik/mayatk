import unittest
import maya.cmds as cmds
from mayatk.rig_utils.tube_rig import TubeRig


class TestTubeRigCleanExport(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.tube = cmds.polyCylinder(r=1, h=10, sy=10, sx=12, ax=(1, 0, 0))[0]
        cmds.rotate(0, 45, 45, self.tube)
        cmds.makeIdentity(self.tube, apply=True, t=1, r=1, s=1, n=0, pn=1)

    def _orphan_groups(self, rig):
        return [
            n
            for n in (cmds.ls(assemblies=True) or [])
            if n.endswith("_GRP") and n != rig.rig_group
        ]

    def _assert_ctrl_under_rig(self, rig, ctrl_name, require_ctrl_grp_suffix=False):
        ctrl_grp = (cmds.listRelatives(ctrl_name, parent=True) or [None])[0]
        self.assertIsNotNone(
            ctrl_grp, f"Control {ctrl_name} has no parent offset group"
        )
        if require_ctrl_grp_suffix:
            self.assertTrue(
                ctrl_grp.endswith("_CTRL_GRP"),
                f"Control parent is {ctrl_grp}, expected *_CTRL_GRP",
            )
        self.assertEqual(
            (cmds.listRelatives(ctrl_grp, parent=True) or [None])[0],
            rig.rig_group,
            "Control Group should be parented to Rig Group",
        )

    def test_spline_mode_cleanliness(self):
        """Verify no empty groups are left at root after Spline rig build."""
        rig = TubeRig(self.tube, rig_name="SplineTest")
        rig.build(strategy="spline")

        orphans = self._orphan_groups(rig)
        self.assertEqual(orphans, [], f"Found orphaned groups at root: {orphans}")
        self._assert_ctrl_under_rig(
            rig, "SplineTest_start_CTRL", require_ctrl_grp_suffix=True
        )

    def test_anchor_mode_cleanliness(self):
        """Verify no empty groups are left at root after Anchor rig build."""
        rig = TubeRig(self.tube, rig_name="AnchorTest")
        rig.build(strategy="anchor")

        orphans = self._orphan_groups(rig)
        self.assertEqual(orphans, [], f"Found orphaned groups at root: {orphans}")
        self._assert_ctrl_under_rig(rig, "AnchorTest_start_CTRL")

    def _scale_rig_group(self, rig, factor=2.0):
        for axis in "XYZ":
            cmds.setAttr(f"{rig.rig_group}.scale{axis}", factor)

    def test_anchor_scale_rig_group(self):
        """Test if scaling the rig group causes double transforms on Anchor joints."""
        rig = TubeRig(self.tube, rig_name="ScaleTest")
        rig.build(strategy="anchor")
        self._scale_rig_group(rig, 2.0)

        ws = cmds.xform("ScaleTest_start_jnt", q=True, ws=True, s=True)

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
        self._scale_rig_group(rig, 2.0)

        joints = rig.bundle.joints
        jnt = joints[len(joints) // 2]
        ws = cmds.xform(str(jnt), q=True, ws=True, s=True)

        self.assertAlmostEqual(
            ws[0], 2.0, places=3, msg="Spline Joint X scale incorrect"
        )


class TestGetCenterlineUsingEdges(unittest.TestCase):
    """Regression: get_centerline_using_edges feeds plain ``[x, y, z]``
    lists from ``cmds.pointPosition`` into ``ptk.arrange_points_as_path``.

    Bug fixed 2026-05-07: the default ``distance_metric`` did
    ``(p1 - p2).length()`` — only valid for ``om.MPoint`` / PyMEL
    ``dt.Point``. Plain lists raised ``TypeError`` on subtraction.
    """

    def setUp(self):
        cmds.file(new=True, force=True)
        self.tube = cmds.polyCylinder(r=1, h=10, sy=10, sx=12, ax=(1, 0, 0))[0]
        cmds.makeIdentity(self.tube, apply=True, t=1, r=1, s=1, n=0, pn=1)

    def test_returns_ordered_points(self):
        from mayatk.rig_utils.tube_rig import TubePath

        # Sample a handful of edges around the tube.
        edges = [f"{self.tube}.e[{i}]" for i in (0, 12, 24, 36, 48)]

        # Should not raise — exercises the list-input arrange_points_as_path path.
        result = TubePath.get_centerline_using_edges(edges)

        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 2)
        # Each point should be a 3-element sequence.
        for p in result:
            self.assertEqual(len(p), 3)


if __name__ == "__main__":
    unittest.main()
