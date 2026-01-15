import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Ensure QApplication exists for QtWidgets
try:
    from qtpy import QtWidgets

    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication([])
except ImportError:
    pass
except Exception:
    pass

# Add package root to path if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mayatk.ui_utils import calculator


class TestCalculator(unittest.TestCase):
    def setUp(self):
        self.mock_sb = MagicMock()
        self.mock_ui = MagicMock()
        self.mock_sb.loaded_ui.calculator = self.mock_ui

        # Mock UI widgets
        self.mock_ui.txt_display = MagicMock()
        self.mock_ui.txt_display.text.return_value = ""

        self.mock_ui.calc_container = MagicMock()
        self.mock_ui.maya_container = MagicMock()

        self.mock_ui.cmb_unit_from = MagicMock()
        self.mock_ui.cmb_unit_to = MagicMock()
        self.mock_ui.btn_convert = MagicMock()

        # Mock layouts
        self.mock_calc_layout = MagicMock()
        self.mock_ui.calc_container.layout.return_value = self.mock_calc_layout

        self.mock_maya_layout = MagicMock()
        self.mock_ui.maya_container.layout.return_value = self.mock_maya_layout

        # Initialize the class
        self.calc = calculator.CalculatorSlots(self.mock_sb)

    def test_initialization(self):
        """Test that the calculator initializes and populates grids."""
        # Check if buttons were added to layouts
        self.assertTrue(self.mock_calc_layout.addWidget.called)
        self.assertTrue(self.mock_maya_layout.addWidget.called)

        # Check unit combos population
        self.assertTrue(self.mock_ui.cmb_unit_from.addItems.called)
        self.assertTrue(self.mock_ui.cmb_unit_to.addItems.called)

    def test_input_logic(self):
        """Test inputting numbers and operations."""
        # Simulate clicking '1'
        self.calc.on_input("1")
        self.mock_ui.txt_display.setText.assert_called_with("1")

        # Simulate clicking '+'
        self.mock_ui.txt_display.text.return_value = "1"
        self.calc.on_input("+")
        self.mock_ui.txt_display.setText.assert_called_with("1+")

    def test_clear(self):
        self.calc.on_clear()
        self.mock_ui.txt_display.clear.assert_called()

    def test_backspace(self):
        self.mock_ui.txt_display.text.return_value = "123"
        self.calc.on_backspace()
        self.mock_ui.txt_display.setText.assert_called_with("12")

    def test_evaluation(self):
        """Test calculation evaluation."""
        # Test addition
        self.mock_ui.txt_display.text.return_value = "2+2"
        self.calc.on_equal()
        self.mock_ui.txt_display.setText.assert_called_with("4")

        # Test float
        self.mock_ui.txt_display.text.return_value = "10/4"
        self.calc.on_equal()
        self.mock_ui.txt_display.setText.assert_called_with("2.5")

        # Test error
        self.mock_ui.txt_display.text.return_value = "1/0"
        self.calc.on_equal()
        self.mock_ui.txt_display.setText.assert_called_with("Error")

    def test_unit_conversion(self):
        """Test unit conversion logic."""
        # cm -> m
        self.mock_ui.txt_display.text.return_value = "100"
        self.mock_ui.cmb_unit_from.currentText.return_value = "cm"
        self.mock_ui.cmb_unit_to.currentText.return_value = "m"
        self.calc.on_convert_units()
        self.mock_ui.txt_display.setText.assert_called_with("1.0")

        # in -> cm
        self.mock_ui.txt_display.text.return_value = "1"
        self.mock_ui.cmb_unit_from.currentText.return_value = "in"
        self.mock_ui.cmb_unit_to.currentText.return_value = "cm"
        self.calc.on_convert_units()
        self.mock_ui.txt_display.setText.assert_called_with("2.54")

        # mm -> cm
        self.mock_ui.txt_display.text.return_value = "10"
        self.mock_ui.cmb_unit_from.currentText.return_value = "mm"
        self.mock_ui.cmb_unit_to.currentText.return_value = "cm"
        self.calc.on_convert_units()
        self.mock_ui.txt_display.setText.assert_called_with("1.0")

    @patch("mayatk.ui_utils.calculator.pm")
    def test_maya_fps(self, mock_pm):
        """Test getting FPS from Maya."""
        # Mock pm.mel.currentTimeUnitToFPS
        mock_pm.mel.currentTimeUnitToFPS.return_value = 24.0

        self.calc.get_fps()
        self.mock_ui.txt_display.setText.assert_called_with("24.0")

    @patch("mayatk.ui_utils.calculator.pm")
    def test_frames_to_sec(self, mock_pm):
        """Test frames to seconds conversion."""
        mock_pm.mel.currentTimeUnitToFPS.return_value = 24.0
        self.mock_ui.txt_display.text.return_value = "48"

        self.calc.frames_to_sec()
        self.mock_ui.txt_display.setText.assert_called_with("2.0")

    @patch("mayatk.ui_utils.calculator.pm")
    def test_sec_to_frames(self, mock_pm):
        """Test seconds to frames conversion."""
        mock_pm.mel.currentTimeUnitToFPS.return_value = 24.0
        self.mock_ui.txt_display.text.return_value = "2"

        self.calc.sec_to_frames()
        self.mock_ui.txt_display.setText.assert_called_with("48.0")


if __name__ == "__main__":
    unittest.main()
