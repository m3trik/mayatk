# !/usr/bin/python
# coding=utf-8
"""Scene framerate utilities for Maya.

Provides a single canonical :func:`get_scene_fps` helper so that every
module in ``anim_utils`` (shots, playblast exporter, audio tracks, …)
resolves the current scene FPS the same way.
"""

_NAMED_FPS = {
    "game": 15,
    "film": 24,
    "pal": 25,
    "ntsc": 30,
    "show": 48,
    "palf": 50,
    "ntscf": 60,
}


def get_scene_fps() -> float:
    """Return the current Maya scene framerate, or 24.0 as fallback.

    Tries Maya's own ``currentTimeUnitToFPS`` MEL procedure first
    (handles every unit including custom fps values).  Falls back to
    a lookup table when MEL is unavailable (e.g. ``mayapy`` without
    ``maya.mel``).
    """
    try:
        import maya.mel

        return float(maya.mel.eval("currentTimeUnitToFPS"))
    except Exception:
        pass
    # Fallback: manual lookup (covers the common named units)
    try:
        import maya.cmds as cmds

        unit = cmds.currentUnit(query=True, time=True)
    except (ImportError, RuntimeError):
        return 24.0
    if unit in _NAMED_FPS:
        return float(_NAMED_FPS[unit])
    if unit.endswith("fps"):
        try:
            return float(unit[:-3])
        except ValueError:
            pass
    return 24.0
