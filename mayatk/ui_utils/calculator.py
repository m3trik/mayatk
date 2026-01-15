# !/usr/bin/python
# coding=utf-8
import math

try:
    import pymel.core as pm
except ImportError:
    pm = None

from qtpy import QtWidgets, QtCore


class CalculatorController:
    @staticmethod
    def calculate(expression):
        if not expression:
            return ""
        try:
            # Safe eval with math module available
            allowed_names = {"__builtins__": None}
            for name in dir(math):
                if not name.startswith("__"):
                    allowed_names[name] = getattr(math, name)

            allowed_names.update(
                {"abs": abs, "round": round, "min": min, "max": max, "pow": pow}
            )

            result = eval(expression, allowed_names)

            # Format result to avoid excessive decimals if integer
            if isinstance(result, float) and result.is_integer():
                result = int(result)

            return str(result)
        except Exception:
            return "Error"

    @staticmethod
    def get_fps_value():
        if not pm:
            return 24.0
        try:
            # currentTimeUnitToFPS is a MEL command that returns the float FPS
            return pm.mel.currentTimeUnitToFPS()
        except Exception:
            # Fallback map if MEL fails
            fps_map = {
                "game": 15.0,
                "film": 24.0,
                "pal": 25.0,
                "ntsc": 30.0,
                "show": 48.0,
                "palf": 50.0,
                "ntscf": 60.0,
                "24fps": 24.0,
                "30fps": 30.0,
                "60fps": 60.0,
            }
            unit = pm.currentUnit(q=True, time=True)
            return fps_map.get(unit, 24.0)

    @staticmethod
    def get_current_time():
        if not pm:
            return "0"
        return str(pm.currentTime(q=True))

    @classmethod
    def frames_to_sec(cls, frames):
        try:
            fps = cls.get_fps_value()
            seconds = frames / fps
            return str(round(seconds, 4))
        except Exception:
            return "Error"

    @classmethod
    def sec_to_frames(cls, seconds):
        try:
            fps = cls.get_fps_value()
            frames = seconds * fps
            return str(round(frames, 2))
        except Exception:
            return "Error"

    @staticmethod
    def convert_unit(value, from_unit, to_unit):
        try:
            factors = {
                "mm": 0.1,
                "cm": 1.0,
                "m": 100.0,
                "km": 100000.0,
                "in": 2.54,
                "ft": 30.48,
                "yd": 91.44,
                "mi": 160934.4,
            }

            if from_unit not in factors or to_unit not in factors:
                return "Error"

            # Convert to cm first
            cm_value = value * factors[from_unit]
            # Convert to target
            result = cm_value / factors[to_unit]

            return str(round(result, 6))
        except Exception:
            return "Error"


class CalculatorSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.calculator
        self.controller = CalculatorController()
        self._init_calc_grid()
        self._init_maya_grid()
        self._init_units()

        # Connect display return pressed to evaluate
        self.ui.txt_display.returnPressed.connect(self.on_equal)

    def _init_units(self):
        units = ["mm", "cm", "m", "km", "in", "ft", "yd", "mi"]
        self.ui.cmb_unit_from.addItems(units)
        self.ui.cmb_unit_to.addItems(units)

        # Set defaults (e.g. cm -> m)
        self.ui.cmb_unit_from.setCurrentText("cm")
        self.ui.cmb_unit_to.setCurrentText("m")

        self.ui.btn_convert.clicked.connect(self.on_convert_units)

    def on_convert_units(self):
        from_unit = self.ui.cmb_unit_from.currentText()
        to_unit = self.ui.cmb_unit_to.currentText()

        text = self.ui.txt_display.text()
        if not text:
            return

        # Evaluate first in case it's an expression
        val_str = self.controller.calculate(text)
        if val_str == "Error":
            self.ui.txt_display.setText("Error")
            return
        try:
            value = float(val_str)
            result = self.controller.convert_unit(value, from_unit, to_unit)
            self.ui.txt_display.setText(result)
        except ValueError:
            self.ui.txt_display.setText("Error")

    def _init_calc_grid(self):
        # Layout:
        # C  (  )  /
        # 7  8  9  *
        # 4  5  6  -
        # 1  2  3  +
        # 0  .  <  =

        buttons = [
            ("C", 0, 0),
            ("(", 0, 1),
            (")", 0, 2),
            ("/", 0, 3),
            ("7", 1, 0),
            ("8", 1, 1),
            ("9", 1, 2),
            ("*", 1, 3),
            ("4", 2, 0),
            ("5", 2, 1),
            ("6", 2, 2),
            ("-", 2, 3),
            ("1", 3, 0),
            ("2", 3, 1),
            ("3", 3, 2),
            ("+", 3, 3),
            ("0", 4, 0),
            (".", 4, 1),
            ("<", 4, 2),
            ("=", 4, 3),
        ]

        for text, r, c in buttons:
            btn = QtWidgets.QPushButton(text)
            btn.setMinimumHeight(35)
            # Set a slightly larger font for buttons
            font = btn.font()
            font.setPointSize(10)
            btn.setFont(font)

            self.ui.calc_container.layout().addWidget(btn, r, c)

            if text == "=":
                btn.clicked.connect(self.on_equal)
            elif text == "C":
                btn.clicked.connect(self.on_clear)
            elif text == "<":
                btn.clicked.connect(self.on_backspace)
            else:
                # Use lambda with default arg to capture variable
                btn.clicked.connect(lambda checked=False, t=text: self.on_input(t))

    def _init_maya_grid(self):
        # Maya specific functions
        maya_buttons = [
            ("Get FPS", self.get_fps),
            ("Get Time", self.get_current_time),
            ("Frames -> Sec", self.frames_to_sec),
            ("Sec -> Frames", self.sec_to_frames),
        ]

        row = 0
        col = 0
        cols = 2

        for text, func in maya_buttons:
            btn = QtWidgets.QPushButton(text)
            btn.setMinimumHeight(30)
            btn.clicked.connect(func)
            self.ui.maya_container.layout().addWidget(btn, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

    def on_input(self, text):
        current = self.ui.txt_display.text()
        self.ui.txt_display.setText(current + text)

    def on_clear(self):
        self.ui.txt_display.clear()

    def on_backspace(self):
        current = self.ui.txt_display.text()
        if current:
            self.ui.txt_display.setText(current[:-1])

    def on_equal(self):
        expression = self.ui.txt_display.text()
        result = self.controller.calculate(expression)
        self.ui.txt_display.setText(result)

    # Maya Functions
    def get_fps(self):
        fps = self.controller.get_fps_value()
        self.ui.txt_display.setText(str(fps))

    def get_current_time(self):
        time = self.controller.get_current_time()
        self.ui.txt_display.setText(time)

    def frames_to_sec(self):
        text = self.ui.txt_display.text()
        # Resolve expression first
        val_str = self.controller.calculate(text)
        if val_str == "Error":
            self.ui.txt_display.setText("Error")
            return
        try:
            frames = float(val_str)
            result = self.controller.frames_to_sec(frames)
            self.ui.txt_display.setText(result)
        except ValueError:
            self.ui.txt_display.setText("Error")

    def sec_to_frames(self):
        text = self.ui.txt_display.text()
        # Resolve expression first
        val_str = self.controller.calculate(text)
        if val_str == "Error":
            self.ui.txt_display.setText("Error")
            return
        try:
            seconds = float(val_str)
            result = self.controller.sec_to_frames(seconds)
            self.ui.txt_display.setText(result)
        except ValueError:
            self.ui.txt_display.setText("Error")
