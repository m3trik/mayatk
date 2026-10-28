# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

import pythontk as ptk



class CalculatorController:
    @staticmethod
    def calculate(expression):
        """Safely evaluate a math expression (delegates to the shared engine)."""
        return ptk.MathUtils.eval_expression(expression)

    @staticmethod
    def get_fps_value():
        if not cmds:
            return 24.0
        try:
            # currentTimeUnitToFPS is a MEL command that returns the float FPS
            return float(mel.eval("currentTimeUnitToFPS"))
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
            unit = cmds.currentUnit(q=True, time=True)
            return fps_map.get(unit, 24.0)

    @staticmethod
    def get_current_time():
        if not cmds:
            return "0"
        return str(cmds.currentTime(q=True))

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
        """Convert a length value between units (delegates to the shared engine)."""
        return ptk.MathUtils.convert_length_unit(value, from_unit, to_unit)


