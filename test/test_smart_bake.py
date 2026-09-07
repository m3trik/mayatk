# !/usr/bin/python
# coding=utf-8
"""Tests for SmartBake module."""

import os
import unittest
import maya.cmds as cmds


class TestSmartBake(unittest.TestCase):
    """Test SmartBake analysis and baking functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures - runs once before all tests."""
        try:
            from maya import standalone

            # Initialize only when running standalone (mayapy script).
            # When invoked via run_tests.py inside an already-running Maya,
            # standalone.initialize() raises — that's expected and harmless.
            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        """Set up test scene before each test."""
        if not self.maya_available:
            self.skipTest("Maya not available")

        from maya import cmds

        cmds.file(new=True, force=True)

    def tearDown(self):
        """Clean up after each test."""
        if self.maya_available:
            from maya import cmds

            cmds.file(new=True, force=True)

    # -------------------------------------------------------------------------
    # Type Classification Tests
    # -------------------------------------------------------------------------

    def test_constraint_detection_uses_inheritance(self):
        """Verify constraints are detected via inheritance, not hardcoded types."""
        from maya import cmds
        from mayatk.node_utils._node_utils import NodeUtils

        # Create a basic constraint setup
        cube = cmds.polyCube(name="driven_cube")[0]
        locator = cmds.spaceLocator(name="driver_loc")[0]
        cmds.parentConstraint(locator, cube)

        # Test inheritance detection via NodeUtils
        constraints = cmds.ls(type="constraint")
        self.assertTrue(len(constraints) > 0)

        for c in constraints:
            self.assertTrue(NodeUtils.is_constraint(c))

    def test_driven_key_detection(self):
        """Verify driven keys are detected by input connection, not type name."""
        from maya import cmds
        from mayatk.node_utils._node_utils import NodeUtils

        # Create a driven key setup
        driver = cmds.polyCube(name="driver")[0]
        driven = cmds.polyCube(name="driven")[0]

        # Set up driven key: driver.tx drives driven.ty
        cmds.setDrivenKeyframe(f"{driven}.ty", currentDriver=f"{driver}.tx")
        cmds.setAttr(f"{driver}.tx", 10)
        cmds.setDrivenKeyframe(f"{driven}.ty", currentDriver=f"{driver}.tx")

        # Find the driven key curve
        curves = cmds.listConnections(driven, type="animCurve", source=True) or []
        driven_key_curves = NodeUtils.is_driven_key_curve(curves, filter=True)

        self.assertTrue(len(driven_key_curves) > 0)

    def test_expression_detection(self):
        """Verify expressions are detected."""
        from maya import cmds
        from mayatk.node_utils._node_utils import NodeUtils

        cube = cmds.polyCube(name="expr_cube")[0]
        cmds.expression(s=f"{cube}.ty = time")

        # Find expression nodes
        expressions = cmds.listConnections(cube, type="expression", source=True) or []
        self.assertTrue(len(expressions) > 0)
        self.assertTrue(NodeUtils.is_expression(expressions[0]))

    # -------------------------------------------------------------------------
    # Analysis Tests
    # -------------------------------------------------------------------------

    def test_analyze_constraint_driven_object(self):
        """Verify analysis correctly identifies constraint-driven channels."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create parent constrained object
        cube = cmds.polyCube(name="test_cube")[0]
        locator = cmds.spaceLocator(name="test_loc")[0]
        cmds.parentConstraint(locator, cube)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertTrue(analysis[cube].requires_bake)
        self.assertIn("constraint", analysis[cube].driven_channels)

        # Should detect translate and rotate channels
        channels = analysis[cube].all_driven_channels
        self.assertTrue(any(c in channels for c in ["tx", "ty", "tz"]))
        self.assertTrue(any(c in channels for c in ["rx", "ry", "rz"]))

    def test_analyze_already_keyed_object(self):
        """Verify analysis detects already-keyed channels."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="keyed_cube")[0]

        # Key the translate
        cmds.setKeyframe(cube, attribute="tx", time=1, value=0)
        cmds.setKeyframe(cube, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertIn("tx", analysis[cube].already_keyed)
        self.assertFalse(analysis[cube].requires_bake)

    def test_analyze_mixed_keyed_and_constrained(self):
        """Verify analysis handles object with both keys and constraints."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="mixed_cube")[0]
        locator = cmds.spaceLocator(name="mixed_loc")[0]

        # Key scale directly
        cmds.setKeyframe(cube, attribute="sx", time=1, value=1)
        cmds.setKeyframe(cube, attribute="sx", time=10, value=2)

        # Constrain position
        cmds.pointConstraint(locator, cube)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertTrue(analysis[cube].requires_bake)
        self.assertIn("sx", analysis[cube].already_keyed)
        self.assertIn("constraint", analysis[cube].driven_channels)

        # Should only bake constrained channels, not sx
        bake_channels = analysis[cube].all_driven_channels
        self.assertNotIn("sx", bake_channels)
        self.assertTrue(any(c in bake_channels for c in ["tx", "ty", "tz"]))

    def test_analyze_muted_constraint_skipped(self):
        """Verify muted constraints are not included in bake analysis."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="muted_cube")[0]
        locator = cmds.spaceLocator(name="muted_loc")[0]
        constraint = cmds.parentConstraint(locator, cube)[0]

        # Mute the constraint
        cmds.setAttr(f"{constraint}.nodeState", 1)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Should not require bake since constraint is muted
        if cube in analysis:
            self.assertFalse(analysis[cube].requires_bake)

    # -------------------------------------------------------------------------
    # Time Range Detection Tests
    # -------------------------------------------------------------------------

    def test_time_range_from_constraint_target(self):
        """Verify time range is detected from constraint target animation."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="range_cube")[0]
        locator = cmds.spaceLocator(name="range_loc")[0]
        cmds.parentConstraint(locator, cube)

        # Animate the locator (constraint target)
        cmds.setKeyframe(locator, attribute="tx", time=5, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=25, value=10)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()
        time_range = baker.get_time_range(analysis)

        self.assertEqual(time_range, (5, 25))

    def test_time_range_fallback_to_playback(self):
        """Verify fallback to playback range when no driver animation."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cmds.playbackOptions(minTime=1, maxTime=100)

        cube = cmds.polyCube(name="fallback_cube")[0]
        locator = cmds.spaceLocator(name="fallback_loc")[0]
        cmds.parentConstraint(locator, cube)
        # No animation on locator

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()
        time_range = baker.get_time_range(analysis)

        self.assertEqual(time_range, (1, 100))

    # -------------------------------------------------------------------------
    # Bake Execution Tests
    # -------------------------------------------------------------------------

    def test_bake_creates_keyframes(self):
        """Verify baking creates keyframes on driven channels."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="bake_cube")[0]
        locator = cmds.spaceLocator(name="bake_loc")[0]
        cmds.pointConstraint(locator, cube)

        # Animate locator
        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        # delete_inputs is a base-layer behavior (ignored under the
        # nondestructive layer default).
        baker = SmartBake(
            objects=[cube],
            delete_inputs=True,
            use_override_layer=False,
            backup_file=False,
        )
        result = baker.execute()

        # Verify bake result
        self.assertIn(cube, result.baked)
        self.assertEqual(result.time_range, (1, 10))

        # Verify keyframes exist on cube now
        curves = cmds.listConnections(f"{cube}.tx", type="animCurve") or []
        self.assertTrue(len(curves) > 0)

        # Verify constraint was deleted
        constraints = cmds.ls(type="pointConstraint")
        self.assertEqual(len(constraints), 0)

    def test_bake_preserves_existing_keys(self):
        """Verify baking preserves keys outside bake range."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="preserve_cube")[0]
        locator = cmds.spaceLocator(name="preserve_loc")[0]

        # Pre-existing key on scale at frame -10 (outside bake range)
        cmds.setKeyframe(cube, attribute="sx", time=-10, value=2)

        # Constrain position
        cmds.pointConstraint(locator, cube)

        # Animate locator
        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cube], preserve_outside_keys=True)
        baker.execute()

        # Verify pre-existing key still exists
        key_times = cmds.keyframe(f"{cube}.sx", query=True, timeChange=True)
        self.assertIn(-10.0, key_times)

    # -------------------------------------------------------------------------
    # Edge Case Tests
    # -------------------------------------------------------------------------

    def test_empty_scene_no_objects(self):
        """Verify graceful handling of empty scene."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Empty scene with no transforms (only default cameras)

        baker = SmartBake(objects=[])
        result = baker.execute()

        self.assertFalse(result.success)
        self.assertEqual(len(result.baked), 0)

    def test_object_with_no_connections(self):
        """Verify objects with no incoming connections are skipped."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="isolated_cube")[0]
        # No constraints, no keys, nothing

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Should not appear in analysis at all (no requires_bake, no already_keyed)
        self.assertNotIn(cube, analysis)

    def test_deleted_object_during_bake(self):
        """Verify handling when referenced object no longer exists."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="deleted_cube")[0]
        locator = cmds.spaceLocator(name="del_loc")[0]
        cmds.pointConstraint(locator, cube)

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Delete object and constraint before baking
        cmds.delete(cube)

        # Should handle gracefully without crashing
        try:
            result = baker.bake(analysis)
            # If it doesn't crash, object should be in skipped
            self.assertIn(cube, result.skipped)
        except Exception:
            # Some errors are expected when object is deleted
            pass

    def test_multiple_constraints_on_same_object(self):
        """Verify handling multiple constraints affecting same object."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="multi_const_cube")[0]
        loc1 = cmds.spaceLocator(name="loc1")[0]
        loc2 = cmds.spaceLocator(name="loc2")[0]

        # Point constraint for position, orient constraint for rotation
        cmds.pointConstraint(loc1, cube)
        cmds.orientConstraint(loc2, cube)

        cmds.setKeyframe(loc1, attribute="tx", time=1, value=0)
        cmds.setKeyframe(loc1, attribute="tx", time=20, value=10)
        cmds.setKeyframe(loc2, attribute="ry", time=5, value=0)
        cmds.setKeyframe(loc2, attribute="ry", time=15, value=90)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertTrue(analysis[cube].requires_bake)
        channels = analysis[cube].all_driven_channels
        # Should have both translate and rotate
        self.assertTrue(any(c in channels for c in ["tx", "ty", "tz"]))
        self.assertTrue(any(c in channels for c in ["rx", "ry", "rz"]))

    def test_constraint_chain(self):
        """Verify baking works with constraint chains (A constrains B constrains C)."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cubeA = cmds.polyCube(name="chainA")[0]
        cubeB = cmds.polyCube(name="chainB")[0]
        cubeC = cmds.polyCube(name="chainC")[0]

        # A -> B -> C chain
        cmds.parentConstraint(cubeA, cubeB)
        cmds.parentConstraint(cubeB, cubeC)

        # Animate the root
        cmds.setKeyframe(cubeA, attribute="tx", time=1, value=0)
        cmds.setKeyframe(cubeA, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cubeB, cubeC])
        analysis = baker.analyze()

        self.assertIn(cubeB, analysis)
        self.assertIn(cubeC, analysis)

    def test_pairblend_intermediate_node(self):
        """Verify tracing through pairBlend nodes (IK/FK blend)."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="pairblend_cube")[0]
        locator = cmds.spaceLocator(name="pb_loc")[0]

        # Animate locator first so constraint creates pairBlend
        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        # Create constraint - when target has animation, pairBlend may be created
        cmds.parentConstraint(locator, cube)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Should detect the constraint (whether through pairBlend or direct)
        self.assertIn(cube, analysis)
        self.assertTrue(analysis[cube].requires_bake)

    def test_unitconversion_intermediate_node(self):
        """Verify tracing through unitConversion nodes."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create joint (uses degrees internally, radians in connections)
        joint = cmds.joint(name="test_joint")
        locator = cmds.spaceLocator(name="uc_loc")[0]
        cmds.orientConstraint(locator, joint)

        cmds.setKeyframe(locator, attribute="rx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="rx", time=10, value=45)

        baker = SmartBake(objects=[joint])
        analysis = baker.analyze()

        self.assertIn(joint, analysis)
        self.assertTrue(analysis[joint].requires_bake)

    def test_driven_key_with_animated_driver(self):
        """Verify driven key detection when driver has animation."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        driver = cmds.polyCube(name="sdk_driver")[0]
        driven = cmds.polyCube(name="sdk_driven")[0]

        # Set up driven key
        cmds.setAttr(f"{driver}.tx", 0)
        cmds.setAttr(f"{driven}.ty", 0)
        cmds.setDrivenKeyframe(f"{driven}.ty", currentDriver=f"{driver}.tx")
        cmds.setAttr(f"{driver}.tx", 10)
        cmds.setAttr(f"{driven}.ty", 5)
        cmds.setDrivenKeyframe(f"{driven}.ty", currentDriver=f"{driver}.tx")

        # Animate the driver
        cmds.setKeyframe(driver, attribute="tx", time=1, value=0)
        cmds.setKeyframe(driver, attribute="tx", time=20, value=10)

        baker = SmartBake(objects=[driven])
        analysis = baker.analyze()

        self.assertIn(driven, analysis)
        self.assertIn("driven_key", analysis[driven].driven_channels)

        # Time range should come from driver's animation
        time_range = baker.get_time_range(analysis)
        self.assertEqual(time_range, (1, 20))

    def test_expression_with_time_reference(self):
        """Verify expression detection with time-based expression."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="time_expr_cube")[0]
        cmds.expression(s=f"{cube}.ty = sin(time * 2) * 5", name="sine_expression")

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertIn("expression", analysis[cube].driven_channels)

    def test_scale_constraint(self):
        """Verify scale constraints are detected."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="scale_cube")[0]
        locator = cmds.spaceLocator(name="scale_loc")[0]
        cmds.scaleConstraint(locator, cube)

        cmds.setKeyframe(locator, attribute="sx", time=1, value=1)
        cmds.setKeyframe(locator, attribute="sx", time=10, value=2)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertTrue(analysis[cube].requires_bake)
        channels = analysis[cube].all_driven_channels
        self.assertTrue(any(c in channels for c in ["sx", "sy", "sz"]))

    def test_aim_constraint(self):
        """Verify aim constraints are detected."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="aim_cube")[0]
        target = cmds.polyCube(name="aim_target")[0]
        cmds.aimConstraint(target, cube)

        cmds.setKeyframe(target, attribute="tx", time=1, value=0)
        cmds.setKeyframe(target, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertIn("constraint", analysis[cube].driven_channels)

    def test_bake_with_sample_by(self):
        """Verify sample_by parameter affects key density."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="sample_cube")[0]
        locator = cmds.spaceLocator(name="sample_loc")[0]
        cmds.pointConstraint(locator, cube)

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        # Bake with sample_by=2 (every 2 frames)
        baker = SmartBake(objects=[cube], sample_by=2, delete_inputs=True)
        baker.execute()

        # Check key count - should be approximately (10-1)/2 + 1 = 5-6 keys
        key_times = cmds.keyframe(f"{cube}.tx", query=True, timeChange=True) or []
        # With sample_by=2 from frame 1-10, expect keys at 1,3,5,7,9 or similar
        self.assertLess(len(key_times), 10)

    def test_long_path_names(self):
        """Verify handling of long DAG path names with namespaces."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create nested hierarchy
        grp1 = cmds.group(empty=True, name="root_group")
        grp2 = cmds.group(empty=True, name="nested_group", parent=grp1)
        cube = cmds.polyCube(name="deeply_nested_cube")[0]
        cmds.parent(cube, grp2)
        locator = cmds.spaceLocator(name="path_loc")[0]

        # Get the full path
        full_path = cmds.ls(cube, long=True)[0]
        cmds.pointConstraint(locator, full_path)

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[full_path])
        analysis = baker.analyze()

        self.assertIn(full_path, analysis)

    def test_non_transform_objects_ignored(self):
        """Verify non-transform nodes don't cause errors."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="shape_test_cube")[0]
        shape = cmds.listRelatives(cube, shapes=True)[0]

        # Try to analyze the shape node (not a transform)
        baker = SmartBake(objects=[shape])
        baker.analyze()

        # Should handle gracefully - shape has no transform attrs
        # May or may not be in analysis, but shouldn't crash

    def test_optimize_keys_option(self):
        """Verify optimize_keys option calls AnimUtils.optimize_keys."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="optimize_cube")[0]
        locator = cmds.spaceLocator(name="opt_loc")[0]
        cmds.pointConstraint(locator, cube)

        # Create static animation on locator (stays at same position)
        cmds.setKeyframe(locator, attribute="tx", time=1, value=5)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=5)

        baker = SmartBake(objects=[cube], optimize_keys=True, delete_inputs=True)
        result = baker.execute()

        # Should have optimized field populated
        self.assertTrue(len(result.optimized) > 0 or cube in result.optimized)

    def test_optimize_keys_with_override_layer(self):
        """Verify optimize_keys finds and optimizes curves on an override layer.

        Bug: listConnections(plug, type='animCurve') doesn't traverse
        animBlendNode intermediaries, so baked curves on override layers
        were silently skipped.
        Fixed: 2026-03-07 — uses cmds.animLayer(layer, q=True, animCurves=True).
        """
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="layer_opt_cube")[0]
        locator = cmds.spaceLocator(name="layer_opt_loc")[0]
        cmds.pointConstraint(locator, cube)

        # Create static animation on locator (stays at same position)
        # This will bake to constant keys → optimize should remove them
        cmds.setKeyframe(locator, attribute="tx", time=1, value=5)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=5)

        baker = SmartBake(
            objects=[cube],
            optimize_keys=True,
            use_override_layer=True,
            delete_inputs=False,
        )
        result = baker.execute()

        # Should have created an override layer
        self.assertIsNotNone(result.override_layer)
        self.assertTrue(cmds.objExists(result.override_layer))

        # Should have optimized the baked curves
        self.assertTrue(len(result.optimized) > 0)

        # Constraint should still exist (not deleted)
        constraints = cmds.ls(type="pointConstraint")
        self.assertTrue(len(constraints) > 0)

        # Clean up the layer
        cmds.delete(result.override_layer)

    def test_override_layer_cleanup_restores_scene(self):
        """Verify deleting the override layer restores the scene state."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="restore_cube")[0]
        locator = cmds.spaceLocator(name="restore_loc")[0]
        cmds.pointConstraint(locator, cube)

        # Animate locator
        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        # Get pre-bake value at frame 5
        cmds.currentTime(5)
        pre_bake_val = cmds.getAttr(f"{cube}.tx")

        baker = SmartBake(
            objects=[cube],
            use_override_layer=True,
            delete_inputs=False,
        )
        result = baker.execute()

        # Value should be the same (override layer reproduces constraint)
        cmds.currentTime(5)
        during_layer_val = cmds.getAttr(f"{cube}.tx")
        self.assertAlmostEqual(pre_bake_val, during_layer_val, places=2)

        # Delete the layer — scene should return to constraint-driven state
        cmds.delete(result.override_layer)

        # Constraint should still exist and drive the object
        constraints = cmds.ls(type="pointConstraint")
        self.assertTrue(len(constraints) > 0)

        cmds.currentTime(5)
        post_delete_val = cmds.getAttr(f"{cube}.tx")
        self.assertAlmostEqual(pre_bake_val, post_delete_val, places=2)

    def test_classmethod_run(self):
        """Verify SmartBake.run() classmethod works correctly."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="run_cube")[0]
        locator = cmds.spaceLocator(name="run_loc")[0]
        cmds.pointConstraint(locator, cube)

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        # Use class method instead of instantiation
        result = SmartBake.run(objects=[cube], delete_inputs=True)

        self.assertTrue(result.success)
        self.assertIn(cube, result.baked)

    def test_bake_result_properties(self):
        """Verify BakeResult dataclass properties work correctly."""
        from mayatk.anim_utils.smart_bake._smart_bake import BakeResult

        # Empty result
        empty_result = BakeResult()
        self.assertEqual(empty_result.baked_count, 0)
        self.assertFalse(empty_result.success)

        # Result with baked objects
        result = BakeResult(baked={"obj1": ["tx"], "obj2": ["rx", "ry"]})
        self.assertEqual(result.baked_count, 2)
        self.assertTrue(result.success)

    def test_bake_analysis_properties(self):
        """Verify BakeAnalysis dataclass properties work correctly."""
        from mayatk.anim_utils.smart_bake._smart_bake import BakeAnalysis

        # Empty analysis
        empty = BakeAnalysis(object="test")
        self.assertFalse(empty.requires_bake)
        self.assertEqual(empty.all_driven_channels, [])

        # Analysis with driven channels
        analysis = BakeAnalysis(
            object="test",
            driven_channels={"constraint": ["tx", "ty"], "expression": ["rz"]},
        )
        self.assertTrue(analysis.requires_bake)
        channels = analysis.all_driven_channels
        self.assertEqual(len(channels), 3)
        self.assertIn("tx", channels)
        self.assertIn("rz", channels)

    def test_constraint_with_multiple_targets(self):
        """Verify constraints with multiple targets (blended) are handled."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="blend_cube")[0]
        loc1 = cmds.spaceLocator(name="blend_loc1")[0]
        loc2 = cmds.spaceLocator(name="blend_loc2")[0]

        # Parent constraint with two targets (blended)
        cmds.parentConstraint(loc1, loc2, cube)

        cmds.setKeyframe(loc1, attribute="tx", time=1, value=0)
        cmds.setKeyframe(loc1, attribute="tx", time=10, value=10)
        cmds.setKeyframe(loc2, attribute="tx", time=1, value=0)
        cmds.setKeyframe(loc2, attribute="tx", time=20, value=-10)

        baker = SmartBake(objects=[cube])
        time_range = baker.get_time_range()

        # Should span both targets' animation ranges
        self.assertEqual(time_range[0], 1)
        self.assertEqual(time_range[1], 20)

    def test_delete_inputs_removes_constraints(self):
        """Verify delete_inputs removes constraint nodes after baking."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="delete_test_cube")[0]
        locator = cmds.spaceLocator(name="del_test_loc")[0]
        constraint = cmds.parentConstraint(locator, cube)[0]

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        baker = SmartBake(
            objects=[cube],
            delete_inputs=True,
            use_override_layer=False,
            backup_file=False,
        )
        result = baker.execute()

        # Constraint should be deleted
        self.assertFalse(cmds.objExists(constraint))
        self.assertIn(constraint, result.deleted)

    def test_delete_inputs_removes_expressions(self):
        """Verify delete_inputs removes expression nodes after baking."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="expr_del_cube")[0]
        expr = cmds.expression(s=f"{cube}.ty = time * 2", name="test_expr")

        cmds.playbackOptions(minTime=1, maxTime=10)

        baker = SmartBake(
            objects=[cube],
            delete_inputs=True,
            use_override_layer=False,
            backup_file=False,
        )
        baker.execute()

        # Expression should be deleted
        self.assertFalse(cmds.objExists(expr))

    def test_visibility_channel(self):
        """Verify visibility channel can be baked from expressions."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="vis_cube")[0]
        # Expression that toggles visibility based on time
        cmds.expression(s=f"{cube}.visibility = (frame % 2 == 0)")

        cmds.playbackOptions(minTime=1, maxTime=10)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        if cube in analysis:
            channels = analysis[cube].all_driven_channels
            self.assertIn("v", channels)

    # -------------------------------------------------------------------------
    # Unity Export Completeness Tests
    # -------------------------------------------------------------------------

    def test_joints_included_in_default_query(self):
        """Verify joints are included when querying all objects for baking."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create a joint chain
        cmds.select(clear=True)
        j1 = cmds.joint(name="joint1", position=(0, 0, 0))
        j2 = cmds.joint(name="joint2", position=(0, 2, 0))
        j3 = cmds.joint(name="joint3", position=(0, 4, 0))

        # Also create a transform
        cube = cmds.polyCube(name="test_cube")[0]

        baker = SmartBake()
        objects = baker._get_objects()

        # Should include both transforms and joints
        self.assertIn(cmds.ls(j1, long=True)[0], objects)
        self.assertIn(cmds.ls(j2, long=True)[0], objects)
        self.assertIn(cmds.ls(j3, long=True)[0], objects)
        # Note: cube transform should also be included
        cube_long = cmds.ls(cube, long=True)[0]
        self.assertIn(cube_long, objects)

    def test_get_objects_no_duplicates(self):
        """Default object query must not list joints twice.

        Bug: ls(type='transform') already includes joints (joint derives
        from transform), so appending a separate ls(type='joint') query
        duplicated every joint and analyzed each twice.
        """
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cmds.select(clear=True)
        cmds.joint(name="dup_joint1", position=(0, 0, 0))
        cmds.joint(name="dup_joint2", position=(0, 2, 0))
        cmds.polyCube(name="dup_cube")

        objects = SmartBake()._get_objects()
        self.assertEqual(
            len(objects), len(set(objects)), f"duplicate entries in {objects}"
        )

    def test_baked_channels_merge_vis_and_transform(self):
        """An object baked in BOTH the inherited-visibility pass and the
        standard channel pass must report the union of its baked channels.

        Bug: phase 2 assigned result.baked[obj] = channels, clobbering the
        ["v"] entry phase 1 recorded for the same object.
        """
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        parent = cmds.group(empty=True, name="merge_parent")
        child_short = cmds.polyCube(name="merge_child")[0]
        cmds.parent(child_short, parent)
        child = cmds.ls(child_short, long=True)[0]

        # Parent toggles visibility (inherited-vis pass picks this up).
        cmds.setKeyframe(parent, attribute="visibility", time=3, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=6, value=1)

        # Child is also constraint-driven (standard pass picks this up).
        loc = cmds.spaceLocator(name="merge_loc")[0]
        cmds.setKeyframe(loc, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(loc, attribute="translateX", time=10, value=5)
        cmds.pointConstraint(loc, child)

        result = SmartBake(objects=[child], bake_inherited_visibility=True).execute()

        baked_channels = result.baked.get(child, [])
        self.assertIn("v", baked_channels, f"vis channel lost: {baked_channels}")
        self.assertTrue(
            any(c in baked_channels for c in ("tx", "ty", "tz")),
            f"translate channels missing: {baked_channels}",
        )

    def test_ik_chain_detection(self):
        """Verify joints in IK chains are detected as needing bake."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create a joint chain
        cmds.select(clear=True)
        j1 = cmds.joint(name="ik_joint1", position=(0, 0, 0))
        j2 = cmds.joint(name="ik_joint2", position=(2, 0, 0))
        j3 = cmds.joint(name="ik_joint3", position=(4, 0, 0))

        # Create IK handle from j1 to j3
        cmds.ikHandle(
            name="test_ikHandle", startJoint=j1, endEffector=j3, solver="ikRPsolver"
        )[0]

        baker = SmartBake(objects=[j1, j2, j3])
        analysis = baker.analyze()

        # All joints in the IK chain should need rotation baking
        for joint in [j1, j2]:  # End joint may not be in chain depending on solver
            if joint in analysis:
                self.assertIn("ik", analysis[joint].driven_channels)
                # Should mark rotation channels
                self.assertTrue(
                    any(
                        ch in ["rx", "ry", "rz"]
                        for ch in analysis[joint].driven_channels.get("ik", [])
                    )
                )

    def test_motion_path_detection(self):
        """Verify objects attached to motion paths are detected."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create a curve path
        curve = cmds.curve(
            name="path_curve",
            degree=3,
            point=[(0, 0, 0), (2, 2, 0), (4, 0, 0), (6, 2, 0)],
        )

        # Create object to attach
        cube = cmds.polyCube(name="path_cube")[0]

        # Attach to path
        cmds.pathAnimation(cube, curve, fractionMode=True, startTimeU=1, endTimeU=30)

        cmds.playbackOptions(minTime=1, maxTime=30)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Cube should be detected as driven by motion path
        self.assertIn(cube, analysis)
        self.assertIn("motion_path", analysis[cube].driven_channels)

    def test_blend_shape_sdk_detection(self):
        """Verify blend shapes driven by SDKs are detected."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create base and target meshes
        base = cmds.polyCube(name="bs_base")[0]
        target = cmds.polyCube(name="bs_target")[0]

        # Modify target to create a blend shape target
        cmds.select(f"{target}.vtx[*]")
        cmds.move(0, 1, 0, relative=True)

        # Create blend shape
        blend_shape = cmds.blendShape(target, base, name="test_blendShape")[0]

        # Delete target (not needed anymore)
        cmds.delete(target)

        # Create driver cube and set up driven key on blend shape weight
        driver = cmds.polyCube(name="bs_driver")[0]
        cmds.setAttr(f"{blend_shape}.{target}", 0)
        cmds.setDrivenKeyframe(f"{blend_shape}.{target}", currentDriver=f"{driver}.tx")
        cmds.setAttr(f"{driver}.tx", 10)
        cmds.setAttr(f"{blend_shape}.{target}", 1)
        cmds.setDrivenKeyframe(f"{blend_shape}.{target}", currentDriver=f"{driver}.tx")

        # Set up time animation on driver
        cmds.setKeyframe(driver, attribute="tx", time=1, value=0)
        cmds.setKeyframe(driver, attribute="tx", time=30, value=10)

        cmds.playbackOptions(minTime=1, maxTime=30)

        baker = SmartBake(objects=[base], bake_blend_shapes=True)
        analysis = baker.analyze()

        # Blend shape should be in analysis
        self.assertIn(blend_shape, analysis)
        self.assertIn("driven_key", analysis[blend_shape].driven_channels)

    def test_animation_layer_passthrough(self):
        """Verify animation layers are traced through properly."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="layer_cube")[0]

        # Create animation layer
        base_layer = cmds.animLayer("BaseAnimation")
        cmds.animLayer(base_layer, edit=True, addSelectedObjects=True)

        # Key on the layer
        cmds.setKeyframe(cube, attribute="tx", time=1, value=0)
        cmds.setKeyframe(cube, attribute="tx", time=30, value=10)

        cmds.playbackOptions(minTime=1, maxTime=30)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Should detect animation (either as keyed or driven through layer)
        if cube in analysis:
            has_keys = bool(analysis[cube].already_keyed)
            has_driven = bool(analysis[cube].driven_channels)
            self.assertTrue(has_keys or has_driven)

    def test_multiply_divide_passthrough(self):
        """Verify connections through multiplyDivide nodes are traced."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # Create a multiply/divide setup
        driver = cmds.polyCube(name="mult_driver")[0]
        driven = cmds.polyCube(name="mult_driven")[0]
        mult_node = cmds.createNode("multiplyDivide", name="test_mult")

        # Connect: driver.tx -> mult.input1X -> driven.ty
        cmds.connectAttr(f"{driver}.tx", f"{mult_node}.input1X")
        cmds.setAttr(f"{mult_node}.input2X", 2)  # Double the value
        cmds.connectAttr(f"{mult_node}.outputX", f"{driven}.ty")

        # Key the driver
        cmds.setKeyframe(driver, attribute="tx", time=1, value=0)
        cmds.setKeyframe(driver, attribute="tx", time=30, value=5)

        cmds.playbackOptions(minTime=1, maxTime=30)

        baker = SmartBake(objects=[driven])
        analysis = baker.analyze()

        # Driven should be detected as having keyed animation (traced through mult)
        self.assertIn(driven, analysis)
        # The tracing should find the keyframe source
        self.assertTrue(
            analysis[driven].already_keyed or analysis[driven].driven_channels
        )


class TestLayerBakeCorrectness(unittest.TestCase):
    """Layer-mode bakes must capture the driver's true per-frame motion.

    Regression: _create_override_layer pre-registered the bake attributes
    onto the fresh layer (create_animation_layer(attributes=...)) before
    bakeResults(destinationLayer=...) targeted it. That combination writes a
    flat constant — the value live at registration time — at every sampled
    frame, so a constrained object read back motionless WHILE baked (and the
    scene exporter's FBX pass, which samples the evaluated scene with the
    layer live, exported flat animation). bakeResults must be handed an
    EMPTY layer and wire the attributes itself.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        from maya import cmds

        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=10)

    def tearDown(self):
        if self.maya_available:
            from maya import cmds

            cmds.file(new=True, force=True)

    def test_layer_bake_matches_driver_motion_while_live(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="lbc_cube")[0]
        loc = cmds.spaceLocator(name="lbc_loc")[0]
        cmds.setKeyframe(loc, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(loc, attribute="translateX", time=10, value=5)
        cmds.parentConstraint(loc, cube)

        result = SmartBake(objects=[cube]).execute()
        self.assertTrue(result.success)
        self.assertTrue(cmds.objExists(result.override_layer))

        # WHILE the layer is live, the cube must track the locator exactly —
        # including mid-range frames whose value depends on the locator's
        # own (auto/spline) tangent shape, not a hardcoded linear ramp.
        for frame in (1, 3, 5, 7, 10):
            cmds.currentTime(frame)
            self.assertAlmostEqual(
                cmds.getAttr(f"{cube}.tx"),
                cmds.getAttr(f"{loc}.tx"),
                places=3,
                msg=f"baked cube diverges from driver at frame {frame}",
            )

        # And the layer's baked curve itself must not be a flat constant.
        layer_curves = (
            cmds.animLayer(result.override_layer, query=True, animCurves=True) or []
        )
        self.assertTrue(layer_curves)
        tx_curves = [c for c in layer_curves if "translateX" in c]
        self.assertTrue(tx_curves, f"no translateX curve on layer: {layer_curves}")
        values = cmds.keyframe(tx_curves[0], query=True, valueChange=True) or []
        self.assertGreater(
            len(set(round(v, 4) for v in values)),
            1,
            f"baked layer curve is a flat constant: {values}",
        )


class TestNondestructiveRestore(unittest.TestCase):
    """SmartBake restore manifest: bake() records, restore() reverses.

    Contract:
    - Every bake with restorable=True (default) pushes a session manifest
      persisted on the data_internal node (survives save/reopen).
    - SmartBake.restore() reverses the most recent session (LIFO) or a
      named one: deletes the override layer, unmutes drivers to their
      recorded states, re-enables IK handles, restores visibility curves
      and values, and reconnects/unstashes base-layer driver networks.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        from maya import cmds

        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=10)

    def tearDown(self):
        if self.maya_available:
            from maya import cmds

            cmds.file(new=True, force=True)

    # -- helpers ---------------------------------------------------------

    def _constraint_scene(self, prefix="ndr"):
        """Keyed locator parent-constraining a cube. Returns (cube, locator, constraint)."""
        from maya import cmds

        cube = cmds.polyCube(name=f"{prefix}_cube")[0]
        loc = cmds.spaceLocator(name=f"{prefix}_loc")[0]
        cmds.setKeyframe(loc, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(loc, attribute="translateX", time=10, value=5)
        constraint = cmds.parentConstraint(loc, cube)[0]
        return cube, loc, constraint

    def _sdk_scene(self, prefix="sdk"):
        """SDK driver.tx -> driven.ty (0->0, 10->5), driver keyed over time."""
        from maya import cmds

        driver = cmds.polyCube(name=f"{prefix}_driver")[0]
        driven = cmds.polyCube(name=f"{prefix}_driven")[0]
        cmds.setDrivenKeyframe(
            f"{driven}.ty", currentDriver=f"{driver}.tx", driverValue=0, value=0
        )
        cmds.setDrivenKeyframe(
            f"{driven}.ty", currentDriver=f"{driver}.tx", driverValue=10, value=5
        )
        cmds.setKeyframe(driver, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(driver, attribute="translateX", time=10, value=10)
        return driver, driven

    # -- session recording -----------------------------------------------

    def test_default_is_override_layer(self):
        """Nondestructive layer mode is the default."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        self.assertTrue(SmartBake().use_override_layer)

    def test_bake_records_session(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore
        from mayatk.node_utils.data_nodes import DataNodes

        cube, loc, constraint = self._constraint_scene()
        result = SmartBake(objects=[cube]).execute()

        self.assertTrue(result.success)
        self.assertIsNotNone(result.session_id)
        self.assertIn(result.session_id, SmartBake.list_sessions())
        # Manifest persists on data_internal (never data_export).
        self.assertTrue(DataNodes.get_internal_string(BakeSessionStore.ATTR))
        if cmds.objExists(DataNodes.EXPORT):
            self.assertFalse(
                cmds.attributeQuery(
                    BakeSessionStore.ATTR, node=DataNodes.EXPORT, exists=True
                )
            )

    def test_restore_layer_mode(self):
        """Restore deletes the override layer and pops the session."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene()
        result = SmartBake(objects=[cube]).execute()
        self.assertTrue(cmds.objExists(result.override_layer))

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        self.assertFalse(cmds.objExists(result.override_layer))
        self.assertEqual(SmartBake.list_sessions(), [])
        # Constraint still drives the cube.
        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{cube}.tx"), 5.0, places=3)

    # -- IK (latent-bug regression) ---------------------------------------

    def test_ik_survives_layer_bake_and_restore(self):
        """IK must work while baked (true per-frame layer curves) and after
        restore (handle still enabled, chain live).

        History: layer bakes originally went through a PRE-REGISTERED layer
        (create_animation_layer(attributes=...) then bakeResults onto it),
        which both flattened the baked curves AND left ikBlend zeroed after
        layer delete — the chain read permanently dead. With bakeResults
        handed an empty layer, curves are correct and ikBlend stays 1.0; the
        manifest still records/restores ikBlend as insurance for base-layer
        bakes, where disableImplicitControl genuinely disables the handle.
        """
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        j1 = cmds.joint(position=(0, 0, 0))
        j2 = cmds.joint(position=(2, 0, 0))
        j3 = cmds.joint(position=(4, 0, 0))
        handle = cmds.ikHandle(startJoint=j1, endEffector=j3)[0]
        cmds.setKeyframe(handle, attribute="translateY", time=1, value=0)
        cmds.setKeyframe(handle, attribute="translateY", time=10, value=3)

        result = SmartBake(objects=[j1, j2]).execute()
        self.assertTrue(result.success)
        # Empty-layer bake path leaves the handle enabled.
        self.assertEqual(cmds.getAttr(f"{handle}.ikBlend"), 1.0)
        # Baked layer curves carry real IK motion, not a flat constant.
        cmds.currentTime(10)
        baked_rot = abs(cmds.getAttr(f"{j1}.rotateY")) + abs(
            cmds.getAttr(f"{j1}.rotateZ")
        )
        self.assertGreater(baked_rot, 0.1, "baked joint motion is flat")

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        self.assertEqual(cmds.getAttr(f"{handle}.ikBlend"), 1.0)
        cmds.currentTime(10)
        rotation = abs(cmds.getAttr(f"{j1}.rotateY")) + abs(
            cmds.getAttr(f"{j1}.rotateZ")
        )
        self.assertGreater(rotation, 0.1, "IK no longer drives the chain after restore")

    # -- driver muting -----------------------------------------------------

    def test_mute_states_restored(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene()
        SmartBake(objects=[cube], mute_drivers=True).execute()
        self.assertEqual(cmds.getAttr(f"{constraint}.nodeState"), 2)

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        self.assertEqual(cmds.getAttr(f"{constraint}.nodeState"), 0)

    # -- inherited visibility ----------------------------------------------

    def test_vis_hijack_original_curve_survives_restore(self):
        """Baking inherited vis onto a child with its OWN vis keys must not
        destroy the original animation once restored."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        parent = cmds.group(empty=True, name="vis_parent")
        child_short = cmds.polyCube(name="vis_child")[0]
        cmds.parent(child_short, parent)
        child = cmds.ls(child_short, long=True)[0]

        # Child's own show/hide animation.
        cmds.setKeyframe(child, attribute="visibility", time=1, value=1)
        cmds.setKeyframe(child, attribute="visibility", time=10, value=0)
        # Parent toggles visibility (inherited at runtime, invisible to FBX).
        cmds.setKeyframe(parent, attribute="visibility", time=3, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=6, value=1)

        result = SmartBake(objects=[child], bake_inherited_visibility=True).execute()
        self.assertIn("v", result.baked.get(child, []))
        # Bake merged keys into the child's curve (more than the original 2).
        baked_times = cmds.keyframe(f"{child}.visibility", query=True, timeChange=True)
        self.assertGreater(len(baked_times), 2)

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        times = cmds.keyframe(f"{child}.visibility", query=True, timeChange=True)
        values = cmds.keyframe(f"{child}.visibility", query=True, valueChange=True)
        self.assertEqual(times, [1.0, 10.0])
        self.assertEqual(values, [1.0, 0.0])

    def test_vis_static_value_restored(self):
        """A child with no vis curve gets its static value back and the baked
        curve removed."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        parent = cmds.group(empty=True, name="vis_parent2")
        child_short = cmds.polyCube(name="vis_child2")[0]
        cmds.parent(child_short, parent)
        child = cmds.ls(child_short, long=True)[0]
        cmds.setKeyframe(parent, attribute="visibility", time=3, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=6, value=1)

        SmartBake(objects=[child], bake_inherited_visibility=True).execute()
        self.assertTrue(
            cmds.listConnections(
                f"{child}.visibility", source=True, destination=False, type="animCurve"
            )
        )

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        self.assertFalse(
            cmds.listConnections(
                f"{child}.visibility", source=True, destination=False, type="animCurve"
            )
        )
        self.assertEqual(cmds.getAttr(f"{child}.visibility"), 1.0)

    def test_destructive_vis_bake_leaves_no_stash_nodes(self):
        """A non-restorable session (delete_inputs, base layer) must not
        stash visibility curves — restore() refuses such sessions, so the
        locked stash nodes could never be reclaimed and would leak into
        the scene permanently."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        parent = cmds.group(empty=True, name="leak_parent")
        child_short = cmds.polyCube(name="leak_child")[0]
        cmds.parent(child_short, parent)
        child = cmds.ls(child_short, long=True)[0]
        # Child's own vis keys — a stash would be the only way to reverse the
        # merge, and a non-restorable session has none, so this one is refused.
        cmds.setKeyframe(child, attribute="visibility", time=1, value=1)
        cmds.setKeyframe(child, attribute="visibility", time=10, value=0)
        # A sibling with no vis animation of its own still bakes here.
        plain_short = cmds.polyCube(name="leak_plain")[0]
        cmds.parent(plain_short, parent)
        plain = cmds.ls(plain_short, long=True)[0]
        cmds.setKeyframe(parent, attribute="visibility", time=3, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=6, value=1)

        result = SmartBake(
            objects=[child, plain],
            bake_inherited_visibility=True,
            use_override_layer=False,
            delete_inputs=True,
            backup_file=False,
        ).execute()
        self.assertIn("v", result.baked.get(plain, []))
        self.assertNotIn("v", result.baked.get(child, []))
        self.assertEqual(
            cmds.keyframe(f"{child}.visibility", query=True, timeChange=True),
            [1.0, 10.0],
            "an unreversible merge was written into the artist's own curve",
        )

        self.assertFalse(
            cmds.ls("*__smartBakeStash*"),
            "orphaned stash nodes left by a non-restorable session",
        )

        # delete_inputs must not treat ancestor vis curves/plugs as driver
        # inputs: the parent's own animation was never baked and must survive.
        parent_vis_times = cmds.keyframe(
            f"{parent}.visibility", query=True, timeChange=True
        )
        self.assertEqual(
            parent_vis_times,
            [3.0, 6.0],
            "delete_inputs destroyed the parent's visibility animation",
        )

    # -- base-layer (destructive-mode) round trips --------------------------

    def test_sdk_base_layer_round_trip(self):
        """Base-layer bake destroys the SDK curve in place; restore rebuilds it."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        driver, driven = self._sdk_scene()
        SmartBake(objects=[driven], use_override_layer=False).execute()
        # Confirm destruction: plug curve is now time-based.
        plug_curve = cmds.listConnections(
            f"{driven}.ty", source=True, destination=False
        )[0]
        self.assertTrue(cmds.nodeType(plug_curve).startswith("animCurveT"))

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        plug_curve = cmds.listConnections(
            f"{driven}.ty", source=True, destination=False, skipConversionNodes=True
        )[0]
        self.assertTrue(
            cmds.nodeType(plug_curve).startswith("animCurveU"),
            f"expected SDK curve back, got {cmds.nodeType(plug_curve)}",
        )
        # SDK relationship evaluates: driver at 10 -> driven.ty == 5.
        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{driven}.ty"), 5.0, places=3)

    def test_blendshape_sdk_base_layer_round_trip(self):
        """Blend-shape weight aliases must survive the base-layer
        snapshot/stash path (attributeQuery can't resolve alias attrs)."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        base = cmds.polyCube(name="bsr_base")[0]
        target = cmds.polyCube(name="bsr_target")[0]
        cmds.select(f"{target}.vtx[*]")
        cmds.move(0, 1, 0, relative=True)
        blend_shape = cmds.blendShape(target, base, name="bsr_blendShape")[0]
        cmds.delete(target)

        driver = cmds.polyCube(name="bsr_driver")[0]
        weight_plug = f"{blend_shape}.bsr_target"
        cmds.setDrivenKeyframe(
            weight_plug, currentDriver=f"{driver}.tx", driverValue=0, value=0
        )
        cmds.setDrivenKeyframe(
            weight_plug, currentDriver=f"{driver}.tx", driverValue=10, value=1
        )
        cmds.setKeyframe(driver, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(driver, attribute="translateX", time=10, value=10)

        result = SmartBake(objects=[base], use_override_layer=False).execute()
        self.assertIn(blend_shape, result.baked)

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        # SDK drives the weight again: driver at 10 -> weight == 1.
        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(weight_plug), 1.0, places=3)

    def test_constraint_base_layer_round_trip(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene(prefix="base")
        SmartBake(objects=[cube], use_override_layer=False).execute()
        self.assertTrue(
            cmds.listConnections(
                f"{cube}.tx", source=True, destination=False, type="animCurve"
            ),
            "expected baked curve on cube.tx",
        )

        restore = SmartBake.restore()
        self.assertTrue(restore.success)
        self.assertFalse(
            cmds.listConnections(
                f"{cube}.tx", source=True, destination=False, type="animCurve"
            ),
            "baked curve should be gone after restore",
        )
        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{cube}.tx"), 5.0, places=3)

    # -- session semantics ---------------------------------------------------

    def test_lifo_restore_order(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube_a, _, _ = self._constraint_scene(prefix="a")
        result_a = SmartBake(objects=[cube_a]).execute()
        cube_b, _, _ = self._constraint_scene(prefix="b")
        result_b = SmartBake(objects=[cube_b]).execute()

        SmartBake.restore()  # pops B
        self.assertFalse(cmds.objExists(result_b.override_layer))
        self.assertTrue(cmds.objExists(result_a.override_layer))
        SmartBake.restore()  # pops A
        self.assertFalse(cmds.objExists(result_a.override_layer))
        self.assertEqual(SmartBake.list_sessions(), [])

    def test_cross_session_restore(self):
        """The manifest persists in the scene file — restore works after reopen."""
        import os
        import tempfile
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene(prefix="xs")
        result = SmartBake(objects=[cube], mute_drivers=True).execute()
        layer = result.override_layer

        tmp_dir = tempfile.mkdtemp()
        scene_path = os.path.join(tmp_dir, "cross_session.ma")
        try:
            cmds.file(rename=scene_path)
            cmds.file(save=True, type="mayaAscii")
            cmds.file(new=True, force=True)
            cmds.file(scene_path, open=True, force=True)

            self.assertTrue(cmds.objExists(layer))
            restore = SmartBake.restore()
            self.assertTrue(restore.success)
            self.assertFalse(cmds.objExists(layer))
            self.assertEqual(cmds.getAttr(f"{constraint}.nodeState"), 0)
            cmds.currentTime(10)
            self.assertAlmostEqual(cmds.getAttr(f"{cube}.tx"), 5.0, places=3)
        finally:
            cmds.file(new=True, force=True)
            if os.path.exists(scene_path):
                os.remove(scene_path)
            os.rmdir(tmp_dir)

    def test_delete_inputs_marks_non_restorable(self):
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene(prefix="del")
        result = SmartBake(
            objects=[cube],
            use_override_layer=False,
            delete_inputs=True,
            backup_file=False,  # explicit opt-out of the auto-backup
        ).execute()
        self.assertTrue(result.success)

        restore = SmartBake.restore()
        self.assertFalse(restore.success)
        self.assertTrue(restore.warnings)
        # The dead session is popped so older sessions stay reachable.
        self.assertEqual(SmartBake.list_sessions(), [])

    def test_restore_missing_layer_warns_gracefully(self):
        """A hand-deleted layer must not make restore raise."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene(prefix="gone")
        result = SmartBake(objects=[cube]).execute()
        cmds.delete(result.override_layer)

        restore = SmartBake.restore()  # must not raise
        self.assertEqual(SmartBake.list_sessions(), [])
        self.assertTrue(restore.warnings)

    def test_session_context_manager(self):
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube, loc, constraint = self._constraint_scene(prefix="ctx")
        with SmartBake.session(objects=[cube]) as result:
            self.assertTrue(result.success)
            self.assertTrue(cmds.objExists(result.override_layer))
        self.assertFalse(cmds.objExists(result.override_layer))
        self.assertEqual(SmartBake.list_sessions(), [])


class TestInheritedVisibilityBake(unittest.TestCase):
    """Fade-safety and range correctness of the inherited-visibility bake.

    BACKLOG 2026-08-02 ``bake_inherited_visibility CORRUPTS RenderOpacity fade
    encoding`` - three independent defects:

    (a) the non-restorable path (the Blender bridge's mode) merged baked keys
        into the child's ORIGINAL visibility curve and listed that same curve
        in ``result.visibility_curves``, whose documented cleanup is deletion,
        so following the contract deleted the artist's own keys.
    (b) sampling added the bake-range BOUNDARIES on top of the ancestor key
        times, splitting the two-key gap ``RenderOpacity.key_fade`` uses to
        encode an opacity fade; objects carrying an ``opacity`` attr were
        never refused.
    (c) ``get_time_range`` had no branch for the ``inherited_visibility`` /
        ``inherited_visibility_plugs`` source types, so a vis-only bake fell
        back to the playback range and clamped out-of-range ancestor keys away.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        from maya import cmds

        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=120)

    def tearDown(self):
        if self.maya_available:
            from maya import cmds

            cmds.file(new=True, force=True)

    # -- helpers ---------------------------------------------------------

    def _ancestor_scene(self, prefix, own_keys=None, opacity=False):
        """Keyed-visibility ancestor with one child. Returns (parent, child)."""
        from maya import cmds

        parent = cmds.group(empty=True, name=f"{prefix}_anc")
        child_short = cmds.polyCube(name=f"{prefix}_kid")[0]
        cmds.parent(child_short, parent)
        child = cmds.ls(child_short, long=True)[0]
        cmds.setKeyframe(parent, attribute="visibility", time=10, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=20, value=1)
        for time_, value in own_keys or ():
            cmds.setKeyframe(child, attribute="visibility", time=time_, value=value)
        if opacity:
            # Mirrors RenderOpacity's ATTR_NAME - the fade-encoding marker.
            cmds.addAttr(child, longName="opacity", attributeType="float")
        return cmds.ls(parent, long=True)[0], child

    # -- (a) the non-restorable path must never eat artist keys ----------

    def test_non_restorable_bake_refuses_child_with_own_vis_curve(self):
        """restorable=False cannot stash the child's pristine curve, so merging
        into it is irreversible AND the documented ``visibility_curves``
        cleanup would delete the artist's own keys. Refuse instead."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, child = self._ancestor_scene("nr", own_keys=[(1, 0), (60, 1)])
        own_curve = cmds.listConnections(
            f"{child}.visibility", source=True, destination=False, type="animCurve"
        )[0]

        result = SmartBake(
            objects=[child], bake_inherited_visibility=True, restorable=False
        ).execute()

        self.assertNotIn(
            own_curve,
            list(result.visibility_curves.values()),
            "the artist's own curve was handed back under a delete-me contract",
        )
        self.assertIn(child, result.skipped)
        self.assertEqual(
            cmds.keyframe(f"{child}.visibility", query=True, timeChange=True),
            [1.0, 60.0],
            "the artist's own visibility keys were mutated",
        )

    def test_visibility_curves_lists_only_bake_created_curves(self):
        """Even in a restorable session, ``visibility_curves`` must list only
        curves the bake CREATED - its docstring promises deletable ones."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, merged = self._ancestor_scene("mrg", own_keys=[(1, 0), (60, 1)])
        own_curve = cmds.listConnections(
            f"{merged}.visibility", source=True, destination=False, type="animCurve"
        )[0]
        _, fresh = self._ancestor_scene("frs")

        result = SmartBake(
            objects=[merged, fresh], bake_inherited_visibility=True
        ).execute()

        self.assertIn("v", result.baked.get(merged, []))
        self.assertNotIn(merged, result.visibility_curves)
        self.assertNotIn(own_curve, list(result.visibility_curves.values()))
        self.assertIn(fresh, result.visibility_curves)

    # -- (b) fade-safe sampling ------------------------------------------

    def test_bake_adds_no_range_boundary_keys(self):
        """Sampling is limited to the child's own key times and the ancestor
        key times; the bake-range boundaries are NOT keyed (they land inside a
        RenderOpacity fade gap and invent transitions that never happened)."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, child = self._ancestor_scene("bnd")

        baker = SmartBake(objects=[child], bake_inherited_visibility=True)
        # Explicit range so the assertion cannot be satisfied by accident when
        # the auto-range happens to equal the ancestor key times.
        baker.bake(baker.analyze(), time_range=(1, 100))

        self.assertEqual(
            cmds.keyframe(f"{child}.visibility", query=True, timeChange=True),
            [10.0, 20.0],
            "bake-range boundary keys were injected",
        )

    def test_bake_refuses_objects_carrying_an_opacity_attr(self):
        """``RenderOpacity`` encodes a fade as the GAP between two opposite
        ``.visibility`` keys; any key inserted inside it splits the ramp. An
        object carrying the ``opacity`` attr must be refused outright."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, child = self._ancestor_scene(
            "fade", own_keys=[(1, 0), (60, 1)], opacity=True
        )

        result = SmartBake(objects=[child], bake_inherited_visibility=True).execute()

        self.assertIn(child, result.skipped)
        self.assertNotIn("v", result.baked.get(child, []))
        self.assertEqual(
            cmds.keyframe(f"{child}.visibility", query=True, timeChange=True),
            [1.0, 60.0],
            "the fade's two-key encoding was split",
        )

    # -- (c) the vis-only time range -------------------------------------

    def test_time_range_covers_out_of_range_ancestor_vis_keys(self):
        """A vis-only analysis must derive its range from the ancestor
        visibility curves, not fall back to the playback range (which clamps
        the very keys the bake exists to resolve)."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        parent = cmds.group(empty=True, name="rng_anc")
        child_short = cmds.polyCube(name="rng_kid")[0]
        cmds.parent(child_short, parent)
        child = cmds.ls(child_short, long=True)[0]
        cmds.playbackOptions(minTime=1, maxTime=24)
        cmds.setKeyframe(parent, attribute="visibility", time=200, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=240, value=1)

        baker = SmartBake(objects=[child], bake_inherited_visibility=True)
        analysis = baker.analyze()
        start, end = baker.get_time_range(analysis)

        self.assertLessEqual(start, 200)
        self.assertGreaterEqual(end, 240)

        baker.bake(analysis, time_range=(start, end))
        self.assertEqual(
            cmds.keyframe(f"{child}.visibility", query=True, timeChange=True),
            [200.0, 240.0],
            "out-of-range ancestor keys were clamped away",
        )

    def test_driver_animation_range_reads_inherited_visibility_sources(self):
        """The primitive behind the range: both inherited-visibility source
        types resolve to the ancestor's key times."""
        from maya import cmds
        from mayatk.anim_utils._anim_utils import AnimUtils

        parent = cmds.group(empty=True, name="drv_anc")
        cmds.setKeyframe(parent, attribute="visibility", time=200, value=0)
        cmds.setKeyframe(parent, attribute="visibility", time=240, value=1)
        curve = cmds.listConnections(
            f"{parent}.visibility", source=True, destination=False, type="animCurve"
        )[0]
        parent_long = cmds.ls(parent, long=True)[0]

        self.assertEqual(
            sorted(
                AnimUtils.get_driver_animation_range(
                    curve, driver_type="inherited_visibility"
                )
            ),
            [200.0, 240.0],
        )
        self.assertEqual(
            sorted(
                AnimUtils.get_driver_animation_range(
                    f"{parent_long}.visibility",
                    driver_type="inherited_visibility_plugs",
                )
            ),
            [200.0, 240.0],
        )


class TestMatrixDrivenBake(unittest.TestCase):
    """offsetParentMatrix-driven objects must be detected and baked.

    Regression: a rig that places joints via ``.offsetParentMatrix`` (a
    ``multMatrix`` network, standard since Maya 2020) left every bind joint
    reporting ``requires_bake=False`` -- ``_analyze_object`` filtered
    destination plugs against ``TRANSFORM_ATTRS``, which holds only the
    scalar t/r/s/v names.  Measured on a production scene: 161 of 210
    skinCluster influences were invisible to the analysis while moving
    tens of units.
    """

    IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def _build(self):
        """Joint placed purely by offsetParentMatrix from an animated locator.

        The multMatrix takes the *group's* worldInverseMatrix, not the
        joint's own parentInverseMatrix -- the latter is a DG cycle
        (joint -> multMatrix -> joint) that silently corrupts evaluation.
        """
        loc = cmds.spaceLocator(name="driver_LOC")[0]
        for attr, (a, b) in (
            ("translateX", (0, 10)),
            ("translateY", (0, 5)),
            ("rotateZ", (0, 45)),
        ):
            cmds.setKeyframe(loc, attribute=attr, time=1, value=a)
            cmds.setKeyframe(loc, attribute=attr, time=10, value=b)
        grp = cmds.group(empty=True, name="rig_GRP")
        jnt = cmds.createNode("joint", name="opm_JNT", parent=grp)
        mmx = cmds.createNode("multMatrix", name="opm_MMX")
        cmds.connectAttr(f"{loc}.worldMatrix[0]", f"{mmx}.matrixIn[0]")
        cmds.connectAttr(f"{grp}.worldInverseMatrix[0]", f"{mmx}.matrixIn[1]")
        cmds.connectAttr(f"{mmx}.matrixSum", f"{jnt}.offsetParentMatrix")
        return loc, jnt, mmx

    @staticmethod
    def _sample(node, frames):
        out = {}
        for t in frames:
            cmds.currentTime(t)
            out[t] = cmds.xform(node, query=True, worldSpace=True, matrix=True)
        return out

    def _assert_matches(self, expected, actual, tol=1e-3):
        for t in expected:
            for a, b in zip(expected[t], actual[t]):
                self.assertAlmostEqual(a, b, delta=tol, msg=f"frame {t}")

    def test_offset_parent_matrix_is_detected(self):
        """analyze() must report a matrix-driven joint as requiring bake."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, jnt, _ = self._build()
        analysis = SmartBake(objects=[jnt])._analyze_object(jnt)

        self.assertTrue(
            analysis.requires_bake,
            "offsetParentMatrix-driven joint reported requires_bake=False",
        )
        self.assertIn("matrix", analysis.driven_channels)
        self.assertEqual(
            sorted(analysis.driven_channels["matrix"]),
            ["rx", "ry", "rz", "sx", "sy", "sz", "tx", "ty", "tz"],
        )
        self.assertIn("opm_MMX", analysis.source_nodes["matrix"])

    def test_base_layer_bake_preserves_matrix_motion(self):
        """Base-layer bake must make a matrix-driven joint self-contained.

        The bake has to sample the *effective* local matrix
        (offsetParentMatrix o TRS) and neutralise offsetParentMatrix.
        Simply keying t/r/s writes zeros -- the local TRS really is
        identity -- and the motion is lost the moment the matrix network
        is disconnected.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, jnt, mmx = self._build()
        frames = (1, 3, 5, 7, 10)
        expected = self._sample(jnt, frames)

        SmartBake(objects=[jnt], use_override_layer=False, delete_inputs=True).execute()

        self.assertFalse(
            cmds.listConnections(
                f"{jnt}.offsetParentMatrix", source=True, destination=False
            ),
            "offsetParentMatrix still connected after a destructive bake",
        )
        self._assert_matches(expected, self._sample(jnt, frames))

    def test_matrix_bake_folds_opm_scale_despite_connected_channels(self):
        """An OPM carrying SCALE must fold completely even when a live
        network drives some TRS channels (the production _01 wire looms:
        curveInfo-driven scale + scale-carrying offsetParentMatrix).

        Excluding the connected channels from the write drops the OPM's
        own scale content entirely -- folded R/T beside unfolded S is an
        inconsistent local, and the worlds drift by the lost scale,
        compounding per link (3.1 cm at the production chain tip).
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        loc, jnt, _ = self._build()
        # Give the OPM real scale content...
        cmds.setKeyframe(loc, attribute="scaleX", time=1, value=0.8)
        cmds.setKeyframe(loc, attribute="scaleX", time=10, value=1.3)
        cmds.setKeyframe(loc, attribute="scaleY", time=1, value=1.1)
        cmds.setKeyframe(loc, attribute="scaleY", time=10, value=0.9)
        # ...and drive the joint's own scale from a live network.
        mdv = cmds.createNode("multiplyDivide", name="squash_MDV")
        cmds.setAttr(f"{mdv}.input1", 1.05, 0.95, 1.0)
        cmds.connectAttr(f"{mdv}.output", f"{jnt}.scale")
        frames = (1, 3, 5, 7, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=True, restorable=True
        ).execute()

        self.assertIn(jnt, result.baked)
        self._assert_matches(expected, self._sample(jnt, frames))

        SmartBake.restore(result.session_id)
        self.assertTrue(
            cmds.listConnections(f"{jnt}.scale", source=True, destination=False),
            "restore did not reconnect the severed scale network",
        )
        self._assert_matches(expected, self._sample(jnt, frames))

    def test_matrix_bake_survives_joint_orient(self):
        """A matrix-driven JOINT with non-zero jointOrient must keep its worlds.

        cmds.xform(matrix=) writes the COMBINED rotation (RA*R*JO) into
        .rotate, which then evaluates on top of the still-present orient --
        double-applying it. The zero-orient fixture above cannot see this;
        the production _01 wire looms (orient-carrying OPM-driven joints)
        shipped rotations fabricated by up to 26 degrees from exactly
        this, probe-pinned against every other bake knob.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, jnt, _ = self._build()
        cmds.setAttr(f"{jnt}.jointOrient", 10.0, 20.0, 30.0)
        cmds.setAttr(f"{jnt}.rotateAxis", 5.0, -3.0, 2.0)
        frames = (1, 3, 5, 7, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=True, restorable=True
        ).execute()

        self.assertIn(jnt, result.baked, "orient-carrying joint left unbaked")
        self._assert_matches(expected, self._sample(jnt, frames))

    def test_override_layer_bakes_matrix_objects_directly(self):
        """Layer mode must bake matrix drives DIRECTLY, not leave them live.

        An animation layer blends keyable scalars -- Maya has no matrix blend
        node -- so a layer can never neutralise ``offsetParentMatrix``. The
        old behavior left the network connected for FBX to resolve, but
        FBXExportBakeComplexAnimation freezes a connected offsetParentMatrix
        whose upstream does not translate to FBX (see TestFbxMatrixOpmExport)
        -- production wire looms shipped 15.9 cm off mid-shot. So matrix
        drives are baked onto their t/r/s plugs in BOTH modes, recorded in
        the session manifest, and reversed with the rest of the restore.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, jnt, mmx = self._build()
        frames = (1, 5, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=True, restorable=True
        ).execute()

        self.assertIn(jnt, result.baked, "layer mode left the matrix drive unbaked")
        self.assertNotIn(jnt, result.skipped)
        self.assertFalse(
            cmds.listConnections(
                f"{jnt}.offsetParentMatrix", source=True, destination=False
            ),
            "matrix drive still connected -- FBX would freeze it",
        )
        self._assert_matches(expected, self._sample(jnt, frames))

        SmartBake.restore(result.session_id)
        self.assertTrue(
            cmds.listConnections(
                f"{jnt}.offsetParentMatrix", source=True, destination=False
            ),
            "restore did not reconnect offsetParentMatrix",
        )
        self._assert_matches(expected, self._sample(jnt, frames))

    def test_restore_matrix_wiring_keeps_layer(self):
        """The keep-bake slice: matrix wiring back, layer and session kept.

        Scene Keys (In Place) keeps the override layer, but baked matrix
        keys were written against the flatten task's staged hierarchy and
        would double-transform once the deferred flatten restore reinstates
        the original offsetParentMatrix. restore_matrix_wiring reverses
        ONLY the matrix bucket.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, jnt, mmx = self._build()
        frames = (1, 5, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=True, restorable=True
        ).execute()
        layer = result.override_layer

        slice_result = SmartBake.restore_matrix_wiring(result.session_id)

        self.assertIn(jnt, slice_result.matrix_restored)
        if layer:
            self.assertTrue(
                cmds.objExists(layer), "matrix-wiring slice deleted the layer"
            )
        self.assertTrue(
            cmds.listConnections(
                f"{jnt}.offsetParentMatrix", source=True, destination=False
            ),
            "matrix wiring not reconnected",
        )
        self.assertFalse(
            cmds.listConnections(f"{jnt}.translateX", type="animCurve", source=True),
            "baked matrix keys survived the slice restore",
        )
        self._assert_matches(expected, self._sample(jnt, frames))
        self.assertIn(
            result.session_id,
            SmartBake.list_sessions(),
            "slice restore popped the session",
        )

    def test_mute_drivers_does_not_freeze_a_matrix_drive(self):
        """mute_drivers must never touch the multMatrix behind a matrix drive.

        ``mute_drivers=True`` with ``use_override_layer=True`` is a documented
        pairing (better playback while keeping drivers recoverable).  Layer
        mode cannot bake a matrix drive, so muting its multMatrix sets
        nodeState=2 on the object's ONLY source of motion and freezes it
        silently -- baked nothing, muted everything.

        The scene needs a SECOND, constrained object: ``_mute_driver_nodes``
        only runs when the bake produced results, so a matrix-only scene bakes
        nothing, mutes nothing, and hides the bug.  That mirrors the
        production case -- a rig where constrained controls bake normally
        alongside matrix-placed bind joints.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        loc, jnt, mmx = self._build()
        # A constrained cube guarantees result.baked is non-empty.
        cube = cmds.polyCube(name="constrained_cube")[0]
        cmds.parentConstraint(loc, cube)

        frames = (1, 5, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt, cube], use_override_layer=True, mute_drivers=True
        ).execute()

        self.assertTrue(
            result.baked, "fixture failed: nothing baked, so muting never ran"
        )
        self.assertNotIn(mmx, result.muted_drivers)
        self.assertEqual(cmds.getAttr(f"{mmx}.nodeState"), 0)
        self._assert_matches(expected, self._sample(jnt, frames))

    def test_matrix_bake_is_restorable(self):
        """restore() must reconnect the matrix network and drop baked keys."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        _, jnt, mmx = self._build()
        frames = (1, 5, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=False, restorable=True
        ).execute()
        self.assertIsNotNone(result.session_id)

        SmartBake.restore(result.session_id)

        self.assertTrue(
            cmds.listConnections(
                f"{jnt}.offsetParentMatrix", source=True, destination=False
            ),
            "restore did not reconnect offsetParentMatrix",
        )
        self._assert_matches(expected, self._sample(jnt, frames))

    def test_matrix_bake_honors_rotate_order_and_stays_euler_continuous(self):
        """The om2 writer must split in the node's rotate order, factor out
        jointOrient/rotateAxis, and keep the baked rotation Euler-continuous.

        A per-frame decomposition is independent per frame: past 180 degrees
        it flips to the equivalent (x+180, 180-y, z+180) triple and the baked
        curve jumps. Worlds still match (the tests above cannot see it), but
        the curve is unusable in the graph editor and a sampled export lands
        mid-flip between frames.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        loc, jnt, _ = self._build()
        cmds.setAttr(f"{jnt}.rotateOrder", 3)  # xzy
        cmds.setAttr(f"{jnt}.jointOrient", 10.0, 20.0, 30.0)
        cmds.setAttr(f"{jnt}.rotateAxis", 5.0, -3.0, 2.0)
        cmds.cutKey(loc, attribute="rotateZ", clear=True)
        cmds.setKeyframe(loc, attribute="rotateZ", time=1, value=0)
        cmds.setKeyframe(loc, attribute="rotateZ", time=20, value=400)
        frames = tuple(range(1, 21))
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=True, restorable=True
        ).execute()

        self.assertIn(jnt, result.baked)
        self._assert_matches(expected, self._sample(jnt, frames))
        for channel in ("rx", "ry", "rz"):
            values = cmds.keyframe(f"{jnt}.{channel}", query=True, valueChange=True)
            self.assertEqual(len(values), len(frames), f"{channel} key count")
            jumps = [abs(b - a) for a, b in zip(values, values[1:])]
            self.assertLess(
                max(jumps), 180.0, f"{channel} flips between frames: {jumps}"
            )

    def test_matrix_bake_on_pivoted_transform_matches_worlds(self):
        """A pivot changes how a local matrix splits into t/r/s: the writer
        must let the node resolve it and land the worlds on EVERY frame.

        The cmds pair could not: ``xform -matrix`` parks the compensation in
        ``rotatePivotTranslate``, which is never keyed, so each frame read
        the last frame's value (frames 1 and 5 were off by up to 1.7 units).
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        loc, jnt, mmx = self._build()
        grp = cmds.listRelatives(jnt, parent=True)[0]
        xf = cmds.createNode("transform", name="opm_XF", parent=grp)
        cmds.setAttr(f"{xf}.rotatePivot", 1.0, 2.0, 3.0)
        cmds.setAttr(f"{xf}.scalePivot", -1.0, 0.5, 2.0)
        cmds.connectAttr(f"{mmx}.matrixSum", f"{xf}.offsetParentMatrix")
        frames = (1, 3, 5, 7, 10)
        expected = self._sample(xf, frames)

        result = SmartBake(
            objects=[xf], use_override_layer=True, restorable=True
        ).execute()

        self.assertIn(xf, result.baked)
        self._assert_matches(expected, self._sample(xf, frames))
        self.assertEqual(cmds.getAttr(f"{xf}.rotatePivot")[0], (1.0, 2.0, 3.0))
        self.assertEqual(
            cmds.getAttr(f"{xf}.rotatePivotTranslate")[0],
            (0.0, 0.0, 0.0),
            "pivot compensation parked in rotatePivotTranslate",
        )

    def test_matrix_bake_survives_negative_scale(self):
        """A mirrored driver (negative determinant) must bake to the same worlds
        whichever writer keys it."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        loc, jnt, _ = self._build()
        cmds.setAttr(f"{loc}.scaleX", -1.0)
        frames = (1, 3, 5, 7, 10)
        expected = self._sample(jnt, frames)

        result = SmartBake(
            objects=[jnt], use_override_layer=True, restorable=True
        ).execute()

        self.assertIn(jnt, result.baked)
        self._assert_matches(expected, self._sample(jnt, frames))


class TestFbxMatrixOpmExport(unittest.TestCase):
    """Maya's FBX exporter freezes a CONNECTED ``offsetParentMatrix`` whose
    upstream does not translate to FBX.

    ``FBXExportBakeComplexAnimation`` samples the t/r/s plugs per frame, but a
    connected offsetParentMatrix is evaluated per frame only when its whole
    upstream converts to FBX (a plain animCurve network does). Anything
    constraint- or IK-driven upstream -- constraints are stripped on export --
    is sampled ONCE at the export-time frame. Minimal repro shipped worldX
    0/0 for a live 0/25; production (VDATS wire looms) shipped 15.9 cm off
    exactly while their shot animated. SmartBake's direct matrix bake is the
    fix: the motion becomes plain plug curves, which FBX ships faithfully.

    If ``test_unbaked_opm_freezes_through_fbx`` ever FAILS, Autodesk fixed
    the exporter and the direct-bake-in-layer-mode workaround can be
    reconsidered.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
        except RuntimeError:
            self.skipTest("fbxmaya plugin not available")
        cmds.file(new=True, force=True)
        temp_dir = os.path.join(os.path.dirname(__file__), "temp_tests")
        os.makedirs(temp_dir, exist_ok=True)
        self._fbx = os.path.join(temp_dir, f"opm_fbx_{os.getpid()}.fbx")

    def tearDown(self):
        cmds.file(new=True, force=True)
        if os.path.isfile(self._fbx):
            os.remove(self._fbx)

    def _build(self):
        """Joint moved ONLY by a constraint-upstream offsetParentMatrix."""
        grp = cmds.group(empty=True, name="fx_GRP")
        anchor = cmds.spaceLocator(name="fx_ANCHOR")[0]
        cmds.setKeyframe(anchor, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(anchor, attribute="translateX", time=30, value=25)
        drv = cmds.group(empty=True, name="fx_DRV")
        cmds.parentConstraint(anchor, drv, maintainOffset=False)
        cmds.select(grp)
        jnt = cmds.joint(name="fx_jnt")
        cmds.select(clear=True)
        mmx = cmds.createNode("multMatrix", name="fx_MMX")
        cmds.connectAttr(f"{drv}.worldMatrix[0]", f"{mmx}.matrixIn[0]")
        cmds.connectAttr(f"{mmx}.matrixSum", f"{jnt}.offsetParentMatrix")
        return grp, jnt

    def _world_x(self, node, t):
        cmds.currentTime(t, edit=True)
        return cmds.getAttr(node + ".worldMatrix[0]")[12]

    def _roundtrip(self, grp):
        import maya.mel as mel

        cmds.currentTime(1, edit=True)
        cmds.select(grp, hierarchy=True)
        mel.eval("FBXResetExport")
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 1")
        mel.eval("FBXExportBakeComplexEnd -v 30")
        mel.eval("FBXExportConstraints -v false")
        mel.eval('FBXExport -f "%s" -s' % self._fbx.replace("\\", "/"))
        cmds.file(new=True, force=True)
        cmds.file(self._fbx, i=True, type="FBX", ignoreVersion=True)
        return cmds.ls("fx_jnt", long=True)[0]

    def test_unbaked_opm_freezes_through_fbx(self):
        """Pins the Maya behavior the direct matrix bake exists for."""
        grp, jnt = self._build()
        self.assertAlmostEqual(self._world_x(jnt, 30), 25.0, delta=1e-3)

        imported = self._roundtrip(grp)
        self.assertAlmostEqual(
            self._world_x(imported, 30),
            0.0,
            delta=1e-3,
            msg="FBX now evaluates constraint-upstream offsetParentMatrix "
            "per frame -- Autodesk fixed it; reconsider the direct bake",
        )

    def test_smart_bake_makes_it_survive_fbx(self):
        """Layer-mode SmartBake first => the FBX carries the real motion."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        grp, jnt = self._build()
        SmartBake(objects=[jnt], use_override_layer=True).execute()

        imported = self._roundtrip(grp)
        self.assertAlmostEqual(
            self._world_x(imported, 1),
            0.0,
            delta=1e-2,
            msg="start pose wrong after bake+export",
        )
        self.assertAlmostEqual(
            self._world_x(imported, 30),
            25.0,
            delta=1e-2,
            msg="motion lost through FBX despite the direct matrix bake",
        )


class TestTimeRangeFromAnimatedAncestor(unittest.TestCase):
    """The bake range must cover motion an ANCESTOR of the target contributes.

    Regression, reported from a production export: a wire loom anchored by
    parentConstraint to a plug locator froze partway through the shot and
    visibly detached from the plug.  The locator's OWN keys ended at frame
    1279, but its animated parent carried it to 1482 -- and the range came
    only from the target's own curves, so the bake stopped at 1279 and the
    loom held still while the plug travelled another ~16 units.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_range_covers_ancestor_animation(self):
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        # target's own keys stop at 20; its PARENT keeps moving to 60.
        parent = cmds.group(empty=True, name="plug_parent_GRP")
        target = cmds.spaceLocator(name="plug_LOC")[0]
        cmds.parent(target, parent)
        cmds.setKeyframe(target, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(target, attribute="translateX", time=20, value=5)
        cmds.setKeyframe(parent, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(parent, attribute="translateX", time=60, value=40)

        driven = cmds.polyCube(name="loom_end")[0]
        cmds.parentConstraint(target, driven)

        baker = SmartBake(objects=[driven])
        start, end = baker.get_time_range()

        self.assertGreaterEqual(
            end,
            60,
            "bake range stopped at the target's own last key, ignoring the "
            "animated ancestor that keeps carrying it",
        )
        self.assertLessEqual(start, 1)


class TestTimeRangeFromNetworkDriver(unittest.TestCase):
    """An unnamed driver type must still contribute to the auto bake range.

    Spline-IK squash/stretch drives ``.scale`` through a ``curveInfo``
    network, which the driver taxonomy does not name.  Such a driver resolved
    to no key times at all, so a scene driven only by one fell back to the
    playback range -- and a range that falls SHORT freezes the bake mid-shot.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_curve_info_driver_contributes_key_times(self):
        from mayatk.anim_utils._anim_utils import AnimUtils

        curve = cmds.curve(point=[(0, 0, 0), (5, 0, 0), (10, 0, 0)], degree=1)
        driver = cmds.cluster(f"{curve}.cv[1]", name="stretch_CL")[1]
        cmds.setKeyframe(driver, attribute="translateY", time=5, value=0)
        cmds.setKeyframe(driver, attribute="translateY", time=48, value=9)

        info = cmds.createNode("curveInfo", name="len_INFO")
        cmds.connectAttr(
            f"{cmds.listRelatives(curve, shapes=True)[0]}.worldSpace[0]",
            f"{info}.inputCurve",
        )

        times = AnimUtils.get_driver_animation_range(info, driver_type="unknown")

        self.assertTrue(times, "curveInfo driver resolved to no key times")
        self.assertEqual((min(times), max(times)), (5.0, 48.0))


class TestUnclassifiedNetworkDriver(unittest.TestCase):
    """A value-carrying network that ends in an unrecognised node type.

    Regression: wire-loom squash/stretch drove ``.scaleX`` through
    ``blendColors -> multiplyDivide -> curveInfo -> nurbsCurve``.
    ``trace_upstream`` walked into the passthrough ``blendColors``, failed
    to classify ``curveInfo``, and returned ``(None, None)`` -- dropping a
    channel that carries real animated values.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_curve_info_network_is_classified(self):
        """A curveInfo-driven scale must resolve to a driver, not None."""
        from mayatk.node_utils.attributes._attributes import Attributes

        cube = cmds.polyCube(name="stretch_cube")[0]
        curve = cmds.curve(point=[(0, 0, 0), (5, 0, 0), (10, 0, 0)], degree=1)
        info = cmds.createNode("curveInfo", name="len_INFO")
        cmds.connectAttr(
            f"{cmds.listRelatives(curve, shapes=True)[0]}.worldSpace[0]",
            f"{info}.inputCurve",
        )
        md = cmds.createNode("multiplyDivide", name="norm_MD")
        cmds.connectAttr(f"{info}.arcLength", f"{md}.input1X")
        blend = cmds.createNode("blendColors", name="stretch_BLEND")
        cmds.connectAttr(f"{md}.outputX", f"{blend}.color1R")
        cmds.connectAttr(f"{blend}.outputR", f"{cube}.scaleX")

        node, kind = Attributes.trace_upstream(f"{cube}.scaleX")

        self.assertIsNotNone(
            node, "curveInfo-driven scale traced to (None, None) and was dropped"
        )
        self.assertIsNotNone(kind)

    def test_network_driven_scale_requires_bake(self):
        """SmartBake must flag the network-driven scale channel."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cube = cmds.polyCube(name="stretch_cube")[0]
        curve = cmds.curve(point=[(0, 0, 0), (5, 0, 0), (10, 0, 0)], degree=1)
        info = cmds.createNode("curveInfo", name="len_INFO")
        cmds.connectAttr(
            f"{cmds.listRelatives(curve, shapes=True)[0]}.worldSpace[0]",
            f"{info}.inputCurve",
        )
        blend = cmds.createNode("blendColors", name="stretch_BLEND")
        cmds.connectAttr(f"{info}.arcLength", f"{blend}.color1R")
        cmds.connectAttr(f"{blend}.outputR", f"{cube}.scaleX")

        analysis = SmartBake(objects=[cube])._analyze_object(cube)

        self.assertTrue(analysis.requires_bake)
        self.assertIn("sx", analysis.all_driven_channels)


class TestBakeTargetFiltering(unittest.TestCase):
    """Nodes and channels that must never enter the bake set."""

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_ik_effector_is_not_a_bake_target(self):
        """ikEffector translate is IK plumbing, not animation.

        Maya wires ``effector.t`` from the chain's last joint, so the
        analysis classified it as a ``joint``-driven channel and baked it.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        j1 = cmds.joint(position=(0, 0, 0), name="ik_j1")
        cmds.joint(position=(5, 0, 0), name="ik_j2")
        cmds.joint(position=(10, 0, 0), name="ik_j3")
        handle, effector = cmds.ikHandle(
            startJoint=j1, endEffector="ik_j3", solver="ikRPsolver"
        )[:2]

        analysis = SmartBake().analyze()
        effector_long = cmds.ls(effector, long=True)[0]

        self.assertNotIn(
            effector_long,
            analysis,
            "ikEffector entered the bake set",
        )

    def test_static_direct_connect_visibility_is_skipped(self):
        """A .v wired from a keyless display toggle is a constant.

        A settings control's ``controlsVis`` attribute is a rig display
        switch -- it carries no keys, so baking it writes a flat constant
        across the whole range for no benefit.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        settings = cmds.spaceLocator(name="settings_CTRL")[0]
        cmds.addAttr(settings, longName="controlsVis", attributeType="bool")
        cmds.setAttr(f"{settings}.controlsVis", 1)
        ctrl = cmds.spaceLocator(name="some_CTRL")[0]
        cmds.connectAttr(f"{settings}.controlsVis", f"{ctrl}.visibility")

        analysis = SmartBake(objects=[ctrl])._analyze_object(ctrl)

        self.assertNotIn(
            "v",
            analysis.all_driven_channels,
            "constant visibility toggle was queued for bake",
        )
        self.assertFalse(analysis.requires_bake)

    def test_animated_direct_connect_visibility_is_kept(self):
        """The same wiring WITH keys on the source must still bake."""
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        settings = cmds.spaceLocator(name="settings_CTRL")[0]
        cmds.addAttr(settings, longName="controlsVis", attributeType="bool")
        cmds.setKeyframe(settings, attribute="controlsVis", time=1, value=0)
        cmds.setKeyframe(settings, attribute="controlsVis", time=10, value=1)
        ctrl = cmds.spaceLocator(name="some_CTRL")[0]
        cmds.connectAttr(f"{settings}.controlsVis", f"{ctrl}.visibility")

        analysis = SmartBake(objects=[ctrl])._analyze_object(ctrl)

        self.assertIn("v", analysis.all_driven_channels)


class TestRestoreUnderChangedWorkingUnit(unittest.TestCase):
    """A bake/restore cycle must not rescale the graph it puts back.

    Maya sizes an implicitly inserted ``unitConversion`` from the working unit
    in force at ``connectAttr`` time. The scene exporter's ``set_linear_unit``
    task is staged and defers its revert to the END of the run, so every
    restore in between lands under the EXPORT's unit rather than the scene's.
    Putting a rig's ``multiplyDivide.outputX -> transform.translateY`` link
    back under metres therefore inserts a cf=100 node where the scene had a
    direct connection, multiplying that channel by 100 for good.

    That is not hypothetical: it is how all seven VDATS wire-loom auto-bend
    channels came to be 100x too large (peak bow 229 cm instead of 2.3 cm,
    displacing the bind joints by up to 108 cm) once the mutated scene was
    saved.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)
        self._units = (
            cmds.currentUnit(q=True, linear=True),
            cmds.currentUnit(q=True, angle=True),
        )

    def tearDown(self):
        if self.maya_available:
            # these tests change the working unit deliberately; a failed
            # assertion must not leave it changed for every test after this one
            linear, angle = self._units
            cmds.currentUnit(linear=linear, angle=angle)
            cmds.file(new=True, force=True)

    @staticmethod
    def _conversion_factor(plug):
        """Factor of a unitConversion feeding *plug*, or None if direct."""
        src = cmds.listConnections(plug, source=True, destination=False, plugs=True)
        if not src:
            return None
        node = src[0].partition(".")[0]
        if cmds.nodeType(node) != "unitConversion":
            return None
        return cmds.getAttr(f"{node}.conversionFactor")

    @staticmethod
    def _auto_bend_network():
        """The loom auto-bend rig, reduced: CTRL worldMatrix -> distanceBetween
        -> plusMinusAverage -> clamp -> multiplyDivide -> translateY.

        The shape matters. A channel driven straight off a keyed animCurve is
        classified as already-animated and never baked, which silently makes
        this test measure nothing; a worldMatrix-rooted network is what
        SmartBake actually takes to an override layer.
        """
        start = cmds.spaceLocator(name="start_CTRL")[0]
        end = cmds.spaceLocator(name="end_CTRL")[0]
        cmds.setKeyframe(end, attribute="translateX", time=1, value=10)
        cmds.setKeyframe(end, attribute="translateX", time=10, value=2)
        dist = cmds.createNode("distanceBetween", name="ab_dist")
        cmds.connectAttr(f"{start}.worldMatrix[0]", f"{dist}.inMatrix1")
        cmds.connectAttr(f"{end}.worldMatrix[0]", f"{dist}.inMatrix2")
        pma = cmds.createNode("plusMinusAverage", name="ab_sub")
        cmds.setAttr(f"{pma}.operation", 2)
        cmds.setAttr(f"{pma}.input1D[0]", 10.0)
        cmds.connectAttr(f"{dist}.distance", f"{pma}.input1D[1]")
        clamp = cmds.createNode("clamp", name="ab_clamp")
        cmds.setAttr(f"{clamp}.maxR", 10000)
        cmds.connectAttr(f"{pma}.output1D", f"{clamp}.inputR")
        md = cmds.createNode("multiplyDivide", name="ab_mult")
        cmds.connectAttr(f"{clamp}.outputR", f"{md}.input1X")
        cmds.setAttr(f"{md}.input2X", 0.5)
        grp = cmds.group(empty=True, name="mid_autoBend_GRP")
        cmds.connectAttr(f"{md}.outputX", f"{grp}.translateY")
        return grp

    def test_restoring_a_linear_driver_under_metres_does_not_rescale_it(self):
        """The reported defect: a direct link comes back multiplied by 100."""
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        cmds.currentUnit(linear="cm")  # author it in cm, whatever the prefs say
        md = cmds.createNode("multiplyDivide", name="ab_mult")
        grp = cmds.group(empty=True, name="mid_autoBend_GRP")
        cmds.connectAttr(f"{md}.outputX", f"{grp}.translateY")
        self.assertIsNone(
            self._conversion_factor(f"{grp}.translateY"),
            "authored in cm, the link should be direct",
        )

        session = {
            "connections": BakeSessionStore.snapshot_connections(f"{grp}.translateY")
        }
        cmds.disconnectAttr(f"{md}.outputX", f"{grp}.translateY")

        cmds.currentUnit(linear="m")  # what the exporter leaves in force
        BakeSessionStore.restore_session(session)
        cmds.currentUnit(linear="cm")

        factor = self._conversion_factor(f"{grp}.translateY")
        self.assertAlmostEqual(
            1.0 if factor is None else factor,
            1.0,
            places=9,
            msg=f"restore rescaled the channel by {factor}x",
        )

    def test_override_layer_restore_under_metres_does_not_rescale(self):
        """The SECOND leak: Maya reconnects when the LAYER is deleted.

        ``restore_session``'s own connection replay is unit-safe, but an
        override-layer bake never records those connections at all -- the
        original wiring stays live under the layer's blend node, so nothing
        appears to need restoring. Deleting the layer makes MAYA re-establish
        the direct link, and it sizes any implicit unitConversion from the
        working unit in force at that moment. The exporter's is metres, so a
        rig's ``multiplyDivide.outputX -> translateY`` comes back x100 with no
        repaired code path anywhere near it.

        Measured on the VDATS looms: auto-bend bow 229 cm instead of 2.3 cm.
        """
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cmds.playbackOptions(minTime=1, maxTime=10)
        grp = self._auto_bend_network()
        self.assertIsNone(
            self._conversion_factor(f"{grp}.translateY"),
            "authored in cm, the link should be direct",
        )

        baked = SmartBake(
            objects=[grp], use_override_layer=True, delete_inputs=False
        ).execute()
        # a bake that baked nothing would make this test vacuous
        self.assertTrue(
            baked.override_layer, "no override layer was created -- nothing was baked"
        )

        cmds.currentUnit(linear="m")  # what the exporter leaves in force
        SmartBake.restore()
        cmds.currentUnit(linear="cm")

        factor = self._conversion_factor(f"{grp}.translateY")
        self.assertAlmostEqual(
            1.0 if factor is None else factor,
            1.0,
            places=9,
            msg=f"deleting the override layer rescaled the channel by {factor}x",
        )

    def test_override_layer_restore_keeps_a_REAL_conversion(self):
        """The layer path must put back the factor that was there, not 1.0.

        The linear case is safe by luck: the loom links were direct, so
        recording a flat 1.0 happened to be right. A channel that legitimately
        CARRIES a conversion proves whether the factor is actually read. A
        unitless output driving an ANGLE gets deg->rad (0.0174533) in a degree
        scene; restoring under radians is where Maya would insert none, so a
        snapshot that recorded 1.0 silently drops the factor and the channel
        comes back ~57x off.
        """
        from maya import cmds
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        cmds.playbackOptions(minTime=1, maxTime=10)
        grp = self._auto_bend_network()
        # re-point the same driven network at an ANGLE channel
        cmds.disconnectAttr("ab_mult.outputX", f"{grp}.translateY")
        cmds.connectAttr("ab_mult.outputX", f"{grp}.rotateY")
        authored = self._conversion_factor(f"{grp}.rotateY")
        self.assertIsNotNone(authored, "expected deg->rad in a degree scene")

        baked = SmartBake(
            objects=[grp], use_override_layer=True, delete_inputs=False
        ).execute()
        self.assertTrue(
            baked.override_layer, "no override layer was created -- nothing was baked"
        )

        cmds.currentUnit(angle="rad")
        SmartBake.restore()
        cmds.currentUnit(angle="deg")

        restored = self._conversion_factor(f"{grp}.rotateY")
        self.assertIsNotNone(restored, "the authored angular conversion was dropped")
        self.assertAlmostEqual(restored, authored, places=9)

    def test_a_conversion_that_was_already_there_is_preserved(self):
        """The fix must put back what WAS there, not force 1.0 blindly."""
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        cmds.currentUnit(angle="deg")  # author it in degrees, whatever the prefs say
        md = cmds.createNode("multiplyDivide", name="spin_mult")
        grp = cmds.group(empty=True, name="spin_GRP")
        # unitless -> ANGLE under degrees: Maya inserts deg->rad (0.0174533)
        cmds.connectAttr(f"{md}.outputX", f"{grp}.rotateY")
        authored = self._conversion_factor(f"{grp}.rotateY")
        self.assertIsNotNone(authored, "expected an angular conversion in a deg scene")

        session = {
            "connections": BakeSessionStore.snapshot_connections(f"{grp}.rotateY")
        }
        src = cmds.listConnections(
            f"{grp}.rotateY", source=True, destination=False, plugs=True
        )[0]
        cmds.disconnectAttr(src, f"{grp}.rotateY")

        cmds.currentUnit(angle="rad")  # a unit under which Maya would insert none
        BakeSessionStore.restore_session(session)
        cmds.currentUnit(angle="deg")

        restored = self._conversion_factor(f"{grp}.rotateY")
        self.assertIsNotNone(restored, "the angular conversion was dropped")
        self.assertAlmostEqual(restored, authored, places=9)


# -----------------------------------------------------------------------------


class TestPerObjectBakeRanges(unittest.TestCase):
    """bake() samples each object over the frames ITS drivers need.

    bakeResults cost tracks frames (halving the range halves it), so one
    global union range paid for every object what its longest sibling
    needed. A keyed driver yields its key extent, a provably static one a
    single frame, and anything the resolver cannot prove gets the global
    range -- what every object got before -- so a wrong call costs frames,
    never motion.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)
        cmds.playbackOptions(minTime=1, maxTime=200)

    def tearDown(self):
        cmds.file(new=True, force=True)

    @staticmethod
    def _keyed_locator(name, start, end, attr="translateX"):
        loc = cmds.spaceLocator(name=name)[0]
        cmds.setKeyframe(loc, attribute=attr, time=start, value=0)
        cmds.setKeyframe(loc, attribute=attr, time=end, value=10)
        return loc

    @staticmethod
    def _keys(obj, attr="tx"):
        return cmds.keyframe(f"{obj}.{attr}", query=True, keyframeCount=True) or 0

    def _spanning_object(self, start, end):
        """A driven object keyed over *start*..*end*, so the global range is
        wider than the one under test and a narrowed range is visible."""
        loc = self._keyed_locator(f"span_{start}_{end}_LOC", start, end)
        cube = cmds.polyCube(name=f"span_{start}_{end}_cube")[0]
        cmds.pointConstraint(loc, cube)
        return cube

    def _bake(self, objects, **kwargs):
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        baker = SmartBake(objects=objects, use_override_layer=False, **kwargs)
        return baker.bake(baker.analyze())

    def test_static_target_bakes_one_frame(self):
        static_loc = cmds.spaceLocator(name="static_LOC")[0]
        cmds.setAttr(f"{static_loc}.translate", 5.0, 2.0, -3.0)
        static_cube = cmds.polyCube(name="static_cube")[0]
        cmds.parentConstraint(static_loc, static_cube)
        moving_loc = self._keyed_locator("moving_LOC", 1, 50)
        moving_cube = cmds.polyCube(name="moving_cube")[0]
        cmds.parentConstraint(moving_loc, moving_cube)

        result = self._bake([static_cube, moving_cube])

        self.assertEqual(result.time_range, (1, 50))
        self.assertEqual(result.object_time_ranges[static_cube], (1, 1))
        self.assertEqual(result.object_time_ranges[moving_cube], (1, 50))
        self.assertEqual(self._keys(static_cube), 1)
        self.assertEqual(self._keys(moving_cube), 50)
        for frame in (1, 25, 50):
            cmds.currentTime(frame)
            self.assertAlmostEqual(cmds.getAttr(f"{static_cube}.tx"), 5.0, places=5)

    def test_static_target_under_animated_parent_keeps_the_parent_range(self):
        """A constraint pins the child in WORLD space: a moving parent changes
        its locals every frame even when the target never moves."""
        static_loc = cmds.spaceLocator(name="static_LOC")[0]
        cmds.setAttr(f"{static_loc}.translate", 5.0, 2.0, -3.0)
        grp = cmds.group(empty=True, name="anim_GRP")
        cmds.setKeyframe(grp, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(grp, attribute="translateX", time=30, value=10)
        cube = cmds.polyCube(name="child_cube")[0]
        cube = cmds.parent(cube, grp)[0]
        cube = cmds.ls(cube, long=True)[0]
        cmds.parentConstraint(static_loc, cube)

        result = self._bake([cube])

        self.assertEqual(result.object_time_ranges[cube], (1, 30))
        self.assertEqual(self._keys(cube), 30)

    def test_staggered_drivers_get_their_own_ranges(self):
        loc_a = self._keyed_locator("a_LOC", 10, 20)
        loc_b = self._keyed_locator("b_LOC", 100, 200)
        cube_a = cmds.polyCube(name="cube_a")[0]
        cube_b = cmds.polyCube(name="cube_b")[0]
        cmds.pointConstraint(loc_a, cube_a)
        cmds.pointConstraint(loc_b, cube_b)

        result = self._bake([cube_a, cube_b])

        self.assertEqual(result.time_range, (10, 200))
        self.assertEqual(result.object_time_ranges[cube_a], (10, 20))
        self.assertEqual(result.object_time_ranges[cube_b], (100, 200))
        self.assertEqual(self._keys(cube_a), 11)
        self.assertEqual(self._keys(cube_b), 101)

    def test_constraint_chain_resolves_through_the_intermediate(self):
        """A target that is itself constrained reports no keys of its own.
        The walk follows the intermediate constraint to the keyed root
        behind it rather than giving up -- and must not read as static."""
        root = self._keyed_locator("root_LOC", 10, 40)
        spanner = self._spanning_object(
            1, 120
        )  # something else widens the global range
        mid = cmds.polyCube(name="mid_cube")[0]
        tip = cmds.polyCube(name="tip_cube")[0]
        cmds.pointConstraint(root, mid)
        cmds.pointConstraint(mid, tip)

        result = self._bake([mid, tip, spanner])

        self.assertEqual(result.time_range, (1, 120))
        self.assertEqual(result.object_time_ranges[mid], (10, 40))
        self.assertEqual(result.object_time_ranges[tip], (10, 40))
        self.assertEqual(self._keys(tip), 31)

    def test_matrix_network_resolves_to_its_animated_leaf(self):
        """An ``offsetParentMatrix`` drive through a matrix network: the walk
        continues through multMatrix to the keyed transform feeding it
        instead of stopping at the first node it did not expect."""
        driver = self._keyed_locator("mtx_driver_LOC", 20, 60)
        spanner = self._spanning_object(1, 150)
        group = cmds.group(empty=True, name="mtx_parent_GRP")
        cube = cmds.parent(cmds.polyCube(name="mtx_cube")[0], group)[0]
        mult = cmds.createNode("multMatrix", name="mtx_MM")
        cmds.connectAttr(f"{driver}.worldMatrix[0]", f"{mult}.matrixIn[0]")
        cmds.connectAttr(f"{group}.worldInverseMatrix[0]", f"{mult}.matrixIn[1]")
        cmds.connectAttr(f"{mult}.matrixSum", f"{cube}.offsetParentMatrix")

        result = self._bake([cube, spanner])

        self.assertEqual(result.time_range, (1, 150))
        self.assertEqual(result.object_time_ranges[cube], (20, 60))
        self.assertEqual(self._keys(cube), 41)

    def test_matrix_objects_keep_their_own_ranges_in_one_pass(self):
        """Two matrix drives with different key extents each get their own
        keys. The pass walks the timeline ONCE for the whole set -- every
        currentTime is a full scene evaluation -- and narrows only the reads
        and the keys, so a mixed set must not smear one object's range onto
        the other."""
        spanner = self._spanning_object(1, 150)
        made = {}
        for name, first, last in (("early", 10, 30), ("late", 100, 140)):
            driver = self._keyed_locator(f"{name}_driver_LOC", first, last)
            group = cmds.group(empty=True, name=f"{name}_parent_GRP")
            cube = cmds.parent(cmds.polyCube(name=f"{name}_cube")[0], group)[0]
            mult = cmds.createNode("multMatrix", name=f"{name}_MM")
            cmds.connectAttr(f"{driver}.worldMatrix[0]", f"{mult}.matrixIn[0]")
            cmds.connectAttr(f"{group}.worldInverseMatrix[0]", f"{mult}.matrixIn[1]")
            cmds.connectAttr(f"{mult}.matrixSum", f"{cube}.offsetParentMatrix")
            made[name] = (cube, first, last)

        worlds = {}
        for name, (cube, first, last) in made.items():
            for frame in (first, (first + last) // 2, last):
                cmds.currentTime(frame)
                worlds[(name, frame)] = cmds.xform(
                    cube, query=True, worldSpace=True, matrix=True
                )

        result = self._bake([c for c, _, _ in made.values()] + [spanner])

        for name, (cube, first, last) in made.items():
            self.assertEqual(result.object_time_ranges[cube], (first, last))
            self.assertEqual(self._keys(cube), last - first + 1, name)
            for frame in (first, (first + last) // 2, last):
                cmds.currentTime(frame)
                baked = cmds.xform(cube, query=True, worldSpace=True, matrix=True)
                for got, want in zip(baked, worlds[(name, frame)]):
                    self.assertAlmostEqual(got, want, places=5, msg=f"{name}@{frame}")

    def test_utility_network_resolves_through_math_nodes(self):
        """The scalar half of a rig's plumbing -- multiplyDivide into a
        condition -- traces to the keyed leaf behind it."""
        driver = self._keyed_locator("math_driver_LOC", 30, 45)
        spanner = self._spanning_object(1, 90)
        multiply = cmds.createNode("multiplyDivide", name="math_MD")
        condition = cmds.createNode("condition", name="math_COND")
        cmds.connectAttr(f"{driver}.translateX", f"{multiply}.input1X")
        cmds.setAttr(f"{multiply}.input2X", 2.0)
        cmds.connectAttr(f"{multiply}.outputX", f"{condition}.colorIfTrueR")
        target = cmds.spaceLocator(name="math_target_LOC")[0]
        cmds.connectAttr(f"{condition}.outColorR", f"{target}.translateY")
        cube = cmds.polyCube(name="math_cube")[0]
        cmds.pointConstraint(target, cube)

        result = self._bake([cube, spanner])

        self.assertEqual(result.object_time_ranges[cube], (30, 45))

    def test_constraint_to_an_ik_driven_joint_covers_the_ik_range(self):
        """An IK solver writes a chain joint's rotation with NO connection to
        show for it, so a wire-walk alone sees an unkeyed, unconstrained
        joint and would call anything following it static -- losing the
        motion. The joint has to report the handle that solves it."""
        cmds.select(clear=True)
        root = cmds.joint(name="ik_root_JNT", position=(0, 0, 0))
        mid = cmds.joint(name="ik_mid_JNT", position=(0, 5, 0))
        tip = cmds.joint(name="ik_tip_JNT", position=(0, 10, 0))
        cmds.joint(root, edit=True, orientJoint="xyz")
        handle = cmds.ikHandle(startJoint=root, endEffector=tip, solver="ikRPsolver")[0]
        cmds.setKeyframe(handle, attribute="translateX", time=5, value=0)
        cmds.setKeyframe(handle, attribute="translateX", time=15, value=6)
        spanner = self._spanning_object(1, 120)
        cube = cmds.polyCube(name="follower_cube")[0]
        cmds.parentConstraint(mid, cube)

        result = self._bake([cube, spanner])

        self.assertEqual(result.time_range, (1, 120))
        self.assertEqual(result.object_time_ranges[cube], (5, 15))

    def test_spline_ik_covers_the_curve_that_shapes_the_chain(self):
        """A spline solver's chain follows its CURVE. When that curve is
        skinned to joints keyed somewhere else entirely -- the shape of every
        wire-loom rig in production -- the handle's own placement says
        nothing about when the chain moves."""
        cmds.select(clear=True)
        chain = [
            cmds.joint(name=f"spline_{i}_JNT", position=(0, i * 3.0, 0))
            for i in range(4)
        ]
        _handle, _effector, curve = cmds.ikHandle(
            startJoint=chain[0],
            endEffector=chain[-1],
            solver="ikSplineSolver",
            createCurve=True,
            numSpans=2,
        )
        cmds.select(clear=True)
        drv_a = cmds.joint(name="curve_drv_a_JNT", position=(0, 0, 0))
        cmds.select(clear=True)
        drv_b = cmds.joint(name="curve_drv_b_JNT", position=(0, 9, 0))
        cmds.skinCluster(drv_a, drv_b, curve, toSelectedBones=True)
        cmds.setKeyframe(drv_b, attribute="translateX", time=40, value=0)
        cmds.setKeyframe(drv_b, attribute="translateX", time=80, value=5)
        spanner = self._spanning_object(1, 200)
        cube = cmds.polyCube(name="spline_follower_cube")[0]
        cmds.parentConstraint(chain[2], cube)

        result = self._bake([cube, spanner])

        self.assertEqual(result.time_range, (1, 200))
        self.assertEqual(result.object_time_ranges[cube], (40, 80))

    def test_a_sheared_folded_local_is_reported_not_lost_quietly(self):
        """A matrix drive whose folded local (``matrix x offsetParentMatrix``)
        SHEARS has no translate/rotate/scale form -- the bake writes the shear
        per frame, it is never keyed, and it is zeroed at the end, so the
        world drifts and the drift compounds down a chain (measured: 0.32 per
        link, 7.8 cm at the tip of a production 22-joint wire loom). The
        remedy is the exporter's flatten_sheared_chains, which runs before
        smart_bake; the bake must at least say so."""
        from unittest import mock

        cube = cmds.polyCube(name="sheared_cube")[0]
        cmds.setAttr(f"{cube}.rotateZ", 30)  # rotate BEFORE the offset's scale
        compose = cmds.createNode("composeMatrix", name="shear_CM")
        cmds.setAttr(f"{compose}.inputScaleX", 2.0)  # non-uniform, so R*S shears
        cmds.setKeyframe(compose, attribute="inputTranslateX", time=1, value=0)
        cmds.setKeyframe(compose, attribute="inputTranslateX", time=20, value=5)
        cmds.connectAttr(f"{compose}.outputMatrix", f"{cube}.offsetParentMatrix")

        with mock.patch.object(cmds, "warning") as warned:
            self._bake([cube])

        said = " ".join(str(call) for call in warned.call_args_list)
        self.assertIn("SHEAR", said.upper(), f"no shear warning; got {said!r}")
        self.assertIn("flatten_sheared_chains", said)

    def test_unvetted_node_type_still_falls_back(self):
        """A node the walk has no ruling on keeps the global range. A
        frameCache reads its input curve at a DIFFERENT time, so its key
        extent proves nothing -- exactly the case the whitelist protects."""
        driver = self._keyed_locator("fc_driver_LOC", 10, 20)
        spanner = self._spanning_object(1, 90)
        cache = cmds.createNode("frameCache", name="fc_NODE")
        cmds.connectAttr(f"{driver}.translateX", f"{cache}.stream")
        target = cmds.spaceLocator(name="fc_target_LOC")[0]
        cmds.connectAttr(f"{cache}.varying", f"{target}.translateY")
        cube = cmds.polyCube(name="fc_cube")[0]
        cmds.pointConstraint(target, cube)

        result = self._bake([cube, spanner])

        self.assertEqual(result.object_time_ranges[cube], result.time_range)

    def test_a_narrowed_bake_reproduces_the_authored_world_everywhere(self):
        """The safety property the whole per-object range rests on: outside
        its own range the baked curve HOLDS its end value, and that has to be
        what the rig was doing there. Sampled inside and outside, against the
        authored evaluation."""
        root = self._keyed_locator("root_LOC", 40, 80)
        spanner = self._spanning_object(1, 160)
        cube = cmds.polyCube(name="narrowed_cube")[0]
        cmds.parentConstraint(root, cube)

        probes = (1, 20, 40, 60, 80, 120, 160)
        authored = {}
        for frame in probes:
            cmds.currentTime(frame)
            authored[frame] = cmds.xform(cube, query=True, worldSpace=True, matrix=True)

        result = self._bake([cube, spanner])

        self.assertEqual(result.object_time_ranges[cube], (40, 80))
        self.assertEqual(self._keys(cube), 41)
        for frame in probes:
            cmds.currentTime(frame)
            baked = cmds.xform(cube, query=True, worldSpace=True, matrix=True)
            for got, want in zip(baked, authored[frame]):
                self.assertAlmostEqual(got, want, places=5, msg=f"frame {frame}")

    def test_time_expression_falls_back_to_the_global_range(self):
        loc = self._keyed_locator("span_LOC", 1, 60)
        keyed_cube = cmds.polyCube(name="keyed_cube")[0]
        cmds.pointConstraint(loc, keyed_cube)
        expr_cube = cmds.polyCube(name="expr_cube")[0]
        cmds.expression(s=f"{expr_cube}.ty = sin(time * 2) * 5;")

        result = self._bake([keyed_cube, expr_cube])

        self.assertEqual(result.object_time_ranges[expr_cube], (1, 60))
        self.assertEqual(self._keys(expr_cube, "ty"), 60)

    def test_cycling_driver_falls_back_to_the_global_range(self):
        loc_short = self._keyed_locator("cycle_LOC", 1, 20)
        cmds.setInfinity(loc_short, attribute="translateX", postInfinite="cycle")
        loc_long = self._keyed_locator("long_LOC", 1, 100)
        cube_short = cmds.polyCube(name="cycle_cube")[0]
        cube_long = cmds.polyCube(name="long_cube")[0]
        cmds.pointConstraint(loc_short, cube_short)
        cmds.pointConstraint(loc_long, cube_long)

        result = self._bake([cube_short, cube_long])

        self.assertEqual(result.object_time_ranges[cube_short], (1, 100))
        self.assertEqual(self._keys(cube_short), 100)

    def test_explicit_time_range_applies_to_every_object(self):
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake

        static_loc = cmds.spaceLocator(name="static_LOC")[0]
        cube = cmds.polyCube(name="static_cube")[0]
        cmds.parentConstraint(static_loc, cube)

        baker = SmartBake(objects=[cube], use_override_layer=False)
        result = baker.bake(baker.analyze(), time_range=(1, 30))

        self.assertEqual(result.time_range, (1, 30))
        self.assertEqual(result.object_time_ranges[cube], (1, 30))
        self.assertEqual(self._keys(cube), 30)

    def test_driven_key_range_follows_its_driver(self):
        def build(keyed):
            cmds.file(new=True, force=True)
            driver = cmds.polyCube(name="sdk_driver")[0]
            driven = cmds.polyCube(name="sdk_driven")[0]
            for dv, v in ((0, 0), (10, 5)):
                cmds.setDrivenKeyframe(
                    f"{driven}.ty", currentDriver=f"{driver}.tx", dv=dv, v=v
                )
            if keyed:
                cmds.setKeyframe(driver, attribute="tx", time=5, value=0)
                cmds.setKeyframe(driver, attribute="tx", time=15, value=10)
            return driven

        driven = build(keyed=False)
        self.assertEqual(self._bake([driven]).object_time_ranges[driven], (1, 1))

        driven = build(keyed=True)
        result = self._bake([driven])
        self.assertEqual(result.object_time_ranges[driven], (5, 15))
        self.assertEqual(self._keys(driven, "ty"), 11)


if __name__ == "__main__":
    unittest.main()
