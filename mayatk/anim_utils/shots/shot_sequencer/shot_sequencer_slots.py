# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Sequencer UI.

Provides ``ShotSequencerSlots`` — bridges the generic
:class:`~uitk.widgets.sequencer._sequencer.SequencerWidget` to the
Maya-specific :class:`~mayatk.anim_utils.shots.shot_sequencer._shot_sequencer.ShotSequencer`.
"""

from collections import defaultdict
from typing import Optional, List

from qtpy import QtWidgets, QtCore

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma
except ImportError:
    cmds = None
    mel = None
    om2 = None
    oma = None

import pythontk as ptk

from uitk.widgets.sequencer._sequencer import (
    AttributeColorDialog,
    _COMMON_ATTRIBUTES,
    _DEFAULT_ATTRIBUTE_COLORS,
)

# Arrow-key labels used in keyboard-shortcut help. Defined as module
# constants so the help builder can pass them into kbd() without
# triggering Python 3.11's "backslash inside f-string expression" error
# (the source file's text auto-escapes non-ASCII as ``\uXXXX``).
_KB_LEFT = "←"  # ←
_KB_RIGHT = "→"  # →
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
    ShotSequencer,
    ShotBlock,
)
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.audio_utils.segments import AudioSegment
from mayatk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin
from mayatk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from mayatk.anim_utils.shots.shot_sequencer.segment_collector import SegmentCollector
from mayatk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin
from mayatk.anim_utils.shots.shot_sequencer.marker_manager import MarkerManagerMixin
from mayatk.anim_utils.shots._shots import StoreEvent
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.attributes._attributes import Attributes


class ShotSequencerController(
    GapManagerMixin,
    ClipMotionMixin,
    ShotNavMixin,
    MarkerManagerMixin,
    ptk.LoggingMixin,
):
    """Business logic controller bridging SequencerWidget ↔ ShotSequencer."""

    #: Frames the context-menu padding prompt opens on.  A beat of room is
    #: what the gesture is usually for; the prompt then remembers whatever
    #: the user actually typed for the rest of the session.
    CONTEXT_SPACE_FRAMES = 15.0

    #: How far past a bound the "Extend to Keys" option reaches, in frames.
    #: ``ANY_REACH`` (-1) means any distance -- a key set anywhere outside
    #: the shot is claimed, as long as it is not inside a neighbouring one.
    EXTEND_REACH_FRAMES = 24.0
    ANY_REACH = -1.0

    #: The shot combobox's cells (``uitk.ComboBox.set_cells``): one row per
    #: shot reads name / start / end / description, and double-clicking it
    #: edits those in place -- the Shots window's fields without the window.
    SHOT_CELLS = (
        {"key": "name", "label": "Name"},
        {"key": "start", "label": "Start", "kind": "int", "format": "{:.0f}"},
        {"key": "end", "label": "End", "kind": "int", "format": "{:.0f}"},
        {"key": "description", "label": "Description"},
    )
    SHOT_CELL_FORMAT = "{name}  [{start:.0f}-{end:.0f}]  {description}"

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._sequencer: Optional[ShotSequencer] = None
        self._undo_callback_ids: List[int] = []
        self._time_change_cb: Optional[int] = None
        self._keyframe_cb: Optional[int] = None
        self._anim_curve_cb: Optional[int] = None
        # Anim curves Maya reported as edited since the last refresh.
        # Lets the debounced refresh add exactly the objects the user
        # just keyed, instead of guessing from the current selection.
        self._edited_curves: set = set()
        self._keyframe_debounce: Optional[QtCore.QTimer] = None
        self._syncing = False
        self._syncing_playhead = False
        self._store_listener_bound = False
        self._shot_display_mode: str = "current"  # "current" | "adjacent" | "all"
        self._segment_cache: dict = {}  # shot_id → segments list
        self._sub_row_cache: dict = {}  # (shot_id, track_name) → sub-row data
        self._color_map_cache: Optional[dict] = None  # persisted attribute color map
        self._audio_segments_cache: Optional[tuple] = None  # (range_key, segments)
        self._last_visible_key: Optional[tuple] = None  # fast-path gating key
        self._reconcile_needed: bool = True  # gated by DAG/store events
        self._shifted_out_keys: dict = {}  # obj_name → {time, …} shift-moved out
        # Last amount the padding prompt was answered with — padding a run of
        # shots by the same beat is the common case, so the field opens on it.
        self._context_space_frames: float = self.CONTEXT_SPACE_FRAMES
        # Global "grow the current shot over keys set just outside it".  Off
        # by default: it moves a bound the user did not touch, so it is opted
        # into, and _extend_reach caps how far out a new key may sit and
        # still count (ANY_REACH = no cap).
        self._extend_to_keys: bool = False
        self._extend_reach: float = self.EXTEND_REACH_FRAMES
        # Keys copied from the key menu, awaiting a paste (AnimUtils.copy_keys
        # output).  Panel-scoped on purpose: it is the sequencer's clipboard,
        # not Maya's, and it must survive a rebuild.
        self._copied_keys = None
        self._prev_action = None  # OptionBox action for prev shot
        self._next_action = None  # OptionBox action for next shot
        self._view_mode_action = None  # OptionBox action for view mode cycle
        self._cmb_mode_widget = None  # mode selector combobox (Shots/Markers)
        self._holds_action = None  # OptionBox action for internal holds toggle
        self._playback_range_mode: str = (
            "follows_view"  # "off" | "follows_view" | "locked"
        )
        self._track_order_scope: str = "visible"  # "visible" | "global"
        self._show_internal_holds: bool = (
            False  # show flat-key spans in attribute sub-rows
        )
        self._cmb_mode: str = "shots"  # "shots" or "markers"

        self._register_maya_undo_callbacks()
        self._register_time_change_callback()
        self._register_keyframe_callback()
        self._bind_store_listener()
        self._bind_invalidation_listener()
        # Tear down on panel close. Without this every callback above
        # outlives the panel — _on_time_changed fires on each DG time change
        # (every frame during playback) against destroyed widgets, and the
        # callbacks accumulate across reopens. connect_cleanup covers the
        # SJM-owned OpenMaya callbacks; remove_callbacks also unbinds the
        # ShotStore listener and stops the keyframe debounce timer.
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().connect_cleanup(self.ui, owner=self)
        self.ui.destroyed.connect(lambda *_: self.remove_callbacks())
        self.logger.debug("ShotSequencerController initialized.")

    # ---- footer helpers --------------------------------------------------

    def _set_footer(self, text: str, *, color: str = "") -> None:
        """Set the window footer text with an optional foreground color."""
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return
        label = footer._status_label
        if color:
            label.setStyleSheet(
                f"background: transparent; border: none; color: {color};"
            )
        else:
            label.setStyleSheet("background: transparent; border: none;")
        footer.setText(text)

    def _update_footer_shot_summary(self) -> None:
        """Update the footer with a summary of the active shot."""
        if self.sequencer is None:
            self._set_footer("No shots defined.")
            return
        shot_id = self.active_shot_id
        if shot_id is None:
            self._set_footer("No shot selected.")
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            self._set_footer("No shot selected.")
            return
        dur = int(shot.end - shot.start)
        n_obj = len(shot.objects)
        n_shots = len(self.sequencer.shots)
        idx = next(
            (
                i
                for i, s in enumerate(self.sequencer.sorted_shots())
                if s.shot_id == shot_id
            ),
            0,
        )
        sep = " \u00b7 "
        parts = [
            f"[{idx + 1}/{n_shots}]",
            f"{dur}f",
            f"{n_obj} object{'s' if n_obj != 1 else ''}",
        ]
        self._set_footer(sep.join(parts))

    # ---- sequencer property (lazy init from ShotStore) -------------------

    @property
    def sequencer(self) -> Optional[ShotSequencer]:
        """Return the ShotSequencer, lazily creating one from the active store."""
        if self._sequencer is None:
            from mayatk.anim_utils.shots._shots import ShotStore

            store = ShotStore.active()
            self._sequencer = ShotSequencer(store=store)
            self.logger.debug("Lazy-initialized ShotSequencer from ShotStore.active().")
        return self._sequencer

    @sequencer.setter
    def sequencer(self, value: Optional[ShotSequencer]) -> None:
        self._sequencer = value

    # ---- ShotStore observer ----------------------------------------------

    def _bind_store_listener(self) -> None:
        """Register as a listener on the active ShotStore."""
        if self._store_listener_bound:
            return
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            store = ShotStore.active()
            store.add_listener(self._on_store_event)
            self._bound_store = store
            self._store_listener_bound = True
        except Exception:
            # A failed bind means the panel silently stops reacting to
            # external shot changes — leave a trace.
            self.logger.warning("store listener bind failed", exc_info=True)

    def _unbind_store_listener(self) -> None:
        """Remove the ShotStore listener."""
        if not self._store_listener_bound:
            return
        try:
            store = getattr(self, "_bound_store", None)
            if store is not None:
                store.remove_listener(self._on_store_event)
                self._bound_store = None
        except Exception:
            self.logger.debug("store listener unbind failed", exc_info=True)
        self._store_listener_bound = False

    def _bind_invalidation_listener(self) -> None:
        """Track active-store swaps (scene new/open).

        Without this the controller keeps its lazily-cached sequencer
        and event listener bound to the store of the *previous* scene —
        edits go to a dead store and external changes stop refreshing
        the panel.
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.add_invalidation_listener(self._on_store_invalidated)

    def _on_store_invalidated(self, event=None) -> None:
        """Rebind to the new active store after a scene swap."""
        self._unbind_store_listener()
        self._sequencer = None
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        # Cross-scene state must not survive the swap.  The boundary
        # ledger needs no clearing here — it lives on the STORE, so the new
        # scene's store starts with a fresh one and the old scene's dies
        # with its store.
        self._shifted_out_keys.clear()
        self._copied_keys = None  # keyed by object NAME; the names are gone
        self._bind_store_listener()
        # Combobox first: _sync_to_widget resolves active_shot_id through
        # the combobox, which still holds the PREVIOUS scene's shot ids
        # until repopulated.
        self._sync_combobox()
        self._sync_to_widget()
        if self._cmb_mode == "markers":
            # Markers mode populates the combobox from the WIDGET's
            # marker items, which only _sync_to_widget rebuilds — re-sync
            # so the list shows the new scene's markers, not the old.
            self._sync_combobox()

    def _on_store_event(self, event: StoreEvent) -> None:
        """React to ShotStore mutations from any source (e.g. manifest build)."""
        if self._syncing or self.sequencer is None:
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        # Refresh combobox and widget when shots change externally
        self._sync_combobox()
        self._sync_to_widget()
        # Emit widget-level signals for any external consumers
        widget = self._get_sequencer_widget()
        if widget is not None and hasattr(widget, "shots_changed"):
            widget.shots_changed.emit()
            widget.app_event.emit(event.name, event)

    # ---- Maya undo/redo event callbacks ----------------------------------

    def _register_maya_undo_callbacks(self) -> None:
        """Listen for Maya Undo/Redo events to refresh the widget.

        Registered through ``ScriptJobManager`` so all callbacks tear down
        through a single ``unsubscribe_all(owner=self)`` path.
        """
        if om2 is None or self._undo_callback_ids:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        for event_name, handler in (
            ("Undo", self._on_maya_undo),
            ("Redo", self._on_maya_redo),
        ):
            token = mgr.add_om_callback(
                om2.MEventMessage.addEventCallback,
                event_name,
                handler,
                owner=self,
            )
            if token is not None:
                self._undo_callback_ids.append(token)

    def remove_callbacks(self) -> None:
        """Remove Maya event callbacks and ShotStore listener (call on teardown).

        All OpenMaya callbacks registered by this controller live under the
        SJM owner ``self``, so a single ``unsubscribe_all`` removes them.
        """
        self._unbind_store_listener()
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.remove_invalidation_listener(self._on_store_invalidated)
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().unsubscribe_all(self)
        self._undo_callback_ids.clear()
        self._time_change_cb = None
        self._keyframe_cb = None
        self._anim_curve_cb = None
        self._edited_curves.clear()
        if self._keyframe_debounce is not None:
            try:
                self._keyframe_debounce.stop()
            except RuntimeError:
                pass  # timer's C++ object died with the closing panel
            self._keyframe_debounce = None

    def _on_maya_undo(self, *_args) -> None:
        """Restore the last shot-state snapshot when Maya's undo fires.

        Only when that undo was OURS: Maya fires this for every undo in the
        session, and consuming a restore point for someone else's edit would
        silently revert a boundary change the user never undid.  Maya has
        already popped the entry by the time this runs, so the pairing is
        read from the state the edit recorded (see :meth:`_undo_plan`).
        """
        if self._syncing:
            return
        if not self._native_event_is_ours():
            return  # the user undid something else — leave our ledger alone
        # Guard the restore: its batch_update exit fires BatchComplete,
        # and an unguarded _on_store_event would do a full rebuild on
        # top of the explicit sync below (two rebuilds per Ctrl+Z).
        self._syncing = True
        try:
            self._restore_shot_state()
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        # The dropdown lists the shots in order and paints their bounds, so
        # it goes as stale as the timeline does -- an undone reorder left it
        # showing the order that had just been undone.
        self._sync_combobox()
        self._sync_to_widget()

    def _on_maya_redo(self, *_args) -> None:
        """Re-apply the redo-side boundary snapshot when Maya's redo fires.

        The undo side must NOT be popped here — that would consume a
        restore point for an operation that was just RE-applied.  The
        ledger's redo direction re-applies the bounds the matching undo
        stepped back from, so keys and bounds stay paired through
        undo→redo cycles.
        """
        if self._syncing:
            return
        if not self._native_event_is_ours(redo=True):
            return  # the user redid something else — leave our ledger alone
        self._syncing = True
        try:
            self._redo_shot_state()
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        # The dropdown lists the shots in order and paints their bounds, so
        # it goes as stale as the timeline does -- an undone reorder left it
        # showing the order that had just been undone.
        self._sync_combobox()
        self._sync_to_widget()

    # ---- Maya keyframe-edited callback ------------------------------------

    def _register_keyframe_callback(self) -> None:
        """Listen for keyframe edits so new keys appear in the sequencer.

        Uses ``MAnimMessage.addAnimKeyframeEditedCallback`` which fires
        once per anim-curve change.  A debounce timer coalesces rapid
        bursts (e.g. keying 10 attributes at once) into a single refresh.
        """
        if oma is None or self._keyframe_cb is not None:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        self._keyframe_cb = mgr.add_om_callback(
            oma.MAnimMessage.addAnimKeyframeEditedCallback,
            self._on_keyframe_edited,
            owner=self,
        )
        # Companion callback: this one names the curves that changed, which
        # is what lets the refresh add the freshly-keyed objects to the shot.
        self._anim_curve_cb = mgr.add_om_callback(
            oma.MAnimMessage.addAnimCurveEditedCallback,
            self._on_anim_curve_edited,
            owner=self,
        )

    #: Above this many banked curves the burst is a bake / import, not a
    #: keying gesture: the per-curve probe in ``_auto_add_keyed_objects``
    #: would cost three cmds round-trips each, and the plain refresh
    #: re-collects the shot wholesale anyway.
    _EDITED_CURVE_CAP = 400

    def _on_anim_curve_edited(self, curves, _client_data=None) -> None:
        """Record which anim curves Maya just changed.

        *curves* is an ``MObjectArray`` of the edited animCurve nodes.  The
        names are banked for :meth:`_on_keyframe_debounce_fire`; the actual
        refresh stays on the keyframe callback's debounce so a burst of
        edits still costs one rebuild.
        """
        if self._syncing or om2 is None:
            return
        if len(self._edited_curves) > self._EDITED_CURVE_CAP:
            return  # already over the cap — the refresh will scan instead
        try:
            for obj in curves:
                self._edited_curves.add(om2.MFnDependencyNode(obj).name())
        except Exception:
            self.logger.debug("anim-curve callback: unreadable payload", exc_info=True)

    def _on_keyframe_edited(self, *_args) -> None:
        """Schedule a debounced refresh when keyframes change.

        Skipped during playback (keys aren't meaningfully added during
        playback) and when the controller is already syncing.
        """
        if self._syncing:
            return
        # Skip during playback — avoid stalling the viewport
        try:
            if cmds is not None and cmds.play(q=True, state=True):
                return
        except Exception:
            pass
        if self._keyframe_debounce is None:
            self._keyframe_debounce = QtCore.QTimer()
            self._keyframe_debounce.setSingleShot(True)
            self._keyframe_debounce.setInterval(200)
            self._keyframe_debounce.timeout.connect(self._on_keyframe_debounce_fire)
        self._keyframe_debounce.start()

    def _on_keyframe_debounce_fire(self) -> None:
        """Perform the actual refresh after the debounce window.

        Only evicts the active shot from the segment cache so that
        non-active shots (adjacent/all view modes) keep their cached
        segments.  The active shot is always re-queried by
        ``collect_segments`` anyway.

        If the keyframe was set on an object not yet in the active shot,
        the object is auto-added to the shot's object list.
        """
        if self._syncing:
            return
        # Only invalidate the active shot — collect_segments always
        # re-queries it, and non-active shots don't need re-collection.
        active_id = self.active_shot_id
        # Audio keys can be edited (e.g. dragging an audio clip) — the
        # cached segments must drop so the next rebuild re-discovers.
        # A keyframe edit can also reveal newly-keyed objects, requiring
        # DAG-path reconciliation on the next rebuild.
        self._audio_segments_cache = None
        self._reconcile_needed = True
        if active_id is not None:
            self._segment_cache.pop(active_id, None)
            self._sub_row_cache = {
                k: v for k, v in self._sub_row_cache.items() if k[0] != active_id
            }
            added = self._auto_add_keyed_objects(active_id)
            # Grow the shot over anything that landed outside it, if the
            # option is on.  After the membership pass: a freshly keyed object
            # has to be IN the shot before its keys count as the shot's.
            if self._auto_extend_to_new_keys(active_id):
                return  # it rebuilt already
        else:
            self._segment_cache.clear()
            self._sub_row_cache.clear()
            # Nothing to attribute the edits to — drop them rather than let
            # the set grow for the rest of the session.
            self._edited_curves.clear()
            added = False
        if not added:
            self._sync_to_widget()

    def _auto_add_keyed_objects(self, shot_id: int) -> bool:
        """Add newly-keyed transforms to the active shot's object list.

        Candidates come from the curves Maya reported as edited since the
        last refresh (:meth:`_on_anim_curve_edited`), falling back to the
        current selection when the callback gave nothing — keying through a
        path that reports no curve edit still gets picked up.

        A candidate qualifies on "has a key in the shot's range on a
        content attribute" (``Detection.CONTENT_ATTRS``: standard
        transform/visibility, or a render-effect channel such as a highlight
        pulse).  It deliberately does NOT require the
        values to vary: an object keyed on a hold inside the shot is still
        the shot's content, and leaving it out of ``shot.objects`` both hid
        it from the panel and stranded its keys when the shot moved.

        Returns ``True`` if objects were added (triggering a store event and
        widget sync), ``False`` otherwise.
        """
        if self.sequencer is None:
            return False
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return False

        from mayatk.anim_utils.shots._shots import Detection

        existing = set(shot.objects)

        # Resolve edited curves → owning transforms.  Curves deleted since
        # the edit are skipped rather than raising.  The content-attr test
        # runs on the curve's TERMINAL destinations: keys routed through a
        # pairBlend (constrained channels), an animBlendNode (animation
        # layers) or a unitConversion arrive on 'inTranslateX1'/'inputB'
        # style attrs, and testing those directly classified every such
        # curve as non-standard.
        candidates: set = set()
        node_cache: dict = {}
        banked = self._edited_curves
        if len(banked) > self._EDITED_CURVE_CAP:
            # A bake or import, not a keying gesture.  Probing each curve
            # would cost more than the rebuild it feeds; fall through to the
            # selection scan below.
            banked = set()
        for crv in banked:
            if not cmds.objExists(crv):
                continue
            hit = Detection.first_standard_destination(crv)
            if hit is None:
                continue
            if not cmds.keyframe(crv, q=True, time=(shot.start, shot.end)):
                continue
            xform = Detection.resolve_to_transform(hit[1], cache=node_cache)
            if xform:
                candidates.add(xform)
        self._edited_curves.clear()

        if not candidates:
            # Selection fallback (and the designated path past the bulk-edit
            # cap).  cmds.keyframe(name=True) resolves the driving curves
            # THROUGH animation layers — listConnections(type='animCurve')
            # was blind to them.
            for sel in cmds.ls(sl=True, long=True, type="transform") or []:
                for crv in cmds.keyframe(sel, q=True, name=True) or []:
                    if Detection.first_standard_destination(crv) is None:
                        continue
                    if cmds.keyframe(crv, q=True, time=(shot.start, shot.end)):
                        candidates.add(sel)
                        break

        # A stored short name and its long path are the same node; compare
        # on the resolved long path so a rename-safe entry isn't duplicated.
        existing_long = (
            set(cmds.ls(list(existing), long=True) or []) if existing else set()
        )
        new_objects = [
            c for c in candidates if c not in existing and c not in existing_long
        ]
        if not new_objects:
            return False
        merged = sorted(existing | set(new_objects))
        self.sequencer.store.update_shot(shot_id, objects=merged)
        return True

    # ---- Maya time-change callback ----------------------------------------

    def _register_time_change_callback(self) -> None:
        """Register an om2 DG time-change callback for reliable playhead sync.

        Unlike scriptJob(event='timeChanged'), MDGMessage.addTimeChangeCallback
        fires on every DG time change including during playback in all
        evaluation modes (DG, Serial, Parallel).
        """
        if om2 is None or self._time_change_cb is not None:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        self._time_change_cb = ScriptJobManager.instance().add_om_callback(
            om2.MDGMessage.addTimeChangeCallback,
            self._on_time_changed,
            owner=self,
        )

    def _on_time_changed(self, time_msg, _client_data=None) -> None:
        """Update the sequencer playhead when Maya's time changes.

        Parameters
        ----------
        time_msg : om2.MTime
            The new DG time supplied by the callback.
        """
        if self._syncing_playhead:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        widget.set_playhead(time_msg.value)

    # -- zone context menus ------------------------------------------------

    def on_zone_context_menu(self, zone: str, time: float, global_pos) -> None:
        """Build a context menu specific to the clicked zone.

        ``"shot_lane"`` is every click on the shot lane itself, plus every
        click over the TRACKS at a time some shot covers -- the widget
        resolves both.  Everywhere else (the ruler, and tracks clear of every
        shot) is the timeline, which has its own menu: the two were merged
        before, which buried a display toggle inside a menu about one shot
        and left the ruler with no menu of its own at all.
        """
        if zone == "shot_lane":
            self._show_shot_lane_context_menu(time, global_pos)
            return
        menu = self._build_timeline_context_menu(time)
        if menu is not None:
            menu.exec_(global_pos)

    def _show_shot_lane_context_menu(self, time: float, global_pos) -> None:
        """Context menu for the shots track: selection, editing, creation."""
        menu = self._build_shot_lane_context_menu(time)
        if menu is not None:
            menu.exec_(global_pos)

    def _build_timeline_context_menu(self, time: float):
        """The timeline's own menu at *time*, built but not shown.

        The widget's marker and display entries
        (:meth:`~uitk.widgets.sequencer._timeline.TimelineView.default_context_entries`),
        opened by a right-click on the ruler or on the tracks clear of every
        shot.  Returned unshown so a test can read its rows; ``None`` without
        a widget.
        """
        from uitk.widgets.context_menu import ContextMenu

        widget = self._get_sequencer_widget()
        if widget is None:
            return None
        menu = ContextMenu(parent=widget)
        menu.add_entries(widget._timeline.default_context_entries(time))
        return menu

    def _build_shot_lane_context_menu(self, time: float):
        """The shot lane's menu at *time*, built but not shown.

        A compact root -- one row per verb -- whose rows fan out on hover
        into their finer forms (:class:`uitk.ContextMenu`): New Shot into
        Insert Before / After, Split Here into Split at Current Time, Trim
        Empty Space into Leading / Trailing.  A row that is both an action
        and a category runs its own action when clicked (Trim Empty Space
        trims both ends).

        Everything here acts on THIS shot.  The timeline's own display
        actions have their own menu on the ruler
        (:meth:`_build_timeline_context_menu`), and key editing belongs to a
        key SELECTION rather than to a whole shot (:meth:`on_key_menu`) --
        both used to hang off this menu, which made a compact root the one
        thing it was not.  Returned unshown so a test can read its rows;
        ``None`` without a widget or a sequencer.
        """
        from uitk.widgets.context_menu import ContextMenu

        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return None
        shot = self._find_shot_at_time(time)
        menu = ContextMenu(parent=widget)
        sid = shot.shot_id if shot is not None else None
        neighbours = self._neighbour_shots(sid) if shot is not None else {}

        if shot is not None:
            menu.add(
                f'Edit "{shot.name}"\u2026',
                callback=lambda: self._edit_shot_dialog(shot),
            )
            menu.add_separator()

        new = menu.add("New Shot", callback=self._create_shot_one_click)
        if shot is not None:
            menu.add(
                "Insert Shot Before",
                parent=new,
                callback=lambda: self._insert_shot(sid, before=True),
            )
            menu.add(
                "Insert Shot After",
                parent=new,
                callback=lambda: self._insert_shot(sid, before=False),
            )
            # A split needs room on both sides; on a bound it divides nothing.
            # Two places to cut: where the menu was opened (the row itself)
            # and where the playhead stands -- the playhead is where the
            # animator decided the cut belongs, and it is rarely the pixel
            # they right-clicked.
            now = self._current_time()
            split = menu.add(
                f"Split Here ({time:.0f})",
                callback=lambda: self.split_shot_at(sid, time),
                setEnabled=bool(shot.start + 1e-6 < time < shot.end - 1e-6),
            )
            menu.add(
                "Split at Current Time" + ("" if now is None else f" ({now:.0f})"),
                parent=split,
                callback=lambda t=now: self.split_shot_at(sid, t),
                setEnabled=bool(
                    now is not None and shot.start + 1e-6 < now < shot.end - 1e-6
                ),
            )
            merge = menu.add("Merge")
            for key, label in (
                ("merge_prev", "Merge with Previous"),
                ("merge_next", "Merge with Next"),
            ):
                other = neighbours[key]  # resolved once, when the menu is built
                menu.add(
                    label,
                    parent=merge,
                    callback=lambda o=other: self.merge_shot_with(sid, o.shot_id),
                    setEnabled=other is not None,
                )
            # Re-slot this shot among the others: picking a shot lands this
            # one immediately BEFORE it and pushes it, and everything after
            # it, downstream.
            #
            # ``move_shot_to_position`` takes the index in the FINAL order,
            # which is not the index the user just read off this list.
            # Moving downstream lifts this shot out first, sliding the picked
            # shot one slot up, so passing its current position landed this
            # shot one PAST it -- correct upstream, off by one downstream.
            ordered = self.sequencer.sorted_shots()
            here = next(
                (i for i, s in enumerate(ordered, start=1) if s.shot_id == sid), None
            )
            if len(ordered) > 1 and here is not None:
                move = menu.add("Move To")
                for pos, other in enumerate(ordered, start=1):
                    slot = pos - 1 if pos > here else pos
                    menu.add(
                        f"{pos}. {other.name}",
                        parent=move,
                        callback=lambda p=slot: self.move_shot_to_position(sid, p),
                        # Its own row, and the neighbour it already sits in
                        # front of, both mean "stay where you are".
                        setEnabled=other.shot_id != sid and slot != here,
                    )
            menu.add_separator()
            # Both prompt for the amount (the ellipsis says so) rather than
            # spending a fixed step: how much room a shot needs is the whole
            # question, and a fixed step meant re-opening the menu to get it.
            pad = menu.add("Add Frames")
            for edge, label in (
                ("leading", "Add Leading Frames\u2026"),
                ("trailing", "Add Trailing Frames\u2026"),
            ):
                menu.add(
                    label,
                    parent=pad,
                    callback=lambda e=edge: self._prompt_shot_space(shot, edge=e),
                )
            trim = menu.add("Trim Empty Space", callback=lambda: self._trim_shot(sid))
            for edge, label in (
                ("leading", "Trim Leading Space"),
                ("trailing", "Trim Trailing Space"),
            ):
                menu.add(
                    label,
                    parent=trim,
                    callback=lambda e=edge: self._trim_shot(sid, edge=e),
                )
        return menu

    def _prompt_shot_space(self, shot, edge: str) -> None:
        """Ask how many frames to pad *shot* at *edge*, then pad it.

        The amount is the whole question a padding gesture asks, so it is
        typed rather than assumed: the field opens on the last answer (the
        class default on the first use) and a run of shots padded by the same
        beat costs one keystroke each.  A negative amount removes room —
        :meth:`ShotSequencer.add_shot_space` clamps it to what is actually
        empty — so the validator gates on "a number that is not zero", not on
        sign.
        """

        def _parse(text):
            try:
                return float(str(text).strip())
            except (TypeError, ValueError):
                return None

        answer = self.sb.input_dialog(
            title=f"Add {edge.capitalize()} Frames",
            label=f'Frames of {edge} space for "{shot.name}":',
            text=f"{self._context_space_frames:g}",
            parent=self._get_sequencer_widget() or self.ui,
            validate=lambda t: (_parse(t) or 0.0) != 0.0,
            error_text="Enter a non-zero number of frames.",
        )
        frames = _parse(answer)
        if not frames:
            return  # cancelled, or an amount that would move nothing
        self._context_space_frames = frames
        self._add_shot_space(shot.shot_id, frames, edge=edge)

    def _add_shot_space(self, shot_id: int, frames: float, edge: str) -> None:
        """Pad *shot_id* by *frames* at *edge*, as one undoable step.

        The context-menu twin of the Shots panel's Add Space field, routed
        through the same engine call so both mean exactly the same thing.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict

        if self.sequencer is None:
            return
        try:
            with self.sequencer.store.scene_edit("addspace"):
                head, tail = self.sequencer.add_shot_space(shot_id, frames, edge=edge)
        except ShotBoundaryConflict as exc:
            # Declined BEFORE writing anything: two shots would have had to
            # share a sample whose poses disagree.  Nothing changed, so the
            # restore point is dropped rather than left for an undo to
            # "restore" the state it is already in -- the same contract the
            # Shots panel's Add Space field keeps (``_boundary_edit``).  A
            # refusal is an answer, not a crash: report it instead of
            # handing the user a traceback for a menu click.
            self._discard_shot_state()
            self.logger.warning(str(exc))
            self._set_footer(str(exc))
            return
        except Exception:
            self._discard_shot_state()
            raise
        if abs(head) < 1e-6 and abs(tail) < 1e-6:
            # Nothing moved, so a restore point would make the next undo
            # visibly do nothing.
            self._discard_shot_state()
            self._set_footer(f"Add {edge} space: nothing to do")
            return
        self._after_shot_change(shot_id)
        self._set_footer(f"Added {tail - head:.0f}f of {edge} space")

    def _neighbour_shots(self, shot_id: int) -> dict:
        """``{"merge_prev": shot|None, "merge_next": shot|None}`` around *shot_id*."""
        shots = self.sequencer.sorted_shots() if self.sequencer else []
        idx = next((i for i, s in enumerate(shots) if s.shot_id == shot_id), None)
        if idx is None:
            return {"merge_prev": None, "merge_next": None}
        return {
            "merge_prev": shots[idx - 1] if idx > 0 else None,
            "merge_next": shots[idx + 1] if idx + 1 < len(shots) else None,
        }

    def _after_shot_change(self, shot_id=None) -> None:
        """Rebuild everything a shot add/remove/resize invalidates."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_combobox()
        self._sync_to_widget(shot_id=shot_id)
        self._apply_view_playback_range()

    def delete_shot(self, shot_id: int) -> None:
        """Delete *shot_id* with its contents, closing the timeline behind it.

        This is what "delete a shot" means from the timeline: the shot, the
        animation it owns, and the space it occupied all go, and the next shot
        lands where this one started.  The confirmation says so, because the
        keys are the animator's and a menu click should not eat them silently.
        """
        if self.sequencer is None:
            return
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return
        reply = QtWidgets.QMessageBox.question(
            self._get_sequencer_widget() or self.ui,
            "Delete Shot",
            f'Delete "{shot.name}" [{shot.start:.0f}\u2013{shot.end:.0f}]\n'
            "\u2014 its keyframes, closing the gap behind it?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        store = self.sequencer.store
        try:
            with store.scene_edit("delshot"):
                result = self.sequencer.delete_shot(shot_id)
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(None)
        self._after_shot_change()
        cut = result.get("curves_cut", 0)
        closed = result.get("closed", 0.0)
        parts = [f"Deleted {result.get('name', shot.name)}"]
        if cut:
            parts.append(f"{cut} curve(s) cleared")
        if closed:
            parts.append(f"closed {closed:.0f}f")
        self._set_footer(" \u00b7 ".join(parts))

    def move_shot_to_position(self, shot_id: int, position: int) -> None:
        """Re-slot *shot_id* at 1-based *position*, pushing the rest along.

        The shot that held the slot -- and every shot after it -- moves
        downstream to make room, so a reorder never writes over a shot or
        drops its keys.  ``ShotSequencer.move_shot_to_position`` resolves the
        whole new order before touching a keyframe, which is why this cannot
        leave two shots transiently claiming one span.
        """
        if self.sequencer is None:
            return
        store = self.sequencer.store
        try:
            with store.scene_edit("reordershot"):
                self.sequencer.move_shot_to_position(shot_id, position)
        except Exception:
            self._discard_shot_state()
            raise
        self._after_shot_change(shot_id=shot_id)
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is not None:
            self._set_footer(
                f"Moved {shot.name} to {position} \u00b7 "
                f"{shot.start:.0f}\u2013{shot.end:.0f}"
            )

    def merge_shot_with(self, shot_id: int, other_id: int) -> None:
        """Fuse two neighbouring shots into one spanning both."""
        if self.sequencer is None:
            return
        store = self.sequencer.store
        try:
            with store.scene_edit("mergeshots"):
                merged = self.sequencer.merge_shots([shot_id, other_id])
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(merged.shot_id)
        self._after_shot_change(shot_id=merged.shot_id)
        self._set_footer(
            f"Merged into {merged.name} \u00b7 {merged.start:.0f}\u2013{merged.end:.0f}"
        )

    def split_shot_at(self, shot_id: int, time: float) -> None:
        """Cut *shot_id* in two at *time*, leaving its content where it is."""
        if self.sequencer is None:
            return
        store = self.sequencer.store
        try:
            with store.scene_edit("splitshot"):
                tail = self.sequencer.split_shot(shot_id, time)
        except ValueError as exc:
            self._discard_shot_state()
            self._set_footer(str(exc))
            return
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(tail.shot_id)
        self._after_shot_change(shot_id=tail.shot_id)
        self._set_footer(
            f"Split at {time:.0f} \u00b7 {tail.name} {tail.start:.0f}\u2013{tail.end:.0f}"
        )

    def _trim_shot(self, shot_id: int, edge: str = "both") -> None:
        """Trim empty space from *shot_id*, undoable, then refresh the widget.

        *edge* selects which end gives way: ``"both"`` (the default),
        ``"leading"`` (head only) or ``"trailing"`` (tail only).  Trimming
        one end is the common case when hand-tuning a cut — the other end
        is usually already where the animator wants it.
        """
        if self.sequencer is None:
            return
        with self.sequencer.store.scene_edit("trim"):
            head, tail = self.sequencer.trim_shot_to_content(shot_id, edge=edge)
        if abs(head) < 1e-6 and abs(tail) < 1e-6:
            # Nothing moved: drop the snapshot (a dead restore point would
            # make the next undo visibly do nothing) and skip the rebuild.
            self._discard_shot_state()
            self._set_footer(
                "Nothing to trim \u2014 the shot already fits its content."
            )
            return
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()
        self._sync_combobox()
        self._apply_view_playback_range()
        self._set_footer(
            f"Trimmed {abs(head):.0f}f from the head, {abs(tail):.0f}f from the tail"
        )

    # ---- extend to keys (global option) ----------------------------------

    def _set_extend_to_keys(self, enabled: bool) -> None:
        """Turn the global "grow the current shot over new keys" option on/off."""
        self._extend_to_keys = bool(enabled)

    def _set_extend_reach(self, frames: float) -> None:
        """Set how far outside a bound a new key may sit and still be claimed.

        ``ANY_REACH`` (-1, the spin box's special value) removes the cap
        entirely; every other value is a distance in frames.
        """
        value = float(frames)
        self._extend_reach = (
            self.ANY_REACH if value <= self.ANY_REACH else max(0.0, value)
        )

    @property
    def _extend_reach_arg(self):
        """:attr:`_extend_reach` as ``extend_shot_to_fit`` takes it.

        The engine's ``reach=None`` is "no cap", which is what the option's
        -1 means -- the UI cannot offer ``None`` in a spin box.
        """
        return None if self._extend_reach <= self.ANY_REACH else self._extend_reach

    def _auto_extend_to_new_keys(self, shot_id: int) -> bool:
        """Grow *shot_id* over keys just set outside it, when the option is on.

        The one place the "Extend to Keys" option acts: keys were just
        created (or pasted) and some of them landed past the shot's bounds,
        which is the moment the animator means the shot to cover them.  It
        runs as its OWN undo step rather than joining the keying that
        triggered it -- the keys and the bound move are two decisions, and
        undoing the bound must not take the keys with it.

        Returns ``True`` when a bound actually moved (the caller then skips
        its own rebuild, since this one already did it).
        """
        if not self._extend_to_keys or self.sequencer is None:
            return False
        return self._extend_shot_to_keys(shot_id, quiet=True)

    def _extend_shot_to_keys(
        self, shot_id: int, edge: str = "both", quiet: bool = False
    ) -> bool:
        """Grow *shot_id* over the keys its members have within reach of its
        bounds, undoable, then refresh the widget.

        The explicit form of the auto-extend a Move to Shot performs: the
        animator set keys just past a bound and wants the shot to cover
        them.  Reach is the global option's setting (:attr:`_extend_reach`,
        -1 for any distance); keys beyond it, and keys inside a neighbouring
        shot, are left alone.  *edge* limits the growth to ``"leading"`` or
        ``"trailing"``.  With *quiet* a no-op reports nothing -- the
        automatic path runs on every keying burst, and "nothing to extend
        to" is its normal answer, not news.

        Returns whether a bound moved.
        """
        from mayatk.anim_utils.shots._shot_plan import ShotBoundaryConflict

        if self.sequencer is None:
            return False
        was_syncing = self._syncing
        # Only the WRITES: re-sampling a bound edits keys, and on the automatic
        # path that would re-arm the very debounce that called us.  The rebuild
        # below runs unguarded, as it does for every other edit here -- it holds
        # its own guard where it needs one (``_rebuild_content``).
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit("extend"):
                head, tail = self.sequencer.extend_shot_to_fit(
                    shot_id, edge=edge, reach=self._extend_reach_arg
                )
        except ShotBoundaryConflict as exc:
            # Declined before writing anything (see _add_shot_space).
            self._discard_shot_state()
            self.logger.warning(str(exc))
            if not quiet:
                self._set_footer(str(exc))
            return False
        except Exception:
            self._discard_shot_state()
            raise
        finally:
            self._syncing = was_syncing
        if abs(head) < 1e-6 and abs(tail) < 1e-6:
            self._discard_shot_state()
            if not quiet:
                reach = self._extend_reach_arg
                self._set_footer(
                    "Nothing to extend to \u2014 no keys "
                    + (
                        "outside the bounds."
                        if reach is None
                        else f"within {reach:g} frames of the bounds."
                    )
                )
            return False
        self._after_shot_change(shot_id)
        self._set_footer(
            f"Extended {abs(head):.0f}f at the head, {abs(tail):.0f}f at the tail"
        )
        return True

    def _insert_shot(self, anchor_shot_id: int, before: bool) -> None:
        """Insert a new shot before or after *anchor_shot_id*.

        Downstream shots (and their keys and audio) ripple to open the
        space, so this never overwrites existing content.
        """
        if self.sequencer is None:
            return
        seq = self.sequencer
        store = seq.store
        anchor = seq.shot_by_id(anchor_shot_id)
        if anchor is None:
            return

        sorted_s = seq.sorted_shots()
        existing_names = {sh.name for sh in sorted_s}
        idx = next(
            (i for i, sh in enumerate(sorted_s) if sh.shot_id == anchor_shot_id), 0
        )
        n = len(sorted_s) + 1
        while f"Shot {n}" in existing_names:
            n += 1

        from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        duration = Behaviors.compute_duration([], fallback=100.0)
        try:
            with self.sequencer.store.scene_edit("insert"):
                shot = seq.insert_shot(
                    name=f"Shot {n}",
                    duration=duration,
                    at_position=(idx + 1) if before else (idx + 2),
                )
        except Exception:
            self._discard_shot_state()
            raise
        store.set_active_shot(shot.shot_id)
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_combobox()
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == shot.shot_id:
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(i)
                    cmb.blockSignals(False)
                    break
        self.select_shot(shot.shot_id)
        self._sync_to_widget()
        self._set_footer(
            f"Inserted {shot.name} \u00b7 {shot.start:.0f}\u2013{shot.end:.0f}"
        )

    def _create_shot_one_click(self) -> None:
        """Append a new shot using the configured gap and default duration."""
        if self.sequencer is None:
            return
        store = self.sequencer.store
        gap = store.gap or 0
        existing = self.sequencer.sorted_shots()
        existing_names = {s.name for s in existing}
        idx = len(existing) + 1
        while f"Shot {idx}" in existing_names:
            idx += 1
        name = f"Shot {idx}"
        from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        duration = Behaviors.compute_duration([], fallback=100.0)
        # Through the sequencer-level append (insert_shot with no anchor),
        # not the pure store.append_shot: the engine probes the last shot's
        # trailing envelope content (fade tails, trailing audio) so the new
        # shot is never built on top of it — and the snapshot makes the
        # creation undoable via the ledger's membership diff.
        try:
            with self.sequencer.store.scene_edit("newshot"):
                shot = self.sequencer.insert_shot(name=name, duration=duration, gap=gap)
        except Exception:
            self._discard_shot_state()
            raise
        self._sync_combobox()
        # Select the new shot in the combobox
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                if cmb.itemData(i) == shot.shot_id:
                    cmb.setCurrentIndex(i)
                    break
        self.select_shot(shot.shot_id)
        self._sync_to_widget()
        self._set_footer(
            f"Created {shot.name} \u00b7 {shot.start:.0f}\u2013{shot.end:.0f}"
        )

    def _find_shot_at_time(self, time: float):
        """Return the shot whose range contains *time*, or ``None``."""
        if self.sequencer is None:
            return None
        for s in self.sequencer.sorted_shots():
            if s.start <= time <= s.end:
                return s
        return None

    def _on_shot_switch_requested(self, time: float) -> None:
        """Ctrl+Shift+Click on timeline — switch to the shot at *time*."""
        shot = self._find_shot_at_time(time)
        if shot is not None:
            self.on_shot_block_clicked(shot.name)

    def _edit_shot_dialog(self, shot) -> None:
        """Open Shot Settings with the given shot pre-selected for editing."""
        self.sequencer.store.set_active_shot(shot.shot_id)
        self.sb.handlers.marking_menu.show("shots")

    def _set_view_mode(self, mode: str) -> None:
        """Set the shot display mode and rebuild the widget."""
        self._shot_display_mode = mode
        if self._playback_range_mode != "off":
            self._apply_view_playback_range()
        self._sync_to_widget()

    def _set_playback_range_mode(self, mode: str) -> None:
        """Set the playback-range tracking mode.

        *mode* must be one of ``"off"``, ``"follows_view"``, or
        ``"locked"``.
        """
        self._playback_range_mode = mode
        if mode != "off":
            self._apply_view_playback_range()

    def _set_cmb_mode(self, mode: str) -> None:
        """Switch the combobox between shots and scene markers."""
        self._cmb_mode = mode
        # Keep the mode selector in sync (guard against re-entry)
        cmb_mode = self._cmb_mode_widget
        if cmb_mode is not None:
            idx = 1 if mode == "markers" else 0
            if cmb_mode.currentIndex() != idx:
                cmb_mode.blockSignals(True)
                cmb_mode.setCurrentIndex(idx)
                cmb_mode.blockSignals(False)
        self._sync_combobox()

    # ---- widget ↔ engine sync -------------------------------------------

    @property
    def active_shot_id(self) -> Optional[int]:
        """Return the shot_id currently selected, or the first shot's id."""
        # In markers mode the combobox itemData holds marker TIMES
        # (floats), not shot ids — reading it would return a bogus id
        # and silently break shot resolution downstream.
        cmb = getattr(self.ui, "cmb_shot", None)
        if self._cmb_mode != "markers" and cmb is not None and cmb.currentIndex() >= 0:
            sid = cmb.itemData(cmb.currentIndex())
            if sid is not None:
                return sid
        if self.sequencer and self.sequencer.shots:
            # The store's active shot (set by shot selection here or in
            # the Shots settings panel) wins over the first-shot
            # fallback — in markers mode the combobox can't provide it,
            # and defaulting to the first shot retargets the view away
            # from the shot being worked on after every store event.
            store_active = self.sequencer.store.active_shot_id
            if store_active is not None and self.sequencer.shot_by_id(store_active):
                return store_active
            # Nothing selected yet (the panel just opened, or markers mode):
            # the shot under the playhead is the one being worked on, and the
            # one the first framing should show.  The first shot stands in
            # only when the playhead sits outside every shot.
            under_playhead = self._shot_at_current_time()
            if under_playhead is not None:
                return under_playhead.shot_id
            return self.sequencer.sorted_shots()[0].shot_id
        return None

    def _current_time(self):
        """The playhead's frame, or ``None`` when there is no scene to ask."""
        if cmds is None:
            return None
        try:
            return float(cmds.currentTime(q=True))
        except Exception:
            return None

    def _shot_at_current_time(self):
        """The shot the playhead is in, or ``None``."""
        now = self._current_time()
        if now is None or self.sequencer is None:
            return None
        return self._find_shot_at_time(now)

    # Boundary snapshots delegate to the STORE's ledger (pythontk
    # ShotStore.push/restore/redo_boundary_snapshot): scene keyframes ride
    # Maya's undo queue but shot bounds live in the store, outside it, and
    # more than one panel mutates them — per-controller stacks desynced
    # from each other and from the queue.  One ledger per store = per
    # scene, so a scene swap isolates it for free.

    def _save_shot_state(self) -> None:
        """Record the current shot boundaries as an undo restore point.

        No production path here calls this any more: every mayatk shot edit
        brackets through :meth:`ShotStore.scene_edit`, which pushes the
        restore point BEFORE the mutation and tags it with whether the edit
        reached Maya's undo queue.  This stays as the untagged primitive —
        blendertk's mirror uses it directly (its undo chunk always pushes, so
        it needs no pairing), and an untagged point deliberately keeps the
        pre-pairing "restore and undo" behaviour.  Prefer ``scene_edit``.
        """
        if self.sequencer is not None:
            self.sequencer.store.push_boundary_snapshot()

    def _discard_shot_state(self) -> None:
        """Drop the most recent restore point (the edit was a no-op)."""
        if self.sequencer is not None:
            self.sequencer.store.discard_boundary_snapshot()

    def _restore_shot_state(self) -> None:
        """Apply the most recent restore point (the undo direction).

        Membership is restored symmetrically: a shot ABSENT from the
        snapshot is removed (undoing an insert leaves no phantom), and a
        shot the snapshot names but the store lost is re-created from its
        record (undoing a delete — the keys were never deleted with it).
        """
        if self.sequencer is not None:
            self.sequencer.store.restore_boundary_snapshot()

    def _redo_shot_state(self) -> None:
        """Re-apply the state undo stepped back from (the redo direction).

        Without this, a redo re-applies the scene keys while the bounds
        stay restored — resurrecting the keys-outside-their-shot state.
        """
        if self.sequencer is not None:
            self.sequencer.store.redo_boundary_snapshot()

    def _native_event_is_ours(self, redo: bool = False) -> bool:
        """True when the undo/redo Maya JUST performed is our newest one.

        Maya fires its Undo/Redo events for every undo in the session, so the
        event alone says nothing about whose edit it was.  The entry Maya just
        moved is named by the OPPOSITE queue (verified: after ``cmds.undo()``,
        ``undoInfo -q -redoName`` returns the chunk that was undone), and our
        restore point records the marker its edit landed under — so the two
        match only when this event is that edit.  Without the test, undoing an
        unrelated scene edit consumed a restore point and reverted shot bounds
        whose keys Maya had left exactly where they were.

        An UNPAIRED restore point can never match: its edit put nothing on the
        queue, so whatever Maya just undid was somebody else's.
        """
        store = self.sequencer.store if self.sequencer is not None else None
        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return True  # untagged (legacy push) — pre-pairing behaviour
        paired, marker = tag
        return bool(paired) and marker == store.undo_queue_top(redo=not redo)

    def _undo_plan(self, redo: bool = False) -> tuple:
        """Decide how one undo/redo splits between the ledger and Maya.

        Returns ``(apply_ledger, call_maya)``.  The two stacks are not in
        lockstep — see :meth:`ShotStore.scene_edit`:

        * no restore point of ours → pass straight through to Maya;
        * our newest restore point is no longer the newest thing on the
          queue → an unrelated edit followed it, so Maya's undo belongs to
          THAT and our restore point stays put;
        * our restore point is on top → apply it, and touch Maya's queue
          only if the edit actually recorded a step there (a bounds-only
          edit records nothing, and an unconditional ``cmds.undo()`` would
          pop the user's previous, unrelated operation).

        Which queue still has to be showing ``marker`` depends on BOTH the
        direction and the pairing.  A paired edit's chunk rides Maya's
        queues, so after its undo the marker names the top of the *redo*
        queue.  An unpaired edit never put anything on either queue — its
        marker names whatever was on the *undo* queue when it was recorded,
        and that stays true in both directions.  Comparing an unpaired entry
        against the redo queue always mismatches, which silently dropped the
        bounds restore AND redid an unrelated operation.
        """
        store = self.sequencer.store if self.sequencer is not None else None
        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False, True
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return True, True  # untagged (legacy push) — pre-pairing behaviour
        paired, marker = tag
        if marker != store.undo_queue_top(redo=redo and bool(paired)):
            return False, True
        return True, bool(paired)

    def on_undo(self) -> None:
        """Handle undo_requested from the widget — delegate to Maya undo."""
        if cmds is None:
            return
        apply_ledger, call_maya = self._undo_plan()
        self._syncing = True
        try:
            try:
                if apply_ledger:
                    self._restore_shot_state()
            except Exception:
                self.logger.debug("on_undo: _restore_shot_state failed", exc_info=True)
            if call_maya:
                cmds.undo()
        except RuntimeError:
            pass
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

    def on_redo(self) -> None:
        """Handle redo_requested from the widget — delegate to Maya redo."""
        if cmds is None:
            return
        apply_ledger, call_maya = self._undo_plan(redo=True)
        self._syncing = True
        try:
            try:
                if apply_ledger:
                    self._redo_shot_state()
            except Exception:
                self.logger.debug("on_redo: _redo_shot_state failed", exc_info=True)
            if call_maya:
                cmds.redo()
        except RuntimeError:
            pass
        finally:
            self._syncing = False
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget()

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

    # -- key context menu ---------------------------------------------------

    #: Tangent types a key's context menu offers, in the Graph Editor's order:
    #: ``(label, keyTangent type)``.
    _TANGENT_TYPES = (
        ("Auto", "auto"),
        ("Spline", "spline"),
        ("Clamped", "clamped"),
        ("Linear", "linear"),
        ("Flat", "flat"),
        ("Step", "step"),
        ("Plateau", "plateau"),
    )

    def _key_targets(self, widget, key_groups: list) -> list:
        """``[(obj, attr, [times], shot_id), ...]`` for a key selection.

        One entry per writable sub-row clip in *key_groups* (the payload of
        ``key_selection_changed`` / ``key_menu_requested``); read-only clips
        and object-level rows (no attribute) contribute nothing.
        """
        targets = []
        for group in key_groups:
            clip = widget.get_clip(group["clip_id"])
            if clip is None or clip.data.get("read_only"):
                continue
            obj = clip.data.get("obj")
            attr = clip.data.get("attr_name")
            times = sorted(group.get("times") or [])
            if not obj or not attr or not times:
                continue
            targets.append((obj, attr, times, clip.data.get("shot_id")))
        return targets

    @staticmethod
    def _key_targets_to_sequences(targets: list) -> list:
        """The key-level sequence dicts ``move_sequences_to_shot`` takes."""
        return [
            {
                "kind": "anim",
                "obj": obj,
                "attr": attr,
                "times": list(times),
                "start": times[0],
                "end": times[-1],
            }
            for obj, attr, times, _sid in targets
        ]

    def on_key_menu(self, menu, key_groups: list) -> None:
        """Add the key actions to a key's context menu.

        Mirrors the Graph Editor's own right-click: a Tangents submenu that
        sets both sides, In/Out submenus for one side, Break / Unify, and --
        the sequencer's own -- Move to Shot, which sends exactly the selected
        keys, per attribute, as sequences.  It also carries the Animation
        panel's key edits (:meth:`_add_key_edit_actions`), which belong to a
        key SELECTION: they were briefly offered per SHOT, where "remove the
        intermediate keys" meant every member's every attribute across the
        whole span -- a far bigger edit than the words promise.  The widget
        appends Delete.
        """
        if cmds is None:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return
        targets = self._key_targets(widget, key_groups)
        if not targets:
            return
        from qtpy import QtWidgets

        n = sum(len(t) for _o, _a, t, _s in targets)
        suffix = f" ({n})" if n > 1 else ""

        # Explicit parents: an ``addMenu(str)`` wrapper can go stale once this
        # frame drops it (PySide 6.11), and the actions bind to the submenu.
        for label, sides in (
            ("Tangents", ("in", "out")),
            ("In Tangent", ("in",)),
            ("Out Tangent", ("out",)),
        ):
            sub = QtWidgets.QMenu(label, menu)
            menu.addMenu(sub)
            for name, tangent in self._TANGENT_TYPES:
                act = sub.addAction(name)
                act.triggered.connect(
                    lambda _checked=False, t=tangent, s=sides: self._set_key_tangents(
                        targets, t, s
                    )
                )
        act_break = menu.addAction(f"Break Tangents{suffix}")
        act_break.triggered.connect(lambda: self._lock_key_tangents(targets, False))
        act_unify = menu.addAction(f"Unify Tangents{suffix}")
        act_unify.triggered.connect(lambda: self._lock_key_tangents(targets, True))

        self._add_key_edit_actions(menu, targets, suffix)

        if self.sequencer:
            seqs = self._key_targets_to_sequences(targets)
            shots = self.sequencer.sorted_shots()
            if seqs and len(shots) > 1:
                menu.addSeparator()
                move_menu = QtWidgets.QMenu(f"Move to Shot{suffix}", menu)
                menu.addMenu(move_menu)
                self._populate_move_to_shot(move_menu, seqs, noun="key")

    def _set_key_tangents(self, targets: list, tangent: str, sides=("in", "out")):
        """Set the tangent type on the selected keys (one or both sides)."""
        kwargs = {}
        if "in" in sides:
            kwargs["inTangentType"] = tangent
        if "out" in sides:
            kwargs["outTangentType"] = tangent
        side = "" if len(sides) == 2 else f" {sides[0]}"
        self._edit_key_tangents(targets, kwargs, f"{tangent}{side}")

    def _lock_key_tangents(self, targets: list, lock: bool) -> None:
        """Break (``lock=False``) or unify the selected keys' tangents."""
        self._edit_key_tangents(
            targets, {"lock": lock}, "unified" if lock else "broken"
        )

    @staticmethod
    def tangent_from_handle(side: str, dt: float, dv: float) -> tuple:
        """``(angle_degrees, weight)`` of the tangent a dragged handle stands for.

        The inverse of how the preview places its control points
        (``SegmentCollector.build_curve_preview``): an OUT handle sits
        ``weight * (cos a, sin a)`` after its key and an IN handle the same
        distance BEFORE it, so the IN vector points back along the tangent
        and is flipped before its angle is read.  The weight is the handle's
        length in curve units, which is what a weighted curve stores.
        """
        import math

        if side == "in":
            dt, dv = -dt, -dv
        return math.degrees(math.atan2(dv, dt)), math.hypot(dt, dv)

    def on_key_tangent_dragged(
        self, clip_id: int, time: float, side: str, dt: float, dv: float
    ) -> None:
        """Write the tangent a dragged handle asks for on the key's curve.

        Angle always; weight too when the curve carries weighted tangents
        (``weightedTangents``), since only then does the handle's length
        mean anything -- an unweighted curve's control point sits a third
        of the span out whatever the drag did.  Maya turns the edited side
        ``fixed`` and, on a unified key, swings the other side with it.
        """
        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
            curves_for_attr,
        )

        widget = self._get_sequencer_widget()
        if widget is None:
            return
        targets = self._key_targets(widget, [{"clip_id": clip_id, "times": [time]}])
        if not targets:
            return
        obj, attr, _times, _sid = targets[0]
        angle, weight = self.tangent_from_handle(side, dt, dv)
        kwargs = {("inAngle" if side == "in" else "outAngle"): angle}
        curves = curves_for_attr(obj, attr)
        if curves and cmds.getAttr(f"{curves[0]}.weightedTangents"):
            kwargs["inWeight" if side == "in" else "outWeight"] = weight
        self._edit_key_tangents(targets, kwargs, f"{side} handle dragged")

    def _edit_key_tangents(self, targets: list, kwargs: dict, what: str) -> None:
        """One ``keyTangent`` edit per curve over the selected times, undoable.

        The rebuild that follows retires every key dot, so the selection is
        put back by object/attribute/time afterwards -- the user is looking
        at the handles they just changed, and they must stay selected to
        show them.
        """
        from mayatk.anim_utils.shots.shot_sequencer.clip_motion import (
            curves_for_attr,
        )

        widget = self._get_sequencer_widget()
        if widget is None or not targets:
            return
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        n = 0
        was_syncing = self._syncing
        self._syncing = True  # own cmds edits must not arm the keyframe debounce
        try:
            with CoreUtils.undo_chunk("Key tangents"):
                for obj, attr, times, _sid in targets:
                    tt = tuple((t, t) for t in times)
                    for crv in curves_for_attr(obj, attr):
                        try:
                            cmds.keyTangent(str(crv), edit=True, time=tt, **kwargs)
                        except RuntimeError:
                            continue  # e.g. a weight on an unweighted curve
                        n += len(times)
        finally:
            self._syncing = was_syncing
        self._sub_row_cache.clear()
        self._sync_to_widget(shot_id=shot_id)
        widget.select_keys(
            [
                {"data": {"obj": obj, "attr_name": attr}, "times": list(times)}
                for obj, attr, times, _sid in targets
            ]
        )
        self._set_footer(f"Tangents {what} on {n} key{'s' if n != 1 else ''}")

    # -- key-selection edits (tentacle's Animation panel, per selection) -----

    #: The key edits offered under the key menu's Edit row, as
    #: ``(label, method name)``.  Declared rather than inlined so the two
    #: forks can be read side by side: these four are spelled identically in
    #: both, unlike the tangent rows above them.
    _KEY_EDITS = (
        ("Remove Intermediate Keys", "_thin_selected_keys"),
        ("Snap Fractional Keys", "_snap_selected_keys"),
        ("Invert Keys", "_invert_selected_keys"),
        ("Align Keys", "_align_selected_keys"),
    )

    def _add_key_edit_actions(self, menu, targets, suffix) -> None:
        """Append the stash and Edit rows to the key menu.

        Everything here is scoped to the keys actually SELECTED -- the
        objects they belong to and the span they cover -- which is what
        makes them safe to offer at all: the same verbs applied to a whole
        shot silently reached every member's every attribute.

        Copy and Paste are NOT rows: they are the panel's ``Ctrl+C`` /
        ``Ctrl+V`` (see ``_copy_keys_shortcut``), because they are the one
        pair here that every editor already binds a key for.
        """
        menu.addSeparator()
        act_store = menu.addAction(f"Store Keys{suffix}")
        act_store.triggered.connect(lambda: self._stash_key_targets(targets))

        edit = QtWidgets.QMenu("Edit", menu)
        menu.addMenu(edit)
        for label, method in self._KEY_EDITS:
            act = edit.addAction(label)
            act.triggered.connect(
                lambda _checked=False, m=method: getattr(self, m)(targets)
            )

    @staticmethod
    def _target_objects(targets: list) -> list:
        """*targets*' objects, de-duplicated, in the order they appear."""
        return list(dict.fromkeys(obj for obj, _a, _t, _s in targets))

    @staticmethod
    def _target_span(targets: list) -> tuple:
        """The frame range *targets* covers, end to end."""
        times = [t for _o, _a, ts, _s in targets for t in ts]
        return (min(times), max(times))

    def _key_scene_edit(self, label: str, fn, shot_id=None):
        """Run *fn* as ONE undoable scene edit, reconcile, rebuild.

        The bracket every key edit needs and none of them should re-state:
        the store's ``scene_edit`` (a named undo chunk plus a boundary
        restore point), then ``reconcile_system_edits`` because an edit that
        adds, moves or removes keys may have touched a sample the shot
        system authored.  ``_syncing`` is held throughout so our own writes
        do not re-arm the keyframe debounce and rebuild underneath us.

        Returns whatever *fn* returned, or ``None`` with no sequencer.
        """
        if self.sequencer is None:
            return None
        was_syncing = self._syncing
        self._syncing = True
        try:
            with self.sequencer.store.scene_edit(label):
                result = fn()
                self.sequencer.reconcile_system_edits()
        finally:
            self._syncing = was_syncing
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._sync_to_widget(shot_id=shot_id)
        return result

    def _key_selection_edit(self, targets, label: str, fn):
        """Run ``fn(objects, span)`` over a key selection; ``(ran, result)``.

        Asserts the Graph Editor selection first
        (:meth:`_select_target_keys`): several of these read it rather than
        taking a range.

        Two return values because these engine calls disagree about what to
        report -- a count, a bool, nothing at all -- so "it ran" cannot be
        read off the result.
        """
        if not targets or self.sequencer is None:
            return False, None
        self._select_target_keys(targets)
        objects = self._target_objects(targets)
        span = self._target_span(targets)
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        result = self._key_scene_edit(label, lambda: fn(objects, span), shot_id=shot_id)
        return True, result

    def _thin_selected_keys(self, targets) -> None:
        """Keep only the first and last key of each selected attribute."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, n = self._key_selection_edit(
            targets,
            "thinkeys",
            lambda objects, span: AnimUtils.remove_intermediate_keys(
                objects, time_range=span
            ),
        )
        if ran:
            self._set_footer(
                f"Removed {n or 0} intermediate key{'s' if n != 1 else ''}"
            )

    def _snap_selected_keys(self, targets) -> None:
        """Pull the selected keys off fractional frames onto whole ones."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, n = self._key_selection_edit(
            targets,
            "snapkeys",
            lambda objects, span: AnimUtils.snap_keys_to_frames(
                objects, selected_only=True, time_range=span
            ),
        )
        if ran:
            self._set_footer(
                f"Snapped {n or 0} key{'s' if n != 1 else ''} to whole frames"
            )

    def _invert_selected_keys(self, targets) -> None:
        """Mirror the selected keys in time, in place."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, _ = self._key_selection_edit(
            targets,
            "invertkeys",
            # time=None mirrors within the selection's own range rather than
            # placing a reversed COPY somewhere -- the sequencer's keys are
            # already where the animator put them.
            lambda objects, _span: AnimUtils.invert_keys(objects),
        )
        if ran:
            n = sum(len(t) for _o, _a, t, _s in targets)
            self._set_footer(f"Inverted {n} key{'s' if n != 1 else ''}")

    def _align_selected_keys(self, targets) -> None:
        """Line the selected keys up on the earliest one's frame."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        ran, ok = self._key_selection_edit(
            targets,
            "alignkeys",
            lambda objects, _span: AnimUtils.align_selected_keyframes(objects),
        )
        if ran:
            self._set_footer("Aligned the selected keys" if ok else "Nothing to align")

    def _selected_key_targets(self) -> list:
        """The key menu's ``targets`` for whatever is selected right now.

        What a SHORTCUT has to resolve for itself: a menu is handed its
        groups, a key press is not.
        """
        widget = self._get_sequencer_widget()
        if widget is None:
            return []
        return self._key_targets(widget, widget.selected_keys())

    def _copy_keys_shortcut(self) -> None:
        """Ctrl+C over the sequencer: copy the selected keys."""
        self._copy_selected_keys(self._selected_key_targets())

    def _paste_keys_shortcut(self) -> None:
        """Ctrl+V over the sequencer: paste them at the playhead."""
        self._paste_selected_keys(self._selected_key_targets())

    def _copy_selected_keys(self, targets) -> None:
        """Copy the selected keys (times, values, tangents) for a later paste."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        if not targets:
            return
        self._select_target_keys(targets)
        data = AnimUtils.copy_keys(
            self._target_objects(targets), mode="selected", tangent_detail=True
        )
        if not data:
            self._set_footer("Nothing to copy")
            return
        self._copied_keys = data
        n = sum(
            len(v) if isinstance(v, list) else 1
            for attrs in data.values()
            for v in attrs.values()
        )
        self._set_footer(f"Copied {n} key{'s' if n != 1 else ''}")

    def _paste_selected_keys(self, targets) -> None:
        """Paste the copied keys onto the selection at the current frame."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        if not self._copied_keys:
            self._set_footer("Nothing copied yet \u2014 use Copy Keys first")
            return
        objects = self._target_objects(targets)
        if not objects:
            self._set_footer("Select some keys to paste onto")
            return
        shot_id = next((sid for _o, _a, _t, sid in targets if sid is not None), None)
        # target_time is passed, not defaulted: mayatk's default IS the current
        # frame but blendertk's is the buffer's own frames, and the two panels
        # have to mean the same thing.
        now = self._current_time()
        n = self._key_scene_edit(
            "pastekeys",
            lambda: AnimUtils.paste_keys(
                objects, copied_data=self._copied_keys, target_time=now
            ),
            shot_id=shot_id,
        )
        self._set_footer(
            f"Pasted onto {n or 0} object{'s' if n != 1 else ''}"
            if n
            else "Nothing pasted \u2014 no matching attributes on the selection"
        )

    def _stash_key_targets(self, targets: list) -> None:
        """Park the selected keys in the key stash.

        The key-selection twin of :meth:`_stash_clip_keys`: same store, same
        loop (:meth:`_run_stash`), scoped to the selected times of each
        attribute instead of a whole clip's span.
        """
        if cmds is None or not targets:
            return
        jobs = []
        for obj, attr, times, shot_id in targets:
            full = self._resolve_full_name(obj)
            if not cmds.objExists(full):
                continue
            jobs.append(
                (
                    full,
                    [attr],
                    min(times),
                    max(times),
                    None if shot_id == -1 else shot_id,
                )
            )
        self._run_stash(jobs)

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

    _node_icons_cls_cache = ...  # sentinel — not yet resolved

    @classmethod
    def _try_load_maya_icons(cls):
        """Return the :class:`NodeIcons` class if Maya is available, else ``None``.

        Resolved once per process; the result (including the ``None``
        no-Maya case) is memoised on the class so every rebuild pays a
        single attribute read instead of an import + try/except.
        """
        if cls._node_icons_cls_cache is not ...:
            return cls._node_icons_cls_cache
        try:
            from mayatk.ui_utils.node_icons import NodeIcons

            cls._node_icons_cls_cache = NodeIcons
        except ImportError:
            cls._node_icons_cls_cache = None
        return cls._node_icons_cls_cache

    def _visible_shots(self, active_shot):
        """Return the shots to render based on ``_shot_display_mode``."""
        if self._shot_display_mode == "current":
            return [active_shot]
        sorted_shots = self.sequencer.sorted_shots()
        if self._shot_display_mode == "all":
            return sorted_shots
        # "adjacent" — previous + current + next
        idx = next(
            (i for i, s in enumerate(sorted_shots) if s.shot_id == active_shot.shot_id),
            None,
        )
        if idx is None:
            return [active_shot]
        result = []
        if idx > 0:
            result.append(sorted_shots[idx - 1])
        result.append(active_shot)
        if idx < len(sorted_shots) - 1:
            result.append(sorted_shots[idx + 1])
        return result

    def _sync_to_widget(
        self, shot_id: Optional[int] = None, *, frame: bool = False
    ) -> None:
        """Full rebuild: content + decoration + viewport.

        When the display mode is ``"adjacent"`` or ``"all"``, clips from
        non-active shots are also rendered (greyed-out, locked) and their
        ranges are shown as non-interactive overlays.

        Parameters:
            shot_id: Shot to display.  Falls back to :attr:`active_shot_id`.
            frame: If True, reframe the viewport on the active shot.
        """
        widget, shot = self._resolve_sync_target(shot_id)
        if widget is None or shot is None:
            # No shots — try scene-wide display
            widget = self._get_sequencer_widget()
            if (
                widget is not None
                and self.sequencer is not None
                and not self.sequencer.shots
            ):
                self._sync_shotless(widget, frame=frame)
            return

        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        visible_shots = self._visible_shots(shot)

        # bulk_updates defers the per-add scene-rect recompute (which
        # walks every clip/marker/gap) to one pass at exit — without it
        # a rebuild is O(n²) in clip count.
        bulk = getattr(widget, "bulk_updates", None)
        if callable(bulk):
            with bulk():
                self._rebuild_content(widget, shot, visible_shots)
                self._rebuild_decoration(widget, shot, visible_shots)
        else:
            self._rebuild_content(widget, shot, visible_shots)
            self._rebuild_decoration(widget, shot, visible_shots)
        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        self._update_footer_shot_summary()

    def _sync_shotless(self, widget, *, frame: bool = False) -> None:
        """Populate the widget with scene-wide animation when no shots exist.

        Discovers animated transforms across the full playback range and
        displays them as tracks/clips so the user can inspect animation
        before defining any shots.
        """
        if cmds is None:
            return
        start = cmds.playbackOptions(q=True, min=True)
        end = cmds.playbackOptions(q=True, max=True)

        h_scroll, zoom, expanded_names = self._save_viewport_state(widget)
        widget.clear()
        self._sync_header_settings(widget)

        if end <= start:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No valid playback range.")
            return

        discovered = self.sequencer._find_keyed_transforms(start, end)
        if not discovered:
            self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
            self._set_footer("No animated objects in scene.")
            return

        scene_shot = ShotBlock(
            shot_id=-1,
            name="Scene",
            start=start,
            end=end,
            objects=sorted(set(discovered)),
        )

        from mayatk.anim_utils.segment_keys import SegmentKeys

        valid = cmds.ls(scene_shot.objects, long=True) or []
        segments = SegmentKeys.collect_segments(
            valid,
            split_static=True,
            time_range=(start, end),
            ignore_holds=True,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=1e-3,
        )
        for seg in segments:
            seg["obj"] = str(seg["obj"])

        segments_by_shot = {scene_shot.shot_id: segments}
        all_objects = set(scene_shot.objects) | {seg["obj"] for seg in segments}

        track_ids = self._build_tracks(
            widget, all_objects, all_objects, active_shot=scene_shot
        )
        self._build_clips(widget, scene_shot, [scene_shot], segments_by_shot, track_ids)
        self._ensure_scene_attr_colors(widget)
        self._build_audio_tracks(widget, scene_shot, [scene_shot])

        current_time = cmds.currentTime(q=True)
        widget.set_playhead(current_time)
        widget.set_active_range(start, end)

        self._restore_viewport(widget, frame, h_scroll, zoom, expanded_names)
        n = len(scene_shot.objects)
        self._set_footer(
            f"Scene  {start:.0f}\u2013{end:.0f}  \u00b7  "
            f"{n} object{'s' if n != 1 else ''}"
        )

    def refresh(self) -> None:
        """Clear cached segments and rebuild the sequencer widget."""
        self._segment_cache.clear()
        self._sub_row_cache.clear()
        self._audio_segments_cache = None
        self._last_visible_key = None
        self._reconcile_needed = True
        self._sync_to_widget()

    # ---- _sync_to_widget helpers -----------------------------------------

    def _resolve_sync_target(self, shot_id=None):
        """Return ``(widget, shot)`` or ``(None, None)`` if unavailable."""
        widget = self._get_sequencer_widget()
        if widget is None or self.sequencer is None:
            return None, None

        if shot_id is None:
            shot_id = self.active_shot_id
        if shot_id is None:
            return None, None

        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return None, None
        return widget, shot

    def _save_viewport_state(self, widget):
        """Capture scroll, zoom, and expanded tracks for later restoration."""
        h_scroll = widget._timeline.horizontalScrollBar().value()
        zoom = widget._timeline.pixels_per_unit
        expanded_names = set()
        for tid in list(widget._expanded_tracks):
            td = widget.get_track(tid)
            if td is not None:
                expanded_names.add(td.name)
        return h_scroll, zoom, expanded_names

    def _rebuild_content(self, widget, shot, visible_shots) -> None:
        """Clear widget and rebuild tracks + clips from segments (expensive)."""
        # Suppress store-event → _sync_to_widget re-entrancy for the
        # entire rebuild.  Both reconciliation and auto-discovery may
        # call store.update_shot(); without this guard each call would
        # trigger a nested _sync_to_widget mid-build → duplicate tracks.
        # Restored, not cleared: a caller that rebuilds from inside its own
        # guard would otherwise have it dropped here, halfway through.
        was_syncing = self._syncing
        self._syncing = True
        try:
            widget.clear(keep_range_highlight=True)
            self._sub_row_cache.clear()
            self._sync_header_settings(widget)

            # Re-resolve any stale DAG paths (e.g. parent renamed) across
            # ALL shots before collecting segments so that global track sets
            # and segment caches never mix old and new paths.  Gated by a
            # dirty flag so pure shot-switches (which can't rename nodes)
            # don't pay the cmds.ls cost on every rebuild.
            if self._reconcile_needed:
                if self.sequencer.reconcile_all_shots():
                    self._segment_cache.clear()
                self._reconcile_needed = False

            segments_by_shot, all_objects = SegmentCollector.collect_segments(
                self.sequencer,
                shot,
                visible_shots,
                self._segment_cache,
                self._shifted_out_keys,
                self.logger,
            )

            # When "global" scope is active, expand the object set to include
            # every object across all shots so track positions never shift.
            if self._track_order_scope == "global":
                for s in self.sequencer.sorted_shots():
                    all_objects.update(s.objects)

            active_objects = SegmentCollector.active_object_set(shot, segments_by_shot)
            track_ids = self._build_tracks(
                widget, all_objects, active_objects, active_shot=shot
            )
            self._build_clips(widget, shot, visible_shots, segments_by_shot, track_ids)
            self._ensure_scene_attr_colors(widget)
            self._build_audio_tracks(widget, shot, visible_shots)
        finally:
            self._syncing = was_syncing

    def _rebuild_decoration(self, widget, shot, visible_shots) -> None:
        """Recreate overlays, markers, gap indicators, and active-shot tint."""
        try:
            current_time = cmds.currentTime(q=True) if cmds is not None else shot.start
        except Exception:
            current_time = shot.start
        widget.set_playhead(current_time)
        widget.set_hidden_tracks(sorted(self.sequencer.hidden_objects))
        widget.set_active_range(shot.start, shot.end)
        widget.set_range_highlight(shot.start, shot.end)

        # Populate the shot lane with all shots so the user always sees
        # the full shot structure (including gaps) regardless of display mode.
        all_sorted = self.sequencer.sorted_shots()
        store = self.sequencer.store
        shot_blocks = [
            {
                "id": s.shot_id,
                "name": s.name,
                "start": s.start,
                "end": s.end,
                "active": s.shot_id == shot.shot_id,
            }
            for s in all_sorted
        ]
        widget.set_shot_blocks(shot_blocks)

        for m in self.sequencer.markers:
            widget.add_marker(
                time=m["time"],
                note=m.get("note", ""),
                color=m.get("color"),
                draggable=m.get("draggable", True),
                style=m.get("style", "triangle"),
                line_style=m.get("line_style", "dashed"),
                opacity=m.get("opacity", 1.0),
            )

        # Gap overlays between ALL consecutive shots — they serve as
        # interactive handles the user can drag even when gap is zero.
        gap_count = 0
        for i in range(len(all_sorted) - 1):
            left = all_sorted[i]
            right = all_sorted[i + 1]
            gap_start = left.end
            gap_end = right.start
            gap_size = gap_end - gap_start
            if gap_size > -0.5:
                locked = store.is_gap_locked(left.shot_id, right.shot_id)
                widget.add_gap_overlay(gap_start, gap_end, locked=locked)
                gap_count += 1
        # The last shot has no following shot, so the loop above leaves it
        # with no drag handle at its end — the one shot in the timeline that
        # could not be resized like the others.  A zero-width tail overlay
        # supplies that handle; its left edge IS the shot's end, which
        # on_gap_left_resized already knows how to act on.
        if all_sorted:
            widget.add_gap_overlay(all_sorted[-1].end, all_sorted[-1].end, tail=True)
            # ...and the FIRST shot's start, which no gap precedes either.
            widget.add_gap_overlay(all_sorted[0].start, all_sorted[0].start, head=True)
        self.logger.debug(
            "Gap overlays: %d created across %d shots", gap_count, len(all_sorted)
        )

        # Gray tint over inactive shot regions so the active shot
        # stands out visually against the rest of the timeline.
        for s in all_sorted:
            if s.shot_id != shot.shot_id:
                widget.add_range_overlay(s.start, s.end, color="#000000", alpha=40)

    #: Set by the first :meth:`_restore_viewport`; see its docstring.
    _viewport_framed = False

    def _restore_viewport(self, widget, frame, h_scroll, zoom, expanded_names) -> None:
        """Restore scroll/zoom/expansion and trigger geometry recalculation.

        The FIRST restore always frames: there is no prior view to preserve
        on the first build, and the panel opening on frame 0 of a
        several-thousand-frame scene starts every session by hunting for the
        shot being worked on.  (``SequencerWidget.frame_on_first_show`` does
        the same at show time; whichever runs last frames the same range, so
        the two agree however the panel is brought up.)
        """
        frame = frame or not self._viewport_framed
        self._viewport_framed = True
        if frame:
            widget._timeline._refresh_all()
            widget.frame_shot()
        else:
            widget._timeline._pixels_per_unit = zoom
            widget._timeline._refresh_all()
            widget._timeline.horizontalScrollBar().setValue(h_scroll)

        widget.sub_row_provider = self._provide_sub_rows

        if expanded_names:
            for td in widget.tracks():
                if td.name in expanded_names:
                    widget.expand_track(td.track_id)

    def _sync_header_settings(self, widget) -> None:
        """Push header spinbox values and attribute colors to the widget."""
        spn_snap = getattr(self.ui, "spn_snap", None)
        if spn_snap is not None:
            widget.snap_interval = float(spn_snap.value())
        # Read on every rebuild so the widget also picks up the value the
        # checkbox restored from settings on load.
        chk_snap_keys = getattr(self.ui, "chk_snap_to_keys", None)
        if chk_snap_keys is not None:
            widget.snap_to_keys = bool(chk_snap_keys.isChecked())
        chk_overlay = getattr(self.ui, "chk_shortcut_overlay", None)
        if chk_overlay is not None:
            widget.shortcut_overlay_visible = bool(chk_overlay.isChecked())

        # QSettings.allKeys() is a disk-backed scan (~4ms each) — cache
        # the resolved color map and only rebuild when the color dialog
        # publishes a new one via btn_colors.
        if self._color_map_cache is None:
            from uitk.managers.settings_manager import SettingsManager

            color_settings = SettingsManager(
                namespace=AttributeColorDialog._SETTINGS_NS
            )
            color_map = dict(_DEFAULT_ATTRIBUTE_COLORS)
            for key in color_settings.keys():
                val = color_settings.value(key)
                if val:
                    color_map[key] = val
            self._color_map_cache = color_map
        widget.attribute_colors = self._color_map_cache

    # Palette for auto-assigning colors to scene-specific attributes
    # not present in the user's color map (e.g. custom/plugin attrs).
    _AUTO_PALETTE = [
        "#5B8BD4",
        "#6EBF6E",
        "#D4A65B",
        "#C45C5C",
        "#8E6FBF",
        "#5BBFB4",
        "#BF6E8E",
        "#8EB05B",
    ]

    def _ensure_scene_attr_colors(self, widget) -> None:
        """Auto-assign colors to scene attributes missing from the color map.

        Scans all clips for attribute names not yet in
        ``widget.attribute_colors`` and assigns each a deterministic
        color from ``_AUTO_PALETTE`` (hash-based so the same attribute
        always gets the same color).  The widget's live color map is
        updated in-place so that both ``ClipItem._resolve_color`` and
        ``_provide_sub_rows`` see the assignments.
        """
        if widget is None:
            return
        color_map = widget.attribute_colors
        changed = False
        from hashlib import md5

        for clip in widget._clips.values():
            for attr in clip.data.get("attributes", []):
                if attr not in color_map:
                    # Deterministic hash — same attribute always maps to
                    # the same palette slot (built-in hash() is randomized).
                    idx = int(md5(attr.encode()).hexdigest(), 16) % len(
                        self._AUTO_PALETTE
                    )
                    color_map[attr] = self._AUTO_PALETTE[idx]
                    changed = True
        if changed:
            widget.attribute_colors = color_map

    def _build_tracks(
        self, widget, all_objects, active_objects, active_shot=None
    ) -> dict:
        """Create one track per unique object and return ``{obj_name: track_id}``.

        Non-pinned objects that no longer exist in the scene are silently
        skipped.  Pinned objects (e.g. from a manifest) are kept with a
        'missing' icon so users can see them and re-import.
        """
        from mayatk.anim_utils.shots._shots import SHOT_PALETTE

        node_icons_cls = self._try_load_maya_icons()
        obj_classes = active_shot.classify_objects() if active_shot else {}
        track_ids: dict = {}
        _NOT_FOUND_COLOR = "#E0A0A0"
        if self._track_order_scope == "global":
            ordered = sorted(all_objects)
        else:
            sorted_active = sorted(o for o in all_objects if o in active_objects)
            sorted_inactive = sorted(o for o in all_objects if o not in active_objects)
            ordered = sorted_active + sorted_inactive

        # Batch existence check: one `cmds.ls` round-trip instead of N
        # `cmds.objExists` calls.  Scenes with many tracks hit this on
        # every rebuild.
        existing_set = set(cmds.ls(ordered, long=True) or []) if ordered else set()

        for obj_name in ordered:
            if self.sequencer.is_object_hidden(obj_name):
                continue
            exists = obj_name in existing_set
            # Skip missing objects unless they are pinned
            if not exists and not self.sequencer.store.is_object_pinned(obj_name):
                continue
            in_active = obj_name in active_objects
            icon = node_icons_cls.get_icon(obj_name) if node_icons_cls else None
            if not exists and icon is None:
                from uitk.managers.icon_manager import IconManager

                icon = IconManager.get("close", size=(16, 16), color=_NOT_FOUND_COLOR)
            color_kw: dict = {}
            status = obj_classes.get(obj_name, "valid")
            if status != "valid":
                pair = SHOT_PALETTE.get(status)
                if pair is not None:
                    fg, bg = pair[0], pair[1]
                    if bg:
                        color_kw["color"] = bg
                    if fg:
                        color_kw["text_color"] = fg
            tid = widget.add_track(
                CoreUtils.leaf_name(obj_name),
                icon=icon,
                dimmed=not in_active or not exists,
                italic=not in_active and exists,
                **color_kw,
            )
            track_ids[obj_name] = tid
        return track_ids

    def _build_clips(self, widget, shot, visible_shots, segments_by_shot, track_ids):
        """Add animation and stepped clips for each visible shot."""
        from mayatk.anim_utils.shots._shots import SHOT_PALETTE

        for vs in visible_shots:
            is_active = vs.shot_id == shot.shot_id
            segs = segments_by_shot[vs.shot_id]
            obj_classes = vs.classify_objects()

            by_obj: dict = defaultdict(list)
            for seg in segs:
                by_obj[seg["obj"]].append(seg)

            store = self.sequencer.store if self.sequencer else None

            for obj_name in sorted(set(vs.objects) | set(by_obj)):
                if self.sequencer.is_object_hidden(obj_name):
                    continue
                tid = track_ids.get(obj_name)
                if tid is None:
                    continue
                obj_segs = by_obj.get(obj_name, [])
                if not obj_segs:
                    continue

                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}
                elif store and obj_name in store.locked_objects:
                    extra = {"locked": True}
                status = obj_classes.get(obj_name, "valid")
                if status != "valid":
                    pair = SHOT_PALETTE.get(status)
                    if pair is not None:
                        fg = pair[0]
                        if fg:
                            extra["status_color"] = fg

                # Merge adjacent segments separated only by flat-key
                # gaps so the main track shows fewer, larger clips.
                # Stepped (zero-duration) segments are kept separate — they
                # are point events and must not be absorbed into spans.
                gap = store.detection_threshold if store else 10.0
                span_segs = [sg for sg in obj_segs if not sg.get("is_stepped")]
                stepped_segs = [sg for sg in obj_segs if sg.get("is_stepped")]

                span_segs.sort(key=lambda sg: sg["start"])
                merged: list = []
                if span_segs:
                    merged.append(
                        {
                            "start": span_segs[0]["start"],
                            "end": span_segs[0]["end"],
                            "segs": [span_segs[0]],
                        }
                    )
                    for seg in span_segs[1:]:
                        if seg["start"] <= merged[-1]["end"] + gap:
                            merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                            merged[-1]["segs"].append(seg)
                        else:
                            merged.append(
                                {
                                    "start": seg["start"],
                                    "end": seg["end"],
                                    "segs": [seg],
                                }
                            )

                for m in merged:
                    s = m["start"]
                    e = m["end"]
                    attrs = SegmentCollector.extract_attributes(m["segs"])
                    clip_extra = dict(extra)
                    if is_active and attrs:
                        clip_extra["label_center"] = Attributes.abbreviate_attrs(attrs)
                    widget.add_clip(
                        track_id=tid,
                        start=s,
                        duration=e - s,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=s,
                        orig_end=e,
                        attributes=attrs,
                        **clip_extra,
                    )

                # Add stepped (zero-duration) clips individually
                for seg in stepped_segs:
                    t = seg["start"]
                    # Skip stepped keys that fall inside a merged span —
                    # the span clip already covers that time.
                    if any(m["start"] <= t <= m["end"] for m in merged):
                        self.logger.debug(
                            "[SYNC]   stepped key at %s inside span — skipped",
                            t,
                        )
                        continue
                    clip_extra = dict(extra)
                    widget.add_clip(
                        track_id=tid,
                        start=t,
                        duration=0.0,
                        label="",
                        shot_id=vs.shot_id,
                        obj=obj_name,
                        orig_start=t,
                        orig_end=t,
                        is_stepped=True,
                        stepped_key_time=t,
                        **clip_extra,
                    )

    def _build_audio_tracks(self, widget, shot, visible_shots) -> None:
        """Add audio tracks and clips for visible shots.

        Iterates segments produced by the unified audio system
        (``mayatk.audio_utils.segments``).  Each canonical
        ``track_id`` becomes one widget track; segments are keyed into
        the sequencer with ``audio_track_id`` for downstream consumers.
        """
        scene_start = min(vs.start for vs in visible_shots)
        scene_end = max(vs.end for vs in visible_shots)
        # Audio discovery hammers maya.cmds.keyframe / attributeQuery
        # (~28ms per rebuild on a busy carrier).  Segments only change
        # on audio edits, not on shot-switches — cache by range.
        cache_key = (scene_start, scene_end)
        cached = self._audio_segments_cache
        if cached is not None and cached[0] == cache_key:
            segs = cached[1]
        else:
            segs = AudioSegment.collect_all_segments(
                scene_start=scene_start,
                scene_end=scene_end,
                include_waveform=True,
            )
            self._audio_segments_cache = (cache_key, segs)

        # Group by canonical track_id.
        by_track: dict = defaultdict(list)
        for seg in segs:
            by_track[seg.track_id].append(seg)

        node_icons_cls = self._try_load_maya_icons()

        for track_id, track_segs in by_track.items():
            if self.sequencer.is_object_hidden(track_id):
                continue

            # Pre-compute visible clip descriptors; skip the track
            # entirely if no segment strictly overlaps any visible shot.
            clip_descs: list = []
            for seg in track_segs:
                for vs in visible_shots:
                    vis_start = max(seg.start, vs.start)
                    vis_end = min(seg.end, vs.end)
                    if vis_end <= vis_start:
                        continue
                    clip_descs.append((seg, vs, vis_start, vis_end))

            if not clip_descs:
                continue

            # Track icon: look up DG node if one exists (rendered view).
            dg_node = audio_utils.find_dg_node_for_track(track_id)
            icon = (
                node_icons_cls.get_icon(dg_node)
                if (node_icons_cls and dg_node)
                else None
            )
            widget_track_id = widget.add_track(track_id, icon=icon)

            for seg, vs, vis_start, vis_end in clip_descs:
                is_active = vs.shot_id == shot.shot_id

                full_waveform = seg.waveform or []
                full_dur = seg.end - seg.start
                if full_waveform and full_dur > 0:
                    n = len(full_waveform)
                    frac_lo = (vis_start - seg.start) / full_dur
                    frac_hi = (vis_end - seg.start) / full_dur
                    i_lo = int(frac_lo * n)
                    i_hi = max(i_lo + 1, int(frac_hi * n))
                    vis_waveform = full_waveform[i_lo:i_hi]
                else:
                    vis_waveform = full_waveform

                extra: dict = {}
                if not is_active:
                    extra = {"locked": True, "read_only": True, "dimmed": True}

                widget.add_clip(
                    track_id=widget_track_id,
                    start=vis_start,
                    duration=vis_end - vis_start,
                    label=seg.label or track_id,
                    color="#3A7D44",
                    is_audio=True,
                    audio_track_id=seg.track_id,
                    file_path=seg.file_path,
                    waveform=vis_waveform,
                    orig_start=seg.start,
                    orig_end=seg.end,
                    shot_id=vs.shot_id,
                    **extra,
                )

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

        Also opens the Graph Editor so the selected object's animation
        curves are immediately visible.
        """
        if not clip_ids or cmds is None or self._syncing:
            return
        widget = self._get_sequencer_widget()
        if widget is None:
            return

        resolved = []
        clip_labels = []
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
                if not attrs:
                    attr_name = clip.data.get("attr_name")
                    if attr_name:
                        attrs = [attr_name]
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
        if clip_labels:
            self._set_footer("  |  ".join(clip_labels[:3]))
            if len(clip_labels) > 3:
                self._set_footer(
                    "  |  ".join(clip_labels[:3]) + f"  (+{len(clip_labels) - 3} more)"
                )

    def on_track_selected(self, track_names: list) -> None:
        """Select Maya objects when track labels are clicked in the header."""
        if not track_names or cmds is None:
            return
        resolved = []
        for name in track_names:
            full = self._resolve_full_name(name)
            if cmds.objExists(full):
                resolved.append(full)
        self._select_and_show(resolved)

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

    def _on_frame_on_shot_change_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.frame_on_shot_change = checked
        self.sequencer.store.mark_dirty()

    def _on_select_on_load_toggled(self, checked: bool) -> None:
        if self.sequencer is None:
            return
        self.sequencer.store.select_on_load = checked
        self.sequencer.store.mark_dirty()

    def _set_show_internal_holds(self, enabled: bool) -> None:
        """Toggle flat-key span visibility in attribute sub-rows."""
        self._show_internal_holds = enabled
        self._sub_row_cache.clear()
        self._sync_to_widget()

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
        if resolved:
            # A view mirror, not an edit -- same guard as _select_and_show.
            with CoreUtils.undo_disabled():
                cmds.select(resolved, replace=True)
        try:
            mel.eval("SpreadSheetEditor")
        except Exception:
            pass

    def _select_and_show(self, objects: list) -> None:
        """Select the given Maya objects and open the Graph Editor.

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
        try:
            mel.eval("GraphEditor")
        except Exception:
            pass

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

        from uitk.widgets.sequencer._keyframe import KeyframeItem

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
                        # Count a time only when at least one cutKey succeeded
                        # — otherwise an all-failed pass still reports "Deleted
                        # N keys" and triggers the state resync for nothing.
                        cut_ok = False
                        for crv in curves:
                            try:
                                cmds.cutKey(str(crv), time=(t, t), clear=True)
                                cut_ok = True
                            except Exception:
                                self.logger.debug(
                                    "_delete_selected_clip_keys: cutKey failed for '%s'.",
                                    crv,
                                    exc_info=True,
                                )
                        if cut_ok:
                            deleted += 1

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

    def _get_sequencer_widget(self):
        """Return the SequencerWidget from the UI."""
        return getattr(self.ui, "sequencer_widget", None)

    def _provide_sub_rows(self, track_id, track_name):
        """Return per-attribute sub-row data for a track.

        Called by the widget's ``sub_row_provider`` protocol when a user
        double-clicks a header label to expand a track.

        Uses the same ``SegmentKeys.collect_segments`` pipeline as the
        object row so that hold absorption, hold-only synthesis, and
        motion detection are consistent between both views.

        Returns
        -------
        list
            ``[(attr_name, [(start, dur, label, color, extra), ...]), ...]``
            where *extra* is a dict of kwargs passed through to ``add_clip``.
        """
        if self.sequencer is None or cmds is None:
            return []

        shot_id = self.active_shot_id
        if shot_id is None:
            return []
        shot = self.sequencer.shot_by_id(shot_id)
        if shot is None:
            return []

        obj_name = self._resolve_full_name(track_name)

        # Return cached result if available
        cache_key = (shot_id, track_name)
        cached = self._sub_row_cache.get(cache_key)
        if cached is not None:
            return cached
        # Resolve to long DAG path to avoid ambiguous short-name errors
        long_names = cmds.ls(obj_name, long=True)
        if not long_names:
            return []
        obj_name = long_names[0]

        from mayatk.anim_utils.segment_keys import SegmentKeys

        all_curves = (
            cmds.listConnections(obj_name, type="animCurve", s=True, d=False) or []
        )
        if not all_curves:
            return []

        widget = self._get_sequencer_widget()
        color_map = widget.attribute_colors if widget else {}
        show_holds = self._show_internal_holds

        # Discover this object's animated attributes in one pass, mapping
        # each to its animCurve node (first curve wins) — the names drive
        # the per-attribute iteration below, the curves feed the
        # full-range background previews.  Per-attribute curve filtering
        # is handled by collect_segments via channel_box_attrs.
        attr_to_curve: dict = {}
        for curve in all_curves:
            try:
                conns = (
                    cmds.listConnections(
                        str(curve), plugs=True, destination=True, source=False
                    )
                    or []
                )
                for conn in conns:
                    if "." in conn:
                        attr_to_curve.setdefault(conn.rsplit(".", 1)[-1], curve)
            except Exception:
                continue
        attr_names = set(attr_to_curve)

        store = self.sequencer.store if self.sequencer else None
        is_obj_locked = bool(store and obj_name in store.locked_objects)

        # Determine the visible time range for the full-range curve based
        # on the current display mode.
        visible = self._visible_shots(shot)
        curve_range_start = min(s.start for s in visible)
        curve_range_end = max(s.end for s in visible)

        result = []
        for attr_name in sorted(attr_names):
            # Reuse the same collect_segments pipeline as the object row.
            # channel_box_attrs filters to just this attribute's curves.
            segs = SegmentKeys.collect_segments(
                [obj_name],
                split_static=True,
                channel_box_attrs=[attr_name],
                ignore_holds=not show_holds,
                ignore_visibility_holds=True,
                motion_only=True,
                motion_rate=1e-3,
                time_range=(shot.start, shot.end),
            )

            if not segs:
                continue

            # Determine which segments are pure holds by comparing
            # against active-only results.  A segment is a pure hold
            # only when it has zero overlap with any active span.
            # Motion-extended segments (motion + trailing hold) keep
            # normal styling since they contain real motion.
            hold_ranges: set = set()
            if show_holds:
                active_segs = SegmentKeys.collect_segments(
                    [obj_name],
                    split_static=True,
                    channel_box_attrs=[attr_name],
                    ignore_holds=True,
                    ignore_visibility_holds=True,
                    motion_only=True,
                    motion_rate=1e-3,
                    time_range=(shot.start, shot.end),
                )
                active_spans = [(s["start"], s["end"]) for s in active_segs]
                for seg in segs:
                    ss, se = seg["start"], seg["end"]
                    # Pure hold: no overlap with any active span
                    if not any(a_s < se and a_e > ss for a_s, a_e in active_spans):
                        hold_ranges.add((ss, se))

            color = color_map.get(attr_name)
            segments = []
            for seg in segs:
                s, e = seg["start"], seg["end"]
                dur = e - s
                is_hold = (s, e) in hold_ranges

                # Build curve preview from the segment's own curves
                preview = None
                for crv in seg.get("curves", []):
                    preview = SegmentCollector.build_curve_preview(crv, s, e)
                    if preview:
                        break
                extra = {
                    "obj": obj_name,
                    "attr_name": attr_name,
                    "shot_id": shot_id,
                    "orig_start": s,
                    "orig_end": e,
                }
                if preview:
                    extra["curve_preview"] = preview
                if is_hold:
                    extra["is_hold"] = True
                if is_obj_locked:
                    extra["locked"] = True
                segments.append((s, dur, attr_name, color, extra))
            result.append((attr_name, segments))

        # Push full-range background curve previews to the widget for each
        # attribute sub-row.  These are static reference lines painted in
        # drawBackground — no interaction, no updates during drag.
        if widget is not None:
            for attr_name, _ in result:
                crv = attr_to_curve.get(attr_name)
                if crv is None:
                    continue
                bg_preview = SegmentCollector.build_curve_preview(
                    crv, curve_range_start, curve_range_end
                )
                hex_color = color_map.get(attr_name, "#CCCCCC")
                widget.set_bg_curve_preview(
                    track_id, attr_name, bg_preview, color=hex_color or "#CCCCCC"
                )

        self._sub_row_cache[cache_key] = result
        return result

    # ---- signal handlers (clip motion in _clip_motion.py) ----------------

    def on_clip_renamed(self, clip_id: int, new_label: str) -> None:
        """Handle inline rename — currently a no-op (shot clips removed)."""
        pass

    def on_playhead_moved(self, frame: float) -> None:
        """Sync the Maya playhead to the widget playhead.

        Audio scrub is handled by the widget's own :class:`ScrubPlayer`
        (bound via :meth:`_ensure_sound_on_timeline`); this method only
        needs to mirror the Maya time value.
        """
        self._syncing_playhead = True
        try:
            self._ensure_sound_on_timeline()
            # Undo-disabled like every other view mirror here: scrubbing is
            # not an edit, and ``cmds.currentTime`` IS undoable (measured at
            # 2 presses to reach past it), so a drag would otherwise bury the
            # user's edits one Ctrl+Z per playhead move and leave the queue
            # top owned by a scrub -- which ``_undo_plan``'s marker test then
            # reads as "an unrelated edit followed ours".
            with CoreUtils.undo_disabled():
                cmds.currentTime(frame, update=True)
        finally:
            self._syncing_playhead = False

    def _ensure_sound_on_timeline(self) -> None:
        """Bind the composite audio to both Maya's time slider and the
        sequencer widget's :class:`ScrubPlayer`.

        Maya's Time Slider handles playback/loop audio; the widget's
        scrub player handles drag-scrub (since ``cmds.currentTime(
        update=True)`` does not emit audio).  Both are refreshed together
        so they stay in lockstep.
        """
        cached = getattr(self, "_active_sound", None)
        if cached and cmds.objExists(cached):
            node = cached
        else:
            node = self._resolve_preferred_audio_node()
            if not node:
                self._active_sound = ""
                return
            try:
                slider = mel.eval("$tmp = $gPlayBackSlider")
                cmds.timeControl(slider, e=True, sound=node, displaySound=True)
            except Exception:
                pass
            self._active_sound = node

        # Push the bound node's WAV into the widget's ScrubPlayer.  Works
        # for both the composite node *and* a per-track DG node —
        # whichever the Time Slider ended up bound to.
        if getattr(self, "_bound_audio_node", None) == node:
            return  # path already in sync with current node
        wav_path = self._get_bound_audio_wav(node)
        if not wav_path:
            return
        widget = self._get_sequencer_widget()
        set_audio = getattr(widget, "set_audio_source", None)
        if set_audio is None:
            return
        if set_audio(wav_path, audio_utils.get_fps()):
            self._bound_audio_node = node

    @staticmethod
    def _resolve_preferred_audio_node() -> str:
        """Return the composite DG audio node name, else the first per-track
        DG node, else empty string."""
        try:
            from mayatk.audio_utils.audio_clips._audio_clips import AudioClips

            comp = AudioClips._find_composite_node()
            if comp and cmds.objExists(comp):
                return comp
        except Exception:
            pass
        for track_id in audio_utils.list_tracks():
            dg = audio_utils.find_dg_node_for_track(track_id)
            if dg and cmds.objExists(dg):
                return dg
        return ""

    # ---- Transport controls (footer) -------------------------------------

    #: Button edge of the footer transport, in pixels.  Sized so the glyphs
    #: land on the 16px icon grid the rest of uitk draws on (icons are 0.7 of
    #: the button) -- at the old 20px the transport rendered 14px glyphs, a
    #: half-step off every other icon in the panel and small for a control
    #: that gets clicked constantly.
    TRANSPORT_BUTTON_HEIGHT = 23

    def _setup_transport_controls(self) -> None:
        """Install the reusable :class:`TransportControls` row on the
        RIGHT of the footer, wired to a Maya :class:`PlayController`.

        Frame/key/go-to actions interrupt playback by default (see
        :attr:`TransportControls.interrupt_mode`).  Playhead navigation
        goes through the :class:`SequencerWidget` so scrub audio fires
        via ``playhead_moved``.
        """
        footer = getattr(self.ui, "footer", None)
        if footer is None:
            return

        # Key the rebuild guard off the persistent footer, not this
        # controller's own attr: a slots re-init builds a NEW controller
        # whose _transport_controls is always None, so a per-controller guard
        # never trips and attach_to_footer (append-only) would stack a
        # duplicate row plus a second _MayaPlayController on every reopen.
        existing = getattr(footer, "_shot_transport_controls", None)
        if existing is not None:
            # Re-init over a live UI: adopt the existing row and repoint its
            # playback AND its range provider at this controller — range_fn
            # is an instance method now, and the constructor binding would
            # otherwise keep reading (and keep alive) the retired
            # controller's stale sequencer/mode state.
            existing.set_play_controller(_MayaPlayController(self))
            existing.set_range_fn(self._playback_range)
            self._transport_controls = existing
            return

        widget = self._get_sequencer_widget()
        if widget is None:
            return

        from uitk.widgets.sequencer import TransportControls

        pc = _MayaPlayController(self)
        # The footer grows to fit a taller child (Footer.add_widget), so this
        # is a floor, not a ceiling -- never shrink to the footer's height.
        h = max(footer.height(), self.TRANSPORT_BUTTON_HEIGHT)
        transport = TransportControls(
            sequencer=widget,
            play_controller=pc,
            parent=footer,
            button_height=h,
            interrupt_mode=TransportControls.INTERRUPT_STOP,
            range_fn=self._playback_range,
            button_names=(
                "go_to_start",
                "prev_key",
                "play_back",
                "play_forward",
                "next_key",
                "go_to_end",
            ),
        )
        transport.attach_to_footer(footer, side="right")
        self._transport_controls = transport
        footer._shot_transport_controls = transport

        # Prime the audio binding now so the first scrub produces
        # sound — the widget's built-in audio slot runs before the
        # controller's ``on_playhead_moved``, so without this the first
        # drag fires into an unsourced player.
        try:
            self._ensure_sound_on_timeline()
        except Exception:
            pass

    def _playback_range(self) -> tuple:
        """Range the transport's go-to-start / go-to-end buttons target.

        The ACTIVE SHOT wins over Maya's playback range.  Reading Maya's
        range made the two buttons skip the current shot's own boundaries
        whenever the range covered more than that shot — which it does in
        the "adjacent" and "all" view modes, and whenever the playback-range
        mode is "off".  An empty shot has no clips to fall back on, so it
        was the case where the skip was total.

        Falls back to Maya's playback range when no shot is selected.
        """
        if self.sequencer is not None:
            sid = self.active_shot_id
            shot = self.sequencer.shot_by_id(sid) if sid is not None else None
            if shot is not None and shot.end > shot.start:
                return float(shot.start), float(shot.end)
        try:
            lo = float(cmds.playbackOptions(q=True, min=True))
            hi = float(cmds.playbackOptions(q=True, max=True))
        except Exception:
            lo, hi = 1.0, 120.0
        return lo, hi

    @staticmethod
    def _get_bound_audio_wav(node: str) -> str:
        """Return the WAV path stored on *node* (composite or per-track).

        Both the composite DG audio node and Maya's per-track audio nodes
        expose a ``.filename`` attr pointing at an on-disk WAV, so the
        same accessor works for either — letting the widget's scrub
        player fall back to a per-track preview when no composite yet
        exists.
        """
        if not node:
            return ""
        try:
            path = cmds.getAttr(f"{node}.filename") or ""
            return path.replace("\\", "/")
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Play controller (Maya)
# ---------------------------------------------------------------------------


