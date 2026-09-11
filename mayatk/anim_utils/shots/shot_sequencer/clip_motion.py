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
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.anim_utils.segment_keys import SegmentKeys
from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

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
) -> bool:
    """Scale only the curves driving *attr_name* on *obj_name*.

    Unlike :meth:`ShotSequencer.scale_object_keys` which scales every
    curve on the whole object, this targets a single attribute so that
    resizing an attribute sub-row clip leaves other attributes untouched.

    Returns ``True`` when a scale was actually issued — a caller that
    snapshotted for undo needs to know a no-op happened so it can discard
    the snapshot instead of leaving a dead restore point.
    """
    curves = curves_for_attr(obj_name, attr_name)
    if not curves:
        return False
    if abs(old_end - old_start) < FLOAT_ZERO_EPS:
        return False
    for crv in curves:
        cmds.scaleKey(
            str(crv),
            time=(old_start, old_end),
            newStartTime=new_start,
            newEndTime=new_end,
        )
    return True


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ClipMotionMixin:
    """Mixin supplying clip move, resize, and batch-move handlers.

    Expects the host class to provide:

    * ``sequencer`` — :class:`ShotSequencer` instance
    * ``_get_sequencer_widget()``
    * ``_shifted_out_keys`` — dict
    * ``_segment_cache`` / ``_sub_row_cache`` — dicts (flushed after
      boundary-moving edits)
    * ``_audio_segments_cache`` — invalidated after audio-clip moves
    * ``_syncing`` — bool re-entrancy guard shared with the store listener
    * ``_discard_shot_state()`` (edits bracket through
      ``sequencer.store.scene_edit()``, which records the restore point)
    * ``_sync_to_widget()`` / ``_sync_combobox()``
    * ``_gap_edit_epilogue()`` — shared post-edit cleanup
      (:class:`~.gap_manager.GapManagerMixin`)
    * ``_set_footer()``
    * ``logger``
    """

    def on_clip_resized(
        self, clip_id: int, new_start: float, new_duration: float
    ) -> None:
        """Handle one clip's edge drag — see :meth:`_commit_clip_resizes`."""
        self._commit_clip_resizes([(clip_id, new_start, new_duration)])

    def on_clips_batch_resized(self, resizes) -> None:
        """Handle an edge drag that scaled a SELECTION of clips as one unit.

        *resizes* is ``[(clip_id, new_start, new_duration), ...]``, already
        ordered so committing them one at a time never lands a clip on a
        span another has not left yet (the widget's
        ``ClipItem._collision_free_order``).  The whole gesture rides ONE
        undo chunk, so the selection comes back in a single Ctrl+Z.
        """
        self._commit_clip_resizes(list(resizes))

    def _resize_one_clip(self, widget, clip_id, new_start, new_duration):
        """Scale one clip's keys into its new span; ``None`` if nothing moved.

        Sub-row attribute clips scale only the targeted attribute's curves.
        Main track clips scale all curves on the object via
        :meth:`ShotSequencer.resize_object`, which also ripple-shifts
        downstream shots.  Audio clips are not resizable.

        Returns the label to report the clip by, or ``None`` when the resize
        wrote nothing (missing clip, audio, curves gone, zero-length span).
        """
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None or clip.data.get("is_audio"):
            return None

        shot_id = clip.data.get("shot_id")
        obj_name = clip.data.get("obj")
        if shot_id is None or obj_name is None:
            return None

        orig_start = clip.data.get("orig_start")
        orig_end = clip.data.get("orig_end")
        if orig_start is None or orig_end is None:
            return None

        new_end = new_start + new_duration
        attr_name = clip.data.get("attr_name")
        if attr_name:
            if not scale_attribute_keys(
                obj_name, attr_name, orig_start, orig_end, new_start, new_end
            ):
                return None
            return f"{obj_name}.{attr_name}"
        self.sequencer.resize_object(
            shot_id, obj_name, orig_start, orig_end, new_start, new_end
        )
        return obj_name

    def _commit_clip_resizes(self, resizes) -> None:
        """Commit one edge-drag gesture, however many clips it scaled.

        Every clip in *resizes* is written inside ONE ``scene_edit`` — the
        gesture is one edit, so it is one undo step — and the epilogue runs
        once at the end rather than once per clip.
        """
        if self.sequencer is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None or not resizes:
            return

        # _syncing up while our own cmds edits run: the controller's
        # MAnimMessage callbacks fire synchronously on them and would arm
        # the 200ms debounce into a SECOND full rebuild after the epilogue's
        # own sync (the issue-7 refresh storm).  Same pattern as the gap
        # handlers; the epilogue runs after the guard is released.
        labels: list = []
        spans: list = []
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("resize"):
                for clip_id, new_start, new_duration in resizes:
                    label = self._resize_one_clip(
                        widget, clip_id, new_start, new_duration
                    )
                    if label is None:
                        continue
                    labels.append(label)
                    spans.append((new_start, new_start + new_duration))
        finally:
            self._syncing = was_syncing
        if not labels:
            # Nothing was scaled (curves gone, zero-length span) — drop the
            # snapshot rather than leave a dead restore point.
            self._discard_shot_state()
            return
        # Full edit epilogue: resize_object can move shot boundaries and
        # ripple downstream — without the cache flush, adjacent/all view
        # keeps painting downstream shots from stale segments, and the
        # combobox range labels go stale.
        self._gap_edit_epilogue()
        lo = min(a for a, _b in spans)
        hi = max(b for _a, b in spans)
        what = labels[0] if len(labels) == 1 else f"{len(labels)} clips"
        self._set_footer(
            f"Resized {what} \u00b7 {lo:.0f}\u2013{hi:.0f} ({int(hi - lo)}f)"
        )

    def _apply_clip_move(self, clip_id: int, new_start: float) -> bool:
        """Move a single clip's keys without rebuilding the widget.

        Returns True if a widget sync is needed afterward.
        """
        widget = self._get_sequencer_widget()
        clip = widget.get_clip(clip_id) if widget else None
        if clip is None:
            return False

        # Audio clip move — shift the track's keys in the canonical
        # store; the compositor (via batch.mark_dirty) re-syncs the
        # derived DG node automatically.
        if clip.data.get("is_audio"):
            orig_start = clip.data.get("orig_start")
            orig_end = clip.data.get("orig_end")
            track_id = clip.data.get("audio_track_id")
            if orig_start is None or orig_end is None or not track_id:
                return False
            delta = new_start - orig_start
            if abs(delta) < FLOAT_ZERO_EPS:
                return False
            with audio_utils.batch() as b:
                audio_utils.shift_keys_in_range(
                    orig_start, orig_end, delta, track_ids=[track_id]
                )
                b.mark_dirty([track_id])
            full_dur = orig_end - orig_start
            new_end = new_start + full_dur
            clip.data["orig_start"] = new_start
            clip.data["orig_end"] = new_end
            # The audio segment cache still holds the pre-move span —
            # the immediate rebuild would snap the clip back to its old
            # position until the keyframe debounce fires.
            self._audio_segments_cache = None
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
            if not cmds.objExists(obj_name):
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
        # shot_by_id returns the live ShotBlock — capture the pre-move
        # bounds by value for the post-move staleness check below.
        pre_bounds = (shot.start, shot.end) if shot else None
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
        # move_object_in_shot may have expanded the boundary and rippled
        # downstream — cached segments for the other visible shots are
        # stale until flushed.
        if (
            pre_bounds is not None
            and shot_after is not None
            and (
                abs(shot_after.start - pre_bounds[0]) > FLOAT_ZERO_EPS
                or abs(shot_after.end - pre_bounds[1]) > FLOAT_ZERO_EPS
            )
        ):
            self._segment_cache.clear()
            self._sub_row_cache.clear()
        return True

    def _expand_shot_for_clip(self, clip, new_start: float, new_end: float) -> None:
        """Grow the shot if the clip's new range exceeds shot boundaries.

        Skipped when shift is held — shift means "move freely across shot
        boundaries without changing them".
        """
        self._expand_shot_range(clip.data.get("shot_id"), new_start, new_end)

    def _expand_shot_range(self, shot_id, new_start: float, new_end: float) -> None:
        """Grow *shot_id* so ``[new_start, new_end]`` fits inside it.

        The single chokepoint for "content moved past the shot edge, so the
        shot follows it" — shared by clip drags and per-key drags.  Without
        it a key dragged onto the next shot's first frame ends up owned by
        that shot while its siblings stay behind, which is what splits one
        dragged selection across two shots at zero gap.

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
        start_delta = expanded_start - prior_start
        end_delta = expanded_end - prior_end
        # One epsilon gate for the whole block: an exact != here with
        # epsilon-gated ripples below could update the shot yet skip
        # both ripples on sub-epsilon drift, desyncing neighbors.
        if abs(start_delta) > 1e-6 or abs(end_delta) > 1e-6:
            # Guard the store event so the mid-drag update_shot doesn't
            # trigger a full widget rebuild — the caller syncs once at
            # the end of the move (same pattern as the gap handlers).
            was_syncing = self._syncing
            self._syncing = True
            try:
                # Ripple FIRST, from the bound the shot is about to have.
                # Writing the grown bound first made the store read the two
                # shots as contiguous for a moment when the key landed on the
                # neighbour's start, and the planner then "split" a shared
                # sample that never was one -- re-keying the neighbour's
                # opening pose at its new start (measured 2026-09-05: every
                # FAILED_CMPT_LOC curve gained a key).  Rippling from the NEW
                # bound keeps the dragged key on the seam as this shot's
                # (the planner's carry rule) and leaves the neighbour whole.
                if abs(start_delta) > 1e-6:
                    self.sequencer.ripple_upstream(shot_id, expanded_start, start_delta)
                if abs(end_delta) > 1e-6:
                    self.sequencer.ripple_downstream(shot_id, expanded_end, end_delta)
                self.sequencer.store.update_shot(
                    shot_id, start=expanded_start, end=expanded_end
                )
            finally:
                self._syncing = was_syncing
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

    def _report_boundary_refusal(self, exc) -> None:
        """Surface a declined boundary edit instead of raising through a drag.

        Overrunning a shot's bound expands it and ripples the neighbour,
        which the planner can REFUSE (:class:`ShotBoundaryConflict`) when
        the ripple would force two shots' disagreeing poses onto one frame.
        A refusal is an answer, not a crash -- and this arrives on the end
        of a mouse drag, where a traceback is the worst possible reply.

        The restore point is deliberately KEPT (unlike ``_add_shot_space``,
        which discards it): the planner declines before writing, but the
        clip's own keys have already moved by then, so the scene did change
        and the user must still be able to undo it.
        """
        self.logger.warning(str(exc))
        self._set_footer(str(exc))

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

        # Guarded commit (see on_clip_resized); the rebuild runs after the
        # guard is released — _rebuild_content resets _syncing in its own
        # finally, so a guard spanning it would be silently dropped.
        was_syncing = self._syncing
        self._syncing = True
        refused = None
        try:
            with self.sequencer.store.scene_edit("clip"):
                applied = self._apply_clip_move(clip_id, new_start)
        except ShotBoundaryConflict as exc:
            applied, refused = True, exc  # the keys moved; the ripple did not
        finally:
            self._syncing = was_syncing
        if refused is not None:
            self._report_boundary_refusal(refused)
        if not applied:
            self._discard_shot_state()
            return
        self.logger.debug(
            "[CLIP MOVED] sync triggered — cache_keys=%s shifted_out=%s",
            list(self._segment_cache.keys()),
            {k: sorted(v) for k, v in self._shifted_out_keys.items()},
        )
        self._sync_to_widget(shot_id=shot_id)
        self._sync_combobox()
        # A refusal already owns the footer; "Moved ..." would overwrite the
        # only notice the user gets, and claim a move that was declined.
        if obj_name and refused is None:
            self._set_footer(f"Moved {obj_name} \u2192 {new_start:.0f}")

    def on_clips_batch_moved(self, moves) -> None:
        """Handle a batch of clip moves (group drag), syncing once at the end.

        *moves* arrives in a collision-free ORDER (see uitk's
        ``ClipItem._collision_free_order``), not selection order, and it has to
        be applied in that order: each move addresses its clip's content by the
        range that clip used to occupy, so a landing that overruns a clip which
        has not moved yet would be grabbed twice and the group would deform.
        """
        shot_id = None
        widget = self._get_sequencer_widget() if moves else None
        if widget is not None:
            # Any member's shot will do -- this only picks which shot to
            # re-render -- but reading moves[0] tied that choice to the
            # batch's ORDER, which is now decided by direction of travel.
            for clip_id, _new_start in moves:
                clip = widget.get_clip(clip_id)
                if clip is not None and clip.data.get("shot_id") is not None:
                    shot_id = clip.data.get("shot_id")
                    break
        was_syncing = self._syncing
        self._syncing = True  # see on_clip_resized — own edits must not
        needs_sync = False
        refused = None
        try:  # arm the keyframe debounce into a second rebuild
            with self.sequencer.store.scene_edit("clips"):
                for clip_id, new_start in moves:
                    if self._apply_clip_move(clip_id, new_start):
                        needs_sync = True
        except ShotBoundaryConflict as exc:
            # Whatever landed before the refusal stands and must be drawn.
            needs_sync, refused = True, exc
        finally:
            self._syncing = was_syncing
        if refused is not None:
            self._report_boundary_refusal(refused)
        if not needs_sync:
            self._discard_shot_state()
            return
        self._sync_to_widget(shot_id=shot_id)
        self._sync_combobox()
        if refused is None:  # see on_clip_moved -- a refusal keeps the footer
            self._set_footer(f"Moved {len(moves)} clip{'s' if len(moves) != 1 else ''}")

    # -- per-key handlers ---------------------------------------------------

    def _gesture_plan(self, widget, groups):
        """Resolve one key gesture into per-curve merged moves.

        Returns ``(curve_moves, shot_extents, labels, moved)``:

        * ``curve_moves`` — ``{curve: {"pairs": [(old, new, shot_id), ...],
          "plug": plug}}``, merged across clips.  Two clips of the SAME
          ``obj.attr`` (split_static segments) share anim curves; committing
          them as separate groups lets one group's landed key be re-grabbed
          — or overwritten — by the next group's time window.
        * ``shot_extents`` — ``{shot_id: (lo, hi)}`` landing extents of the
          pairs that actually qualified (a key exists at ``old_t`` on the
          pristine curve), never from times the commit won't apply.
        * ``moved`` — unique qualified key times across the gesture.

        All queries run against the PRISTINE curves — nothing has been
        committed when this runs.
        """
        eps = 1e-3
        curve_moves: dict = {}
        shot_extents: dict = {}
        labels: list = []
        moved = 0
        for clip_id, changes in groups:
            clip = widget.get_clip(clip_id)
            if clip is None:
                continue
            obj_name = clip.data.get("obj")
            attr_name = clip.data.get("attr_name")
            if not obj_name or not attr_name:
                continue
            curves = curves_for_attr(obj_name, attr_name)
            if not curves:
                continue
            sid = clip.data.get("shot_id")
            clip_applied: dict = {}  # old_t -> new_t (unique per gesture step)
            for crv in curves:
                entry = curve_moves.setdefault(
                    str(crv), {"pairs": [], "plug": f"{obj_name}.{attr_name}"}
                )
                known = entry["pairs"]
                for old_t, new_t in changes:
                    if abs(new_t - old_t) < 1e-6:
                        continue
                    if not cmds.keyframe(crv, q=True, time=(old_t - eps, old_t + eps)):
                        continue
                    # Dedupe within the eps window — times arrive from widget
                    # drag records and curve queries that agree only to
                    # rounding.
                    if any(abs(o - old_t) <= eps for o, _n, _s in known):
                        continue
                    known.append((old_t, new_t, sid))
                    clip_applied[round(old_t, 3)] = new_t
            if clip_applied:
                moved += len(clip_applied)
                labels.append(f"{obj_name}.{attr_name}")
                if sid is not None:
                    lo = min(clip_applied.values())
                    hi = max(clip_applied.values())
                    prev = shot_extents.get(sid)
                    shot_extents[sid] = (
                        (lo, hi)
                        if prev is None
                        else (min(prev[0], lo), max(prev[1], hi))
                    )
        curve_moves = {k: v for k, v in curve_moves.items() if v["pairs"]}
        return curve_moves, shot_extents, labels, moved

    def _expand_and_compensate(self, curve_moves, shot_extents) -> None:
        """Open the touched shots' boundaries, dragging pending landings along.

        Runs BEFORE the key commit: expansion ripples the NEXT shot's keys
        through its envelope, and a freshly-landed key at/past that
        envelope's start would be swept a second time.  Pre-commit, the
        dragged keys still sit at their old times inside the pivot shot —
        outside every rippled envelope (the pivot is excluded from the
        ripple plan) — so the ripple cannot touch them.

        When one expansion ripples ANOTHER touched shot, that shot's
        pending (old, new) times and extents ride the ripple, exactly as
        its existing keys just did.  (Reachable only by a gesture spanning
        shots — rare, since sub-rows are built for the active shot — but a
        wrong answer here silently corrupts key times.)
        """
        if self.sequencer is None:
            return
        sids = sorted(
            (sid for sid in shot_extents if self.sequencer.shot_by_id(sid)),
            key=lambda sid: self.sequencer.shot_by_id(sid).start,
        )
        for i, sid in enumerate(sids):
            others = {
                o: self.sequencer.shot_by_id(o).start
                for o in sids
                if o != sid and self.sequencer.shot_by_id(o) is not None
            }
            lo, hi = shot_extents[sid]
            self._expand_shot_range(sid, lo, hi)
            for o, pre_start in others.items():
                shot_o = self.sequencer.shot_by_id(o)
                if shot_o is None:
                    continue
                shift = shot_o.start - pre_start
                if abs(shift) < 1e-6:
                    continue
                olo, ohi = shot_extents[o]
                shot_extents[o] = (olo + shift, ohi + shift)
                for entry in curve_moves.values():
                    entry["pairs"] = [
                        (old + shift, new + shift, psid)
                        if psid == o
                        else (old, new, psid)
                        for old, new, psid in entry["pairs"]
                    ]

    @staticmethod
    def _commit_curve_moves(curve_moves, ledger=None) -> None:
        """Land every merged move, one primitive call per curve.

        A single shared delta travels as a real move (tangents intact);
        mixed deltas take the snapshot-and-recreate two-pass.  Both
        primitives handle intra-call collisions — which is exactly why the
        gesture is merged per curve first.

        *ledger* rides along so a gap hold or boundary sample the drag picks
        up moves with its key: a key dragged AWAY from a seam has to be
        findable at its new frame for the hold to be released there.
        """
        eps = 1e-3
        for crv, entry in curve_moves.items():
            pairs = [(old, new) for old, new, _sid in entry["pairs"]]
            deltas = {round(new - old, 6) for old, new in pairs}
            if len(deltas) == 1:
                ShotSequencer.move_curve_keys(
                    crv,
                    [old for old, _ in pairs],
                    deltas.pop(),
                    plug=entry["plug"],
                    eps=eps,
                    ledger=ledger,
                )
            else:
                ShotSequencer.recreate_curve_keys(
                    crv, pairs, plug=entry["plug"], eps=eps, ledger=ledger
                )

    def on_keys_moved(self, clip_id: int, changes: list) -> None:
        """Move individual keyframes on the Maya curves, then refresh.

        Parameters
        ----------
        clip_id : int
            The sequencer clip whose keys were dragged.
        changes : list[tuple[float, float]]
            ``[(old_time, new_time), ...]`` for every key that moved.
        """
        self.on_keys_batch_moved([(clip_id, changes)])

    def on_keys_batch_moved(self, groups) -> None:
        """Commit one key drag that spanned any number of clips.

        *groups* is ``[(clip_id, [(old_time, new_time), ...]), ...]`` — the
        whole gesture, resolved into per-curve merged moves first (see
        :meth:`_gesture_plan`) and committed inside ONE undo chunk so a
        drag across several attribute rows reverses in a single step.

        Boundary follow-up runs BEFORE the keys land (see
        :meth:`_expand_and_compensate`) and rides the same chunk, so undo
        reverses keys and bounds together.  An empty plan costs nothing:
        no snapshot, no undo chunk, no rebuild.
        """
        widget = self._get_sequencer_widget()
        if widget is None or not groups:
            return

        # The drag it started in stays the shot on screen — expansion can
        # ripple neighbours, and picking the target from the touched set
        # would retarget the panel to whichever shot came first.
        origin = widget.get_clip(groups[0][0])
        origin_shot_id = origin.data.get("shot_id") if origin else None

        curve_moves, shot_extents, labels, moved = self._gesture_plan(widget, groups)
        if not curve_moves:
            return

        was_syncing = self._syncing
        self._syncing = True  # own cmds edits must not arm the keyframe
        try:  # debounce into a second full rebuild (issue-7 storm)
            # scene_edit snapshots BEFORE any mutation — a snapshot taken
            # after the boundary expansion records the post-edit bounds, so
            # undo would re-apply the very expansion it should reverse.
            with self.sequencer.store.scene_edit("keys"):
                self._expand_and_compensate(curve_moves, shot_extents)
                seq = self.sequencer
                self._commit_curve_moves(
                    curve_moves, ledger=seq.ledger if seq is not None else None
                )
                # The drag may have grown a bound and it may have moved the
                # seam: the system's own samples follow, in the same step.
                if seq is not None:
                    seq.reconcile_system_edits()
        finally:
            self._syncing = was_syncing

        # A shot whose bounds moved rippled its neighbours; adjacent shots'
        # cached segments are stale either way once keys crossed an edge.
        if shot_extents:
            self._segment_cache.clear()
            self._sub_row_cache.clear()

        self._sync_to_widget(shot_id=origin_shot_id)
        self._sync_combobox()
        names = set(labels)
        where = names.pop() if len(names) == 1 else f"{len(names)} curves"
        self._set_footer(f"Moved {moved} key{'s' if moved != 1 else ''} on {where}")

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
        # Guarded like every other key edit here: the MAnimMessage callbacks
        # fire synchronously on each cutKey, and an unguarded pass arms the
        # 200ms debounce into a SECOND full rebuild on top of the sync below.
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("delkeys"):
                for t in times:
                    for crv in curves:
                        cmds.cutKey(str(crv), time=(t, t), clear=True)
                        deleted = True
        finally:
            self._syncing = was_syncing

        if not deleted:
            self._discard_shot_state()
            return

        shot_id = clip.data.get("shot_id")
        self._sync_to_widget(shot_id=shot_id)
        n = len(times)
        self._set_footer(
            f"Deleted {n} key{'s' if n != 1 else ''} on {obj_name}.{attr_name}"
        )
