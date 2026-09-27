# !/usr/bin/python
# coding=utf-8
"""The Shot Sequencer panel's controller: :class:`ShotSequencerController`.

Bridges the generic :class:`~uitk.widgets.sequencer.SequencerWidget` to the
Maya :class:`~mayatk.anim_utils.shots.shot_sequencer._shot_sequencer.ShotSequencer`
engine. This module holds the controller's own state -- construction, the
footer, the active-store binding and the active shot -- and composes the
interaction handlers from the concept mixins beside it:

- ``scene_callbacks`` -- Maya undo/redo, keyframe-edit and time-change events.
- ``undo_ledger`` -- the shot-boundary snapshots undo/redo step through.
- ``shot_lane`` -- the shot lane's context menu and shot structure edits.
- ``clip_menu`` -- clip context menus, Move to Shot, clip-key delete / stash.
- ``key_menu`` -- key context menus, tangent edits and drags, key edits.
- ``widget_sync`` -- rebuilding the widget: tracks, clips, sub-rows, colours.
- ``scene_selection`` -- tracks and the Maya selection / Graph Editor sync.
- ``transport`` -- playhead, audio scrub and the transport row.
- ``gap_manager`` / ``clip_motion`` / ``shot_nav`` / ``marker_manager`` --
  gaps, clip drags, shot navigation and markers.

The panel's Switchboard slots (:class:`ShotSequencerSlots`) stay in
``shot_sequencer_slots``, which re-imports this class.
"""

from typing import List, Optional

from qtpy import QtCore

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

from mayatk.anim_utils.shots._shots import StoreEvent
from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
    ShotSequencer,
)
from mayatk.anim_utils.shots.shot_sequencer.clip_menu import ClipMenuMixin
from mayatk.anim_utils.shots.shot_sequencer.clip_motion import ClipMotionMixin
from mayatk.anim_utils.shots.shot_sequencer.gap_manager import GapManagerMixin
from mayatk.anim_utils.shots.shot_sequencer.key_menu import KeyMenuMixin
from mayatk.anim_utils.shots.shot_sequencer.marker_manager import MarkerManagerMixin
from mayatk.anim_utils.shots.shot_sequencer.scene_callbacks import SceneCallbacksMixin
from mayatk.anim_utils.shots.shot_sequencer.scene_selection import SceneSelectionMixin
from mayatk.anim_utils.shots.shot_sequencer.shot_lane import ShotLaneMixin
from mayatk.anim_utils.shots.shot_sequencer.shot_nav import ShotNavMixin
from mayatk.anim_utils.shots.shot_sequencer.transport import TransportMixin
from mayatk.anim_utils.shots.shot_sequencer.undo_ledger import UndoLedgerMixin
from mayatk.anim_utils.shots.shot_sequencer.widget_sync import WidgetSyncMixin


class ShotSequencerController(
    SceneCallbacksMixin,
    UndoLedgerMixin,
    ShotLaneMixin,
    ClipMenuMixin,
    KeyMenuMixin,
    WidgetSyncMixin,
    SceneSelectionMixin,
    TransportMixin,
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

    def _get_sequencer_widget(self):
        """Return the SequencerWidget from the UI."""
        return getattr(self.ui, "sequencer_widget", None)
