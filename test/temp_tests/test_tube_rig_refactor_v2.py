import pymel.core as pm
import unittest
import sys
import os

# Ensure mayatk is in path if needed (might be handled by environment)
try:
    from mayatk.rig_utils.tube_rig import TubeRig
except ImportError:
    # Fallback for running directly if path not set
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
    from mayatk.rig_utils.tube_rig import TubeRig


class TestTubeRigRefactor(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

    def test_spline_build(self):
        """Verify Spline Strategy functionality"""
        # Create cylinder
        cyl = pm.polyCylinder(
            r=1, h=10, sx=8, sy=10, sz=1, ax=(0, 1, 0), name="SplineTube", ch=False
        )[0]

        rig = TubeRig(cyl)
        rig.build(strategy="spline")

        self.assertIsNotNone(rig.bundle, "Rig Bundle not created")

        # 1. Assert ikSplineSolver
        ik_handle = rig.bundle.ik_handle
        self.assertIsNotNone(ik_handle, "IK Handle not created")
        solver = ik_handle.getSolver()
        self.assertEqual(
            solver, "ikSplineSolver", f"Expected ikSplineSolver, got {solver}"
        )

        # 2. Assert Joints X-oriented
        joints = rig.bundle.joints
        self.assertTrue(len(joints) > 1, "Not enough joints generated")

        for i in range(len(joints) - 1):
            j1 = joints[i]
            j2 = joints[i + 1]
            vec = j2.getTranslation(space="world") - j1.getTranslation(space="world")
            vec.normalize()

            # X axis of the joint in world space
            # Pymel Matrix is row-major. Row 0 is X axis.
            x_world = pm.datatypes.Vector(j1.getMatrix(worldSpace=True)[0][:3]).normal()

            dot = x_world.dot(vec)
            self.assertGreater(
                dot, 0.9, f"Joint {j1.name()} not X-oriented (dot={dot:.2f})"
            )

        # 3. Assert Stretch (curveInfo)
        found_connection = False
        for jnt in joints:
            # Check for curveInfo in history of scaleX
            hist = jnt.scaleX.listConnections(skipConversionNodes=True)
            for node in hist:
                # Could be connected to multiplyDivide for normalization
                if isinstance(node, pm.nodetypes.MultiplyDivide):
                    inputs = node.input1X.listConnections()
                    if inputs and isinstance(inputs[0], pm.nodetypes.CurveInfo):
                        found_connection = True
                        break
                # Or directly (unlikely for stretch)
                if isinstance(node, pm.nodetypes.CurveInfo):
                    found_connection = True
                    break

            if found_connection:
                break

        self.assertTrue(
            found_connection, "curveInfo node not connected to Scale X (Stretch)"
        )

        # 4. Assert Controls Created
        self.assertIsNotNone(rig.bundle.controls, "Spline Controls not created")
        self.assertEqual(
            len(rig.bundle.controls), 3, "Expected 3 main controls (Start, Mid, End)"
        )

        # 5. Assert Skinning
        hist = rig.mesh.listHistory(type="skinCluster")
        self.assertTrue(hist, "Mesh not skinned to joints (Spline)")

    def test_anchor_build(self):
        """Verify Anchor Strategy functionality"""
        cyl = pm.polyCylinder(
            r=1, h=10, sx=8, sy=10, sz=1, ax=(0, 1, 0), name="AnchorTube", ch=False
        )[0]

        rig = TubeRig(cyl)
        rig.build(strategy="anchor")

        self.assertIsNotNone(rig.bundle, "Rig Bundle not created")

        # 1. Assert only 2 joints (start and end)
        joints = rig.bundle.joints
        self.assertEqual(
            len(joints), 2, f"Anchor rig should have 2 joints, got {len(joints)}"
        )

        # 2. Assert joints are NOT parented to controls (clean export hierarchy)
        j_start = joints[0]
        j_parent = j_start.getParent()
        self.assertIsNotNone(j_parent, "Start joint should have a parent")
        self.assertIn(
            "_joints_GRP",
            j_parent.name(),
            "Joints should be in _joints_GRP, not parented to controls",
        )

        # 3. Assert Point Constraint on Start Joint (position driven by control)
        point_constraints = j_start.listRelatives(type="pointConstraint")
        self.assertTrue(point_constraints, "Start joint missing Point Constraint")

        # 4. Assert Orient Constraint on Start Joint (rotation driven by control)
        orient_constraints = j_start.listRelatives(type="orientConstraint")
        self.assertTrue(orient_constraints, "Start joint missing Orient Constraint")

        # 5. Assert Scale Connection (Distance based)
        found_dist = False
        hist = j_start.scaleX.listConnections(skipConversionNodes=True)
        for node in hist:
            if isinstance(node, pm.nodetypes.DistanceBetween):
                found_dist = True
                break
            if isinstance(node, pm.nodetypes.MultiplyDivide):
                inputs = node.input1X.listConnections() + node.input2X.listConnections()
                if any(isinstance(i, pm.nodetypes.DistanceBetween) for i in inputs):
                    found_dist = True
                    break

        self.assertTrue(found_dist, "Start joint scale not driven by distance logic")

        # 6. Assert Controls Created
        self.assertIsNotNone(rig.bundle.controls, "Anchor Controls not created")
        self.assertEqual(
            len(rig.bundle.controls), 2, "Expected 2 controls (Start, End)"
        )

        # 7. Assert Skinning
        hist = rig.mesh.listHistory(type="skinCluster")
        self.assertTrue(hist, "Mesh not skinned to joints (Anchor)")


if __name__ == "__main__":
    unittest.main()