class _MayaPlayController:
    """:class:`PlayController` adapter driving Maya's timeline via ``cmds.play``.

    Ensures audio is bound to the Time Slider before starting playback.
    Tracks direction so ``TransportControls`` can resume the right way.
    """

    def __init__(self, controller: "ShotSequencerController"):
        self._ctl = controller
        self._forward = True

    def is_playing(self) -> bool:
        try:
            return bool(cmds.play(q=True, state=True))
        except Exception:
            return False

    def play(self, forward: bool) -> None:
        self._forward = bool(forward)
        try:
            self._ctl._ensure_sound_on_timeline()
        except Exception:
            pass
        try:
            if self.is_playing():
                cmds.play(state=False)
            cmds.play(forward=bool(forward))
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self.is_playing():
                cmds.play(state=False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shot Edit Dialog
# ---------------------------------------------------------------------------


class ShotEditDialog:
    """Lightweight dialog for creating or editing a shot.

    Uses plain Qt widgets — no dependency on uitk beyond the parent.
    Returns ``(name, start, end, description)`` on accept, ``None`` on cancel.
    """

    @staticmethod
    def show(
        parent=None,
        name: str = "",
        start: float = 1.0,
        end: float = 100.0,
        description: str = "",
        title: str = "Shot",
    ):
        """Show a modal dialog and return the result tuple or ``None``."""
        dlg = QtWidgets.QDialog(parent)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(280)

        layout = QtWidgets.QFormLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)

        name_edit = QtWidgets.QLineEdit(name)
        name_edit.setPlaceholderText("Shot name")
        layout.addRow("Name:", name_edit)

        start_spin = QtWidgets.QDoubleSpinBox()
        start_spin.setDecimals(1)
        start_spin.setRange(-1e6, 1e6)
        start_spin.setValue(start)
        layout.addRow("Start:", start_spin)

        end_spin = QtWidgets.QDoubleSpinBox()
        end_spin.setDecimals(1)
        end_spin.setRange(-1e6, 1e6)
        end_spin.setValue(end)
        layout.addRow("End:", end_spin)

        desc_edit = QtWidgets.QLineEdit(description)
        desc_edit.setPlaceholderText("Optional description")
        layout.addRow("Description:", desc_edit)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return None

        return (
            name_edit.text().strip() or "Shot",
            start_spin.value(),
            end_spin.value(),
            desc_edit.text().strip(),
        )


class ShotSequencerSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shot_sequencer

        # The shot dropdown mirrors the scene's shots (repopulated by
        # _sync_combobox each sync), never a persisted UI value — opt out of
        # cross-session index restore, or a stale index auto-selects (and
        # fires cmb_shot on) the wrong shot next session. Mirrors
        # ShotsController's cmb_shot_select opt-out.
        cmb_shot = getattr(self.ui, "cmb_shot", None)
        if cmb_shot is not None:
            cmb_shot.restore_state = False

        # Create controller. A slots re-init over the same still-alive loaded
        # UI does NOT destroy self.ui, so the previous controller's
        # remove_callbacks() (wired only to ui.destroyed at __init__) never
        # runs — its Maya OM callbacks, ShotStore listener, and the
        # class-global invalidation listener would otherwise accumulate
        # one-per-reopen (bound methods of distinct instances never dedup),
        # leaving orphaned controllers that rebuild the widget N times per
        # event. remove_callbacks() is idempotent, so the old controller's
        # still-connected ui.destroyed lambda stays harmless.
        prev_controller = getattr(self.ui, "_shot_seq_controller", None)
        if prev_controller is not None:
            try:
                prev_controller.remove_callbacks()
            except Exception:
                self.logger.debug("prev controller teardown failed", exc_info=True)
        self.controller = ShotSequencerController(self)
        self.ui._shot_seq_controller = self.controller

        # SequencerWidget is promoted directly in the .ui file.
        # When loaded outside tentacle (deferred promotion), the widget
        # may still be the placeholder QSplitter — skip signal wiring.
        sequencer = self.controller._get_sequencer_widget()
        if sequencer is not None and hasattr(sequencer, "clip_resized"):
            sequencer.window_shortcuts = True

            # (widget signal, controller slot name) wiring table.  The
            # live connections are recorded on the widget so that a
            # slots re-init over the same loaded UI disconnects the
            # PREVIOUS controller first — without this both controllers
            # stay connected and every signal double-fires (two
            # cmds.undo() per Ctrl+Z, duplicate markers, ...).
            wiring = [
                ("clip_resized", "on_clip_resized"),
                ("clip_moved", "on_clip_moved"),
                ("clips_batch_moved", "on_clips_batch_moved"),
                ("clip_renamed", "on_clip_renamed"),
                ("playhead_moved", "on_playhead_moved"),
                ("track_hidden", "hide_track"),
                ("track_shown", "show_track"),
                ("track_deleted", "delete_track"),
                ("selection_changed", "on_selection_changed"),
                ("track_selected", "on_track_selected"),
                ("track_menu_requested", "on_track_menu"),
                ("clip_locked", "on_clip_locked"),
                ("undo_requested", "on_undo"),
                ("redo_requested", "on_redo"),
                ("marker_added", "on_marker_added"),
                ("marker_moved", "on_marker_moved"),
                ("marker_changed", "on_marker_changed"),
                ("marker_removed", "on_marker_removed"),
                ("gap_resized", "on_gap_resized"),
                ("gap_left_resized", "on_gap_left_resized"),
                ("gap_moved", "on_gap_moved"),
                ("gap_lock_changed", "on_gap_lock_changed"),
                ("gap_lock_all_requested", "on_gap_lock_all"),
                ("gap_unlock_all_requested", "on_gap_unlock_all"),
                ("clip_menu_requested", "on_clip_menu"),
                ("gap_menu_requested", "on_gap_menu"),
                ("range_highlight_changed", "on_range_highlight_changed"),
                ("zone_context_menu_requested", "on_zone_context_menu"),
                ("shot_switch_requested", "_on_shot_switch_requested"),
                ("header_menu_requested", "on_header_menu"),
                ("keys_moved", "on_keys_moved"),
                ("keys_batch_moved", "on_keys_batch_moved"),
                ("keys_deleted", "on_keys_deleted"),
                ("key_selection_changed", "on_key_selection_changed"),
                ("key_menu_requested", "on_key_menu"),
                ("key_tangent_dragged", "on_key_tangent_dragged"),
            ]
            for sig_name, slot in getattr(sequencer, "_slots_connections", []):
                try:
                    getattr(sequencer, sig_name).disconnect(slot)
                except (RuntimeError, TypeError):
                    pass  # connection already died with the old controller
            connections = []
            for sig_name, slot_name in wiring:
                # Guarded on BOTH sides (mirrors blendertk): a uitk that
                # predates a signal must degrade that one connection, not
                # kill the whole panel init — but never silently.
                signal = getattr(sequencer, sig_name, None)
                slot = getattr(self.controller, slot_name, None)
                if signal is None or slot is None:
                    self.logger.warning(
                        "sequencer wiring skipped: %s -> %s (signal or slot "
                        "missing - uitk version mismatch?)",
                        sig_name,
                        slot_name,
                    )
                    continue
                signal.connect(slot)
                connections.append((sig_name, slot))
            sequencer._slots_connections = connections
            sequencer._zone_menu_connected = True

            # The panel's own key bindings.  ``add_shortcut`` disposes and
            # replaces a same-sequence binding, so a slots re-init over the
            # same loaded UI re-points them at the new controller instead of
            # stacking a second one.  WindowShortcut context: Qt claims the key
            # at the window level and Maya never sees it -- which is also why
            # the panel binds these itself, the host's own hotkeys never
            # reaching a focused Qt tool window.
            _ctx = (
                QtCore.Qt.WindowShortcut
                if sequencer.window_shortcuts
                else QtCore.Qt.WidgetWithChildrenShortcut
            )
            for _key, _action, _desc in (
                (
                    "Delete",
                    self.controller._delete_selected_clip_keys,
                    "Delete keys for selected clips",
                ),
                (
                    "Ctrl+C",
                    self.controller._copy_keys_shortcut,
                    "Copy the selected keys",
                ),
                (
                    "Ctrl+V",
                    self.controller._paste_keys_shortcut,
                    "Paste copied keys at the playhead",
                ),
            ):
                sequencer._shortcut_mgr.add_shortcut(_key, _action, _desc, _ctx)
        self._setup_shot_nav()
        self.controller._setup_transport_controls()

        # Initial population so gaps and clips are visible immediately.
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _setup_shot_nav(self) -> None:
        """Configure prev/next option box actions on cmb_shot.

        Every callback is late-bound through ``cmb._nav_controller`` /
        ``cmb._nav_slots`` so a slots re-init over the same loaded UI
        only repoints those attributes — the option-box actions and
        menu connects are created exactly once and never duplicated
        (the old per-controller ``_prev_action`` guard never tripped on
        a fresh controller, stacking duplicates on the widget).
        """
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None or not hasattr(cmb, "option_box"):
            return

        cmb._nav_controller = self.controller
        cmb._nav_slots = self
        # Before the re-init early return: the cells are the controller's
        # (a fresh one on every re-init), the option boxes are the widget's.
        self.controller._configure_shot_combobox(cmb)

        _VIEW_MODE_MAP = {0: "current", 1: "adjacent", 2: "all"}
        existing = getattr(cmb, "_shot_nav_options", None)
        if existing is not None:
            # Re-init: adopt the already-built options for this controller.
            ctl = self.controller
            ctl._prev_action = existing["prev"]
            ctl._next_action = existing["next"]
            ctl._view_mode_action = existing["view"]
            ctl._holds_action = existing["holds"]
            ctl._show_internal_holds = existing["holds"].current_state == 1
            ctl._shot_display_mode = _VIEW_MODE_MAP.get(
                existing["view"].current_state, "current"
            )
            ctl._cmb_mode_widget = getattr(self.ui, "cmb_mode", None)
            return

        from uitk.widgets.optionBox.options.action import ActionOption

        prev_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: cmb._nav_controller._navigate_shot(-1),
            icon="chevron_left",
            tooltip="Previous Shot",
            order=0,
        )
        next_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: cmb._nav_controller._navigate_shot(1),
            icon="chevron_right",
            tooltip="Next Shot",
            order=1,
        )

        # View mode cycle: Current → Adjacent → All
        _VIEW_STATES = [
            {
                "icon": "target",
                "tooltip": "View: Current Shot (click for adjacent)",
                "callback": lambda: cmb._nav_controller._set_view_mode("adjacent"),
            },
            {
                "icon": "columns",
                "tooltip": "View: Adjacent Shots (click for all)",
                "callback": lambda: cmb._nav_controller._set_view_mode("all"),
            },
            {
                "icon": "grid",
                "tooltip": "View: All Shots (click for current)",
                "callback": lambda: cmb._nav_controller._set_view_mode("current"),
            },
        ]
        view_opt = ActionOption(
            wrapped_widget=cmb,
            states=_VIEW_STATES,
            order=4,
        )

        cmb.option_box.set_order(["action"])
        cmb.option_box.add_option(prev_opt)
        cmb.option_box.add_option(next_opt)
        cmb.option_box.add_option(view_opt)

        # "+" button — one-click shot creation
        add_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: cmb._nav_controller._create_shot_one_click(),
            icon="add",
            tooltip="New Shot",
            order=2,
        )
        cmb.option_box.add_option(add_opt)

        # Refresh button — re-collect animation data and rebuild widget
        refresh_opt = ActionOption(
            wrapped_widget=cmb,
            callback=lambda: cmb._nav_controller.refresh(),
            icon="refresh",
            tooltip="Refresh Sequencer",
            order=6,
        )
        cmb.option_box.add_option(refresh_opt)

        # Show Internal Holds toggle (two-state: off / on)
        _HOLD_STATES = [
            {
                "icon": "eye_off",
                "tooltip": "Show Internal Holds (off)\nClick to reveal flat-key spans in sub-rows",
                "callback": lambda: cmb._nav_controller._set_show_internal_holds(True),
            },
            {
                "icon": "eye",
                "tooltip": "Show Internal Holds (on)\nClick to hide flat-key spans in sub-rows",
                "callback": lambda: cmb._nav_controller._set_show_internal_holds(False),
            },
        ]
        holds_opt = ActionOption(
            wrapped_widget=cmb,
            states=_HOLD_STATES,
            order=5,
            settings_key="shot_sequencer_show_holds",
        )
        cmb.option_box.add_option(holds_opt)
        # Sync controller state from persisted option state
        self.controller._show_internal_holds = holds_opt.current_state == 1
        self.controller._holds_action = holds_opt

        self.controller._prev_action = prev_opt
        self.controller._next_action = next_opt
        self.controller._view_mode_action = view_opt
        # Sync controller view mode from persisted button state
        self.controller._shot_display_mode = _VIEW_MODE_MAP.get(
            view_opt.current_state, "current"
        )

        cmb._shot_nav_options = {
            "prev": prev_opt,
            "next": next_opt,
            "view": view_opt,
            "holds": holds_opt,
        }

        # Install right-click context menu on the combobox
        from qtpy import QtCore

        cmb.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        cmb.customContextMenuRequested.connect(
            lambda pos: cmb._nav_slots._cmb_context_menu(pos)
        )

        # Wire the mode selector combobox (Shots / Markers)
        cmb_mode = getattr(self.ui, "cmb_mode", None)
        if cmb_mode is not None:
            cmb_mode.blockSignals(True)
            cmb_mode.clear()
            cmb_mode.addItem("Shots:", "shots")
            cmb_mode.addItem("Markers:", "markers")
            cmb_mode.setCurrentIndex(0)
            cmb_mode.blockSignals(False)
            cmb_mode.currentIndexChanged.connect(
                lambda i: cmb._nav_slots._on_cmb_mode_changed(i)
            )
            self.controller._cmb_mode_widget = cmb_mode

    def _on_shortcut_overlay_toggled(self, checked: bool) -> None:
        """Show or hide the corner legend of gestures and keys."""
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget.shortcut_overlay_visible = bool(checked)

    def _on_snap_to_keys_toggled(self, checked: bool) -> None:
        """Turn the opt-in pull onto existing key frames on or off.

        The alignment guides are unconditional -- this only decides whether
        the drag is also captured by the frames they mark.
        """
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget.snap_to_keys = bool(checked)

    def _on_playback_range_changed(self, index: int) -> None:
        """Handle playback-range combobox selection."""
        cmb_pb = getattr(self.ui, "cmb_playback_range", None)
        if cmb_pb is None:
            return
        mode = cmb_pb.itemData(index)
        if mode:
            self.controller._set_playback_range_mode(mode)

    def _on_cmb_mode_changed(self, index: int) -> None:
        """Handle the Shots/Markers mode selector combobox."""
        cmb_mode = getattr(self.ui, "cmb_mode", None)
        if cmb_mode is None:
            return
        mode = cmb_mode.itemData(index)
        if mode:
            self.controller._set_cmb_mode(mode)

    def _on_track_order_changed(self, index: int) -> None:
        """Handle track-order scope combobox selection."""
        cmb = getattr(self.ui, "cmb_track_order", None)
        if cmb is None:
            return
        scope = cmb.itemData(index)
        if scope and scope != self.controller._track_order_scope:
            self.controller._track_order_scope = scope
            self.controller._sync_to_widget()

    # ---- shot CRUD helpers -----------------------------------------------

    def _edit_shot_in_settings(self) -> None:
        """Open Shot Settings with the active shot pre-selected."""
        if self.controller.sequencer is not None:
            sid = self.controller.active_shot_id
            if sid is not None:
                self.controller.sequencer.store.set_active_shot(sid)
        self.sb.handlers.marking_menu.show("shots")

    def _delete_shot(self) -> None:
        """Delete the selected shot (combobox menu / nav bar).

        One implementation for every entry point: the controller owns the
        confirmation and the engine call, so the combobox menu, the shot-lane
        menu and the Delete key cannot drift into three different ideas of
        what deleting a shot does.
        """
        sid = self.controller.active_shot_id
        if self.controller.sequencer is None or sid is None:
            return
        self.controller.delete_shot(sid)

    def _detect_next_shot(self) -> None:
        """Generate a shot from the next unregistered animation cluster."""
        if self.controller.sequencer is None or cmds is None:
            return
        store = self.controller.sequencer.store if self.controller.sequencer else None
        cand = self.controller.sequencer.detect_next_shot(
            gap_threshold=(store.detection_threshold if store else 5.0),
        )
        if cand is None:
            om2.MGlobal.displayInfo("No additional animation clusters found.")
            return
        result = ShotEditDialog.show(
            parent=self.ui,
            name=cand["name"],
            start=cand["start"],
            end=cand["end"],
            title="Generated Shot",
        )
        if result is None:
            return
        name, s, e, desc = result
        if e <= s:
            return
        self.controller.sequencer.define_shot(
            name=name,
            start=s,
            end=e,
            objects=cand["objects"],
            description=desc,
        )
        self.controller._sync_combobox()
        self.controller._sync_to_widget()

    def _cmb_context_menu(self, pos) -> None:
        """Right-click context menu on the shot combobox."""
        if self.controller._cmb_mode != "shots":
            return

        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None:
            return

        from uitk.widgets.context_menu import ContextMenu

        has_shot = self.controller.active_shot_id is not None
        menu = ContextMenu(parent=cmb)
        # Editing the shot you just picked is what this menu is reached
        # for most often, so it leads; creation and the structural edits
        # follow, each verb fanning out into its forms on hover.
        menu.add(
            "Edit Shot\u2026", callback=self._edit_shot_in_settings, setEnabled=has_shot
        )
        menu.add_separator()
        # New Shot (with Insert Before / After), Split at Playhead and Merge
        # all live on the shot body's own menu, over the shot they act on.
        # Repeating them here made this menu a worse copy of that one.
        # Generate Next Shot has no twin there, so it stays -- at the top
        # level, since the row it used to hang under is gone.
        menu.add("Generate Next Shot\u2026", callback=self._detect_next_shot)
        menu.add_separator()
        menu.add("Delete Shot\u2026", callback=self._delete_shot, setEnabled=has_shot)
        menu.exec_(cmb.mapToGlobal(pos))

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.add(
            "QSpinBox",
            setMinimum=0,
            setMaximum=1000,
            setValue=1,
            setObjectName="spn_snap",
            setPrefix="Snap: ",
            setToolTip="Snap interval for clip edges when dragging or resizing (0 = free movement).",
        )
        chk_snap_keys = widget.menu.add(
            "QCheckBox",
            setText="Snap to Keys",
            setObjectName="chk_snap_to_keys",
            setToolTip="Pull clip and key drags onto frames that already carry keys.\nAlignment guides are shown either way.",
        )
        chk_snap_keys.toggled.connect(self._on_snap_to_keys_toggled)
        # Extend to Keys: one global switch, not a per-shot action.  Keys set
        # (or pasted) outside the current shot pull its bound out to cover
        # them, capped by the reach below -- which is the whole question the
        # option asks, so it sits right under it and greys out with it.
        chk_extend = widget.menu.add(
            "QCheckBox",
            setText="Extend to Keys",
            setObjectName="chk_extend_to_keys",
            setToolTip=(
                "Grow the current shot to cover keys created outside it.\n"
                "Keys inside a neighbouring shot are never claimed."
            ),
        )
        spn_reach = widget.menu.add(
            "QSpinBox",
            setObjectName="spn_extend_reach",
            setPrefix="Extend Distance: ",
            setSuffix=" frames",
            setMinimum=int(self.controller.ANY_REACH),
            setMaximum=100000,
            setSpecialValueText="Extend Distance: any",
            setValue=int(self.controller.EXTEND_REACH_FRAMES),
            setToolTip=(
                "How far outside a bound a new key may sit and still be "
                "reached for.\nAt the minimum (-1) the distance is not "
                "capped at all."
            ),
        )
        spn_reach.setEnabled(False)
        chk_extend.toggled.connect(self.controller._set_extend_to_keys)
        chk_extend.toggled.connect(spn_reach.setEnabled)
        spn_reach.valueChanged.connect(self.controller._set_extend_reach)
        chk_overlay = widget.menu.add(
            "QCheckBox",
            setText="Shortcut Overlay",
            setObjectName="chk_shortcut_overlay",
            setToolTip="Keep a legend of the drag grammar and keys in the timeline's corner;\nthe group under the pointer is lit.",
        )
        chk_overlay.toggled.connect(self._on_shortcut_overlay_toggled)
        from uitk.widgets.widgetComboBox import WidgetComboBox

        cmb_pb = widget.menu.add(
            WidgetComboBox,
            setObjectName="cmb_playback_range",
            setToolTip="Control how Maya's playback range tracks the visible shots.",
        )
        cmb_pb.addItem("Playback Range: Off", "off")
        cmb_pb.addItem("Playback Range: Follows View", "follows_view")
        cmb_pb.addItem("Playback Range: Locked to Shot", "locked")
        cmb_pb.setCurrentIndex(1)
        cmb_pb.currentIndexChanged.connect(self._on_playback_range_changed)

        from uitk.widgets.widgetComboBox import WidgetComboBox as _WCB2

        cmb_scope = widget.menu.add(
            _WCB2,
            setObjectName="cmb_track_order",
            setToolTip=self.sb.tooltip.fmt(
                title="Track Order",
                bullets=[
                    "<b>Visible:</b> Show objects from visible shots only.",
                    "<b>Global:</b> Show all objects from every shot so tracks never reorder when switching shots.",
                ],
            ),
        )
        cmb_scope.addItem("Track Order: Visible", "visible")
        cmb_scope.addItem("Track Order: Global", "global")
        cmb_scope.setCurrentIndex(
            0 if self.controller._track_order_scope == "visible" else 1
        )
        cmb_scope.currentIndexChanged.connect(self._on_track_order_changed)

        chk_select = widget.menu.add(
            "QCheckBox",
            setText="Select Members on Load",
            setObjectName="chk_select_on_load",
            setToolTip=(
                "Select all objects belonging to the shot\n"
                "when navigating to it in the sequencer."
            ),
        )
        chk_select.restore_state = False  # ShotStore owns this setting
        seq = getattr(self.controller, "sequencer", None)
        if seq is not None and hasattr(seq, "store"):
            chk_select.setChecked(seq.store.select_on_load)
        chk_select.toggled.connect(self.controller._on_select_on_load_toggled)

        chk_frame = widget.menu.add(
            "QCheckBox",
            setText="Frame on Shot Change",
            setObjectName="chk_frame_on_shot_change",
            setToolTip=(
                "Automatically frame the camera on the shot's objects\n"
                "when navigating to a different shot."
            ),
        )
        chk_frame.restore_state = False  # ShotStore owns this setting
        if seq is not None and hasattr(seq, "store"):
            chk_frame.setChecked(seq.store.frame_on_shot_change)
        chk_frame.toggled.connect(self.controller._on_frame_on_shot_change_toggled)

        widget.menu.add("Separator", setTitle="Actions")
        widget.menu.add(
            "QPushButton",
            setText="Attribute Colors",
            setObjectName="btn_colors",
            setToolTip="Customize the colors used to display each animated attribute in the sequencer.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Shortcuts\u2026",
            setObjectName="btn_shortcuts",
            setToolTip="View and customise sequencer keyboard shortcuts.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Shots\u2026",
            setObjectName="btn_shot_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shot Sequencer",
                body="Visual timeline editor for per-shot animation with ripple editing, gap management, markers, and audio tracks.",
                sections=[
                    (
                        "Quick Start",
                        [
                            "Click <b>+</b> to create a shot (or use the Manifest).",
                            "Select a shot from the dropdown to load its clips.",
                            "Drag clips to adjust timing; drag edges to resize.",
                            "Use <b>View Mode</b> to see adjacent or all shots.",
                        ],
                    ),
                    (
                        "Shot Navigation",
                        [
                            "<b>Dropdown</b> \u2014 Select shot (sets playback range, selects objects, reframes the timeline). Right-click for Edit Shot, Generate Next Shot, Delete Shot \u2014 the shot body's own menu carries the rest.",
                            "<b>\u25c4 / \u25ba</b> \u2014 Previous / next shot. &nbsp; <b>+</b> \u2014 Append new shot.",
                            "<b>View Mode</b> (cycles): Current \u2192 Adjacent \u2192 All.",
                            "<b>Refresh</b> \u2014 Rebuild from Maya.",
                        ],
                    ),
                    (
                        "Clips",
                        [
                            "<b>Drag body</b> \u2014 Move in time (ripple editing).",
                            "<b>Drag edge</b> \u2014 Resize the clip (scales its keyframes).",
                            "<b>Shift+drag</b> \u2014 Move across shot boundaries without changing them.",
                            "<b>Ctrl</b> while dragging \u2014 Snap to whole frames.",
                            "A drag that lands on a frame already carrying keys is marked with a guide; <i>Snap to Keys</i> in the header menu also pulls the drag onto it.",
                            "<b>Right-click</b> \u2014 Lock/Unlock, Rename, Store Keys (one entry per gesture, however many channels it covered), Retrieve Stored Keys (\u25b8 Restore Keys\u2026 opens the Key Stash panel), Move to Shot (Next / Previous Shot lead the list). On a key: tangent types, Break/Unify Tangents, Store Keys, the key edits under Edit, Move to Shot (keys); drag a selected key's handles to shape its tangents. All edits undoable (Ctrl+Z).",
                        ],
                    ),
                    (
                        "Shot Edges",
                        [
                            "The shot's own edges (and the ruler band's edges) never move its keyframes:",
                            "<b>Drag</b> \u2014 Move the bound; the neighbouring shots move with their keys to keep the gaps.",
                            "<b>Ctrl+drag</b> \u2014 Move the bound and nothing else: the shot grows into the gap (taking the keys it covers) or shrinks and leaves them for the next shot.",
                            "<b>Shift+drag</b> \u2014 Retime: the shot's keyframes scale into the new range.",
                            "<b>Drag the ruler band</b> \u2014 Move the shot with its keys. A gap's edge belongs to the shot beyond it: drag to slide that shot, Ctrl moves that bound only, Shift retimes it. The <i>Shortcut Overlay</i> (header menu) keeps this legend in the timeline's corner.",
                        ],
                    ),
                    (
                        "Ruler / Tracks / Gaps / Markers",
                        [
                            "<b>Ruler:</b> Click/drag to move playhead, double-click to add a marker, scroll to zoom, middle-drag to pan.",
                            "<b>Shot Lane:</b> Right-click a shot block on the ruler for its menu: Edit, New Shot (insert before / after), Split Here (or at the current time), Merge (previous / next), Move To (re-slot it among the other shots; the one holding that slot moves downstream), Add Frames, Trim Empty Space (leading / trailing). Hover a row to open its finer forms. Right-click the ruler, or the tracks clear of every shot, for the timeline's own menu (markers and display toggles). The selected shot is drawn with a tinted band, a rule along the top of the lane, and ticks at its two bounds. Double-click the shot dropdown to edit name / start / end / description in place.",
                            "<b>Tracks:</b> Double-click header to expand per-attribute sub-rows. Right-click to hide, delete, or reveal in Outliner.",
                            "<b>Gaps:</b> Drag an edge to slide the shot beyond it (Ctrl moves the bound only, Shift retimes); drag the body to slide the gap. Right-click to lock. The caps before the first shot and after the last are those shots' own bounds: a plain drag moves the bound and nothing else.",
                            "<b>Markers:</b> M or double-click ruler to add. Drag to move. Right-click to edit note, color, or style.",
                            "<b>Audio:</b> Auto-discovered from Maya audio nodes. Read-only.",
                        ],
                    ),
                    (
                        "Keyboard",
                        [
                            # NOTE: \u2190/\u2192 keys + Shift+\u2190/\u2192 \u2014 kbd() args are pulled
                            # out of the expression because Python 3.11 forbids
                            # backslashes (and thus ``\uXXXX`` escapes) inside
                            # f-string ``{}`` expressions. Using module-level
                            # constants keeps the surface text readable in tools
                            # that auto-escape non-ASCII on save.
                            (
                                self.sb.tooltip.kbd(_KB_LEFT)
                                + " / "
                                + self.sb.tooltip.kbd(_KB_RIGHT)
                                + " \u2014 prev / next key &nbsp;\u00b7&nbsp; "
                                + self.sb.tooltip.kbd("Shift", _KB_LEFT)
                                + " / "
                                + self.sb.tooltip.kbd("Shift", _KB_RIGHT)
                                + " \u2014 step \u00b11 frame"
                            ),
                            (
                                self.sb.tooltip.kbd("Home")
                                + " / "
                                + self.sb.tooltip.kbd("End")
                                + " \u2014 start / end &nbsp;\u00b7&nbsp; "
                                + self.sb.tooltip.kbd("F")
                                + " \u2014 frame shot &nbsp;\u00b7&nbsp; "
                                + self.sb.tooltip.kbd("M")
                                + " \u2014 add marker"
                            ),
                            (
                                self.sb.tooltip.kbd("Ctrl", "Z")
                                + " \u2014 undo &nbsp;\u00b7&nbsp; "
                                + self.sb.tooltip.kbd("Ctrl", "Shift", "Z")
                                + " \u2014 redo &nbsp;\u00b7&nbsp; "
                                + self.sb.tooltip.kbd("Del")
                                + " \u2014 delete keys, or the selected shot when the tracks have no selection"
                            ),
                            (
                                self.sb.tooltip.kbd("Ctrl", "C")
                                + " \u2014 copy the selected keys &nbsp;\u00b7&nbsp; "
                                + self.sb.tooltip.kbd("Ctrl", "V")
                                + " \u2014 paste them at the playhead"
                            ),
                        ],
                    ),
                ],
            )
        )

    def btn_colors(self):
        """Open the attribute color configuration dialog."""
        from uitk.managers.settings_manager import SettingsManager

        widget = self.controller._get_sequencer_widget()

        # Collect active attributes from all clips in the current widget
        active_attrs = set()
        if widget:
            for clip in widget._clips.values():
                for attr in clip.data.get("attributes", []):
                    active_attrs.add(attr)

        color_settings = SettingsManager(namespace=AttributeColorDialog._SETTINGS_NS)
        dlg = AttributeColorDialog(
            defaults=dict(_DEFAULT_ATTRIBUTE_COLORS),
            common_attrs=list(_COMMON_ATTRIBUTES),
            active_attrs=sorted(active_attrs),
            settings=color_settings,
            parent=widget or self.ui,
        )

        def _apply(cmap):
            if widget:
                widget.attribute_colors = cmap
            # Invalidate cached map so the next rebuild reloads it.
            self.controller._color_map_cache = None

        dlg.colors_changed.connect(_apply)
        dlg.exec_()

    def cmb_shot(self, index):
        """Handle direct combobox selection of a shot or marker."""
        cmb = getattr(self.ui, "cmb_shot", None)
        if cmb is None or index < 0:
            return
        if self.controller._cmb_mode == "markers":
            # In markers mode, navigate playhead to the marker time
            marker_time = cmb.itemData(index)
            if marker_time is not None:
                widget = self.controller._get_sequencer_widget()
                if widget:
                    widget.set_playhead(marker_time)
                    widget.playhead_moved.emit(marker_time)
            return
        shot_id = cmb.itemData(index)
        if shot_id is None:
            return
        self.controller._shifted_out_keys.clear()
        self.controller.select_shot(shot_id)
        store = self.controller.sequencer.store if self.controller.sequencer else None
        do_frame = store.frame_on_shot_change if store else False
        self.controller._sync_to_widget(frame=do_frame)
        self.controller._update_shot_nav_state()

    def spn_snap(self, value):
        """Set the snap interval on the sequencer widget."""
        widget = self.controller._get_sequencer_widget()
        if widget is None:
            return
        widget.snap_interval = float(value)

    def btn_shortcuts(self):
        """Open the sequencer shortcut editor."""
        widget = self.controller._get_sequencer_widget()
        if widget is not None:
            widget._shortcut_mgr.show_editor(parent=widget, title="Sequencer Shortcuts")

    def btn_shot_settings(self):
        """Open the shared shots settings panel."""
        self.sb.handlers.marking_menu.show("shots")
