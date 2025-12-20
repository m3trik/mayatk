import pymel.core as pm
import maya.standalone

maya.standalone.initialize()

try:
    from PySide2.QtWidgets import QApplication
except ImportError:
    from PySide6.QtWidgets import QApplication
import sys

if not QApplication.instance():
    app = QApplication(sys.argv)

from mayatk.anim_utils._anim_utils import AnimUtils
import unittest


class TestVisTangents(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        self.cube = pm.polyCube()[0]

        # Create visibility animation
        pm.setKeyframe(self.cube.visibility, t=1, v=1)
        pm.setKeyframe(self.cube.visibility, t=10, v=0)
        pm.keyTangent(self.cube.visibility, edit=True, outTangentType="step")

        # Create custom bool animation
        pm.addAttr(self.cube, longName="myBool", attributeType="bool", keyable=True)
        pm.setKeyframe(self.cube.myBool, t=1, v=1)
        pm.setKeyframe(self.cube.myBool, t=10, v=0)
        pm.keyTangent(self.cube.myBool, edit=True, outTangentType="step")

        self.vis_curve = self.cube.visibility.inputs()[0]
        self.bool_curve = self.cube.myBool.inputs()[0]

    def test_detection(self):
        vis_curves, other = AnimUtils._get_visibility_curves(
            [self.vis_curve, self.bool_curve]
        )
        print(f"Vis curves: {vis_curves}")
        print(f"Other curves: {other}")

        self.assertIn(self.vis_curve, vis_curves)
        self.assertIn(self.bool_curve, vis_curves)

    def test_preservation(self):
        # Manually set to auto to simulate "bad" state if we were to smooth it
        # But wait, the test is about _set_smart_tangents preserving it.

        # Let's try to run _set_smart_tangents with auto
        AnimUtils._set_smart_tangents(
            [self.vis_curve, self.bool_curve], tangent_type="auto"
        )

        t1_type = pm.keyTangent(self.vis_curve, q=True, time=(1,), outTangentType=True)[
            0
        ]
        t2_type = pm.keyTangent(
            self.bool_curve, q=True, time=(1,), outTangentType=True
        )[0]

        print(f"Vis tangent type: {t1_type}")
        print(f"Bool tangent type: {t2_type}")

        self.assertEqual(t1_type, "step")
        self.assertEqual(t2_type, "step")


if __name__ == "__main__":
    unittest.main()
