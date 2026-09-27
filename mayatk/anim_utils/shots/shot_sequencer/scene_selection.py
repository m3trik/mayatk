# !/usr/bin/python
# coding=utf-8
"""Tracks and the Maya selection.

Provides :class:`SceneSelectionMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. Hiding, showing
and deleting tracks; mirroring clip, track, channel and key selections onto the
scene (outliner, channels, Graph Editor); the track and header context menus.
"""

from qtpy import QtWidgets

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = None
    mel = None

from mayatk.core_utils._core_utils import CoreUtils


class SceneSelectionMixin:
    """Tracks, and the DCC selection that follows the sequencer's."""

    def hide_track(self, track_names) -> None:
        """Hide one or more tracks by name, persist, and rebuild the widget."""
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            full_name = self._resolve_full_name(name)
            self.sequencer.set_object_hidden(full_name, True)
        self._sync_to_widget()

    def show_track(self, track_name: str) -> None:
        """Un-hide a track by object name, persist, and rebuild the widget."""
        if self.sequencer is None:
            return
        self.sequencer.set_object_hidden(track_name, False)
        self._sync_to_widget()

    def delete_track(self, track_names) -> None:
        """Permanently remove objects from all shots and rebuild the widget."""
        if self.sequencer is None:
            return
        if isinstance(track_names, str):
            track_names = [track_names]
        for name in track_names:
            full_name = self._resolve_full_name(name)
            self.sequencer.store.remove_object_from_shots(full_name)
        self._sync_to_widget()

    def on_selection_changed(self, clip_ids: list) -> None:
        """Select the corresponding Maya objects when clips are clicked.

        An open Graph Editor follows that selection; a closed one stays
        closed (:meth:`_select_and_show`).  Also mirrors an ATTRIBUTE
        selection into the Channel Box (:meth:`_mirror_channel_box_attrs`):
        clicking a sub-row clip means that channel, exactly as highlighting
        it in the Channel Box does, and every helper that reads that
        highlight narrows with it.  An object row means the whole object, so
        it clears the highlight rather than listing the object's channels --
        a mixed selection is therefore object-scoped.
        """
        if not clip_ids or cmds is None or self._syncing:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return

        resolved = []
        clip_labels = []
        cb_attrs = []
        whole_object = False
        for cid in clip_ids:
            clip = widget.get_clip(cid)
            if clip is None:
                continue
            obj = clip.data.get("obj")
            if obj:
                full = self._resolve_full_name(obj)
                if cmds.objExists(full):
                    resolved.append(full)
                attrs = clip.data.get("attributes", [])
                attr_name = clip.data.get("attr_name")
                if not attrs:
                    if attr_name:
                        attrs = [attr_name]
                if attr_name:
                    cb_attrs.append(attr_name)
                else:
                    whole_object = True
                start = clip.data.get("orig_start")
                end = clip.data.get("orig_end")
                parts = [obj]
                if attrs:
                    parts.append(", ".join(attrs[:3]))
                    if len(attrs) > 3:
                        parts[-1] += f" +{len(attrs) - 3}"
                if start is not None and end is not None:
                    dur = int(end - start)
                    parts.append(f"{start:.0f}\u2013{end:.0f} ({dur}f)")
                clip_labels.append(" \u00b7 ".join(parts))
        self._select_and_show(resolved)
        if cb_attrs or whole_object:  # something addressable was clicked
            self._mirror_channel_box_attrs([] if whole_object else cb_attrs)
        if clip_labels:
            self._set_footer("  |  ".join(clip_labels[:3]))
            if len(clip_labels) > 3:
                self._set_footer(
                    "  |  ".join(clip_labels[:3]) + f"  (+{len(clip_labels) - 3} more)"
                )

    def on_track_selected(self, track_names: list) -> None:
        """Select Maya objects when track labels are clicked in the header.

        A header label names the OBJECT, so the Channel Box highlight is
        cleared: whatever attribute scope a previous sub-row click left
        behind would otherwise keep narrowing edits the user has since
        aimed at the whole track.
        """
        if not track_names or cmds is None:
            return
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if cmds.objExists(full):
                resolved.append(full)
        self._select_and_show(resolved)
        self._mirror_channel_box_attrs([])

    def on_sub_track_selected(self, rows: list) -> None:
        """Select a channel when its sub-row label is clicked in the header.

        ``rows`` is ``[(track_name, attr_name), ...]``.  The twin of clicking
        the sub-row's CLIP: the object goes on the scene selection and the
        attributes go on the Channel Box highlight, so picking channels here
        scopes an edit exactly as picking them in the Channel Box does.
        """
        if not rows or cmds is None:
            return
        resolved, attrs = [], []
        for track_name, attr_name in rows:
            full = self._resolve_full_name(track_name)
            if cmds.objExists(full) and full not in resolved:
                resolved.append(full)
            if attr_name and attr_name not in attrs:
                attrs.append(attr_name)
        self._select_and_show(resolved)
        self._mirror_channel_box_attrs(attrs)
        shown = ", ".join(attrs[:6]) + (f" +{len(attrs) - 6}" if len(attrs) > 6 else "")
        self._set_footer(
            f"{len(attrs)} channel{'s' if len(attrs) != 1 else ''}: {shown}"
        )

    def on_clip_locked(self, clip_id: int, locked: bool) -> None:
        """Persist per-object clip lock and propagate to sibling clips."""
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return
        clip = widget._clips.get(clip_id)
        if clip is None:
            return
        obj_name = clip.data.get("obj")
        if not obj_name:
            return

        # Persist on the store
        store = self.sequencer.store
        if locked:
            store.locked_objects.add(obj_name)
        else:
            store.locked_objects.discard(obj_name)

        # Propagate to every clip (main + sub-row) for the same object.
        # The originating clip is included — contextMenuEvent routes
        # through set_clip_locked, and a redundant call is harmless.
        for cid, cd in widget._clips.items():
            if cd.data.get("obj") == obj_name:
                widget.set_clip_locked(cid, locked)
        self._sub_row_cache.clear()

    def on_track_menu(self, menu, track_names) -> None:
        """Add Maya-specific actions to the track header context menu."""
        if not track_names:
            return

        if cmds is None:
            return

        menu.addSeparator()
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if cmds.objExists(full):
                resolved.append(full)
        if resolved:
            menu.addAction(
                "Reveal in Outliner",
                lambda objs=list(resolved): self._reveal_in_outliner(objs),
            )
        # Offered for every track, resolved or not -- pasting the name of an
        # object the scene no longer holds is exactly how it gets found again.
        shorts = [CoreUtils.leaf_name(self._resolve_full_name(n)) for n in track_names]
        copy_label = (
            f"Copy '{shorts[0]}' to Clipboard"
            if len(shorts) == 1
            else f"Copy {len(shorts)} Names to Clipboard"
        )
        menu.addAction(copy_label, lambda n=list(shorts): self._copy_names(n))
        menu.addAction(
            "Attribute Spreadsheet",
            lambda names=list(track_names): self._open_spreadsheet(names),
        )

    def on_header_menu(self, menu) -> None:
        """Add settings actions to the header background context menu."""

    @staticmethod
    def _copy_names(names) -> None:
        """Put the given short names on the clipboard, one per line."""
        QtWidgets.QApplication.clipboard().setText("\n".join(names))

    def _open_spreadsheet(self, track_names) -> None:
        """Select the objects and open Maya's Attribute Spread Sheet."""
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if cmds.objExists(full):
                resolved.append(full)
        self._select_and_show(resolved)
        try:
            mel.eval("SpreadSheetEditor")
        except Exception:
            pass

    def _select_and_show(self, objects: list) -> None:
        """Select the given Maya objects; the editors that follow it show them.

        Nothing is opened.  An open Graph Editor follows the scene selection
        as the Outliner and the Channel Box do, so the ``GraphEditor`` call
        this used to make only ever changed the user's layout: every clip or
        header click -- and every marquee move that caught a clip -- expanded
        a collapsed Graph Editor, or opened a closed one and moved Maya's
        panel focus into it.

        The selection is NOT recorded on the undo queue.  This runs on every
        clip/track click and again on the rebuild after each edit, and
        ``cmds.select`` is undoable — so each click buried the panel's own
        edits one Ctrl+Z deeper, and a group gesture buried them by several.
        Mirroring a panel selection into Maya is a view concern, not a scene
        edit; the user's undo history belongs to the edits.
        """
        if not objects:
            return
        # Resolve to long DAG paths to avoid ambiguous short-name errors
        long_names = cmds.ls(objects, long=True)
        if not long_names:
            return
        with CoreUtils.undo_disabled():
            cmds.select(long_names, replace=True)

    def _mirror_channel_box_attrs(self, attrs) -> None:
        """Put *attrs* on Maya's Channel Box highlight; empty clears it.

        The panel's attribute selection and the Channel Box's are meant to
        be one thing -- a sub-row IS a channel -- so a click here highlights
        there, and every helper that already reads that highlight
        (``remove_intermediate_keys``, ``copy_keys``, ``AnimUtils._resolve_keys``
        and the rest) narrows to the same channels without being told twice.

        Deferred to idle: the Channel Box refills itself after a selection
        change on an idle callback, not synchronously, so a highlight written
        in the same beat as ``cmds.select`` is wiped by the refill that
        follows it.  A failure here costs the highlight and nothing else --
        the key edits carry their own explicit attribute scope rather than
        reading it back out of the UI.
        """
        if cmds is None:
            return
        try:
            from mayatk.ui_utils.channel_box import ChannelBox
        except ImportError:  # headless / no Qt
            return

        names = list(dict.fromkeys(attrs or ()))

        def _apply():
            try:
                ChannelBox.select_visual(names)
            except Exception:
                self.logger.debug("channel box mirror failed", exc_info=True)

        try:
            cmds.evalDeferred(_apply, lowestPriority=True)
        except Exception:
            _apply()

    def on_key_selection_changed(self, key_groups: list) -> None:
        """Sync the Maya Graph Editor selection to match the sequencer.

        Parameters
        ----------
        key_groups : list[dict]
            ``[{clip_id, times}, ...]`` — one entry per clip with
            selected :class:`KeyframeItem` children.
        """
        if cmds is None or self._syncing:
            # During a rebuild the scene selection empties as items are torn
            # down.  Mirroring that into Maya would clear the user's Graph
            # Editor key selection on every refresh.
            return
        self._mirror_key_selection(key_groups)

    def _mirror_key_selection(self, key_groups: list) -> None:
        """Put *key_groups* on Maya's Graph Editor key selection.

        Resolves the widget's clips to ``(obj, attr, times)`` rows and hands
        them to :meth:`_apply_key_selection`; the key menu asserts the same
        selection from its own targets (:meth:`_select_target_keys`).
        """
        if cmds is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        rows = []
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None:
                continue
            obj_name = clip.data.get("obj")
            attr_name = clip.data.get("attr_name")
            if not obj_name or not attr_name:
                continue
            rows.append((obj_name, attr_name, group["times"]))
        self._apply_key_selection(rows)
        if rows:
            # An EMPTIED key selection says nothing about attribute scope --
            # the clip selection that outlives it already made that call, and
            # clearing here would undo it whenever the two signals cross.
            self._mirror_channel_box_attrs([a for _o, a, _t in rows])

    def _select_target_keys(self, targets: list) -> None:
        """Put the key menu's *targets* on Maya's Graph Editor selection.

        The edits offered there read that selection rather than taking a
        range (``invert_keys``, ``align_selected_keyframes``,
        ``copy_keys(mode="selected")``, ``snap_keys_to_frames(selected_only)``),
        and the reaction that normally keeps it in step is skipped while the
        panel is rebuilding -- so it can be a rebuild out of date by the time
        a menu opens on it.  Asserted from *targets* rather than the raw
        groups: that is already the resolved, writable selection the menu was
        built from.
        """
        self._apply_key_selection([(o, a, t) for o, a, t, _s in targets])

    @staticmethod
    def _apply_key_selection(rows) -> None:
        """Select exactly the ``(obj, attr, times)`` *rows*' keys in Maya.

        Mirroring a panel selection is not a scene edit, and it must not be
        recorded as one: ``selectKey`` IS undoable, so the unguarded version
        pushed one entry per (curve, time) AFTER the edit that caused the
        rebuild.  A group gesture then took a Ctrl+Z per key just to walk
        back to its own step -- and, worse, left the queue top owned by a
        selection, which is exactly what ``_undo_plan``'s marker test reads
        to decide whether the shot-bounds restore point is still ours.
        (Verified: edit + 5 raw selectKey calls = 6 undos to revert the
        edit; guarded = 1.)
        """
        if cmds is None:
            return
        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
            curves_for_attr,
        )

        with CoreUtils.undo_disabled():
            cmds.selectKey(clear=True)
            for obj_name, attr_name, times in rows:
                tt = tuple((t, t) for t in times)
                if not tt:
                    continue
                for crv in curves_for_attr(obj_name, attr_name):
                    # One call per curve carrying every time: the per-time loop
                    # was O(curves x times) commands on every selection change.
                    cmds.selectKey(str(crv), add=True, time=tt)

    def _reveal_in_outliner(self, objects) -> None:
        """Select and reveal object(s) in Maya's Outliner."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.reveal_in_outliner(objects)

    def _resolve_full_name(self, short_name: str) -> str:
        """Map a short display name back to the full DAG path.

        Handles both regular object tracks and audio tracks (prefixed
        with ``♫ ``).
        """
        # Strip audio track prefix
        if short_name.startswith("\u266b "):
            short_name = short_name[2:]
        if self.sequencer is None:
            return short_name
        # Check shot objects
        for shot in self.sequencer.shots:
            for obj in shot.objects:
                if CoreUtils.leaf_name(obj) == short_name:
                    return obj
        # Check audio source nodes
        if cmds is not None:
            try:
                matches = cmds.ls(short_name, long=True)
                if matches:
                    return matches[0]
            except Exception:
                pass
        return short_name

    # ---- signal handlers (clip motion in _clip_motion.py) ----------------

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Handle inline rename — currently a no-op (shot clips removed)."""
        pass
