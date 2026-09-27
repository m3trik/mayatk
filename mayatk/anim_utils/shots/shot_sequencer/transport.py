# !/usr/bin/python
# coding=utf-8
"""Transport: the playhead, audio scrub and the transport row.

Provides :class:`TransportMixin` -- mixed into
:class:`~.shot_sequencer_controller.ShotSequencerController` -- and
:class:`_MayaPlayController`, the play/stop driver the transport row calls.
Moving the playhead moves Maya's time; the composite audio is bound to the time
slider so scrubbing is audible.
"""

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    cmds = None
    mel = None

from typing import TYPE_CHECKING

from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils
from mayatk.core_utils._core_utils import CoreUtils

if TYPE_CHECKING:  # annotation only: the controller imports this module
    from mayatk.anim_utils.shots.shot_sequencer.shot_sequencer_controller import (
        ShotSequencerController,
    )


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


class TransportMixin:
    """The playhead, audible scrubbing and the transport button row."""

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
