import unittest
from unittest.mock import MagicMock, patch
import sys

# Detect whether the real maya.cmds is already loaded (run_tests.py path).
# If so, do NOT mock — replacing sys.modules["maya.cmds"] in a live Maya
# session corrupts every later import of it (and made the skip guard below
# unsatisfiable, so these mock tests ran — and errored — against real Maya).
# Same pattern as test_marmoset_bridge.py.
_REAL_MAYA_AVAILABLE = "maya.cmds" in sys.modules and not isinstance(
    sys.modules.get("maya.cmds"), MagicMock
)

# conftest.py auto-loads under pytest and injects sys.modules["maya.cmds"].
# Reuse that mock so production code paths (which call cmds.*) and the test
# expectations (which configure mock_cmds.*) share the same MagicMock object.
mock_cmds = sys.modules.get("maya.cmds")
if not _REAL_MAYA_AVAILABLE and not isinstance(mock_cmds, MagicMock):
    mock_cmds = MagicMock()
    sys.modules["maya.cmds"] = mock_cmds

# Now import the module to test
try:
    from mayatk.rig_utils import telescope_rig
except ImportError as e:
    print(f"ImportError during test setup: {e}")
    raise


# Skip when the real Maya runtime is loaded — these tests configure MagicMock
# return values that real Maya would never honour.
_REAL_MAYA_LOADED = _REAL_MAYA_AVAILABLE
_CMDS_IS_MOCKED = not _REAL_MAYA_LOADED


def setUpModule():
    if _REAL_MAYA_LOADED:
        raise unittest.SkipTest(
            "Mock-based suite — skipped when real Maya is loaded."
        )


def _restore_conftest_defaults():
    """Re-apply the shared conftest mock defaults other mock suites rely on."""
    mock_cmds.reset_mock(return_value=True, side_effect=True)
    mock_cmds.currentTime.return_value = 1.0
    mock_cmds.playbackOptions.return_value = 0.0
    mock_cmds.objExists.return_value = True
    mock_cmds.ls.return_value = []
    mock_cmds.nodeType.return_value = "transform"
    mock_cmds.listConnections.return_value = []
    mock_cmds.listRelatives.return_value = []
    mock_cmds.keyframe.return_value = []
    mock_cmds.keyTangent.return_value = []
    mock_cmds.scriptJob.side_effect = lambda **kw: 999 if "event" in kw else True


