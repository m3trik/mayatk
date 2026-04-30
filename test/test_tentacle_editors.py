import unittest
from unittest.mock import MagicMock, patch
from base_test import MayaTkTestCase
from tentacle.slots.maya.editors import Editors
import maya.cmds as cmds


class TestTentacleEditors(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.mock_switchboard = MagicMock()
        # Mock the loaded_ui.editors attribute access
        self.mock_switchboard.loaded_ui = MagicMock()
        self.mock_switchboard.loaded_ui.editors = MagicMock()
        # Avoid QShortcut construction during slot init: a falsy sequence
        # makes _update_repeat_last_shortcut return before reaching Qt.
        self.mock_switchboard.configurable.repeat_last_shortcut.get.return_value = ""

        self.editors = Editors(self.mock_switchboard)

    def test_b009_time_range_toggle(self):
        """Test b009 toggles Time and Range sliders intelligently.

        Production code uses ``mel.eval('isUIComponentVisible "X"')`` to query
        visibility and ``mel.eval('ToggleTimeSlider')`` / ``mel.eval('ToggleRangeSlider')``
        to toggle. We patch ``mel.eval`` and inspect the strings it received.
        """
        with patch("tentacle.slots.maya.editors.mel") as mock_mel:
            def make_eval(time_vis, range_vis, calls):
                def _eval(cmd):
                    calls.append(cmd)
                    if cmd == 'isUIComponentVisible "Time Slider"':
                        return time_vis
                    if cmd == 'isUIComponentVisible "Range Slider"':
                        return range_vis
                    return None
                return _eval

            # Case 1: Both Hidden -> Toggle Both ON
            calls = []
            mock_mel.eval.side_effect = make_eval(False, False, calls)
            self.editors.b009()
            self.assertIn("ToggleTimeSlider", calls)
            self.assertIn("ToggleRangeSlider", calls)

            # Case 2: Both Visible -> Toggle Both OFF
            calls = []
            mock_mel.eval.side_effect = make_eval(True, True, calls)
            self.editors.b009()
            self.assertIn("ToggleTimeSlider", calls)
            self.assertIn("ToggleRangeSlider", calls)

            # Case 3: Time Visible, Range Hidden -> Toggle Time OFF
            calls = []
            mock_mel.eval.side_effect = make_eval(True, False, calls)
            self.editors.b009()
            self.assertIn("ToggleTimeSlider", calls)
            self.assertNotIn("ToggleRangeSlider", calls)

            # Case 4: Time Hidden, Range Visible -> Toggle Range OFF
            calls = []
            mock_mel.eval.side_effect = make_eval(False, True, calls)
            self.editors.b009()
            self.assertNotIn("ToggleTimeSlider", calls)
            self.assertIn("ToggleRangeSlider", calls)
