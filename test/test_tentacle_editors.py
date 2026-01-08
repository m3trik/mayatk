import unittest
from unittest.mock import MagicMock, patch
import pymel.core as pm
from base_test import MayaTkTestCase
from tentacle.slots.maya.editors import Editors


class TestTentacleEditors(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.mock_switchboard = MagicMock()
        # Mock the loaded_ui.editors attribute access
        self.mock_switchboard.loaded_ui = MagicMock()
        self.mock_switchboard.loaded_ui.editors = MagicMock()

        self.editors = Editors(self.mock_switchboard)

    def test_b009_time_range_toggle(self):
        """Test b009 toggles Time and Range sliders intelligently."""

        # Helper to set visibility state
        def set_visibility(time_vis, range_vis):
            # We mock pm.mel.isUIComponentVisible to return specific values
            # However, since we might be running in real Maya, let's try to mock the specific call
            # But pm.mel is dynamic.
            pass

        # Since we are in a Maya environment, we can't easily mock pm.mel attributes directly
        # if we are doing integration tests.
        # But for this specific logic, we want to unit test the logic flow.

        # We can patch 'pymel.core.mel.isUIComponentVisible' and 'pymel.core.mel.ToggleTimeSlider' etc.
        # But given pymel.core.mel is a wrapper, we might need to patch the Mel class or the call.

        # Let's rely on standard unittest.mock on the module level if possible,
        # or just mock the pymel.core used in editors.py

        with patch("tentacle.slots.maya.editors.pm.mel") as mock_mel:
            # Case 1: Both Hidden -> Toggle Both ON
            mock_mel.isUIComponentVisible.side_effect = lambda x: False

            self.editors.b009()

            # Verify toggles called
            self.assertTrue(mock_mel.ToggleTimeSlider.called)
            self.assertTrue(mock_mel.ToggleRangeSlider.called)

            mock_mel.reset_mock()

            # Case 2: Both Visible -> Toggle Both OFF
            mock_mel.isUIComponentVisible.side_effect = lambda x: True

            self.editors.b009()

            self.assertTrue(mock_mel.ToggleTimeSlider.called)
            self.assertTrue(mock_mel.ToggleRangeSlider.called)

            mock_mel.reset_mock()

            # Case 3: Time Visible, Range Hidden -> Toggle Time OFF (Hide All)
            def side_effect(arg):
                if arg == "Time Slider":
                    return True
                if arg == "Range Slider":
                    return False
                return False

            mock_mel.isUIComponentVisible.side_effect = side_effect

            self.editors.b009()

            self.assertTrue(mock_mel.ToggleTimeSlider.called)
            self.assertFalse(
                mock_mel.ToggleRangeSlider.called
            )  # Should NOT toggle range (which would turn it ON)

            mock_mel.reset_mock()

            # Case 4: Time Hidden, Range Visible -> Toggle Range OFF (Hide All)
            def side_effect(arg):
                if arg == "Time Slider":
                    return False
                if arg == "Range Slider":
                    return True
                return False

            mock_mel.isUIComponentVisible.side_effect = side_effect

            self.editors.b009()

            self.assertFalse(mock_mel.ToggleTimeSlider.called)
            self.assertTrue(mock_mel.ToggleRangeSlider.called)
