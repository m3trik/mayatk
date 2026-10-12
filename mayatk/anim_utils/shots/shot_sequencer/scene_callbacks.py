# !/usr/bin/python
# coding=utf-8
"""Maya scene callbacks for the shot sequencer.

Provides :class:`SceneCallbacksMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController`. A redraw after
the shot store followed Maya's undo/redo, and OpenMaya anim-curve-edited and DG
time-change callbacks: new keys join the active shot (and grow it, when that
option is on) through a debounced refresh, and the playhead follows Maya's
time.
"""

from qtpy import QtCore

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma
except ImportError:
    cmds = None
    om2 = None
    oma = None


class SceneCallbacksMixin:
    """Scene event hooks: undo/redo, key edits and time changes refresh the widget."""

    # ---- Maya undo/redo: the store follows, the panel redraws ------------

    def _register_maya_undo_callbacks(self) -> None:
        """Redraw after Maya's own undo / redo (:meth:`_on_native_step`).

        The STORE follows every native undo and redo -- applying the restore
        point of the shot edit it took back -- with this panel open or not
        (``ShotStore.follow_native_step``, subscribed by
        ``ShotStore.active()``).  When this listener applied it, an edit made
        with no Sequencer this session was undone in its keys and kept in its
        shots.  The panel only hears that the store did
        (``ShotStore.add_native_step_listener``); idempotent.
        """
        if self._native_step_bound:
            return
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.add_native_step_listener(self._on_native_step)
        self._native_step_bound = True

    def remove_callbacks(self) -> None:
        """Remove Maya event callbacks and ShotStore listener (call on teardown).

        All OpenMaya callbacks registered by this controller live under the
        SJM owner ``self``, so a single ``unsubscribe_all`` removes them.
        """
        self._unbind_store_listener()
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.remove_invalidation_listener(self._on_store_invalidated)
        ShotStore.remove_native_step_listener(self._on_native_step)
        self._native_step_bound = False
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().unsubscribe_all(self)
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

    def _on_native_step(self, event) -> None:
        """The store followed a Maya undo or redo: redraw once, if it put a
        shot edit's restore point back (``event.applied``).

        Only then: Maya fires its Undo/Redo for every undo in the session,
        and an unrelated one's keys reach the panel through the keyframe
        callback.  The store's own events during the restore are skipped
        (:meth:`_on_store_event`), so a Ctrl+Z is one rebuild -- and this one
        also refreshes the dropdown, which lists the shots in order and paints
        their bounds: an undone reorder left it showing the order that had
        just been undone.  Only for the store this panel shows: a panel left
        behind by a scene swap or a module reload must not redraw for it.
        """
        if self._syncing or not event.applied:
            return
        if event.store is not getattr(self._sequencer, "store", None):
            return
        self._after_shot_change()

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
        edits still costs one rebuild.  Nothing is banked inside a transient
        edit (``ShotStore.transient_edit``: an export or a bridge send, which
        puts the scene back) -- its keys are no shot's -- and the keyframe
        callback Maya batches to the next idle, after the edit has closed,
        is marked as its echo (``_transient_echo``, read there).
        """
        import pythontk as ptk

        if self._syncing or om2 is None:
            return
        transient = ptk.ShotStore.in_transient_edit()
        # This callback fires on the edit itself, so the latest one says whose
        # keys the next idle's keyframe callback reports.
        self._transient_echo = transient
        if transient:
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
        playback), when the controller is already syncing, and for a
        transient edit's keys (``ShotStore.transient_edit``: an export's own
        key edits, put back after its write, join and grow no shot).  Maya
        reports those at the next idle, after the edit has closed: armed then,
        with nothing banked the refresh fell back to the SELECTION and took a
        selected keyed non-member in (measured in a fresh GUI Maya,
        2026-10-11) -- so the echo :meth:`_on_anim_curve_edited` marked arms
        nothing, once.
        """
        import pythontk as ptk

        if self._syncing or ptk.ShotStore.in_transient_edit():
            return
        if getattr(self, "_transient_echo", False):
            self._transient_echo = False
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
        # The system's own addition, not a hand edit: the manifest's
        # edited_by_hand reads past it (ShotStore.SYSTEM_MEMBERS).
        self.sequencer.store.add_system_members(shot_id, new_objects)
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
