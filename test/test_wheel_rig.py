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

    # ------------------------------------------------------------------
    # Basic creation & attributes
    # ------------------------------------------------------------------

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

    def test_rig_name_persistence(self):
        """wheelRigId is stamped on the control and survives re-instantiation.

        Bug: Previously re-creating a WheelRig on a control that already had
        a wheelRigId would generate a new name, orphaning old expressions.
        Fixed: 2025-02-23
        """
        rig1 = WheelRig(self.control, self.wheels, rig_name="persist_rig")
        self.assertEqual(self.control.wheelRigId.get(), "persist_rig")

        # Re-instantiate WITHOUT passing rig_name — should recover from attr
        rig2 = WheelRig(self.control, self.wheels)
        self.assertEqual(rig2.rig_name, "persist_rig")
        self.assertEqual(self.control.wheelRigId.get(), "persist_rig")

    def test_freeze_transforms_default(self):
        """freeze_transforms=True (default) should zero out non-zero transforms."""
        self.control.setTranslation([1, 2, 3], space="world")
        rig = WheelRig(self.control, self.wheels, rig_name="freeze_rig")

        # After construction with default freeze_transforms=True the
        # translate values should be frozen (zeroed).
        t = self.control.getTranslation(space="object")
        self.assertAlmostEqual(t.x, 0, places=4)
        self.assertAlmostEqual(t.y, 0, places=4)
        self.assertAlmostEqual(t.z, 0, places=4)

    def test_freeze_transforms_false(self):
        """freeze_transforms=False should preserve existing transforms."""
        self.control.setTranslation([1, 2, 3], space="world")
        rig = WheelRig(
            self.control,
            self.wheels,
            rig_name="nofreeze_rig",
            freeze_transforms=False,
        )

        t = self.control.getTranslation(space="object")
        self.assertAlmostEqual(t.x, 1.0, places=4)
        self.assertAlmostEqual(t.y, 2.0, places=4)
        self.assertAlmostEqual(t.z, 3.0, places=4)

    # ------------------------------------------------------------------
    # Re-entrancy
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Axis inference & explicit rotation_axis
    # ------------------------------------------------------------------

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

    def test_y_movement_infers_y_rotation(self):
        """translateY movement should auto-infer rotateY."""
        rig = WheelRig(self.control, self.wheels, rig_name="y_rig")
        rig.rig_rotation(movement_axis="translateY", wheel_height=1.0)

        code = pm.PyNode(f"y_rig_{self.wheel1.name()}_expr").expression.get()
        self.assertIn("rotateY", code)

    def test_explicit_rotation_axis(self):
        """Passing rotation_axis explicitly should bypass auto-inference.

        Bug: Before the rotation_axis parameter was added, the combo label
        said 'Movement Axis: X' but the expression always used auto-inferred
        rotation, confusing users.
        Fixed: 2025-02-22
        """
        rig = WheelRig(self.control, self.wheels, rig_name="explicit_rig")
        rig.rig_rotation(
            movement_axis="translateX",
            rotation_axis="rotateY",  # Override auto-infer (would be rotateZ)
            wheel_height=1.0,
        )

        # Check the wheel's actual driven attribute rather than just
        # string-matching expression text, which could be fragile.
        ry_attr = self.wheel1.attr("rotateY")
        rz_attr = self.wheel1.attr("rotateZ")

        ry_connected = bool(ry_attr.listConnections(source=True, destination=False))
        rz_connected = bool(rz_attr.listConnections(source=True, destination=False))

        self.assertTrue(ry_connected, "rotateY should be driven by the expression")
        self.assertFalse(rz_connected, "rotateZ should NOT be connected")

    # ------------------------------------------------------------------
    # Auto-flip
    # ------------------------------------------------------------------

    def test_auto_flip(self):
        """Test that mirrored wheel gets negative multiplier."""
        rig = WheelRig(self.control, self.wheels, rig_name="flip_rig")
        rig.rig_rotation(movement_axis="translateZ")

        # Wheel 1 (Formatted normal)
        code1 = pm.PyNode(f"flip_rig_{self.wheel1.name()}_expr").expression.get()
        # Wheel 2 (Rotated 180)
        code2 = pm.PyNode(f"flip_rig_{self.wheel2.name()}_expr").expression.get()

        self.assertIn("float $auto_flip = 1.0;", code1)
        self.assertIn("float $auto_flip = -1.0;", code2)

    # ------------------------------------------------------------------
    # Multiple wheel sizes
    # ------------------------------------------------------------------

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
        w1_expr = pm.PyNode(f"multi_size_rig_{self.wheel1.name()}_expr")
        self.assertIn(".wheelHeight;", w1_expr.expression.get())

        w2_expr = pm.PyNode(f"multi_size_rig_{self.wheel2.name()}_expr")
        self.assertIn(".wheelHeight_1;", w2_expr.expression.get())

        # 4. Rig Wheel 1 AGAIN with Height 2.0 (Should Reuse wheelHeight)
        rig.rig_rotation(
            movement_axis="translateZ", wheel_height=2.0, wheels=[self.wheel1]
        )
        w1_expr_new = pm.PyNode(f"multi_size_rig_{self.wheel1.name()}_expr")
        self.assertIn(".wheelHeight;", w1_expr_new.expression.get())

    # ------------------------------------------------------------------
    # Functional rotation
    # ------------------------------------------------------------------

    def test_expression_produces_rotation(self):
        """Moving the control should produce non-zero wheel rotation.

        This validates the expression actually evaluates correctly,
        not just that the text looks right.
        """
        rig = WheelRig(self.control, self.wheels, rig_name="func_rig")
        rig.rig_rotation(
            movement_axis="translateZ", wheel_height=2.0, wheels=[self.wheel1]
        )

        # Move the control
        self.control.translateZ.set(5.0)

        # Force expression evaluation
        pm.dgeval(self.wheel1.attr("rotateX"))

        rot = self.wheel1.rotateX.get()
        self.assertNotAlmostEqual(
            rot,
            0.0,
            places=2,
            msg="Wheel should have rotated after control moved",
        )

    # ------------------------------------------------------------------
    # World-space mode (decomposeMatrix)
    # ------------------------------------------------------------------

    def test_world_space_creates_decompose_node(self):
        """use_world_space=True should create a decomposeMatrix node.

        Bug: Wheels didn't turn when the parent group was moved because
        the expression read local translateZ which stays 0.
        Fixed: 2025-02-22
        """
        rig = WheelRig(self.control, self.wheels, rig_name="ws_rig")
        rig.rig_rotation(
            movement_axis="translateZ",
            wheel_height=2.0,
            use_world_space=True,
        )

        decomp_name = "ws_rig_decompose"
        self.assertTrue(
            pm.objExists(decomp_name),
            "decomposeMatrix node should be created in world-space mode",
        )

        decomp = pm.PyNode(decomp_name)
        self.assertEqual(pm.nodeType(decomp), "decomposeMatrix")

        # Verify worldMatrix is connected to the decompose node
        inputs = decomp.inputMatrix.listConnections(plugs=True, source=True)
        self.assertTrue(
            any("worldMatrix" in str(p) for p in inputs),
            "worldMatrix should be connected to decomposeMatrix.inputMatrix",
        )

    def test_world_space_expression_reads_decompose(self):
        """Expression should reference the decomposeMatrix output, not local translate."""
        rig = WheelRig(self.control, self.wheels, rig_name="ws_expr_rig")
        rig.rig_rotation(
            movement_axis="translateZ",
            wheel_height=2.0,
            use_world_space=True,
        )

        code = pm.PyNode(f"ws_expr_rig_{self.wheel1.name()}_expr").expression.get()
        self.assertIn("ws_expr_rig_decompose.outputTranslateZ", code)
        # Should NOT reference local translate
        self.assertNotIn(f"{self.control}.translateZ", code)

    def test_local_mode_no_decompose_node(self):
        """use_world_space=False (default) should not create a decomposeMatrix node."""
        rig = WheelRig(self.control, self.wheels, rig_name="local_rig")
        rig.rig_rotation(
            movement_axis="translateZ",
            wheel_height=2.0,
            use_world_space=False,
        )

        decomp_name = "local_rig_decompose"
        self.assertFalse(
            pm.objExists(decomp_name),
            "decomposeMatrix node should NOT be created in local mode",
        )

        code = pm.PyNode(f"local_rig_{self.wheel1.name()}_expr").expression.get()
        self.assertIn(f"{self.control}.translateZ", code)

    def test_world_space_reentrancy(self):
        """Re-running in world-space mode should reuse the decomposeMatrix node."""
        rig = WheelRig(self.control, self.wheels, rig_name="ws_reuse_rig")
        decomp_name = "ws_reuse_rig_decompose"

        rig.rig_rotation(movement_axis="translateZ", use_world_space=True)
        self.assertTrue(pm.objExists(decomp_name))

        rig.rig_rotation(movement_axis="translateZ", use_world_space=True)
        # Still exactly one node with this name (not duplicated)
        self.assertTrue(pm.objExists(decomp_name))
        matches = pm.ls(f"{decomp_name}*", type="decomposeMatrix")
        self.assertEqual(
            len(matches),
            1,
            "Should reuse existing decomposeMatrix node, not create a new one.",
        )

    def test_switch_world_to_local_cleans_decompose(self):
        """Switching from world-space to local should clean up the decompose node.

        If the user rigs with world-space then re-rigs in local mode, the
        orphaned decomposeMatrix should be removed by delete_expressions.
        """
        rig = WheelRig(self.control, self.wheels, rig_name="switch_rig")
        decomp_name = "switch_rig_decompose"

        # Rig in world-space first
        rig.rig_rotation(movement_axis="translateZ", use_world_space=True)
        self.assertTrue(pm.objExists(decomp_name))

        # Clean up and re-rig in local mode
        rig.delete_expressions(filter_by_rig=True)
        rig.rig_rotation(movement_axis="translateZ", use_world_space=False)

        self.assertFalse(
            pm.objExists(decomp_name),
            "decomposeMatrix should be cleaned up after switching to local mode",
        )

        # Verify local expression works
        code = pm.PyNode(f"switch_rig_{self.wheel1.name()}_expr").expression.get()
        self.assertIn(f"{self.control}.translateZ", code)

    # ------------------------------------------------------------------
    # get_expressions & delete_expressions
    # ------------------------------------------------------------------

    def test_get_expressions_unfiltered(self):
        """get_expressions() returns all expressions on the control."""
        rig = WheelRig(self.control, self.wheels, rig_name="get_rig")
        rig.rig_rotation(movement_axis="translateZ")

        exprs = rig.get_expressions(filter_by_rig=False)
        self.assertGreaterEqual(len(exprs), 2, "Should have at least 2 expressions")

    def test_get_expressions_filtered(self):
        """get_expressions(filter_by_rig=True) only returns this rig's expressions."""
        rig_a = WheelRig(self.control, [self.wheel1], rig_name="rig_a")
        rig_a.rig_rotation(movement_axis="translateZ", wheels=[self.wheel1])

        rig_b = WheelRig(self.control, [self.wheel2], rig_name="rig_b")
        rig_b.rig_rotation(movement_axis="translateZ", wheels=[self.wheel2])

        a_exprs = rig_a.get_expressions(filter_by_rig=True)
        b_exprs = rig_b.get_expressions(filter_by_rig=True)

        self.assertEqual(len(a_exprs), 1)
        self.assertEqual(len(b_exprs), 1)
        self.assertIn("rig_a", a_exprs[0].name())
        self.assertIn("rig_b", b_exprs[0].name())

    def test_delete_expressions(self):
        """delete_expressions should remove expressions and decomposeMatrix node."""
        rig = WheelRig(self.control, self.wheels, rig_name="del_rig")
        rig.rig_rotation(
            movement_axis="translateZ",
            use_world_space=True,
        )

        # Verify they exist first
        self.assertTrue(pm.objExists("del_rig_decompose"))
        self.assertTrue(pm.objExists(f"del_rig_{self.wheel1.name()}_expr"))

        rig.delete_expressions(filter_by_rig=True)

        # Expressions and decompose node should be gone
        self.assertFalse(pm.objExists(f"del_rig_{self.wheel1.name()}_expr"))
        self.assertFalse(pm.objExists(f"del_rig_{self.wheel2.name()}_expr"))
        self.assertFalse(pm.objExists("del_rig_decompose"))

    def test_delete_expressions_only_affects_own_rig(self):
        """delete_expressions(filter_by_rig=True) should not remove other rigs' expressions."""
        rig_a = WheelRig(self.control, [self.wheel1], rig_name="keep_a")
        rig_a.rig_rotation(movement_axis="translateZ", wheels=[self.wheel1])

        rig_b = WheelRig(self.control, [self.wheel2], rig_name="kill_b")
        rig_b.rig_rotation(movement_axis="translateZ", wheels=[self.wheel2])

        rig_b.delete_expressions(filter_by_rig=True)

        # rig_a's expression should survive
        self.assertTrue(
            pm.objExists(f"keep_a_{self.wheel1.name()}_expr"),
            "Other rig's expressions should not be deleted",
        )
        self.assertFalse(pm.objExists(f"kill_b_{self.wheel2.name()}_expr"))

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_invalid_wheel_height_raises(self):
        """wheel_height <= 0 should raise ValueError."""
        rig = WheelRig(self.control, self.wheels, rig_name="err_rig")
        with self.assertRaises(ValueError):
            rig.rig_rotation(movement_axis="translateZ", wheel_height=0.0)

        with self.assertRaises(ValueError):
            rig.rig_rotation(movement_axis="translateZ", wheel_height=-1.0)

    def test_no_wheels_raises(self):
        """Passing an empty wheels list should raise ValueError."""
        rig = WheelRig(self.control, self.wheels, rig_name="empty_rig")
        with self.assertRaises(ValueError):
            rig.rig_rotation(movement_axis="translateZ", wheels=[])

    def test_invalid_control_raises(self):
        """Non-existent control should raise ValueError on construction.

        NodeUtils.get_transform_node returns None for non-existent nodes,
        which triggers the 'if not self.control' guard in __init__.
        """
        with self.assertRaises((ValueError, Exception)):
            WheelRig("nonexistent_node", self.wheels)


if __name__ == "__main__":
    unittest.main()
