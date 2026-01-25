# !/usr/bin/python
# coding=utf-8
import pymel.core as pm
import unittest
from base_test import MayaTkTestCase
from mayatk.rig_utils.wheel_rig import WheelRig


class TestWheelRig(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.control = pm.polyCube(n="Control")[0]
        self.wheel1 = pm.polyCylinder(n="Wheel1")[0]
        self.wheel2 = pm.polyCylinder(n="Wheel2")[0]

        # Move wheel2 to mirror side to test auto-flip
        self.wheel2.setTranslation([-5, 0, 0], space="world")
        # Rotate wheel2 180 degrees to simulate mirrored joint/transform
        self.wheel2.setRotation([0, 180, 0], space="world")

        self.wheels = [self.wheel1, self.wheel2]

    def test_rig_creation_defaults(self):
        """Test basic setup: Move Z -> Rotate X"""
        rig = WheelRig(self.control, self.wheels, rig_name="test_rig")
        rig.rig_rotation(
            movement_axis="translateZ", wheel_height=2.0, wheels=self.wheels
        )

        # Check Attributes
        self.assertTrue(self.control.hasAttr("wheelHeight"))
        self.assertTrue(self.control.hasAttr("enableRotation"))
        self.assertTrue(self.control.hasAttr("wheelRigId"))
        self.assertTrue(self.control.hasAttr("spinDirection"))

        self.assertAlmostEqual(self.control.wheelHeight.get(), 2.0)

        # Check Expressions created
        expr_name = f"test_rig_{self.wheel1.name()}_expr"
        self.assertTrue(pm.objExists(expr_name), f"Expression {expr_name} should exist")

        # Verify Expression Content
        expr = pm.PyNode(expr_name)
        code = expr.expression.get()
        self.assertIn(f"{self.control}.translateZ", code)
        self.assertIn("wheelHeight", code)

    def test_reentrancy(self):
        """Test that running the rig repeatedly cleans up old nodes."""
        rig_name = "reentry_rig"
        rig1 = WheelRig(self.control, self.wheels, rig_name=rig_name)

        # 1. Initial run
        rig1.rig_rotation(
            movement_axis="translateZ", wheel_height=2.0, wheels=self.wheels
        )

        expr_count_1 = len(pm.ls(type="expression"))

        # 2. Update run (simulate user changing height)
        # In a real scenario, the object might be re-instantiated, but sharing the name.
        rig2 = WheelRig(self.control, self.wheels, rig_name=rig_name)
        rig2.rig_rotation(
            movement_axis="translateZ", wheel_height=3.0, wheels=self.wheels
        )

        expr_count_2 = len(pm.ls(type="expression"))

        self.assertEqual(
            expr_count_1,
            expr_count_2,
            "Should have replaced expressions, not added new ones.",
        )

    def test_smart_rotate_inference(self):
        """Test that X movement infers Z rotation."""
        rig = WheelRig(self.control, self.wheels, rig_name="x_rig")
        rig.rig_rotation(
            movement_axis="translateX", wheel_height=1.0, wheels=self.wheels
        )

        expr_name = f"x_rig_{self.wheel1.name()}_expr"
        expr = pm.PyNode(expr_name)
        code = expr.expression.get()

        self.assertIn("rotateZ", code)

    def test_auto_flip(self):
        """Test that mirrored wheel gets negative multiplier."""
        rig = WheelRig(self.control, self.wheels, rig_name="flip_rig")
        rig.rig_rotation(movement_axis="translateZ")

        # Wheel 1 (Formatted normal)
        code1 = pm.PyNode(f"flip_rig_{self.wheel1.name()}_expr").expression.get()
        # Wheel 2 (Rotated 180)
        code2 = pm.PyNode(f"flip_rig_{self.wheel2.name()}_expr").expression.get()

        # Regex or string check for $auto_flip variable
        # code: float $auto_flip = 1.0;
        self.assertIn("float $auto_flip = 1.0;", code1)
        self.assertIn("float $auto_flip = -1.0;", code2)

    def test_multiple_wheel_sizes(self):
        """Test multiple wheel sizes on same control generating unique attributes."""
        rig = WheelRig(self.control, self.wheels, rig_name="multi_size_rig")

        # 1. Rig Wheel 1 with Height 2.0
        rig.rig_rotation(
            movement_axis="translateZ", wheel_height=2.0, wheels=[self.wheel1]
        )
        self.assertTrue(self.control.hasAttr("wheelHeight"))
        self.assertAlmostEqual(self.control.wheelHeight.get(), 2.0)

        # 2. Rig Wheel 2 with Height 5.0 (Expect new attribute)
        rig.rig_rotation(
            movement_axis="translateZ", wheel_height=5.0, wheels=[self.wheel2]
        )
        self.assertTrue(self.control.hasAttr("wheelHeight_1"))
        self.assertAlmostEqual(self.control.wheelHeight_1.get(), 5.0)

        # 3. Verify Connections
        # Wheel 1 expr should use wheelHeight
        w1_expr = pm.PyNode(f"multi_size_rig_{self.wheel1.name()}_expr")
        self.assertIn(".wheelHeight;", w1_expr.expression.get())

        # Wheel 2 expr should use wheelHeight_1
        w2_expr = pm.PyNode(f"multi_size_rig_{self.wheel2.name()}_expr")
        self.assertIn(".wheelHeight_1;", w2_expr.expression.get())

        # 4. Rig Wheel 1 AGAIN with Height 2.0 (Should Reuse wheelHeight)
        rig.rig_rotation(
            movement_axis="translateZ", wheel_height=2.0, wheels=[self.wheel1]
        )
        w1_expr_new = pm.PyNode(f"multi_size_rig_{self.wheel1.name()}_expr")
        self.assertIn(".wheelHeight;", w1_expr_new.expression.get())


if __name__ == "__main__":
    unittest.main()
