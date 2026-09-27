# !/usr/bin/python
# coding=utf-8
"""The playback clock behind :class:`mayatk.AnimUtils`.

The scene's authored range, the current frame, the playback range and the time
slider's drag selection. Reached through :class:`mayatk.AnimUtils`; nothing
here is called directly.
"""

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

import pythontk as ptk


class _TimelineInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _scene_animation_range():
        """Body of :meth:`AnimUtils.scene_animation_range`."""
        return (
            float(cmds.playbackOptions(query=True, animationStartTime=True)),
            float(cmds.playbackOptions(query=True, animationEndTime=True)),
        )

    @staticmethod
    def _set_current_frame(time, update, relative, snap_mode, invert_snap):
        """Body of :meth:`AnimUtils.set_current_frame`."""
        current_time = cmds.currentTime(query=True)

        # Determine base target time
        if time is None:
            target_time = current_time
        elif relative:
            target_time = current_time + time
        else:
            target_time = time

        # Apply snapping
        if snap_mode and snap_mode.lower() != "none":
            # Handle alias for aggressive
            mode = snap_mode.lower()
            if mode == "aggressive":
                mode = "aggressive_preferred"

            # Invert swaps directional modes (floor ↔ ceil)
            if invert_snap:
                if mode == "floor":
                    mode = "ceil"
                elif mode == "ceil":
                    mode = "floor"

            target_time = ptk.MathUtils.round_value(
                target_time,
                mode=mode,
            )

        cmds.currentTime(target_time, edit=True, update=update)
        return target_time

    @classmethod
    def _fit_playback_range(cls, objects, padding):
        """Body of :meth:`AnimUtils.fit_playback_range`."""
        if objects is None:
            # Query the range from TIME-based curves directly — resolving
            # curves to transforms misses animation routed through anim
            # layers (blend nodes), and driven-key curves (animCurveU*) have
            # driver-value inputs, not times, so they must be excluded.
            curves = cmds.ls(
                type=["animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"]
            )
            if not curves:
                cmds.warning("No animation curves in the scene.")
                return False
            result = cls.get_keyframe_times(curves, from_curves=True, as_range=True)
        else:
            result = cls.get_keyframe_times(objects, as_range=True)
        if result is None:
            cmds.warning("No keyframes found on the given objects.")
            return False

        start, end = result
        start -= padding
        end += padding

        cmds.playbackOptions(
            minTime=start,
            maxTime=end,
            animationStartTime=start,
            animationEndTime=end,
        )
        return True

    @staticmethod
    def _get_timeline_selection():
        """Body of :meth:`AnimUtils.get_timeline_selection`."""
        try:
            slider = mel.eval("$_tmp = $gPlayBackSlider")
            lo, hi = cmds.timeControl(slider, query=True, rangeArray=True)
        except RuntimeError:
            # No time slider: batch / mayapy never defines the global.
            return None
        if hi - lo <= 1.0:
            return None
        # rangeArray's end is exclusive (one past the last selected frame).
        return float(lo), float(hi - 1)
