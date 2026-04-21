# !/usr/bin/python
# coding=utf-8
"""Shot navigation and combobox synchronization.

Provides :class:`ShotNavMixin` — mixed into
:class:`~.shot_sequencer_slots.ShotSequencerController` to handle shot
selection, navigation, and combobox population.
"""
from __future__ import annotations

from typing import Optional

try:
    import pymel.core as pm
except ImportError:
    pm = None

__all__ = ["ShotNavMixin"]


class ShotNavMixin:
    """Mixin supplying shot selection and navigation.

    Expects the host class to provide:

    * ``sequencer`` — :class:`ShotSequencer` instance
    * ``ui`` — loaded UI with ``cmb_shot`` combobox
    * ``active_shot_id`` — property
    * ``_playback_range_mode`` / ``_shot_display_mode``
    * ``_shifted_out_keys`` — dict
    * ``_cmb_mode`` / ``_cmb_mode_widget``
    * ``_prev_action`` / ``_next_action``
    * ``_sync_to_widget()`` / ``_update_shot_nav_state()``
    * ``_visible_shots()``
    * ``_get_sequencer_widget()``
    """

    def select_shot(self, shot_id: int) -> None:
        """Set Maya's playback range to the shot and select its objects."""
        if self.sequencer is None:
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return
        self.sequencer.store.set_active_shot(shot_id)
        self._apply_view_playback_range(shot)

        if not self.sequencer.store.select_on_load:
            return

        import maya.cmds as cmds

        long_names = []
        for o in shot.objects:
            resolved = cmds.ls(o, long=True)
            if resolved:
                long_names.extend(resolved)
        if long_names:
            pm.select(long_names)
        else:
            pm.select(clear=True)

    def _apply_view_playback_range(self, shot=None) -> None:
        """Set Maya's playback range based on the current playback-range mode.

        * ``"off"`` — no change to Maya's playback range.
        * ``"follows_view"`` — range covers all visible shots.
        * ``"locked"`` — range covers only the active shot.
        """
        if self._playback_range_mode == "off":
            return
        if self.sequencer is None:
            return
        if shot is None:
            sid = self.active_shot_id
            shot = self.sequencer.shot_by_id(sid) if sid is not None else None
        if shot is None:
            return

        if self._playback_range_mode == "follows_view":
            visible = self._visible_shots(shot)
            rng_start = min(s.start for s in visible)
            rng_end = max(s.end for s in visible)
        else:
            rng_start, rng_end = shot.start, shot.end

        pm.playbackOptions(min=rng_start, max=rng_end)

    def _sync_combobox(self) -> None:
        """Populate the shot combobox and update prev/next action state."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return

        old_sid = self.active_shot_id

        cmb.blockSignals(True)
        cmb.clear()

        if self._cmb_mode == "markers":
            widget = self._get_sequencer_widget()
            markers = sorted(widget.markers(), key=lambda m: m.time) if widget else []
            if markers:
                for md in markers:
                    label = f"@ {md.time:.0f}"
                    if md.note:
                        label += f"  {md.note}"
                    cmb.addItem(label, md.time)
            else:
                cmb.addItem("No markers", None)
            cmb.blockSignals(False)
            self._update_shot_nav_state()
            return

        if self.sequencer is None:
            cmb.blockSignals(False)
            return
        for shot in self.sequencer.sorted_shots():
            label = f"{shot.name}  [{shot.start:.0f}-{shot.end:.0f}]"
            if shot.description:
                label += f"  {shot.description}"
            cmb.addItem(label, shot.shot_id)
        # Restore previous selection
        if old_sid is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == old_sid:
                    cmb.setCurrentIndex(i)
                    break
        cmb.blockSignals(False)
        self._update_shot_nav_state()

    def _update_shot_nav_state(self) -> None:
        """Enable/disable prev/next option box actions based on combobox index."""
        cmb = getattr(self.ui, "cmb_shot", None)
        idx = cmb.currentIndex() if cmb is not None else 0
        count = cmb.count() if cmb is not None else 0
        if self._prev_action is not None:
            self._prev_action.widget.setEnabled(idx > 0)
        if self._next_action is not None:
            self._next_action.widget.setEnabled(idx < count - 1)

    def _navigate_shot(self, delta: int) -> None:
        """Move to the previous (-1) or next (+1) shot."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        new_idx = cmb.currentIndex() + delta
        if new_idx < 0 or new_idx >= cmb.count():
            return
        cmb.setCurrentIndex(new_idx)
        shot_id = cmb.itemData(new_idx)
        self._shifted_out_keys.clear()
        self.select_shot(shot_id)
        store = self.sequencer.store if self.sequencer else None
        do_frame = store.frame_on_shot_change if store else False
        self._sync_to_widget(frame=do_frame)
        self._update_shot_nav_state()

    def on_shot_block_clicked(self, shot_name: str) -> None:
        """Select a shot by name when its block is clicked in the shot lane."""
        if self.sequencer is None:
            return
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return
        for shot in self.sequencer.sorted_shots():
            if shot.name == shot_name:
                for i in range(cmb.count()):
                    if cmb.itemData(i) == shot.shot_id:
                        cmb.setCurrentIndex(i)
                        break
                self._shifted_out_keys.clear()
                self.select_shot(shot.shot_id)
                store = self.sequencer.store if self.sequencer else None
                do_frame = store.frame_on_shot_change if store else False
                self._sync_to_widget(frame=do_frame)
                self._update_shot_nav_state()
                return