def _ls_passthrough(*args, **kwargs):
    """cmds.ls stand-in: echo the queried names back as a flat string list."""
    flat = []
    for arg in args:
        if isinstance(arg, (list, tuple, set)):
            flat.extend(str(a) for a in arg)
        else:
            flat.append(str(arg))
    return flat


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test — run via pytest, not run_tests.py"
)
class TestTelescopeRig(unittest.TestCase):
    """Node-graph expectations for TelescopeRig.setup_telescope_rig/teardown."""

    def setUp(self):
        _restore_conftest_defaults()
        self.addCleanup(_restore_conftest_defaults)

        mock_cmds.ls.side_effect = _ls_passthrough
        mock_cmds.shadingNode.return_value = "telescope_distance"
        mock_cmds.aimConstraint.return_value = ["aim_C"]
        mock_cmds.parentConstraint.return_value = ["par_C"]
        mock_cmds.pointConstraint.return_value = ["pnt_C"]

        def getattr_side_effect(plug, **kwargs):
            if kwargs.get("settable"):
                return True
            if kwargs.get("lock"):
                return False
            return 1.0  # build-pose scale

        mock_cmds.getAttr.side_effect = getattr_side_effect

        # World distance is om-math; om is a MagicMock here, so stub the helper.
        patcher = patch.object(
            telescope_rig.TelescopeRig, "_world_distance", return_value=10.0
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.rig = telescope_rig.TelescopeRig()
        self.rig.logger = MagicMock()

    def test_setup_telescope_rig_basic_flow(self):
        """Happy path: world-space driver, graded weights, linear driven keys."""
        bundle = self.rig.setup_telescope_rig(
            "base_LOC", "end_LOC", ["seg_01", "seg_02", "seg_03"],
            collapsed_distance=2.0,
        )

        # Distance node driven by worldMatrix (not local .translate).
        mock_cmds.shadingNode.assert_called_once_with(
            "distanceBetween", asUtility=True, name="telescope_distance"
        )
        mock_cmds.connectAttr.assert_any_call(
            "base_LOC.worldMatrix[0]", "telescope_distance.inMatrix1"
        )
        mock_cmds.connectAttr.assert_any_call(
            "end_LOC.worldMatrix[0]", "telescope_distance.inMatrix2"
        )

        # Mutual locator aims + one interior aim; end segments parent-constrained.
        self.assertEqual(mock_cmds.aimConstraint.call_count, 3)
        self.assertEqual(mock_cmds.parentConstraint.call_count, 2)

        # Interior constrained to BOTH locators with graded weights (f=0.5),
        # each weight applied at creation so the maintained offset is valid.
        mock_cmds.pointConstraint.assert_any_call(
            "base_LOC", "seg_02", mo=True, weight=0.5, name="telescope_seg1_PNT"
        )
        mock_cmds.pointConstraint.assert_any_call(
            "end_LOC", "seg_02", mo=True, weight=0.5
        )

        # Driven keys through (initial, s0) and (collapsed, s0*ratio) with
        # SPLINE tangents (an end key's "linear" tangent is flat, which would
        # break the linear post-infinity); infinity clamps collapse, extends
        # stretch.
        driver = "telescope_distance.distance"
        mock_cmds.setDrivenKeyframe.assert_any_call(
            "seg_02.scaleY", currentDriver=driver, driverValue=10.0, value=1.0,
            inTangentType="spline", outTangentType="spline",
        )
        mock_cmds.setDrivenKeyframe.assert_any_call(
            "seg_02.scaleY", currentDriver=driver, driverValue=2.0, value=0.2,
            inTangentType="spline", outTangentType="spline",
        )
        mock_cmds.setInfinity.assert_called_once_with(
            "seg_02", attribute="scaleY",
            preInfinite="constant", postInfinite="linear",
        )

        # Off-axis scales locked on every segment.
        for seg in ("seg_01", "seg_02", "seg_03"):
            mock_cmds.setAttr.assert_any_call(f"{seg}.scaleX", lock=True)
            mock_cmds.setAttr.assert_any_call(f"{seg}.scaleZ", lock=True)

        # Bundle records the build.
        self.assertEqual(bundle.distance_node, "telescope_distance")
        self.assertEqual(bundle.driven_plugs, ["seg_02.scaleY"])
        self.assertEqual(bundle.initial_distance, 10.0)
        self.assertEqual(bundle.original_scales, {"seg_02.scaleY": 1.0})
        self.assertIs(self.rig.bundle, bundle)

    def test_axis_parameter_remaps_channels(self):
        """aim_axis="x" drives scaleX and locks scaleY/scaleZ."""
        bundle = self.rig.setup_telescope_rig(
            "base_LOC", "end_LOC", ["s1", "s2", "s3"],
            collapsed_distance=2.0, aim_axis="x",
        )
        self.assertEqual(bundle.driven_plugs, ["s2.scaleX"])
        mock_cmds.setAttr.assert_any_call("s2.scaleY", lock=True)
        mock_cmds.setAttr.assert_any_call("s2.scaleZ", lock=True)
        aim_call = mock_cmds.aimConstraint.call_args_list[0]
        self.assertEqual(aim_call.kwargs["aimVector"], (1.0, 0.0, 0.0))
        self.assertEqual(aim_call.kwargs["upVector"], (0.0, 1.0, 0.0))

        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig(
                "base_LOC", "end_LOC", ["s1", "s2"], aim_axis="w"
            )

    def test_setup_telescope_rig_validation(self):
        """Every refusal path raises BEFORE any node is created."""
        # Unresolvable base / end.
        mock_cmds.ls.side_effect = lambda *a, **kw: []
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("bad_base", "end", ["s1", "s2"])

        mock_cmds.ls.side_effect = lambda *a, **kw: (
            [] if any("bad_end" in str(x) for x in _ls_passthrough(*a)) else _ls_passthrough(*a)
        )
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("base", "bad_end", ["s1", "s2"])

        # A nonexistent segment refuses (not silently dropped).
        mock_cmds.ls.side_effect = lambda *a, **kw: (
            [] if any("ghost" in str(x) for x in _ls_passthrough(*a)) else _ls_passthrough(*a)
        )
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("base", "end", ["s1", "ghost", "s2"])

        # Too few / duplicate / role-overlapping segments.
        mock_cmds.ls.side_effect = _ls_passthrough
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("base", "end", ["only_one"])
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("base", "end", ["s1", "s1", "s2"])
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("base", "end", ["s1", "base"])
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("same", "same", ["s1", "s2"])

        # collapsed_distance out of range (initial is stubbed at 10.0).
        for bad in (0.0, -1.0, 10.0, 15.0):
            with self.assertRaises(ValueError):
                self.rig.setup_telescope_rig(
                    "base", "end", ["s1", "s2", "s3"], collapsed_distance=bad
                )

        # A locked/connected driven channel fails pre-flight.
        def getattr_locked(plug, **kwargs):
            if kwargs.get("settable"):
                return plug != "s2.scaleY"
            return 1.0

        mock_cmds.getAttr.side_effect = getattr_locked
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig(
                "base", "end", ["s1", "s2", "s3"], collapsed_distance=2.0
            )

        # Nothing was ever built.
        mock_cmds.shadingNode.assert_not_called()
        mock_cmds.parentConstraint.assert_not_called()
        mock_cmds.pointConstraint.assert_not_called()

    def test_teardown_removes_and_restores(self):
        """teardown deletes recorded nodes, unlocks, and restores scales."""
        # The build records its own anim curves (captured right after keying).
        mock_cmds.listConnections.side_effect = lambda *a, **kw: ["animCurveUU1"]
        bundle = self.rig.setup_telescope_rig(
            "base_LOC", "end_LOC", ["seg_01", "seg_02", "seg_03"],
            collapsed_distance=2.0,
        )
        self.assertEqual(bundle.anim_curves, ["animCurveUU1"])
        mock_cmds.reset_mock()
        mock_cmds.objExists.return_value = True

        self.assertTrue(self.rig.teardown())

        mock_cmds.delete.assert_any_call("animCurveUU1")
        mock_cmds.delete.assert_any_call("telescope_distance")
        for node in bundle.constraints:
            mock_cmds.delete.assert_any_call(node)
        mock_cmds.setAttr.assert_any_call("seg_02.scaleX", lock=False)
        mock_cmds.setAttr.assert_any_call("seg_02.scaleY", 1.0)
        self.assertIsNone(self.rig.bundle)

        # Second call: nothing left to do.
        self.assertFalse(self.rig.teardown())


@unittest.skipUnless(
    _CMDS_IS_MOCKED, "Mock-based test — run via pytest, not run_tests.py"
)
class TestTelescopeRigSlots(unittest.TestCase):
    def setUp(self):
        _restore_conftest_defaults()
        self.addCleanup(_restore_conftest_defaults)

        self.mock_sb = MagicMock()
        self.mock_ui = MagicMock()
        self.mock_sb.loaded_ui.telescope_rig = self.mock_ui

        self.mock_ui.txt003 = MagicMock()
        self.mock_ui.btn_build = MagicMock()
        self.mock_ui.spin_collapsed = MagicMock()
        self.mock_ui.spin_collapsed.value.return_value = 2.0
        self.mock_ui.cmb_axis = MagicMock()
        self.mock_ui.cmb_axis.currentIndex.return_value = 1  # Y

        self.slots = telescope_rig.TelescopeRigSlots(self.mock_sb)
        self.slots.logger = MagicMock()

    def test_build_rig_execution(self):
        """Selection order maps to roles; UI options flow into the engine."""
        mock_cmds.ls.side_effect = lambda *a, **kw: ["Base", "S1", "S2", "End"]

        with patch.object(telescope_rig, "TelescopeRig") as MockRigClass:
            mock_rig = MockRigClass.return_value
            mock_rig.logger = MagicMock()

            self.slots.build_rig()

            mock_rig.setup_telescope_rig.assert_called_once_with(
                base_locator="Base",
                end_locator="End",
                segments=["S1", "S2"],
                collapsed_distance=2.0,
                aim_axis="y",
            )

    def test_build_rig_axis_from_combo(self):
        """The axis combo index selects the aim axis."""
        mock_cmds.ls.side_effect = lambda *a, **kw: ["Base", "S1", "S2", "End"]
        self.mock_ui.cmb_axis.currentIndex.return_value = 2  # Z

        with patch.object(telescope_rig, "TelescopeRig") as MockRigClass:
            mock_rig = MockRigClass.return_value
            mock_rig.logger = MagicMock()

            self.slots.build_rig()

            self.assertEqual(
                mock_rig.setup_telescope_rig.call_args.kwargs["aim_axis"], "z"
            )

    def test_build_rig_insufficient_selection(self):
        """Fewer than 4 selected objects: message box, engine never invoked."""
        mock_cmds.ls.side_effect = lambda *a, **kw: ["a", "b", "c"]

        with patch.object(telescope_rig, "TelescopeRig") as MockRigClass:
            self.slots.build_rig()
            MockRigClass.assert_not_called()

        self.assertTrue(self.slots.logger.error.called)
        self.assertTrue(self.mock_sb.message_box.called)

    def test_build_rig_engine_error_reaches_message_box(self):
        """Engine ValueErrors surface to the user instead of raising."""
        mock_cmds.ls.side_effect = lambda *a, **kw: ["Base", "S1", "S2", "End"]

        with patch.object(telescope_rig, "TelescopeRig") as MockRigClass:
            mock_rig = MockRigClass.return_value
            mock_rig.logger = MagicMock()
            mock_rig.setup_telescope_rig.side_effect = ValueError("boom")

            self.slots.build_rig()

        self.assertTrue(self.mock_sb.message_box.called)


# unittest.makeSuite does not invoke setUpModule; apply the skip post hoc
# to every TestCase in this module so ad-hoc loaders honour it.
if _REAL_MAYA_LOADED:
    _skip = unittest.skipIf(True, "Mock-based suite — skipped when real Maya is loaded.")
    for _name, _obj in list(globals().items()):
        if (
            isinstance(_obj, type)
            and issubclass(_obj, unittest.TestCase)
            and _obj is not unittest.TestCase
        ):
            globals()[_name] = _skip(_obj)


if __name__ == "__main__":
    unittest.main()
