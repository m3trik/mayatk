import unittest
from unittest.mock import MagicMock, patch
import sys
import importlib

# Mock pymel.core globally before importing the module under test
# This ensures that when telescope_rig imports pymel.core, it gets our mock
if "pymel.core" not in sys.modules:
    mock_pm = MagicMock()
    sys.modules["pymel.core"] = mock_pm
else:
    mock_pm = sys.modules["pymel.core"]

# try to import pythontk normally, if it fails, mock it carefully
try:
    import pythontk as ptk
except ImportError:
    mock_ptk = MagicMock()

    # Mock LoggingMixin to avoid super() calls failing
    class MockLoggingMixin:
        def __init__(self, *args, **kwargs):
            self.logger = MagicMock()
            # method needed for set_text_handler call in slots
            self.logger.set_text_handler = MagicMock()
            self.logger.setup_logging_redirect = MagicMock()

    mock_ptk.LoggingMixin = MockLoggingMixin
    sys.modules["pythontk"] = mock_ptk

    # We must also mock the submodules that mayatk imports
    mock_core_utils = MagicMock()
    sys.modules["pythontk.core_utils"] = mock_core_utils
    mock_resolver = MagicMock()
    sys.modules["pythontk.core_utils.module_resolver"] = mock_resolver

    # And mock what they export?
    # from pythontk.core_utils.module_resolver import bootstrap_package
    mock_resolver.bootstrap_package = MagicMock()

# Now import the module to test
# We use a try-except block to catch import errors and print them
try:
    from mayatk.rig_utils import telescope_rig
except ImportError as e:
    print(f"ImportError during test setup: {e}")
    raise


class TestTelescopeRig(unittest.TestCase):
    def setUp(self):
        # Reload to ensure mocks are fresh if needed, but for now simple cleanup
        mock_pm.reset_mock()
        self.rig = telescope_rig.TelescopeRig()
        # Ensure logger is a mock
        self.rig.logger = MagicMock()

    def test_setup_telescope_rig_basic_flow(self):
        """Test the happy path for setting up the telescope rig."""
        # Setup mocks for this test
        base_loc_name = "base_LOC"
        end_loc_name = "end_LOC"
        segments = ["seg_01", "seg_02", "seg_03"]

        # Helper to create mock PyNodes
        def create_mock_pynode(name):
            node = MagicMock()
            node.name.return_value = name
            node.getTranslation.return_value = [0.0, 0.0, 0.0]
            node.translate = MagicMock()  # Attribute access
            # Make sure it behaves like a string if needed (Optional but good)
            node.__str__.return_value = name
            return node

        # Mock pm.ls to return list of mock objects
        def side_effect_ls(obj, flatten=True):
            if isinstance(obj, list):
                return [create_mock_pynode(x) for x in obj]
            return [create_mock_pynode(obj)]

        mock_pm.ls.side_effect = side_effect_ls

        # Mock datatypes.Vector behavior
        # In the code: pm.datatypes.Vector(start_locator.getTranslation(...)) + ...
        # We need Vector to support addition and division
        class MockVector(list):  # Inherit from list to be iterable if needed
            def __init__(self, *args):
                super().__init__([0, 0, 0])

            def __add__(self, other):
                return MockVector()

            def __truediv__(self, other):
                return MockVector()

        mock_pm.datatypes.Vector = MockVector

        # Mock distance node creation return
        mock_dist_node = MagicMock()
        mock_dist_node.distance = "distance_attr"
        mock_pm.shadingNode.return_value = mock_dist_node
        mock_pm.getAttr.return_value = 10.0  # Initial distance

        # Run the method
        self.rig.setup_telescope_rig(
            base_loc_name, end_loc_name, segments, collapsed_distance=2.0
        )

        # Assertions
        # 1. Check validations passed (pm.ls called)
        self.assertTrue(mock_pm.ls.called)

        # 2. Check distance node creation
        mock_pm.shadingNode.assert_called_with(
            "distanceBetween", asUtility=True, name="strut_distance"
        )

        # 3. Check constraints were applied
        # We expect some aim constraints and parent constraints
        self.assertTrue(mock_pm.aimConstraint.called)
        self.assertTrue(mock_pm.parentConstraint.called)

        # 4. Check driven keys
        # Should set keys for each segment between start and end
        self.assertTrue(mock_pm.setDrivenKeyframe.called)

    def test_setup_telescope_rig_validation(self):
        """Test that validation logic raises ValueErrors."""
        mock_pm.ls.side_effect = (
            lambda x, flatten=True: []
        )  # Return empty list simulating invalid obj

        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("invalid_base", "valid_end", ["s1", "s2"])

        mock_pm.ls.side_effect = lambda x, flatten=True: (
            [x] if x != "invalid_end" else []
        )
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig("valid_base", "invalid_end", ["s1", "s2"])

        mock_pm.ls.side_effect = lambda x, flatten=True: (
            [x] if isinstance(x, str) else list(x)
        )
        with self.assertRaises(ValueError):
            self.rig.setup_telescope_rig(
                "valid_base", "valid_end", ["only_one_segment"]
            )


class TestTelescopeRigSlots(unittest.TestCase):
    def setUp(self):
        mock_pm.reset_mock()
        self.mock_sb = MagicMock()
        self.mock_ui = MagicMock()
        self.mock_sb.loaded_ui.telescope_rig = self.mock_ui

        # Mock widgets
        self.mock_ui.txt003 = MagicMock()
        # Buttons might be accessed in init
        self.mock_ui.btn_base = MagicMock()
        self.mock_ui.btn_end = MagicMock()
        self.mock_ui.btn_segments = MagicMock()
        self.mock_ui.btn_build = MagicMock()
        self.mock_ui.spin_collapsed = MagicMock()
        self.mock_ui.spin_collapsed.value.return_value = 1.0

        self.slots = telescope_rig.TelescopeRigSlots(self.mock_sb)
        # Verify logger setup
        self.slots.logger = MagicMock()

    def test_build_rig_execution(self):
        # Mock selection: Base, Seg1, Seg2, End
        base = MagicMock()
        base.name.return_value = "Base"
        seg1 = MagicMock()
        seg1.name.return_value = "S1"
        seg2 = MagicMock()
        seg2.name.return_value = "S2"
        end = MagicMock()
        end.name.return_value = "End"

        # Mock selection returning these transforms
        mock_pm.selected.return_value = [base, seg1, seg2, end]

        # Mock TelescopeRig instantiation inside build_rig
        with patch("mayatk.rig_utils.telescope_rig.TelescopeRig") as MockRigClass:
            mock_rig_instance = MockRigClass.return_value
            mock_rig_instance.logger = MagicMock()

            self.slots.build_rig()

            # Verify usage
            mock_rig_instance.setup_telescope_rig.assert_called_with(
                base_locator="Base",
                end_locator="End",
                segments=["S1", "S2"],
                collapsed_distance=1.0,
            )

    def test_build_rig_insufficient_selection(self):
        # Only 3 items selected
        mock_pm.selected.return_value = [MagicMock(), MagicMock(), MagicMock()]

        self.slots.build_rig()

        self.assertTrue(self.slots.logger.error.called)
        self.assertTrue(self.mock_sb.message_box.called)


if __name__ == "__main__":
    unittest.main()
