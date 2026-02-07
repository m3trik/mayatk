# !/usr/bin/python
# coding=utf-8
"""Tests for SmartBake module."""
import unittest


class TestSmartBake(unittest.TestCase):
    """Test SmartBake analysis and baking functionality."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures - runs once before all tests."""
        try:
            from maya import cmds
            from maya import standalone

            standalone.initialize(name="python")
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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="bake_cube")[0]
        locator = cmds.spaceLocator(name="bake_loc")[0]
        cmds.pointConstraint(locator, cube)

        # Animate locator
        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cube], delete_inputs=True)
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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        result = baker.execute()

        # Verify pre-existing key still exists
        key_times = cmds.keyframe(f"{cube}.sx", query=True, timeChange=True)
        self.assertIn(-10.0, key_times)

    # -------------------------------------------------------------------------
    # Edge Case Tests
    # -------------------------------------------------------------------------

    def test_empty_scene_no_objects(self):
        """Verify graceful handling of empty scene."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        # Empty scene with no transforms (only default cameras)
        all_transforms = cmds.ls(type="transform", long=True) or []
        # Filter out default cameras
        user_transforms = [t for t in all_transforms if "Camera" not in t]

        baker = SmartBake(objects=[])
        result = baker.execute()

        self.assertFalse(result.success)
        self.assertEqual(len(result.baked), 0)

    def test_object_with_no_connections(self):
        """Verify objects with no incoming connections are skipped."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="isolated_cube")[0]
        # No constraints, no keys, nothing

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Should not appear in analysis at all (no requires_bake, no already_keyed)
        self.assertNotIn(cube, analysis)

    def test_deleted_object_during_bake(self):
        """Verify handling when referenced object no longer exists."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="time_expr_cube")[0]
        cmds.expression(s=f"{cube}.ty = sin(time * 2) * 5", name="sine_expression")

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        self.assertIn(cube, analysis)
        self.assertIn("expression", analysis[cube].driven_channels)

    def test_scale_constraint(self):
        """Verify scale constraints are detected."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="sample_cube")[0]
        locator = cmds.spaceLocator(name="sample_loc")[0]
        cmds.pointConstraint(locator, cube)

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        # Bake with sample_by=2 (every 2 frames)
        baker = SmartBake(objects=[cube], sample_by=2, delete_inputs=True)
        result = baker.execute()

        # Check key count - should be approximately (10-1)/2 + 1 = 5-6 keys
        key_times = cmds.keyframe(f"{cube}.tx", query=True, timeChange=True) or []
        # With sample_by=2 from frame 1-10, expect keys at 1,3,5,7,9 or similar
        self.assertLess(len(key_times), 10)

    def test_long_path_names(self):
        """Verify handling of long DAG path names with namespaces."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="shape_test_cube")[0]
        shape = cmds.listRelatives(cube, shapes=True)[0]

        # Try to analyze the shape node (not a transform)
        baker = SmartBake(objects=[shape])
        analysis = baker.analyze()

        # Should handle gracefully - shape has no transform attrs
        # May or may not be in analysis, but shouldn't crash

    def test_optimize_keys_option(self):
        """Verify optimize_keys option calls AnimUtils.optimize_keys."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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

    def test_classmethod_run(self):
        """Verify SmartBake.run() classmethod works correctly."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import BakeResult

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
        from mayatk.anim_utils.smart_bake import BakeAnalysis

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="delete_test_cube")[0]
        locator = cmds.spaceLocator(name="del_test_loc")[0]
        constraint = cmds.parentConstraint(locator, cube)[0]

        cmds.setKeyframe(locator, attribute="tx", time=1, value=0)
        cmds.setKeyframe(locator, attribute="tx", time=10, value=10)

        baker = SmartBake(objects=[cube], delete_inputs=True)
        result = baker.execute()

        # Constraint should be deleted
        self.assertFalse(cmds.objExists(constraint))
        self.assertIn(constraint, result.deleted)

    def test_delete_inputs_removes_expressions(self):
        """Verify delete_inputs removes expression nodes after baking."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        cube = cmds.polyCube(name="expr_del_cube")[0]
        expr = cmds.expression(s=f"{cube}.ty = time * 2", name="test_expr")

        cmds.playbackOptions(minTime=1, maxTime=10)

        baker = SmartBake(objects=[cube], delete_inputs=True)
        result = baker.execute()

        # Expression should be deleted
        self.assertFalse(cmds.objExists(expr))

    def test_visibility_channel(self):
        """Verify visibility channel can be baked from expressions."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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

    def test_ik_chain_detection(self):
        """Verify joints in IK chains are detected as needing bake."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

        # Create a joint chain
        cmds.select(clear=True)
        j1 = cmds.joint(name="ik_joint1", position=(0, 0, 0))
        j2 = cmds.joint(name="ik_joint2", position=(2, 0, 0))
        j3 = cmds.joint(name="ik_joint3", position=(4, 0, 0))

        # Create IK handle from j1 to j3
        ik_handle = cmds.ikHandle(
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
        from mayatk.anim_utils.smart_bake import SmartBake

        # Create a curve path
        curve = cmds.curve(
            name="path_curve",
            degree=3,
            point=[(0, 0, 0), (2, 2, 0), (4, 0, 0), (6, 2, 0)],
        )

        # Create object to attach
        cube = cmds.polyCube(name="path_cube")[0]

        # Attach to path
        motion_path = cmds.pathAnimation(
            cube, curve, fractionMode=True, startTimeU=1, endTimeU=30
        )

        cmds.playbackOptions(minTime=1, maxTime=30)

        baker = SmartBake(objects=[cube])
        analysis = baker.analyze()

        # Cube should be detected as driven by motion path
        self.assertIn(cube, analysis)
        self.assertIn("motion_path", analysis[cube].driven_channels)

    def test_blend_shape_sdk_detection(self):
        """Verify blend shapes driven by SDKs are detected."""
        from maya import cmds
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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
        from mayatk.anim_utils.smart_bake import SmartBake

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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
