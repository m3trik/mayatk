import pymel.core as pm
import unittest
import sys
import os

# Ensure mayatk is in path if needed (might be handled by environment)
from mayatk.rig_utils.tube_rig import TubeRig


class TestTubeRigRefactor(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

    def test_spline_build(self):
        # Create cylinder
        cyl = pm.polyCylinder(r=1, h=10, sx=8, sy=10, sz=1, ax=(0, 1, 0))[0]

        # TubeRig(cyl).build(strategy="spline")
        rig = TubeRig(cyl)

        # Verify build method exists (or just call it and let it fail if not)
        if not hasattr(rig, "build"):
            self.fail("TubeRig does not have 'build' method - Refactor not applied?")

        built_rig = rig.build(strategy="spline")

        # Assert ikSplineSolver is used
        ik_handle = rig.ik_handle
        self.assertIsNotNone(ik_handle, "IK Handle not created")
        solver = ik_handle.getSolver()
        self.assertEqual(
            solver, "ikSplineSolver", f"Expected ikSplineSolver, got {solver}"
        )

        # Assert Joints are X-oriented (dot product > 0.9)
        joints = rig.joints
        self.assertTrue(len(joints) > 1, "Not enough joints generated")

        for i in range(len(joints) - 1):
            j1 = joints[i]
            j2 = joints[i + 1]
            vec = j2.getTranslation(space="world") - j1.getTranslation(space="world")
            vec.normalize()

            # X axis of the joint in world space
            # Pymel Matrix is row-major? Standard Maya is.
            # Row 0 is X axis.
            x_world = pm.datatypes.Vector(j1.getMatrix(worldSpace=True)[0][:3]).normal()

            dot = x_world.dot(vec)
            self.assertGreater(dot, 0.9, f"Joint {j1} not X-oriented (dot={dot})")

        # Assert curveInfo node exists and connects to Scale X (Stretch)
        # Search for curveInfo connected to the rig
        curve_infos = pm.ls(type="curveInfo")
        found_connection = False

        # Simply check if ANY curveInfo is driving the scaleX of ANY joint in the chain, possibly through other nodes
        # This can be complex to traverse.
        # Let's look for a curveInfo that is an input to the joints' scale

        for jnt in joints:
            # Check inputs to scaleX
            inputs = jnt.scaleX.inputs()
            if not inputs:
                continue

            # Traverse up history basic check
            stack = list(inputs)
            visited = set()
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)

                if isinstance(node, pm.nodetypes.CurveInfo):
                    found_connection = True
                    break

                # Add inputs of this node to stack
                # We only care about nodes that might be part of the math (multDoubleLinear, multiplyDivide, etc)
                if isinstance(
                    node,
                    (
                        pm.nodetypes.MultiplyDivide,
                        pm.nodetypes.MultDoubleLinear,
                        pm.nodetypes.UnitConversion,
                        pm.nodetypes.PlusMinusAverage,
                    ),
                ):
                    stack.extend(node.inputs())

            if found_connection:
                break

        self.assertTrue(
            found_connection, "curveInfo node not connected to Scale X (Stretch)"
        )


if __name__ == "__main__":
    unittest.main()
