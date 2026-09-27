# !/usr/bin/python
# coding=utf-8
"""Clip context menus and clip-level key operations.

Provides :class:`ClipMenuMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. The clip and gap
context menus, Move to Shot, clip locking, and deleting, stashing and
retrieving the keys under clips.
"""

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None


class ClipMenuMixin:
    """Clip and gap context menus, and the clip-level key operations they run."""

    # -- item menu extensibility hooks -------------------------------------

    def on_clip_menu(self, menu, clip_id: int) -> None:
        """Add domain-specific actions to a clip's context menu.

        Called before ``menu.exec_`` so consumers can append actions.
        Override or extend in subclasses for custom clip menu items.

        When multiple clips are selected the actions operate on all of
        them.  The *clip_id* parameter identifies the right-clicked clip
        for actions that need a single target (e.g. "Lock Others").
        """
        if cmds is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        clip = widget.get_clip(clip_id)
        if clip is None:
            return

        obj_name = clip.data.get("obj")

        # Gather all selected clip IDs for batch operations.
        selected_ids = widget.selected_clips() or [clip_id]
        if clip_id not in selected_ids:
            selected_ids = [clip_id]
        multi = len(selected_ids) > 1

        menu.addSeparator()
        # No Delete row: the Delete KEY runs _delete_selected_clip_keys over
        # this same selection (selected keyframes, or every key on the
        # selected clips), so a row here would duplicate a key every editor
        # already binds.
        #
        # Key stash: park the clips' keys out of the working animation (inert,
        # never exported, retrievable across sessions) — the non-destructive
        # sibling of deleting them — and bring stored clips back onto this lane.
        act_store = menu.addAction(
            f"Store Keys ({len(selected_ids)})" if multi else "Store Keys"
        )
        act_store.triggered.connect(lambda: self._stash_clip_keys(selected_ids))
        if obj_name:
            self._add_retrieve_menu(menu, obj_name)

        # Per-object lock helpers
        if obj_name and self.sequencer:
            menu.addSeparator()
            act_lock_others = menu.addAction("Lock Others")
            act_unlock_all = menu.addAction("Unlock All")
            act_lock_others.triggered.connect(
                lambda: self._lock_others(widget, obj_name)
            )
            act_unlock_all.triggered.connect(lambda: self._unlock_all(widget))

        # "Move to Shot" submenu — anim/audio clips moved as sequences.
        if self.sequencer:
            seqs = self._clips_to_sequences(
                widget, selected_ids, include_read_only=True
            )
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                move_label = f"Move to Shot ({len(seqs)})" if multi else "Move to Shot"
                move_menu = menu.addMenu(move_label)
                self._populate_move_to_shot(move_menu, seqs)

    def _clips_to_sequences(self, widget, clip_ids, include_read_only=False):
        """Convert widget clip ids to unified sequence dicts.

        A stepped (zero-duration) clip is ONE key and moves as one: it rides
        the key-level path (``"times"`` on the dict), the same one a key
        selection takes from the key context menu.  A single-attribute
        sub-row clip moves as THAT attribute's sequence (``"attr"``): the
        engine then relocates only its curves, where a whole-object move
        would take every attribute's keys in the span along with the one the
        user grabbed.

        Clips of a non-active visible shot are read-only for DRAGS (the drag
        path assumes the active shot) and are skipped unless
        *include_read_only*; Move to Shot passes it, because the engine
        resolves each sequence's source shot itself and "you cannot move a
        clip you can see" was the report (2026-08-29: "move to shot
        sometimes fails").
        """
        seqs = []
        seen: set = set()
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            if clip.data.get("read_only") and not include_read_only:
                continue
            start = clip.data.get("orig_start")
            end = clip.data.get("orig_end")
            if start is None or end is None:
                continue
            stepped = bool(clip.data.get("is_stepped"))
            if end <= start and not stepped:
                continue
            attr = None
            if clip.data.get("is_audio"):
                obj = clip.data.get("audio_track_id")
                kind = "audio"
            else:
                obj = clip.data.get("obj")
                kind = "anim"
                attr = clip.data.get("attr_name") or None
            if not obj:
                continue
            # Dedupe: the same underlying segment can produce multiple
            # clips when it spans multiple visible shots (esp. audio).
            key = (kind, obj, attr, round(start, 6), round(end, 6))
            if key in seen:
                continue
            seen.add(key)
            seq = {"kind": kind, "obj": obj, "start": start, "end": end}
            if attr:
                seq["attr"] = attr
            if stepped:
                seq["times"] = [float(start)]
                seq["end"] = start
            seqs.append(seq)
        return seqs

    #: The two moves an animator makes most: one shot along the sequence.
    _RELATIVE_MOVES = (("Next Shot", "merge_next"), ("Previous Shot", "merge_prev"))

    def _populate_move_to_shot(self, move_menu, seqs: list, noun: str = "clip"):
        """Fill a Move to Shot submenu: the neighbours first, then every shot.

        **Next Shot** and **Previous Shot** head the list so the common move --
        nudging a selection one shot along the sequence -- is always in the
        same place, whatever the shots are called.  They are relative to the
        shot the selection lives in (the active shot when it spans several),
        and each is listed only when that neighbour exists.  Below a
        separator comes every shot by name and range, minus the one the whole
        selection already occupies.

        Parameters:
            move_menu: The submenu to fill.
            seqs: Sequence dicts to move (clips or key selections).
            noun: What the footer counts afterwards -- ``"clip"`` or ``"key"``.
        """
        source_ids = {self.sequencer._source_shot_id_for(sq) for sq in seqs}
        single = next(iter(source_ids)) if len(source_ids) == 1 else None
        anchor = single if single is not None else self.active_shot_id
        near = (
            self._neighbour_shots(anchor)
            if anchor is not None
            else {"merge_prev": None, "merge_next": None}
        )
        entries = [
            (f"{label}  ({near[key].name})", near[key].shot_id)
            for label, key in self._RELATIVE_MOVES
            if near[key] is not None
        ]
        if entries:
            entries.append(None)  # separator
        entries += [
            (f"{sh.name}  [{sh.start:.0f}\u2013{sh.end:.0f}]", sh.shot_id)
            for sh in self.sequencer.sorted_shots()
            if single is None or sh.shot_id != single
        ]
        for entry in entries:
            if entry is None:
                move_menu.addSeparator()
                continue
            label, shot_id = entry
            act = move_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, sid=shot_id: self._move_clips_to_shot(
                    seqs, sid, noun=noun
                )
            )

    def _move_clips_to_shot(self, sequences, dest_shot_id, noun: str = "clip"):
        """Run move_sequences_to_shot, undoable, then refresh.

        Reports the outcome in the footer.  The move is a no-op whenever
        every selected sequence already lives in the destination — that
        used to look like the command silently failing.  *noun* is what the
        footer counts: a clip menu moves clips, a key menu moves keys.
        """
        if self.sequencer is None or not sequences:
            self._set_footer(
                "Move to Shot: nothing movable in the selection.", color="#E0A0A0"
            )
            return
        dest = self.sequencer.shot_by_id(dest_shot_id)
        movable = [
            sq
            for sq in sequences
            if self.sequencer._source_shot_id_for(sq) != dest_shot_id
        ]
        if not movable:
            self._set_footer(
                "Move to Shot: selection is already in "
                f"{dest.name if dest else 'that shot'}.",
                color="#E0A0A0",
            )
            return

        with self.sequencer.store.scene_edit("movetoshot"):
            self.sequencer.move_sequences_to_shot(movable, dest_shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._sync_combobox()
        self._apply_view_playback_range()
        n = (
            sum(len(sq.get("times") or ()) for sq in movable)
            if noun == "key"
            else len(movable)
        )
        self._set_footer(
            f"Moved {n} {noun}{'s' if n != 1 else ''} to "
            f"{dest.name if dest else dest_shot_id}"
        )

    # -- lock helpers -------------------------------------------------------

    def _lock_others(self, widget, keep_obj: str) -> None:
        """Lock every main-row object clip except *keep_obj*."""
        store = self.sequencer.store if self.sequencer else None
        if store is None:
            return
        # Collect all unique main-row object names in the active shot
        obj_names: set = set()
        for cd in widget._clips.values():
            o = cd.data.get("obj")
            if o and not cd.sub_row and not cd.data.get("read_only"):
                obj_names.add(o)
        for o in obj_names:
            if o == keep_obj:
                store.locked_objects.discard(o)
            else:
                store.locked_objects.add(o)
        # Apply to all clips
        for cid, cd in list(widget._clips.items()):
            o = cd.data.get("obj")
            if o and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, o != keep_obj)
        self._sub_row_cache.clear()

    def _unlock_all(self, widget) -> None:
        """Unlock every clip in the current view."""
        store = self.sequencer.store if self.sequencer else None
        if store is not None:
            store.locked_objects.clear()
        for cid, cd in list(widget._clips.items()):
            if cd.locked and not cd.data.get("read_only"):
                widget.set_clip_locked(cid, False)
        self._sub_row_cache.clear()

    def on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None:
        """Add domain-specific actions to a gap overlay's context menu.

        Called before ``menu.exec_`` so consumers can append actions.
        Override or extend in subclasses for custom gap menu items.
        """

    def _delete_clip_keys(self, clip_ids: list) -> None:
        """Delete Maya keyframes for the given clip IDs and refresh."""
        if cmds is None:
            self.logger.debug("_delete_clip_keys: cmds is None, skipping.")
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            self.logger.debug("_delete_clip_keys: no widget, skipping.")
            return

        # Collect all operations first so we can wrap in a single UndoChunk.
        ops: list = []
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                self.logger.debug("_delete_clip_keys: clip %s not found.", cid)
                continue
            if clip.data.get("read_only"):
                self.logger.debug("_delete_clip_keys: clip %s is read-only.", cid)
                continue
            obj = clip.data.get("obj")
            if not obj:
                self.logger.debug("_delete_clip_keys: clip %s has no obj.", cid)
                continue
            full = self._resolve_full_name(obj)
            if not cmds.objExists(full):
                self.logger.debug("_delete_clip_keys: '%s' does not exist.", full)
                continue

            attrs = clip.data.get("attributes", [])
            # Sub-row clips store a single attribute name, not a list.
            if not attrs:
                attr_name = clip.data.get("attr_name")
                if attr_name:
                    attrs = [attr_name]
            start = clip.data.get("orig_start")
            end = clip.data.get("orig_end")
            if start is None or end is None:
                self.logger.debug(
                    "_delete_clip_keys: clip %s missing orig_start/end.", cid
                )
                continue

            if not attrs:
                self.logger.debug("_delete_clip_keys: clip %s has no attributes.", cid)
                continue

            for attr in attrs:
                ops.append((f"{full}.{attr}", start, end))

        if not ops:
            return

        deleted = False
        # Guarded like its sibling ``_delete_selected_clip_keys``: the
        # SYNCHRONOUS ``addAnimCurveEditedCallback`` fires inside each cutKey
        # and banks the curve as "freshly keyed" for ``_auto_add_keyed_objects``,
        # which a curve we just CUT is not.  (It does not stop the refresh
        # debounce -- that callback is idle-deferred; see the sibling.)
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("delkeys"):
                for plug, start, end in ops:
                    try:
                        cmds.cutKey(plug, time=(start, end), clear=True)
                        deleted = True
                    except Exception:
                        self.logger.debug(
                            "_delete_clip_keys: cutKey failed for '%s'.",
                            plug,
                            exc_info=True,
                        )
                if deleted:
                    # A key edit like any other (``_key_scene_edit``): the claims
                    # on the deleted keys go with them and the gap holds re-settle.
                    self.sequencer.reconcile_system_edits()
        finally:
            self._syncing = was_syncing

        if not deleted:
            self._discard_shot_state()  # nothing happened — keep the ledger clean
        else:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            self._sync_to_widget()
            n = len(clip_ids)
            self._set_footer(f"Deleted {n} clip{'s' if n != 1 else ''}")

    def _stash_clip_keys(self, clip_ids: list) -> None:
        """Move the given clips' keys into the key stash (``KeyStash.stash``).

        Same scoping as :meth:`_delete_clip_keys` — the clip's object, its
        attributes (a sub-row clip is one attribute) and its original span —
        but the keys are parked, not destroyed: the shot block stays as it is
        and the clip records the shot it came from.
        """
        if cmds is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return

        jobs: list = []
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None or clip.data.get("read_only"):
                continue
            obj = clip.data.get("obj")
            if not obj:
                continue
            full = self._resolve_full_name(obj)
            if not cmds.objExists(full):
                continue
            attrs = clip.data.get("attributes") or []
            if not attrs and clip.data.get("attr_name"):
                attrs = [clip.data["attr_name"]]
            start, end = clip.data.get("orig_start"), clip.data.get("orig_end")
            if start is None or end is None:
                continue
            shot_id = clip.data.get("shot_id")
            jobs.append((full, attrs, start, end, None if shot_id == -1 else shot_id))
        self._run_stash(jobs)

    def _run_stash(self, jobs: list) -> None:
        """Park ``(obj, attrs, start, end, shot_id)`` *jobs* in the key stash.

        The half of "Store Keys" that does not depend on where the gesture
        came from, so a clip selection and a key selection reach the stash
        through the same call rather than two copies of this loop.

        ONE clip, however many objects, channels and spans the gesture
        covered.  A stash is the thing the animator put away, and a
        three-channel selection stored as three clips left three rows to
        find, and to retrieve one at a time.  ``KeyStash.stash`` takes the
        whole scope list, so the merge happens where the clip is built
        instead of by stitching clips back together afterwards.

        The shot is recorded only when every job came from the same one:
        a clip that spans two shots belongs to neither.
        """
        from mayatk.anim_utils.key_stash._key_stash import KeyStash

        if not jobs or self.sequencer is None:
            return
        shots = {sid for *_scope, sid in jobs if sid is not None}
        try:
            with self.sequencer.store.scene_edit("storekeys"):
                clip_rec = KeyStash.active().stash(
                    targets=[
                        (full, attrs or None, start, end)
                        for full, attrs, start, end, _sid in jobs
                    ],
                    source_shot_id=shots.pop() if len(shots) == 1 else None,
                )
        except Exception:
            # One call for the whole gesture, so a raise means nothing landed
            # and the restore point would "restore" the state we are in.
            self._discard_shot_state()
            raise
        stored = clip_rec.key_count if clip_rec is not None else 0
        if not stored:
            self._discard_shot_state()  # nothing happened — keep the ledger clean
            self._set_footer("No keys to store")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(
            f"Stored {stored} key{'s' if stored != 1 else ''} in the key stash"
        )

    def _add_retrieve_menu(self, menu, obj_name: str) -> None:
        """Append a "Retrieve Stored Keys" submenu listing *obj_name*'s clips."""
        from mayatk.anim_utils.key_stash._key_stash import KeyStash

        clips = KeyStash.active().clips_for_object(self._resolve_full_name(obj_name))
        if not clips:
            return
        sub = menu.addMenu("Retrieve Stored Keys")
        for clip in clips:
            act = sub.addAction(clip.label)
            act.triggered.connect(
                lambda _checked=False, cid=clip.clip_id: self._retrieve_stashed_clip(
                    cid
                )
            )
        # The rows above put a clip straight back on its own frames.  The
        # panel is for everything that needs more than that -- retrieve at a
        # different time, preview before committing, drop a clip -- so it
        # hangs here, under the object that HAS stored keys, rather than
        # adding a second row to the menu root.
        sub.addSeparator()
        sub.addAction("Restore Keys\u2026").triggered.connect(self._open_key_stash)

    def _open_key_stash(self) -> None:
        """Open the Key Stash panel."""
        self.sb.handlers.marking_menu.show("key_stash")

    def _retrieve_stashed_clip(self, clip_id: int) -> None:
        """Put a stored clip back on its original frames (``KeyStash.retrieve``)."""
        if cmds is None or self.sequencer is None:
            return
        from mayatk.anim_utils.key_stash._key_stash import KeyStash

        with self.sequencer.store.scene_edit("retrievekeys"):
            restored = KeyStash.active().retrieve(clip_id)
        if not restored:
            self._discard_shot_state()
            self._set_footer("Nothing retrieved — see the Script Editor")
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._set_footer(f"Retrieved {restored} key{'s' if restored != 1 else ''}")

    def _delete_selected_clip_keys(self) -> None:
        """Delete selected keyframes or, if none, all keys on selected clips.

        Individual keyframes are batched into a single Maya UndoChunk so
        that Ctrl+Z restores all deleted keys in one step.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            self.logger.debug("_delete_selected_clip_keys: no widget.")
            return

        from uitk import KeyframeItem

        try:
            items = widget._timeline._scene.selectedItems()
        except RuntimeError:
            items = []

        # Group selected keyframe items by clip_id.
        by_clip: dict = {}
        for item in items:
            if isinstance(item, KeyframeItem):
                cid = item._parent_clip._data.clip_id
                by_clip.setdefault(cid, []).append(item._time)

        if by_clip:
            # Batch-delete all selected keyframes in a single undo chunk.
            from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
                curves_for_attr,
            )

            deleted = 0
            # ``_syncing`` up while our own cmds run.  NOT to stop the refresh
            # debounce: ``addAnimKeyframeEditedCallback`` is idle-deferred
            # (measured in a live GUI 2026-09-11 -- ``cutKey`` leaves the count
            # at 0 and it becomes 1 a moment later), so the guard is already
            # down again by the time that one fires.  What it does stop is the
            # SYNCHRONOUS companion: ``addAnimCurveEditedCallback`` fires
            # inside each cutKey, and ``_on_anim_curve_edited`` banks the curve
            # names that ``_auto_add_keyed_objects`` later reads as "freshly
            # keyed".  Curves we just CUT are not that, and every other edit
            # path here raises the guard for the same reason.
            was_syncing = self._syncing
            self._syncing = True
            try:
                with self.sequencer.store.scene_edit("delkeys"):
                    for clip_id, times in by_clip.items():
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
                        for t in times:
                            # Count a time only when at least one cutKey
                            # succeeded — otherwise an all-failed pass still
                            # reports "Deleted N keys" and triggers the state
                            # resync for nothing.
                            cut_ok = False
                            for crv in curves:
                                try:
                                    cmds.cutKey(str(crv), time=(t, t), clear=True)
                                    cut_ok = True
                                except Exception:
                                    self.logger.debug(
                                        "_delete_selected_clip_keys: cutKey failed "
                                        "for '%s'.",
                                        crv,
                                        exc_info=True,
                                    )
                            if cut_ok:
                                deleted += 1
                    if deleted:
                        # A key edit like any other (``_key_scene_edit``): the
                        # claims on the deleted keys go with them and the gap
                        # holds re-settle.
                        self.sequencer.reconcile_system_edits()
            finally:
                self._syncing = was_syncing

            if not deleted:
                self._discard_shot_state()
            else:
                shot_id = self.active_shot_id
                self._segment_cache.clear()
                self._sub_row_cache.clear()
                self._sync_to_widget(shot_id=shot_id)
                self._set_footer(f"Deleted {deleted} key{'s' if deleted != 1 else ''}")
            return

        # Fallback: delete entire clips when no individual keys are selected.
        clip_ids = widget.selected_clips()
        self.logger.debug("_delete_selected_clip_keys: selected_clips=%s", clip_ids)
        if clip_ids:
            self._delete_clip_keys(clip_ids)
            return

        # Nothing at all is selected inside the tracks, so Delete is about the
        # SHOT -- the only other thing the panel has selected.  It confirms
        # first, so the key cannot quietly take a shot and its animation.
        selected = widget.selected_shot()
        if selected is not None and selected.get("id") is not None:
            self.delete_shot(selected["id"])
