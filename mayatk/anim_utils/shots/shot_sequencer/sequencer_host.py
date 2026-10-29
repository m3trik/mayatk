# !/usr/bin/python
# coding=utf-8
"""What the Shot Sequencer panel needs from Maya: :class:`SequencerHost`.

The panel is tentacle's, one for both hosts (``slots/tools/shot_sequencer.py``
and its ``_shot_sequencer`` package); every call it makes that names the host
is answered here (``mtk.SequencerHost``), and blendertk's ``SequencerHost``
answers the same calls for Blender -- same names, signatures and return
shapes, mayatk's the reference.  Plain values in and out: object names,
frames, key targets ``(obj, attr, times, shot_id)``, the engine and store
objects, and curve handles this class itself produced (animCurve names).
Everything degrades without Maya (``cmds`` is ``None``): ``None``, an empty
result or a no-op.

The engine is :class:`~mayatk.anim_utils.shots.shot_sequencer._shot_sequencer.ShotSequencer`;
the graph view's half is :class:`~mayatk.anim_utils.shots.shot_sequencer.graph_sync.GraphSync`
(:meth:`SequencerHost.graph_sync_cls`) and the segment reader
:class:`~mayatk.anim_utils.shots.shot_sequencer.segment_collector.SegmentCollector`
(:meth:`SequencerHost.segment_collector`).
"""

import contextlib
import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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
from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.core_utils._core_utils import CoreUtils


_LOG = logging.getLogger(__name__)


class _SceneWatch:
    """Maya's scene hooks for one Shot Sequencer panel
    (:meth:`SequencerHost.watch_scene`).

    Three OpenMaya callbacks, owned by this watcher in the
    ``ScriptJobManager`` (one ``unsubscribe_all`` takes them all):
    anim-curve-edited names the curves a key edit changed (banked until the
    keyframe callback hands them over), anim-keyframe-edited -- which Maya
    batches to the next idle -- hands them to the panel, and the DG
    time-change callback moves its playhead.  ``MDGMessage.addTimeChangeCallback``
    fires on every DG time change, playback included, in every evaluation
    mode (DG, Serial, Parallel), unlike ``scriptJob(event="timeChanged")``.
    OpenMaya callbacks outlive a scene change, so :meth:`refresh` has nothing
    to re-attach.  Maya reports no rename or audio edit here: a rename is
    followed by the DAG-path reconcile of the next rebuild, and audio is keyed
    on a carrier, so its edits arrive as key edits.
    """

    def __init__(
        self,
        busy: Callable[[], bool],
        on_keys_edited: Callable[[set], None],
        on_time_changed: Callable[[float], None],
        on_scene_changed: Callable[[], None],
        cap: int,
    ):
        self._busy = busy
        self._keys_edited = on_keys_edited
        self._time_changed = on_time_changed
        self._scene_changed = on_scene_changed  # Maya reports none (see above)
        self._cap = cap
        #: Curves Maya named since the last hand-over to the panel.
        self._pending: set = set()
        #: The last curve edit was a transient edit's: the keyframe callback
        #: Maya batches to the next idle is its echo (``_on_keyframe_edited``).
        self._transient_echo = False
        self.keyframe_cb: Optional[int] = None
        self.anim_curve_cb: Optional[int] = None
        self.time_change_cb: Optional[int] = None
        self._attach()

    @property
    def active(self) -> bool:
        """Whether the hooks are attached."""
        return self.keyframe_cb is not None

    def _attach(self) -> None:
        """Register the three callbacks once (idempotent)."""
        if oma is None or self.active:
            return
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        self.keyframe_cb = mgr.add_om_callback(
            oma.MAnimMessage.addAnimKeyframeEditedCallback,
            self._on_keyframe_edited,
            owner=self,
        )
        # Companion callback: this one names the curves that changed, which
        # is what lets the refresh add the freshly-keyed objects to the shot.
        self.anim_curve_cb = mgr.add_om_callback(
            oma.MAnimMessage.addAnimCurveEditedCallback,
            self._on_anim_curve_edited,
            owner=self,
        )
        self.time_change_cb = mgr.add_om_callback(
            om2.MDGMessage.addTimeChangeCallback,
            self._on_time_changed,
            owner=self,
        )

    def refresh(self) -> None:
        """Re-attach whatever a scene change dropped: OpenMaya callbacks
        survive one, so only a removed-then-refreshed watcher re-registers."""
        self._attach()

    def remove(self) -> None:
        """Remove every callback (idempotent)."""
        if oma is not None:
            from mayatk.core_utils.script_job_manager import ScriptJobManager

            ScriptJobManager.instance().unsubscribe_all(self)
        self.keyframe_cb = None
        self.anim_curve_cb = None
        self.time_change_cb = None
        self._pending.clear()

    def _on_anim_curve_edited(self, curves, _client_data=None) -> None:
        """Record which anim curves Maya just changed.

        *curves* is an ``MObjectArray`` of the edited animCurve nodes.  The
        names are banked for the panel; its refresh stays on the keyframe
        callback's debounce so a burst of edits still costs one rebuild.
        Nothing is banked during the panel's own edits, nor inside a transient
        edit (``ShotStore.transient_edit``: an export or a bridge send, which
        puts the scene back) -- its keys are no shot's -- and the keyframe
        callback Maya batches to the next idle, after the edit has closed, is
        marked as its echo (``_transient_echo``, read there).
        """
        if self._busy() or om2 is None:
            return
        transient = ptk.ShotStore.in_transient_edit()
        # This callback fires on the edit itself, so the latest one says whose
        # keys the next idle's keyframe callback reports.
        self._transient_echo = transient
        if transient:
            return
        if len(self._pending) > self._cap:
            return  # already over the cap — the refresh will scan instead
        try:
            for obj in curves:
                self._pending.add(om2.MFnDependencyNode(obj).name())
        except Exception:
            _LOG.debug("anim-curve callback: unreadable payload", exc_info=True)

    def _on_keyframe_edited(self, *_args) -> None:
        """Hand the banked curves to the panel (which debounces its refresh).

        Skipped during playback (keys aren't meaningfully added during
        playback), during the panel's own edits, and for a transient edit's
        keys (``ShotStore.transient_edit``: an export's own key edits, put
        back after its write, join and grow no shot).  Maya reports those at
        the next idle, after the edit has closed: armed then, with nothing
        banked the refresh fell back to the SELECTION and took a selected
        keyed non-member in (measured in a fresh GUI Maya, 2026-10-11) -- so
        the echo :meth:`_on_anim_curve_edited` marked arms nothing, once.
        """
        if self._busy() or ptk.ShotStore.in_transient_edit():
            return
        if self._transient_echo:
            self._transient_echo = False
            return
        # Skip during playback — avoid stalling the viewport
        try:
            if cmds is not None and cmds.play(q=True, state=True):
                return
        except Exception:
            pass
        edited, self._pending = self._pending, set()
        self._keys_edited(edited)

    def _on_time_changed(self, time_msg, _client_data=None) -> None:
        """Move the panel's playhead to Maya's new time (*time_msg*: the
        ``om2.MTime`` the callback supplies)."""
        self._time_changed(time_msg.value)


