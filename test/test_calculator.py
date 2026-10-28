"""The Calculator's Maya half: ``CalculatorController`` (the panel is tentacle's).

The math and units are pythontk's; the frame rate and current frame Maya's,
read through MEL / ``cmds`` (mocked here).
"""

import os
import sys
import unittest
from unittest.mock import patch

# Add package root to path if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mayatk.ui_utils.calculator import CalculatorController


class TestCalculatorController(unittest.TestCase):
    def setUp(self):
        self.ctrl = CalculatorController()

    def test_evaluation(self):
        self.assertEqual(self.ctrl.calculate("2+2"), "4")
        self.assertEqual(self.ctrl.calculate("10/4"), "2.5")
        self.assertEqual(self.ctrl.calculate("1/0"), "Error")

    def test_unit_conversion(self):
        self.assertEqual(self.ctrl.convert_unit(100, "cm", "m"), "1.0")
        self.assertEqual(self.ctrl.convert_unit(1, "in", "cm"), "2.54")
        self.assertEqual(self.ctrl.convert_unit(10, "mm", "cm"), "1.0")

    @patch("mayatk.ui_utils.calculator.mel")
    def test_maya_fps(self, mock_mel):
        """The frame rate is MEL's ``currentTimeUnitToFPS``."""
        mock_mel.eval.return_value = 24.0
        self.assertEqual(self.ctrl.get_fps_value(), 24.0)

    @patch("mayatk.ui_utils.calculator.mel")
    def test_frames_to_sec(self, mock_mel):
        mock_mel.eval.return_value = 24.0
        self.assertEqual(self.ctrl.frames_to_sec(48.0), "2.0")

    @patch("mayatk.ui_utils.calculator.mel")
    def test_sec_to_frames(self, mock_mel):
        mock_mel.eval.return_value = 24.0
        self.assertEqual(self.ctrl.sec_to_frames(2.0), "48.0")


if __name__ == "__main__":
    unittest.main()
