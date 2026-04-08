# !/usr/bin/python
# coding=utf-8
"""Clip motion, resize, and key-scaling logic for the shot sequencer.

Provides :class:`ClipMotionMixin` (mixed into
:class:`~.shot_sequencer_slots.ShotSequencerController`) plus two
standalone helpers:

* :func:`curves_for_attr` — find anim curves driving a specific attribute.
* :func:`scale_attribute_keys` — scale keys on a single attribute's curves.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import pymel.core as pm
except ImportError:
    pm = None

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.anim_utils.segment_keys import SegmentKeys
from mayatk.anim_utils.shots.shot_sequencer._audio_tracks import AudioTrackManager

if TYPE_CHECKING:
    pass

# Near-zero guard for floating-point comparisons.
FLOAT_ZERO_EPS = 1e-6

__all__ = ["ClipMotionMixin", "curves_for_attr", "scale_attribute_keys"]


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


def curves_for_attr(obj_name: str, attr_name: str) -> list:
    """Return anim curves connected to a specific attribute on an object."""
    try:
        plug = f"{obj_name}.{attr_name}"
        if not cmds.objExists(plug):
            return []
        return cmds.listConnections(plug, type="animCurve", s=True, d=False) or []
    except Exception:
        return []


def scale_attribute_keys(
    obj_name: str,
    attr_name: str,
    old_start: float,
    old_end: float,
    new_start: float,
    new_end: float,
) -> None:
    """Scale only the curves driving *attr_name* on *obj_name*.

    Unlike :meth:`ShotSequencer.scale_object_keys` which scales every
    curve on the whole object, this targets a single attribute so that
    resizing an attribute sub-row clip leaves other attributes untouched.
    """
    curves = curves_for_attr(obj_name, attr_name)
    if not curves:
        return
    if abs(old_end - old_start) < FLOAT_ZERO_EPS:
        return
    for crv in curves:
        cmds.scaleKey(
            str(crv),
            time=(old_start, old_end),
            newStartTime=new_start,
            newEndTime=new_end,
        )


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ClipMotionMixin:
    """Mixin supplying clip move, resize, and batch-move handlers.

    Expects the host class to provide:

    * ``sequencer`` — :class:`ShotSequencer` instance
    * ``_get_sequencer_widget()``
    * ``_audio_mgr`` — :class:`AudioTrackManager`
    * ``_shifted_out_keys`` — dict
    * ``_save_shot_state()`` / ``_sync_to_widget()`` / ``_sync_combobox()``
    * ``_set_footer()``
    * ``logger``
    """

    def on_clip_resized(
        self, clip_id: int, new_start: float, new_duration: float
    ) -> None:
        """Handle clip resize — routes to attribute, shot-boundary, or per-object logic.

        Sub-row attribute clips scale only the targeted attribute's curves.
        Main track clips scale all curves on the object via
        :meth:`ShotSequencer.resize_object`, which also ripple-shifts
        downstream shots.
        Audio clips are not resizable.
        """
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return

        if clip.data.get("is_audio"):
            return

        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        if shot_id is None or obj_name is None:
            return

        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if orig_start is None or orig_end is None:
            return

        self._save_shot_state()
        new_end = new_start + new_duration

        attr_name = clip.data.get("attr_name")
        with pm.UndoChunk():
            if attr_name:
                scale_attribute_keys(
                    obj_name, attr_name, orig_start, orig_end, new_start, new_end
                )
            else:
                self.sequencer.resize_object(
                    shot_id, obj_name, orig_start, orig_end, new_start, new_end
                )
        self._sync_to_widget()
        label = f"{obj_name}.{attr_name}" if attr_name else obj_name
        dur = int(new_end - new_start)
        self._set_footer(
            f"Resized {label} \u00b7 {new_start:.0f}\u2013{new_end:.0f} ({dur}f)"
        )

    def _apply_clip_move(self, clip_id: int, new_start: float) -> bool:
        """Move a single clip's keys without rebuilding the widget.

        Returns True if a widget sync is needed afterward.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return False

        # Audio clip move — persist the new offset in Maya, then expand
        # the shot boundary if the audio clip now extends beyond it.
        if clip.data.get("is_audio"):
            orig_start = clip.data.get("orig_start")
            if orig_start is not None and abs(new_start - orig_start) < FLOAT_ZERO_EPS:
                return False
            source = clip.data.get("audio_source", "dg")
            if source == "event":
                locator = clip.data.get("audio_node")
                old_frame = clip.data.get("event_key_frame")
                if locator and old_frame is not None:
                    AudioTrackManager.move_event_key(locator, old_frame, new_start)
                    clip.data["event_key_frame"] = new_start
            else:
                audio_node = clip.data.get("audio_node")
                if audio_node:
                    AudioTrackManager.set_audio_offset(audio_node, new_start)
            # Compute the true audio end from the full duration.
            orig_end = clip.data.get("orig_end")
            if orig_end is not None and orig_start is not None:
                full_dur = orig_end - orig_start
            else:
                full_dur = clip.rect().width() if hasattr(clip, "rect") else 0
            new_end = new_start + full_dur
            clip.data["orig_start"] = new_start
            clip.data["orig_end"] = new_end
            self._audio_mgr.invalidate()
            self._expand_shot_for_clip(clip, new_start, new_end)
            return True

        # Sub-row attribute clip move
        attr_name = clip.data.get("attr_name")
        if attr_name:
            obj_name = clip.data.get("obj")
            orig_start = clip.data.get("orig_start")
            orig_end = clip.data.get("orig_end")
            if not obj_name or orig_start is None or orig_end is None:
                return False
            if not pm.objExists(obj_name):
                return False
            delta = new_start - orig_start
            if abs(delta) < FLOAT_ZERO_EPS:
                return False
            if clip.data.get("is_stepped"):
                # Stepped sub-row clip: delete-and-recreate.
                # Note: shift-out tracking is omitted here because sub-rows
                # are built from live Maya data (_provide_sub_rows), not from
                # the cached segments that _shifted_out_keys filters.
                if self.sequencer is not None:
                    self.sequencer.move_stepped_keys(
                        obj_name, orig_start, new_start, attr_name=attr_name
                    )
            else:
                curves = curves_for_attr(obj_name, attr_name)
                if curves:
                    SegmentKeys.shift_curves(
                        curves,
                        delta,
                        time_range=(orig_start, orig_end),
                        remove_flat_at_dest=False,
                    )
            new_end = new_start + (orig_end - orig_start)
            self._expand_shot_for_clip(clip, new_start, new_end)
            return True

        # Animation clip move — per-object within a shot
        if self.sequencer is None:
            return False

        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if shot_id is None or obj_name is None:
            return False
        if orig_start is None or orig_end is None:
            return False

        delta = new_start - orig_start
        if abs(delta) < FLOAT_ZERO_EPS:
            return False

        # Stepped (zero-duration) clips use delete-and-recreate
        if clip.data.get("is_stepped"):
            self.logger.debug(
                "[ANIM MOVE] stepped obj=%s from=%s to=%s shot=%s shift=%s",
                obj_name,
                orig_start,
                new_start,
                shot_id,
                getattr(widget, "shift_held_at_press", False),
            )
            self.sequencer.move_stepped_keys(obj_name, orig_start, new_start)
            shift_held = getattr(widget, "shift_held_at_press", False)
            if shift_held:
                shot = self.sequencer.shot_by_id(shot_id)
                if shot and (new_start < shot.start or new_start > shot.end):
                    self._shifted_out_keys.setdefault(obj_name, set()).add(new_start)
            else:
                # Normal move clears any shift-out exclusions for this object
                self._shifted_out_keys.pop(obj_name, None)
            self._expand_shot_for_clip(clip, new_start, new_start)
            return True

        shot = self.sequencer.shot_by_id(shot_id)
        self.logger.debug(
            "[ANIM MOVE] obj=%s orig=(%s,%s) new_start=%s delta=%s "
            "shot=%s range=(%s,%s) shift=%s",
            obj_name,
            orig_start,
            orig_end,
            new_start,
            delta,
            shot_id,
            shot.start if shot else "?",
            shot.end if shot else "?",
            getattr(widget, "shift_held_at_press", False),
        )

        shift_held = getattr(widget, "shift_held_at_press", False)

        if shift_held:
            self.sequencer.move_object_keys(obj_name, orig_start, orig_end, new_start)
        else:
            self.sequencer.move_object_in_shot(
                shot_id, obj_name, orig_start, orig_end, new_start
            )
            # Normal move clears any shift-out exclusions for this object
            self._shifted_out_keys.pop(obj_name, None)

        shot_after = self.sequencer.shot_by_id(shot_id)
        if shot_after:
            self.logger.debug(
                "[ANIM MOVE] post-move shot range=(%s,%s)",
                shot_after.start,
                shot_after.end,
            )
        return True

    def _expand_shot_for_clip(self, clip, new_start: float, new_end: float) -> None:
        """Grow the shot if the clip's new range exceeds shot boundaries.

        Skipped when shift is held — shift means "move freely across shot
        boundaries without changing them".
        """
        widget = self._get_sequencer_widget()
        if getattr(widget, "shift_held_at_press", False):
            self.logger.debug("[EXPAND] skipped — shift held")
            return
        if self.sequencer is None:
            self.logger.debug("[EXPAND] skipped — no sequencer")
            return
        shot_id = clip.data.get("shot_id")
        if shot_id is None:
            self.logger.debug("[EXPAND] skipped — no shot_id in clip data")
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            self.logger.debug(
                "[EXPAND] skipped — shot_by_id(%s) returned None", shot_id
            )
            return
        prior_start = shot.start
        prior_end = shot.end
        expanded_start = min(shot.start, new_start)
        expanded_end = max(shot.end, new_end)
        if expanded_start != prior_start or expanded_end != prior_end:
            self.sequencer.store.update_shot(
                shot_id, start=expanded_start, end=expanded_end
            )
            # Ripple upstream/downstream so adjacent shots stay in order
            shifted_audio: set = set()
            start_delta = expanded_start - prior_start
            if abs(start_delta) > 1e-6:
                self.sequencer._ripple_upstream(shot_id, prior_start, start_delta, shifted_audio)
            end_delta = expanded_end - prior_end
            if abs(end_delta) > 1e-6:
                self.sequencer._ripple_downstream(shot_id, prior_end, end_delta, shifted_audio)
            # Downstream/upstream shots may have moved — flush stale cache
            # so _sync_to_widget re-collects their segments.
            self._segment_cache.clear()
        self.logger.debug(
            "[EXPAND] shot=%s prior=(%s,%s) new_clip=(%s,%s) result=(%s,%s)",
            shot_id,
            prior_start,
            prior_end,
            new_start,
            new_end,
            shot.start,
            shot.end,
        )

    def on_clip_moved(self, clip_id: int, new_start: float) -> None:
        """Handle clip move — routes to audio or shot-level logic."""
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        self.logger.debug(
            "[CLIP MOVED] clip_id=%s new_start=%s clip_data=%s",
            clip_id,
            new_start,
            dict(clip.data) if clip else None,
        )
        shot_id = clip.data.get("shot_id") if clip else None
        obj_name = clip.data.get("obj", "") if clip else ""

        self._save_shot_state()
        with pm.UndoChunk():
            if self._apply_clip_move(clip_id, new_start):
                self.logger.debug(
                    "[CLIP MOVED] sync triggered — cache_keys=%s shifted_out=%s",
                    list(self._segment_cache.keys()),
                    {k: sorted(v) for k, v in self._shifted_out_keys.items()},
                )
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()
                if obj_name:
                    self._set_footer(f"Moved {obj_name} \u2192 {new_start:.0f}")

    def on_clips_batch_moved(self, moves) -> None:
        """Handle a batch of clip moves (group drag), syncing once at the end."""
        shot_id = None
        if moves:
            widget = self._get_sequencer_widget()
            if widget:
                clip = widget.get_clip(moves[0][0])
                if clip:
                    shot_id = clip.data.get("shot_id")
        self._save_shot_state()
        with pm.UndoChunk():
            needs_sync = False
            for clip_id, new_start in moves:
                if self._apply_clip_move(clip_id, new_start):
                    needs_sync = True
            if needs_sync:
                self._sync_to_widget(shot_id=shot_id)
                self._sync_combobox()
                self._set_footer(
                    f"Moved {len(moves)} clip{'s' if len(moves) != 1 else ''}"
                )

    # -- per-key handlers ---------------------------------------------------

    def on_keys_moved(self, clip_id: int, changes: list) -> None:
        """Move individual keyframes on the Maya curves, then refresh.

        Parameters
        ----------
        clip_id : int
            The sequencer clip whose keys were dragged.
        changes : list[tuple[float, float]]
            ``[(old_time, new_time), ...]`` for every key that moved.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return

        obj_name = clip.data.get("obj")
        attr_name = clip.data.get("attr_name")
        if not obj_name or not attr_name:
            return

        curves = curves_for_attr(obj_name, attr_name)
        if not curves:
            return

        eps = 1e-3
        # Two-pass to avoid batch collisions (new_t of one change
        # landing on old_t of another would corrupt the later query).
        # Pass 1 — snapshot values and tangents, then delete old keys.
        snapshots: list = []  # (crv, new_t, val, itt, ott)
        with pm.UndoChunk():
            for old_t, new_t in changes:
                if abs(new_t - old_t) < 1e-6:
                    continue
                for crv in curves:
                    tr = (old_t - eps, old_t + eps)
                    vals = cmds.keyframe(crv, q=True, time=tr, valueChange=True)
                    if not vals:
                        continue
                    in_tan = cmds.keyTangent(crv, q=True, time=tr, inTangentType=True)
                    out_tan = cmds.keyTangent(crv, q=True, time=tr, outTangentType=True)
                    snapshots.append(
                        (
                            crv,
                            new_t,
                            vals[0],
                            in_tan[0] if in_tan else "auto",
                            out_tan[0] if out_tan else "auto",
                        )
                    )
                    cmds.cutKey(crv, time=tr, clear=True)

            # Pass 2 — recreate at new positions.
            for crv, new_t, val, itt, ott in snapshots:
                target = crv if cmds.objExists(crv) else f"{obj_name}.{attr_name}"
                cmds.setKeyframe(target, time=new_t, value=val)
                cmds.keyTangent(
                    target,
                    time=(new_t, new_t),
                    inTangentType=itt,
                    outTangentType=ott,
                )

        if not snapshots:
            return

        self._save_shot_state()
        shot_id = clip.data.get("shot_id")

        # If any key landed outside the originating shot's range,
        # invalidate the segment cache for adjacent shots so the
        # upcoming _sync_to_widget rebuild picks up fresh data.
        shot = self.sequencer.shot_by_id(shot_id) if self.sequencer else None
        if shot is not None:
            new_times = [new_t for _, new_t in changes]
            if any(t < shot.start or t > shot.end for t in new_times):
                for sid in list(self._segment_cache):
                    if sid != shot_id:
                        self._segment_cache.pop(sid, None)

        self._sync_to_widget(shot_id=shot_id)
        n = len(changes)
        self._set_footer(
            f"Moved {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}"
        )

    def on_keys_deleted(self, clip_id: int, times: list) -> None:
        """Delete individual keyframes from the Maya curves, then refresh.

        .. note::

            The primary Delete-key path now bypasses this method and
            performs batched deletion directly in
            ``_delete_selected_clip_keys``.  This handler remains
            connected to the ``keys_deleted`` signal for any external
            callers that emit it directly.

        Parameters
        ----------
        clip_id : int
            The sequencer clip whose keys should be removed.
        times : list[float]
            Absolute times of the keys to delete.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return

        obj_name = clip.data.get("obj")
        attr_name = clip.data.get("attr_name")
        if not obj_name or not attr_name:
            return

        curves = curves_for_attr(obj_name, attr_name)
        if not curves:
            return

        deleted = False
        with pm.UndoChunk():
            for t in times:
                for crv in curves:
                    cmds.cutKey(str(crv), time=(t, t), clear=True)
                    deleted = True

        if not deleted:
            return

        self._save_shot_state()
        shot_id = clip.data.get("shot_id")
        self._sync_to_widget(shot_id=shot_id)
        n = len(times)
        self._set_footer(
            f"Deleted {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}"
        )