class _MayaPlayController:
    """``PlayController`` adapter driving Maya's timeline via ``cmds.play``.

    Runs *before_play* (the panel binds its timeline audio there) before
    starting playback, and tracks direction so ``TransportControls`` can
    resume the right way.
    """

    def __init__(self, before_play: Optional[Callable[[], None]] = None):
        self._before_play = before_play
        self._forward = True

    def is_playing(self) -> bool:
        try:
            return bool(cmds.play(q=True, state=True))
        except Exception:
            return False

    def play(self, forward: bool) -> None:
        self._forward = bool(forward)
        if self._before_play is not None:
            try:
                self._before_play()
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


def _target_objects(targets: list) -> list:
    """*targets*' objects, de-duplicated, in the order they appear."""
    return list(dict.fromkeys(obj for obj, _a, _t, _s in targets))


class _StepHandle:
    """What :meth:`SequencerHost.undo_chunk` yields: ``cancel()`` is Blender's
    "push no step"; Maya drops an empty chunk on its own, so it only records
    the wish."""

    __slots__ = ("cancelled",)

    def __init__(self):
        self.cancelled = False

    def cancel(self) -> None:
        """Declare the step's body a no-op."""
        self.cancelled = True


class SequencerHost:
    """Maya's answers to the Shot Sequencer panel's host calls."""

    # ---- the scene's time, playback range, scene events, undo and transport ------

    #: The host's name, as the panel's messages say it.
    APP = "Maya"

    #: Whether the panel redraws after EVERY native undo / redo the store
    #: followed, or only after one that put a shot edit's restore point back.
    #: Maya reports an unrelated step's keys through the anim-curve callback,
    #: so only the steps that touched the shots need a redraw.
    REDRAW_EVERY_NATIVE_STEP = False

    @staticmethod
    def current_time() -> Optional[float]:
        """The playhead's frame, or ``None`` without a scene."""
        if cmds is None:
            return None
        try:
            return float(cmds.currentTime(q=True))
        except Exception:
            return None

    @staticmethod
    def set_current_time(frame: float) -> None:
        """Move the playhead to *frame*, recording no undo entry.

        Undo-disabled like every other view mirror: scrubbing is not an edit,
        and ``cmds.currentTime`` IS undoable (measured at 2 presses to reach
        past it), so a drag would otherwise bury the user's edits one Ctrl+Z
        per playhead move and leave the queue top owned by a scrub -- which
        the undo plan's marker test then reads as "an unrelated edit followed
        ours".
        """
        if cmds is None:
            return
        with CoreUtils.undo_disabled():
            cmds.currentTime(frame, update=True)

    @staticmethod
    def playback_range() -> Optional[Tuple[float, float]]:
        """Maya's playback range ``(min, max)``, or ``None`` without a scene."""
        if cmds is None:
            return None
        try:
            return (
                float(cmds.playbackOptions(q=True, min=True)),
                float(cmds.playbackOptions(q=True, max=True)),
            )
        except Exception:
            return None

    @staticmethod
    def set_playback_range(start: float, end: float) -> None:
        """Set Maya's playback range, recording no undo entry: a view mirror
        the panel runs after every shot change must not cost a Ctrl+Z."""
        if cmds is None:
            return
        with CoreUtils.undo_disabled():
            cmds.playbackOptions(min=start, max=end)

    @staticmethod
    def watch_scene(
        *,
        busy: Callable[[], bool],
        on_keys_edited: Callable[[set], None],
        on_time_changed: Callable[[float], None],
        on_scene_changed: Callable[[], None],
        cap: int = 400,
    ) -> _SceneWatch:
        """Attach the panel's scene hooks; returns the watcher.

        Parameters:
            busy: Whether the panel is mid-edit: key edits made then are its
                own and are not reported.
            on_keys_edited: Called with the set of animCurve names Maya
                reported as edited (empty when it named none): the panel
                banks them and debounces its refresh
                (:meth:`newly_keyed_members` resolves them).
            on_time_changed: Called with the new frame on every time change.
            on_scene_changed: A scene change that keys nothing (Maya reports
                none: renames are followed by the next rebuild's reconcile).
            cap: Past this many banked curves the burst is a bake or an
                import, and banking stops.

        Returns:
            The watcher: ``refresh()`` re-attaches after a scene change,
            ``remove()`` detaches (both idempotent), ``active`` says whether
            it is attached.
        """
        return _SceneWatch(busy, on_keys_edited, on_time_changed, on_scene_changed, cap)

    @staticmethod
    def newly_keyed_members(edited: Iterable[str], shot, sequencer) -> List[str]:
        """The objects keyed in *shot*'s frames that are not its members yet.

        Candidates come from *edited* -- the animCurves the scene watcher
        reported -- falling back to the current selection when it names none
        (a keying path that reports no curve edit, or a bulk edit past the
        cap, which the panel hands over as nothing).  A candidate qualifies on
        "has a key in the shot's range on a content attribute"
        (``Detection.CONTENT_ATTRS``: standard transform/visibility, or a
        render-effect channel such as a highlight pulse), and deliberately NOT
        on its values varying: an object keyed on a hold inside the shot is
        still the shot's content.

        Parameters:
            edited: animCurve names banked since the last refresh.
            shot: The shot (``ShotBlock``) the keys would join.
            sequencer: The panel's ``ShotSequencer`` (unused in Maya; blendertk
                reads its key probe).

        Returns:
            The new members' names (DAG paths), sorted.
        """
        if cmds is None:
            return []
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
        for crv in edited:
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
        return sorted(
            c for c in candidates if c not in existing and c not in existing_long
        )

    @classmethod
    def step_undo(cls, store, redo: bool = False) -> bool:
        """One step of Maya's undo (*redo*: redo) for the panel's Undo / Redo,
        the shot bounds following only when the step is a shot edit's.

        The split is planned up front from the queue's chunk names
        (:meth:`_undo_plan`), after stepping over bookkeeping entries that sit
        above the edit (:meth:`_step_past_queue_noise`).  The store's own
        native-undo sync is held for the walk: it would follow the same step a
        second time.

        Parameters:
            store: The panel's store (``None``: Maya's undo alone).
            redo: Step the redo queue instead.

        Returns:
            Whether a step was attempted (``False`` without Maya).
        """
        if cmds is None:
            return False
        from mayatk.anim_utils.shots._shots import ShotStore

        with ShotStore.native_undo_sync_held():
            cls._step_past_queue_noise(store, redo=redo)
            apply_ledger, call_maya = cls._undo_plan(store, redo=redo)
            try:
                try:
                    if apply_ledger and store is not None:
                        if redo:
                            store.redo_boundary_snapshot()
                        else:
                            store.restore_boundary_snapshot()
                except Exception:
                    _LOG.debug(
                        "%s: the restore point failed",
                        "redo" if redo else "undo",
                        exc_info=True,
                    )
                if call_maya:
                    (cmds.redo if redo else cmds.undo)()
            except RuntimeError:
                pass
        return True

    #: Undo entries that are BOOKKEEPING, not scene edits.
    #: Maya defers its selection-mask reset to idle, so the selection a panel
    #: refresh makes (undo-disabled) can still put one of these on the queue
    #: moments LATER -- above whatever the user did next.  Measured in a live
    #: GUI on a production scene (2026-09-10): switch shot, drag a shot bound,
    #: and two ``selectionMaskResetAll`` entries sat between the user and their
    #: drag, so reversing it took three Ctrl+Z.  Stepping past these is safe
    #: because undoing one changes no scene data; anything NOT named here stops
    #: the walk, so a real edit the user made after ours is never swallowed.
    _QUEUE_NOISE = frozenset(
        {
            "selectionMaskResetAll",
            "changeSelectMode",
            "selectMode",
            "selectType",
            "selectPref",
            "hilite",
        }
    )

    @classmethod
    def _step_past_queue_noise(cls, store, redo: bool = False, limit: int = 8) -> bool:
        """Walk Maya's queue back to OUR chunk, over bookkeeping entries only.

        Returns True when the walk moved at all.  Every step is checked by
        NAME before it is taken (:attr:`_QUEUE_NOISE`), and the walk stops the
        moment the top is our own marker or anything unrecognised -- so the
        worst case is that nothing moves and the caller behaves exactly as it
        did before.  If the marker is never reached, everything stepped over
        is put back: those entries were not ours to consume.  The caller holds
        its own redraw hooks for the walk (``step_undo``'s panel holds
        ``_syncing``).
        """
        from mayatk.anim_utils.shots._shots import MayaShotsHost

        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return False
        paired, marker = tag
        if not paired or not marker:
            return False
        step = cmds.redo if redo else cmds.undo
        back = cmds.undo if redo else cmds.redo
        taken = 0
        try:
            while taken < limit:
                top = MayaShotsHost.undo_queue_top(redo=redo)
                if top == marker or top not in cls._QUEUE_NOISE:
                    break
                step()
                taken += 1
            if taken and MayaShotsHost.undo_queue_top(redo=redo) != marker:
                for _ in range(taken):
                    back()
                taken = 0
        except RuntimeError:
            pass
        if taken:
            _LOG.debug(
                "stepped past %s bookkeeping undo entr%s to reach %s",
                taken,
                "y" if taken == 1 else "ies",
                marker,
            )
        return bool(taken)

    @staticmethod
    def _undo_plan(store, redo: bool = False) -> tuple:
        """Decide how one undo/redo splits between the ledger and Maya.

        Returns ``(apply_ledger, call_maya)``.  The two stacks are not in
        lockstep — see :meth:`ShotStore.scene_edit`:

        * no restore point of ours → pass straight through to Maya;
        * our newest restore point is no longer the newest thing on the
          queue → an unrelated edit followed it, so Maya's undo belongs to
          THAT and our restore point stays put;
        * our restore point is on top → apply it, and touch Maya's queue
          only if the edit actually recorded a step there (one made with
          undo off records nothing, and an unconditional ``cmds.undo()``
          would pop the user's previous, unrelated operation).

        Which queue still has to be showing ``marker`` depends on BOTH the
        direction and the pairing.  A paired edit's chunk rides Maya's
        queues, so after its undo the marker names the top of the *redo*
        queue.  An unpaired edit never put anything on either queue — its
        marker names whatever was on the *undo* queue when it was recorded,
        and that stays true in both directions.  Comparing an unpaired entry
        against the redo queue always mismatches, which silently dropped the
        bounds restore AND redid an unrelated operation.
        """
        from mayatk.anim_utils.shots._shots import MayaShotsHost

        if store is None or not store.has_boundary_snapshot(redo=redo):
            return False, True
        tag = store.peek_boundary_tag(redo=redo)
        if not isinstance(tag, tuple):
            return True, True  # untagged (legacy push) — pre-pairing behaviour
        paired, marker = tag
        if marker != MayaShotsHost.undo_queue_top(redo=redo and bool(paired)):
            return False, True
        return True, bool(paired)

    @staticmethod
    def play_controller(before_play: Optional[Callable[[], None]] = None):
        """The transport row's ``PlayController``: Maya's timeline through
        ``cmds.play``, running *before_play* before playback starts."""
        return _MayaPlayController(before_play)

    @classmethod
    def bind_timeline_audio(cls, current: str = "") -> str:
        """Bind the scene's audio to Maya's Time Slider; returns its node.

        Maya's Time Slider handles playback / loop audio; drag-scrubbing time
        (``cmds.currentTime(update=True)``) emits none, so the panel hands the
        node's WAV (:meth:`timeline_audio_wav`) to the widget's own scrub
        player.  *current* is the node the panel bound last: while it exists
        it stands, so a scrub does not re-bind on every step.

        Returns:
            The bound DG audio node (the composite, else the first per-track
            node), or ``""`` when the scene has none.
        """
        if cmds is None:
            return ""
        if current and cmds.objExists(current):
            return current
        node = cls._resolve_preferred_audio_node()
        if not node:
            return ""
        try:
            slider = mel.eval("$tmp = $gPlayBackSlider")
            cmds.timeControl(slider, e=True, sound=node, displaySound=True)
        except Exception:
            pass
        return node

    @staticmethod
    def timeline_audio_wav(source: str) -> str:
        """The WAV path stored on *source* (the composite or a per-track node).

        Both the composite DG audio node and Maya's per-track audio nodes
        expose a ``.filename`` attr pointing at an on-disk WAV, so the same
        accessor works for either — letting the widget's scrub player fall
        back to a per-track preview when no composite yet exists.
        """
        if not source or cmds is None:
            return ""
        try:
            path = cmds.getAttr(f"{source}.filename") or ""
            return path.replace("\\", "/")
        except Exception:
            return ""

    @staticmethod
    def scene_fps() -> float:
        """The scene's frame rate (24.0 without Maya)."""
        return audio_utils.get_fps()

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

    # ---- objects, names, icons, segments and audio rows (the widget's rebuild) ----

    #: ``NodeIcons`` once resolved (``None`` without Maya); ``...`` until then.
    _node_icons_cls = ...

    @staticmethod
    def segment_collector() -> type:
        """The library's ``SegmentCollector``: the shots' motion segments, a
        segment's attributes, the object row's key ticks and a curve's
        preview, read from Maya's animCurves."""
        from mayatk.anim_utils.shots.shot_sequencer.segment_collector import (
            SegmentCollector,
        )

        return SegmentCollector

    @staticmethod
    def existing(names: Iterable[str]) -> set:
        """The subset of *names* that resolve to a scene node.

        One ``cmds.ls`` round-trip for the lot instead of N ``objExists``
        calls (a rebuild asks for every track).  The answer is in LONG paths
        and a member is stored as it was given -- often a short name -- so a
        name not already a long path is resolved before it is compared: read
        as is, ``ctlA`` was missing beside ``|ctlA``.  Without Maya there is
        no scene to ask, and every name counts as present.

        Parameters:
            names: Node names, short or long.

        Returns:
            The names (as given) that exist.
        """
        names = list(names)
        if cmds is None:
            return set(names)
        if not names:
            return set()
        long_set = set(cmds.ls(names, long=True) or [])
        found = set()
        for name in names:
            if name in long_set or (
                not name.startswith("|")
                and any(p in long_set for p in cmds.ls(name, long=True) or [])
            ):
                found.add(name)
        return found

    @staticmethod
    def leaf_name(name: str) -> str:
        """*name* without its DAG path (``|grp|cube`` -> ``cube``)."""
        from mayatk.core_utils._core_utils import CoreUtils

        return CoreUtils.leaf_name(name)

    @classmethod
    def node_icon(cls, name: str):
        """The node's type icon (a ``QIcon``), or ``None`` without Maya or for
        a missing node.  ``NodeIcons`` is resolved once per process."""
        if cls._node_icons_cls is ...:
            try:
                from mayatk.ui_utils.node_icons import NodeIcons
            except ImportError:
                NodeIcons = None
            cls._node_icons_cls = NodeIcons
        node_icons = cls._node_icons_cls
        return node_icons.get_icon(name) if node_icons else None

    @staticmethod
    def select_objects(names: Iterable[str], add: bool = False) -> List[str]:
        """Select *names* as a view mirror: no undo step.

        Following the panel (a shot switch, a track click) is a view action;
        recorded, every switch would be a step the user has to undo past to
        reach their edits.  Each name is resolved to its long path first;
        nothing found clears the selection (unless *add*).

        Parameters:
            names: Node names, short or long.
            add: Add to the selection instead of replacing it.

        Returns:
            The long paths selected.
        """
        if cmds is None:
            return []
        from mayatk.core_utils._core_utils import CoreUtils

        long_names: List[str] = []
        for name in names:
            long_names.extend(cmds.ls(name, long=True) or [])
        with CoreUtils.undo_disabled():
            if long_names:
                if add:
                    cmds.select(long_names, add=True)
                else:
                    cmds.select(long_names)
            elif not add:
                cmds.select(clear=True)
        return long_names

    @staticmethod
    def claimed_key_times(sequencer, obj: str, curve) -> List[float]:
        """The samples the shot system created on *curve* (its ledger claims).

        Maya keys a claim by the animCurve's name, so *obj* is not needed;
        it is part of the signature because Blender keys by object + channel.

        Parameters:
            sequencer: The host's ``ShotSequencer``.
            obj: The object the curve animates.
            curve: The animCurve (a name, as the segments carry it).
        """
        return sequencer.ledger.key_times(curve)

    @staticmethod
    def motion_segments(
        objects: Iterable[str],
        time_range: Tuple[float, float],
        attributes: Optional[Iterable[str]] = None,
        ignore_holds: bool = True,
    ) -> List[Dict[str, Any]]:
        """The motion segments of *objects* inside *time_range*.

        ``SegmentKeys.collect_segments`` with the sequencer's rules: split at
        static gaps, visibility holds ignored, motion-rate classified.  Names
        are resolved to long paths first (an ambiguous short name would
        raise) and each segment's ``obj`` is a plain string.

        Parameters:
            objects: Node names.
            time_range: ``(start, end)`` frames.
            attributes: Restrict to these attributes (``translateX``...);
                ``None`` reads every animated one.
            ignore_holds: Drop trailing holds instead of absorbing them.
        """
        objects = list(objects)
        if cmds is None or not objects:
            return []
        from mayatk.anim_utils.segment_keys import SegmentKeys

        valid = cmds.ls(objects, long=True) or []
        if not valid:
            return []
        extra = {} if attributes is None else {"channel_box_attrs": list(attributes)}
        segments = SegmentKeys.collect_segments(
            valid,
            split_static=True,
            time_range=tuple(time_range),
            ignore_holds=ignore_holds,
            ignore_visibility_holds=True,
            motion_only=True,
            motion_rate=1e-3,
            **extra,
        )
        for seg in segments:
            seg["obj"] = str(seg["obj"])
        return segments

    @staticmethod
    def attribute_curves(name: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """*name*'s long DAG path and its animated attributes.

        Every animCurve driving the node, mapped by the attribute it drives
        (its plug's last segment); the first curve wins.

        Parameters:
            name: The node, short or long.

        Returns:
            ``(long_name, {attribute: curve})``; ``(None, {})`` when the node
            (or Maya) is missing.
        """
        if cmds is None:
            return None, {}
        long_names = cmds.ls(name, long=True)
        if not long_names:
            return None, {}
        node = long_names[0]
        curves = cmds.listConnections(node, type="animCurve", s=True, d=False) or []
        attr_to_curve: Dict[str, Any] = {}
        for curve in curves:
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
        return node, attr_to_curve

    @staticmethod
    def audio_segments(start: float, end: float) -> list:
        """The audio segments overlapping ``[start, end]``, waveforms included
        (``AudioSegment.collect_all_segments`` over the canonical carrier)."""
        if cmds is None:
            return []
        from mayatk.audio_utils.segments import AudioSegment

        return AudioSegment.collect_all_segments(
            scene_start=start, scene_end=end, include_waveform=True
        )

    @classmethod
    def audio_track_icon(cls, track_id: str):
        """The icon of an audio track: its DG audio node's, when it has one."""
        if cmds is None:
            return None
        from mayatk.audio_utils._audio_utils import AudioUtils

        dg_node = AudioUtils.find_dg_node_for_track(track_id)
        return cls.node_icon(dg_node) if dg_node else None

    # ---- clip-level key deletes and the key stash --------------------------------

    @classmethod
    def delete_span_keys(
        cls,
        obj: str,
        attrs: Sequence[str],
        start: float,
        end: float,
        release: Optional[Callable] = None,
    ) -> int:
        """Cut *obj*'s keys on *attrs* inside ``[start, end]``.

        Parameters:
            obj: The object's name as the panel has it (resolved to its full
                DAG path here).
            attrs: The channels to cut (``translateX`` ...); empty: every
                animation curve on the node -- what a clip naming no channel
                (a stepped point clip) stands for, as ``KeyStash.stash``
                reads ``attrs=None``.
            start: First frame of the span (inclusive).
            end: Last frame of the span (inclusive).
            release: ``release(curve, times)`` -- the store's
                ``release_replaced`` hook -- called with each curve's cut
                times, so every claim on a deleted key goes with it.

        Returns:
            How many keys were cut (0 when the object is gone or every cut
            was refused).
        """
        if cmds is None:
            return 0
        full = cls.full_name(obj)
        if not cmds.objExists(full):
            _LOG.debug("delete_span_keys: '%s' does not exist.", full)
            return 0
        plugs = [f"{full}.{attr}" for attr in attrs] if attrs else [full]
        deleted = 0
        for plug in plugs:
            try:
                # Read first: a curve that loses every key goes with them.
                doomed = [
                    (crv, cmds.keyframe(crv, q=True, tc=True, time=(start, end)))
                    for crv in cmds.keyframe(plug, q=True, name=True) or []
                ]
                cmds.cutKey(plug, time=(start, end), clear=True)
            except Exception:
                _LOG.debug(
                    "delete_span_keys: cutKey failed for '%s'.", plug, exc_info=True
                )
                continue
            for crv, times in doomed:
                if times:
                    deleted += len(times)
                    if release is not None:
                        release(crv, times)
        return deleted

    @classmethod
    def stash_keys(
        cls,
        targets: Sequence[Tuple[str, Sequence[str], float, float]],
        source_shot_id: Optional[int] = None,
    ):
        """Park the keys of *targets* in the scene's key stash as ONE clip.

        Parameters:
            targets: ``(obj, attrs, start, end)`` scopes; *attrs* empty for
                every curve on the object.  An object that no longer exists
                is skipped.
            source_shot_id: The shot every scope came from, or ``None``.

        Returns:
            The stored clip (``StashedClip``), or ``None`` when nothing was
            stashable.
        """
        if cmds is None:
            return None
        from mayatk.anim_utils.key_stash._key_stash import KeyStash

        scopes = []
        for obj, attrs, start, end in targets:
            full = cls.full_name(obj)
            if not cmds.objExists(full):
                continue
            scopes.append((full, list(attrs) or None, start, end))
        if not scopes:
            return None
        return KeyStash.active().stash(targets=scopes, source_shot_id=source_shot_id)

    # ---- curves, key moves and scales, tangents and key-selection edits ----------

    @staticmethod
    def curves_for_attr(obj_name: str, attr_name: str) -> list:
        """The anim curves (node names) connected to ``obj_name.attr_name``;
        ``[]`` when the plug or Maya is missing."""
        if cmds is None:
            return []
        try:
            plug = f"{obj_name}.{attr_name}"
            if not cmds.objExists(plug):
                return []
            return cmds.listConnections(plug, type="animCurve", s=True, d=False) or []
        except Exception:
            return []

    @classmethod
    def curves_for_attrs(cls, obj_name: str, attr_names: Iterable[str]) -> list:
        """:meth:`curves_for_attr` over several attributes: every curve behind
        *attr_names* on *obj_name*, each once -- two segments of one channel
        are one curve, and handed twice an edit would run over it twice."""
        out: list = []
        for attr in dict.fromkeys(attr_names):
            for crv in cls.curves_for_attr(obj_name, attr):
                if str(crv) not in out:
                    out.append(str(crv))
        return out

    @staticmethod
    def curve_id(curve) -> str:
        """A curve's identity across reads (its node name): what merged key
        moves are keyed by (``curve_moves``)."""
        return str(curve)

    @staticmethod
    def object_curves(obj_name: str) -> list:
        """Every anim curve keying *obj_name*."""
        if cmds is None:
            return []
        try:
            return cmds.keyframe(obj_name, q=True, name=True) or []
        except Exception:
            return []

    @staticmethod
    def first_key(curves) -> Optional[float]:
        """The earliest key time across *curves*, or ``None`` with no keys."""
        if cmds is None:
            return None
        times = [
            cmds.findKeyframe(str(c), which="first")
            for c in curves or ()
            if cmds.keyframe(str(c), q=True, keyframeCount=True)
        ]
        return min(times) if times else None

    @staticmethod
    def keyed_at(curve, times: Iterable[float], eps: float = 1e-3) -> List[bool]:
        """Whether *curve* holds a key within *eps* of each of *times*."""
        if cmds is None:
            return [False for _t in times]
        crv = str(curve)
        return [
            bool(cmds.keyframe(crv, q=True, time=(t - eps, t + eps))) for t in times
        ]

    @staticmethod
    def any_key_at(curves, frame: float) -> bool:
        """Whether any curve of *curves* holds a key on *frame*: the
        ``seam_keyed`` question ``ShotStore.enclosing_bounds`` asks of the
        curves a landing rides on."""
        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        return ShotSequencer._any_key_at([str(c) for c in curves], frame)

    @classmethod
    def scale_attribute_keys(
        cls,
        obj_name: str,
        attr_name: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
        ledger=None,
    ) -> bool:
        """Scale only the curves driving *attr_name* on *obj_name*.

        Unlike :meth:`ShotSequencer.scale_object_keys` which scales every
        curve on the whole object, this targets a single attribute so that
        resizing an attribute sub-row clip leaves other attributes untouched.

        Parameters:
            obj_name: The object.
            attr_name: The attribute whose curves scale.
            old_start: The span's start before the resize.
            old_end: The span's end before the resize.
            new_start: Where the span starts after it.
            new_end: Where it ends after it.
            ledger: The store's ``edit_ledger``: its claims on the retimed keys
                are carried with them, as every retime does
                (``_ShotApplyInternal._claims_follow``); ``None`` skips that.

        Returns:
            ``True`` when a scale was actually issued -- a caller that
            snapshotted for undo needs to know a no-op happened so it can
            cancel the snapshot instead of leaving a dead restore point.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.anim_utils.shots._shot_apply import ShotApply

        curves = cls.curves_for_attr(obj_name, attr_name)
        if not curves:
            return False
        if abs(old_end - old_start) < 1e-6:
            return False
        ratio = (new_end - new_start) / (old_end - old_start)
        for crv in curves:
            with ShotApply._claims_follow(
                ledger,
                [str(crv)],
                (old_start, old_end),
                lambda t: new_start + (t - old_start) * ratio,
            ):
                with AnimUtils.exact_scale():  # GUI Maya rounds a scaled key
                    cmds.scaleKey(
                        str(crv),
                        time=(old_start, old_end),
                        newStartTime=new_start,
                        newEndTime=new_end,
                    )
        return True

    @staticmethod
    def commit_curve_moves(curve_moves: Dict, ledger=None, eps: float = 1e-3) -> None:
        """Land merged key moves, one primitive call per curve.

        *curve_moves* is ``{curve_id: {"curve": curve, "plug": "obj.attr",
        "pairs": [(old, new, shot_id), ...]}}`` (the panel's gesture plan and
        ``GraphSync`` build it).  A single shared delta travels as a real move
        (tangents intact); mixed deltas take the snapshot-and-recreate
        two-pass.  Both primitives handle intra-call collisions -- which is
        exactly why the gesture is merged per curve first.  *ledger* rides
        along so a gap hold or boundary sample the drag picks up moves with
        its key.
        """
        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import (
            ShotSequencer,
        )

        for entry in curve_moves.values():
            crv = str(entry["curve"])
            plug = entry.get("plug")
            pairs = [(old, new) for old, new, _sid in entry["pairs"]]
            deltas = {round(new - old, 6) for old, new in pairs}
            if len(deltas) == 1:
                ShotSequencer.move_curve_keys(
                    crv,
                    [old for old, _ in pairs],
                    deltas.pop(),
                    plug=plug,
                    eps=eps,
                    ledger=ledger,
                )
            else:
                ShotSequencer.recreate_curve_keys(
                    crv, pairs, plug=plug, eps=eps, ledger=ledger
                )

    @staticmethod
    def delete_keys_at(
        curves, times: Iterable[float], release: Optional[Callable] = None
    ) -> int:
        """Cut the keys of *curves* on each of *times*.

        Parameters:
            curves: The curves (node names) to cut from.
            times: Exact key times.
            release: ``release(curve, frames)`` -- the store's
                ``release_replaced`` -- called for every key cut, so its claims
                go with it.

        Returns:
            How many of *times* had at least one key cut.
        """
        if cmds is None:
            return 0
        cut = 0
        for t in times:
            cut_any = False
            for crv in curves:
                crv = str(crv)
                if not cmds.keyframe(crv, q=True, time=(t, t), keyframeCount=True):
                    continue
                cmds.cutKey(crv, time=(t, t), clear=True)
                cut_any = True
                if release is not None:
                    release(crv, [t])
            if cut_any:
                cut += 1
        return cut

    @staticmethod
    @contextlib.contextmanager
    def moving_audio_track(track_id: str, ledger=None):
        """Yield ``(move, lowest)`` for moving an audio track's content.

        ``move(lo, hi, delta)`` shifts the track's keys inside ``[lo, hi]`` in
        the canonical store (claims carried by *ledger*); ``lowest`` is its
        earliest key.  The composite's DG node re-syncs once, when the block
        closes without raising.
        """
        if cmds is None:
            yield (lambda _lo, _hi, _d: None), None
            return
        from mayatk.audio_utils._audio_utils import AudioUtils

        def move(lo, hi, d):
            AudioUtils.shift_keys_in_range(
                lo, hi, d, track_ids=[track_id], ledger=ledger
            )

        lowest = min((f for f, _v in AudioUtils.read_keys(track_id)), default=None)
        with AudioUtils.batch() as b:
            yield move, lowest
            b.mark_dirty([track_id])

    @staticmethod
    @contextlib.contextmanager
    def undo_chunk(label: str):
        """One named undo chunk (``CoreUtils.undo_chunk``) for an edit made
        without a shot store; yields a handle whose ``cancel()`` declares the
        body a no-op (Maya drops an empty chunk on its own)."""
        from mayatk.core_utils._core_utils import CoreUtils

        handle = _StepHandle()
        with CoreUtils.undo_chunk(label):
            yield handle

    #: Tangent types a key's context menu offers, in the Graph Editor's order:
    #: ``(label, keyTangent type)``.
    TANGENT_TYPES = (
        ("Auto", "auto"),
        ("Spline", "spline"),
        ("Clamped", "clamped"),
        ("Linear", "linear"),
        ("Flat", "flat"),
        ("Step", "step"),
        ("Plateau", "plateau"),
    )

    #: Interpolation modes apart from the tangent types: none in Maya (step
    #: and linear are tangent types), so the key menu shows no such submenu.
    INTERPOLATIONS = ()

    @staticmethod
    def tangent_from_handle(side: str, dt: float, dv: float) -> Tuple[float, float]:
        """``(angle_degrees, weight)`` of the tangent a dragged handle stands for.

        The inverse of how the preview places its control points
        (``SegmentCollector.build_curve_preview``): an OUT handle sits
        ``weight * (cos a, sin a)`` after its key and an IN handle the same
        distance BEFORE it, so the IN vector points back along the tangent
        and is flipped before its angle is read.  The weight is the handle's
        length in curve units, which is what a weighted curve stores.
        """
        if side == "in":
            dt, dv = -dt, -dv
        return math.degrees(math.atan2(dv, dt)), math.hypot(dt, dv)

    @staticmethod
    def tangent_type_edit(tangent: str, sides=("in", "out")) -> dict:
        """The edit that sets *tangent* (a :attr:`TANGENT_TYPES` type) on the
        given sides: ``keyTangent`` flags."""
        flags = {}
        if "in" in sides:
            flags["inTangentType"] = tangent
        if "out" in sides:
            flags["outTangentType"] = tangent
        return flags

    @staticmethod
    def interpolation_edit(mode: str) -> dict:
        """The edit for a Blender interpolation mode (``CONSTANT`` / ``LINEAR``
        / ``BEZIER``) in Maya's terms: a step out tangent, linear tangents, or
        auto tangents.  :attr:`INTERPOLATIONS` offers none in Maya."""
        return {
            "CONSTANT": {"outTangentType": "step"},
            "LINEAR": {"inTangentType": "linear", "outTangentType": "linear"},
            "BEZIER": {"inTangentType": "auto", "outTangentType": "auto"},
        }[mode]

    @staticmethod
    def tangent_lock_edit(lock: bool) -> dict:
        """The edit that unifies (``True``) or breaks the keys' tangents."""
        return {"lock": lock}

    @classmethod
    def handle_drag_edit(cls, vectors: Dict, side: str, broken: bool) -> Callable:
        """The edit a dragged tangent handle asks for.

        *vectors* is ``{(obj, attr, round(time, 6)): (dt, dv)}``, one handle
        vector per key the drag carried.  Angle always; weight too when the
        curve carries weighted tangents (``weightedTangents``), since only then
        does the handle's length mean anything -- an unweighted curve's
        control point sits a third of the span out whatever the drag did.
        Maya turns the edited side ``fixed`` and, on a unified key, swings the
        other side with it; *broken* (the Alt drag) unlocks the key first so
        it stops doing that.

        Returns:
            A resolver ``(obj, attr, curve, time) -> keyTangent flags`` for
            :meth:`edit_key_tangents`.
        """
        weighted: dict = {}  # curve -> weightedTangents, asked once per curve

        def resolve(obj, attr, curve, time):
            vector = vectors.get((obj, attr, round(float(time), 6)))
            if vector is None:
                return {}
            angle, weight = cls.tangent_from_handle(side, *vector)
            flags = {}
            if broken:
                # Unlocked FIRST: a unified key swings its other side with
                # whatever it is told next, which is the whole point of Alt.
                flags["lock"] = False
            flags["inAngle" if side == "in" else "outAngle"] = angle
            if curve not in weighted:
                weighted[curve] = cmds.getAttr(f"{curve}.weightedTangents")
            if weighted[curve]:
                flags["inWeight" if side == "in" else "outWeight"] = weight
            return flags

        return resolve

    @classmethod
    def edit_key_tangents(cls, targets: list, edit) -> int:
        """Apply a tangent *edit* to *targets*' keys in one undo chunk.

        Parameters:
            targets: ``[(obj, attr, [times], shot_id), ...]``.
            edit: ``keyTangent`` flags every key takes, or a resolver
                ``(obj, attr, curve, time) -> flags`` when each key gets its
                own (a handle drag that carried a selection writes a different
                angle per key).  Times that resolve to the same flags still go
                out as ONE command; a key the resolver has nothing for is
                skipped.

        Returns:
            How many keys were written.
        """
        if cmds is None:
            return 0
        from mayatk.core_utils._core_utils import CoreUtils

        resolve = edit if callable(edit) else (lambda *_a: edit)
        n = 0
        with CoreUtils.undo_chunk("Key Tangents"):
            for obj, attr, times, _sid in targets:
                for crv in cls.curves_for_attr(obj, attr):
                    curve = str(crv)
                    batched: dict = {}
                    for t in times:
                        flags = resolve(obj, attr, curve, t)
                        if flags:
                            batched.setdefault(tuple(flags.items()), []).append(t)
                    for flags, batch in batched.items():
                        tt = tuple((t, t) for t in batch)
                        try:
                            cmds.keyTangent(curve, edit=True, time=tt, **dict(flags))
                        except RuntimeError:
                            continue  # e.g. a weight on an unweighted curve
                        n += len(batch)
        return n

    @classmethod
    def target_curves(cls, targets: list) -> list:
        """The anim curves behind *targets*' (object, attribute) pairs, each
        once: the attribute-level scope -- an edit handed these reaches only
        the highlighted channels, where one handed the objects reaches every
        curve they carry."""
        curves: list = []
        for obj, attr, _times, _sid in targets:
            curves.extend(str(c) for c in cls.curves_for_attr(obj, attr))
        return list(dict.fromkeys(curves))

    @classmethod
    def simplify_keys(
        cls, targets: list, span: Tuple[float, float], on_delete=None
    ) -> Tuple[int, int]:
        """Drop the selected keys that carry neither a value nor a shape.

        Both of ``optimize_keys``' middle passes, in its order, over the
        selected attributes' curves and, within them, the selected keys:

        1. the FLAT pass (``get_redundant_flat_keys``) -- the interior keys
           of a run that all hold one value.  The one that matters on real
           footage: a hold is usually spelled with ``step`` out-tangents, and
           a stepped curve is exactly what the reducer below will not touch.
           Measured on a production assembly, the reducer alone left every
           redundant hold key standing on 8 of 9 channels.
        2. the SHAPE pass (``simplify_curve`` / ``filterCurve keyReducer``)
           -- keys whose absence moves the curve less than the tolerance.

        Each leaves the curve outside the selection byte-identical, tangents
        included.  ``optimize_keys`` itself is deliberately NOT the call even
        though it names these passes: it may delete whole static curves,
        which an edit inside the panel's undo step cannot afford.

        Parameters:
            targets: ``[(obj, attr, [times], shot_id), ...]``.
            span: The frame range the selection covers.
            on_delete: ``on_delete(curve, frames)`` for every key removed
                (the store's ``release_replaced``).

        Returns:
            ``(flat keys removed, curves reduced)``.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = cls.target_curves(targets)
        flat = AnimUtils.get_redundant_flat_keys(
            curves, remove=True, selected_only=True, time_range=span
        )
        if on_delete is not None:
            for crv, times in flat:
                on_delete(crv, times)
        shaped = AnimUtils.simplify_curve(
            curves, selected_only=True, time_range=span, on_delete=on_delete
        )
        return sum(len(times) for _c, times in flat), len(shaped)

    @classmethod
    def thin_keys(cls, targets: list, span: Tuple[float, float], on_delete=None) -> int:
        """Keep only the first and last key of each selected attribute.

        The attributes are passed outright rather than left to the Channel
        Box highlight the panel mirrors into: the strip must narrow to the
        sub-rows the user selected even if the highlight never landed.

        Returns:
            How many keys were removed.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        attrs = list(dict.fromkeys(attr for _o, attr, _t, _s in targets))
        return (
            AnimUtils.remove_intermediate_keys(
                _target_objects(targets),
                time_range=span,
                attributes=attrs,
                on_delete=on_delete,
            )
            or 0
        )

    @classmethod
    def snap_keys(cls, targets: list, span: Tuple[float, float], on_move=None) -> int:
        """Pull the selected keys (the Graph Editor selection the panel
        asserted) off fractional frames onto whole ones.

        Returns:
            How many keys were snapped.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        return (
            AnimUtils.snap_keys_to_frames(
                _target_objects(targets),
                selected_only=True,
                time_range=span,
                on_move=on_move,
            )
            or 0
        )

    @classmethod
    def euler_filter_keys(cls, targets: list) -> list:
        """Unflip the rotation curves behind *targets* (whole curves).

        Returns:
            The curves filtered.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        return list(AnimUtils.euler_filter(cls.target_curves(targets)) or [])

    @classmethod
    def invert_keys(cls, targets: list, on_replace=None, on_move=None) -> int:
        """Mirror the selected keys in time, in place.

        ``invert_keys`` with no time mirrors within the selection's own range
        rather than placing a reversed COPY somewhere -- the sequencer's keys
        are already where the animator put them.  It reports nothing, so the
        count is the selection's.

        Returns:
            How many keys were mirrored.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        AnimUtils.invert_keys(
            _target_objects(targets), on_replace=on_replace, on_move=on_move
        )
        return sum(len(times) for _o, _a, times, _s in targets)

    @classmethod
    def align_keys(cls, targets: list, on_replace=None, on_move=None) -> int:
        """Line the selected keys up on the earliest one's frame.

        Returns:
            How many keys were aligned (the selection's count when the align
            ran, 0 when there was nothing to align).
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        ok = AnimUtils.align_selected_keyframes(
            _target_objects(targets), on_replace=on_replace, on_move=on_move
        )
        return sum(len(times) for _o, _a, times, _s in targets) if ok else 0

    @classmethod
    def copy_keys(cls, sequencer, targets: list) -> dict:
        """The panel's key clipboard for *targets*' keys.

        Key records in the shape ``AnimUtils.paste_keys`` writes: ``time``,
        ``value`` and ``AnimUtils.get_tangent_info`` (types, angles, weights,
        lock).  A key the gap hold stepped is read back to its pre-hold out
        side: the type the claim recorded, and for a ``fixed`` one the slope
        of its fixed in side (the step discarded the out angle --
        ``_restore_fixed_out_tangent``).

        Parameters:
            sequencer: The panel's ``ShotSequencer`` (its ``ledger``).
            targets: ``[(obj, attr, [times], shot_id), ...]``.

        Returns:
            ``{"rows": [(obj, attr, [record, ...]), ...], "start": frame}``,
            or ``{}`` when nothing was copied.
        """
        if cmds is None or sequencer is None:
            return {}
        from mayatk.anim_utils._anim_utils import AnimUtils

        ledger = sequencer.ledger
        rows = []
        for obj, attr, times, _sid in targets:
            curves = [str(c) for c in cls.curves_for_attr(obj, attr)]
            if not curves:
                continue
            crv = curves[0]
            keys = []
            for t in times:
                tt = (t, t)
                value = cmds.keyframe(crv, q=True, time=tt, valueChange=True)
                info = AnimUtils.get_tangent_info(crv, t)
                if not value or not info:
                    continue
                kd = {"time": float(t), "value": float(value[0]), **info}
                claim = ledger.claimed_step(crv, t)
                # A key written over the held one since (a Graph Editor paste,
                # the held value with another in slope included) is the
                # animator's, its step with it -- as the release reads it
                # (``_release_gap_holds``: by value and in half).
                if (
                    claim is not None
                    and kd["outTangentType"] == "step"
                    and ledger.is_same_key(
                        claim,
                        kd["inTangentType"],
                        kd["value"],
                        in_half=(kd["inAngle"], kd["inWeight"]),
                    )
                ):
                    kd["outTangentType"] = claim[1] or "auto"
                    if claim[1] == "fixed" and kd["inTangentType"] == "fixed":
                        kd["outAngle"] = kd["inAngle"]
                        kd["outWeight"] = kd["inWeight"]
                keys.append(kd)
            if keys:
                rows.append((obj, attr, keys))
        if not rows:
            return {}
        return {
            "rows": rows,
            "start": min(kd["time"] for _o, _a, keys in rows for kd in keys),
        }

    @staticmethod
    def paste_keys(clipboard: dict, pairs: list, now: float, on_replace=None) -> list:
        """Paste :meth:`copy_keys` rows at the playhead.

        Parameters:
            clipboard: What :meth:`copy_keys` returned (its ``start``).
            pairs: ``[(dest_obj, dest_attr, records), ...]`` --
                ``ptk.ShotSequencer.paste_pairs`` over the clipboard's rows.
            now: The frame the clipboard's earliest key lands on.
            on_replace: ``on_replace(curve, frames)`` for a key a pasted one
                replaces (the store's ``release_replaced``).

        Returns:
            ``[(obj, attr, [landed frame, ...]), ...]`` for the rows that
            keyed anything (a row whose object is gone since the copy keys
            nothing).
        """
        if cmds is None:
            return []
        from mayatk.anim_utils._anim_utils import AnimUtils

        start = clipboard["start"]
        landed = []
        for obj, attr, keys in pairs:
            if AnimUtils.paste_keys(
                [obj],
                {obj: {attr: keys}},
                target_time=now + (keys[0]["time"] - start),
                match_source=False,
                refresh_channel_box=False,
                on_replace=on_replace,
            ):
                landed.append((obj, attr, [now + kd["time"] - start for kd in keys]))
        return landed

    # ---- selection mirrors, names and the graph view's handle writer -------------

    #: Maya has an Attribute Spread Sheet: the track menu offers it.
    ATTRIBUTE_SPREADSHEET = True

    @staticmethod
    def graph_sync_cls() -> type:
        """The library's ``GraphSync`` (the graph view's host half)."""
        from mayatk.anim_utils.shots.shot_sequencer.graph_sync import GraphSync

        return GraphSync

    @staticmethod
    def object_exists(name: str) -> bool:
        """Whether *name* names a scene node."""
        if cmds is None or not name:
            return False
        try:
            return bool(cmds.objExists(name))
        except (RuntimeError, ValueError):
            return False

    @staticmethod
    def full_name(name: str) -> str:
        """*name* as the scene's unique long DAG path (an audio track's source
        node included); *name* itself when the scene holds nothing by it."""
        if cmds is None:
            return name
        try:
            matches = cmds.ls(name, long=True)
            if matches:
                return matches[0]
        except Exception:
            pass
        return name

    @staticmethod
    def undo_disabled():
        """A context in which scene changes record no undo step (a view
        mirror's: ``CoreUtils.undo_disabled``)."""
        return CoreUtils.undo_disabled()

    @staticmethod
    def mirror_selection(names) -> None:
        """Put *names* on the scene selection as a view mirror.

        Replaces the selection; nothing selectable leaves it alone.  Resolved
        to long DAG paths to avoid ambiguous short-name errors (an audio
        track's name is its audio node's, so that node is selected).

        The selection is NOT recorded on the undo queue.  This runs on every
        clip/track click and again on the rebuild after each edit, and
        ``cmds.select`` is undoable -- so each click buried the panel's own
        edits one Ctrl+Z deeper, and a group gesture buried them by several.
        Mirroring a panel selection into Maya is a view concern, not a scene
        edit; the user's undo history belongs to the edits.

        Parameters:
            names: Object (or audio node) names.
        """
        names = [n for n in names or () if n]
        if cmds is None or not names:
            return
        try:
            long_names = cmds.ls(names, long=True)
        except (RuntimeError, ValueError):
            return
        if not long_names:
            return
        with CoreUtils.undo_disabled():
            cmds.select(long_names, replace=True)

    @staticmethod
    def select_channels(objects, attrs=()) -> None:
        """Put *attrs* on the Channel Box highlight; empty clears it.

        Maya's channel scope is the Channel Box's, which shows the selected
        objects, so *objects* is not needed here (Blender's channel scope is
        per object).  Every helper that already reads that highlight
        (``remove_intermediate_keys``, ``copy_keys``, ``AnimUtils.resolve_keys``
        and the rest) narrows to the same channels without being told twice.

        Deferred to idle: the Channel Box refills itself after a selection
        change on an idle callback, not synchronously, so a highlight written
        in the same beat as ``cmds.select`` is wiped by the refill that
        follows it.  A failure here costs the highlight and nothing else.

        Parameters:
            objects: The objects whose channels these are (unused in Maya).
            attrs: Attribute names; empty clears the highlight.
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
                _LOG.debug("channel box mirror failed", exc_info=True)

        try:
            cmds.evalDeferred(_apply, lowestPriority=True)
        except Exception:
            _apply()

    @classmethod
    def select_keys(cls, rows) -> int:
        """Select exactly the ``(obj, attr, times)`` *rows*' keys in the Graph
        Editor; return how many (curve, time) selections were made.

        Mirroring a panel selection is not a scene edit, and it must not be
        recorded as one: ``selectKey`` IS undoable, so the unguarded version
        pushed one entry per (curve, time) AFTER the edit that caused the
        rebuild.  A group gesture then took a Ctrl+Z per key just to walk
        back to its own step -- and, worse, left the queue top owned by a
        selection, which is exactly what the undo plan's marker test reads
        to decide whether the shot-bounds restore point is still ours.
        (Verified: edit + 5 raw selectKey calls = 6 undos to revert the
        edit; guarded = 1.)

        Parameters:
            rows: ``[(object, attribute, [times]), ...]``; a row without an
                attribute contributes the clear and nothing more.
        """
        if cmds is None:
            return 0
        from mayatk.anim_utils._anim_utils import AnimUtils

        n = 0
        with CoreUtils.undo_disabled():
            # Not the bare selectKey -clear: with nothing selected it raised,
            # and the panel's key selection never reached Maya at all.
            AnimUtils.clear_key_selection()
            for obj_name, attr_name, times in rows:
                tt = tuple((t, t) for t in times)
                if not tt or not attr_name:
                    continue
                for crv in cls.curves_for_attr(obj_name, attr_name):
                    # One call per curve carrying every time: the per-time loop
                    # was O(curves x times) commands on every selection change.
                    cmds.selectKey(str(crv), add=True, time=tt)
                    n += len(tt)
        return n

    @staticmethod
    def open_attribute_spreadsheet() -> bool:
        """Open Maya's Attribute Spread Sheet over the selection; ``False``
        when it could not be opened."""
        if mel is None:
            return False
        try:
            mel.eval("SpreadSheetEditor")
        except Exception:
            return False
        return True

    @classmethod
    def set_dragged_handles(cls, curve, items) -> int:
        """Write dragged tangent handles on *curve*; return how many.

        The graph view's handle writer (``GraphSync.on_tangents_edited``),
        one curve at a time; the track view's handle drag batches through
        :meth:`handle_drag_edit` and places each handle by the same rule.  Each handle is a VECTOR
        from its key in curve units (frames along, value up); a weighted
        curve takes the vector's length as the tangent weight, a non-weighted
        one only its angle.

        Parameters:
            curve: The animCurve's name.
            items: ``[(time, side, dt, dv, broken), ...]`` -- *side* ``"in"``
                or ``"out"``; *broken* unlocks the key's tangents first (the
                Alt drag), so the other side stays where it is.
        """
        if cmds is None or not items:
            return 0
        weighted = bool(cmds.getAttr(f"{curve}.weightedTangents"))
        n = 0
        for t, side, dt, dv, broken in items:
            angle, weight = cls.tangent_from_handle(side, dt, dv)
            flags = {f"{side}Angle": angle}
            if weighted:
                flags[f"{side}Weight"] = weight
            tt = (t - 1e-3, t + 1e-3)
            if broken:
                # Unlocked BEFORE the angle, or the other side swings too.
                cmds.keyTangent(curve, edit=True, time=tt, lock=False)
            cmds.keyTangent(curve, edit=True, time=tt, **flags)
            n += 1
        return n
