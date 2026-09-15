# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Effects panel (``render_effects.ui``).

Two key tools, one per render-effect channel: **Key Opacity Fade** and **Key
Highlight Pulse**. Each creates its channel on the selection when missing and
keys it; lookdev is the WebXR push, which shows the deliverable itself, so
nothing in the scene changes for a preview.

Each tool's option box is the whole of that tool: a **Create / Revise**
selector, the fields the chosen mode writes, the action that strips the
channel again, and a **Preview in WebXR** button that pushes the selection
with the effect at those fields' settings, writing nothing. There is no second
surface -- no colour window, no separate Manage section -- because a look set
in one place and revised in another is two editors that drift. The tool button
is the only thing that writes.
Discovered by ``MayaUiHandler`` (``marking_menu.show("render_effects")``).
"""

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import logging

import pythontk as ptk
import mayatk as mtk
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode
from mayatk.mat_utils.render_opacity.channels import (
    CHANNELS,
    HIGHLIGHT,
    OPACITY,
    ChannelSpec,
)

#: The two things Apply can mean. They differ in WHO is acted on, which is the
#: part an artist has to know before pressing it: ``CREATE`` sets an effect up
#: on the selection, making the channel where it is missing; ``REVISE`` changes
#: an effect that is already there and reaches nothing else. A tool whose modes
#: also differ in WHAT they write says so by hiding the fields the mode cannot
#: use (see ``_bind_mode``), so the box always shows exactly what Apply reads.
CREATE, REVISE = "create", "revise"


class RenderEffectsSlots:
    """Switchboard slots for the Render Effects UI.

    Layout
    ------
    - **Header**: Title bar; menu holds Last Selected Only and Delete
      Visibility Keys.
    - **Key**: Key Opacity Fade (``tb000``) and Key Highlight Pulse (``tb001``);
      each option box = the tool's options plus a remove-channel action and a
      Preview in WebXR button.
    - **Footer**: Status messages.
    """

    #: Default seconds for each pulse gap: one cycle's own transition at the
    #: default cadence (25% of a 2.86 s period), so the ends of the train are
    #: shaped like every beat inside it.
    PULSE_GAP_DEFAULT = 0.72

    #: Seed colours for the pulse ramp. The dim end is BLACK, which is the
    #: look this channel had before it had two ends: the glow fades to nothing.
    #: LINEAR light, the space the attribute, the glTF factor and the Unity
    #: controller share; the editor shows them display-encoded, where the
    #: bright end reads #0054DD -- the production blue, adopted 2026-09-13.
    #: Stated as the LINEAR VALUE OF that 8-bit colour, at the 6 decimals
    #: this tool compares colours to: the editor is 8-bit sRGB, so a seed it
    #: cannot represent comes back changed, and Revise would rewrite an
    #: authored colour nobody touched. It must also equal what CREATE writes
    #: (the attribute preset's own default), or the row would show one colour
    #: while the tool authored another. Tests hold both.
    DEFAULT_BRIGHT = (0.0, 0.088656, 0.723055)
    DEFAULT_DIM = (0.0, 0.0, 0.0)

    #: Stand-in albedo for the fade preview. The real one is per object; what
    #: the preview is showing is the alpha riding over it. LINEAR, as a glTF
    #: baseColorFactor is (the preview encodes for display, where this is a
    #: light grey).
    PREVIEW_ALBEDO = (0.57, 0.60, 0.67)

    #: How each mode reads for each channel, for the line under the selector.
    #: A table rather than branches: a channel that gains a mode gains a row.
    VERBS = {
        ("opacity", CREATE): "keys a fade on",
        ("opacity", REVISE): "re-keys the fade on",
        ("highlight", CREATE): "keys a pulse on",
        ("highlight", REVISE): "re-colours",
    }

    #: What Revise promises about the keys, per channel. Opacity's fade IS its
    #: keys, so revising one rewrites them; the highlight's colours are their
    #: own attributes, so revising those leaves a signed-off cadence alone.
    #: Stated because the difference is invisible until it has cost something.
    REVISE_NOTE = {"opacity": "keys are rewritten", "highlight": "keys untouched"}

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_effects
        self._remove_actions = {}  # channel name -> option-box ActionOption
        self._mode_menus = {}  # channel name -> its option-box menu
        self._mode_fields = {}  # channel -> uitk FieldVisibility
        self._mode_hooks = {}  # channel name -> callable run on mode/selection

        # Selection-changed job: the remove actions are live only while the
        # selection carries their channel. Cheap by design -- no scene repair
        # runs here (the key tools own that), so a large selection costs a
        # handful of attributeQuery calls per change.
        self._is_updating = False  # Reentrancy guard
        mgr = ScriptJobManager.instance()
        self._sel_token = mgr.subscribe(
            "SelectionChanged",
            self._on_selection_changed,
            owner=self,
            ephemeral=True,
        )
        mgr.connect_cleanup(self.ui, owner=self)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.add("Separator", setTitle="Options")
        widget.menu.add(
            "QCheckBox",
            setText="Last Selected Only",
            setObjectName="chk_last_selected",
            setChecked=False,
            setToolTip=self.sb.tooltip.fmt(
                body="Applies to every Key and Remove action.",
                bullets=[
                    "<b>On:</b> Only the last selected object is processed.",
                    "<b>Off:</b> All selected objects are processed.",
                ],
            ),
        )
        widget.menu.add(
            "QCheckBox",
            setText="Delete Visibility Keys",
            setObjectName="chk_delete_vis_keys",
            setChecked=False,
            setToolTip=self.sb.tooltip.fmt(
                body="When Key Opacity Fade first gives an object its opacity channel:",
                bullets=[
                    "<b>On:</b> The object's existing visibility keys are "
                    "deleted first.",
                    "<b>Off:</b> They are kept; the fade's visibility mirror is "
                    "keyed over them.",
                ],
            ),
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Render Effects",
                body="Key per-object render effects for engine-ready control: an "
                "<b>Opacity</b> fade (alpha in the GLB, a Unity controller via "
                "FBX) or a <b>Highlight</b> pulse (an additive emissive glow "
                "with a per-object colour). Each tool creates its channel on the "
                "selection when missing, so keying is the only step.",
                steps=[
                    "Select one or more objects.",
                    "Press <b>Key Opacity Fade</b> to key a fade, or "
                    "<b>Key Highlight Pulse</b> to key a repeating glow. Each "
                    "option box (▸) holds everything that tool writes.",
                    "The ⊗ action in each option box removes that channel "
                    "(attribute and keys) from the selection.",
                    "<b>Preview in WebXR</b>, at the foot of each option box, "
                    "pushes the selection with that effect at the settings "
                    "above it -- the box, not the objects' own keys: the "
                    "deliverable's own GLB build, nothing in the scene written, "
                    "and whatever the objects already carry left out.",
                ],
                sections=[
                    (
                        "Create and Revise",
                        [
                            "Each option box opens on <b>Create</b>: the tool "
                            "sets its effect up on the selection, making the "
                            "channel where it is missing.",
                            "<b>Revise</b> changes an effect that is already "
                            "there — the objects in the selection that carry "
                            "the channel, or every such object in the scene "
                            "when nothing is selected.",
                            "The box shows only the fields the mode writes, and "
                            "the line under the selector says what the tool "
                            "button is about to do.",
                            "Nothing in a box touches the scene on its own. "
                            "The tool button is the only thing that writes.",
                        ],
                    ),
                    (
                        "Header menu",
                        [
                            "<b>Last Selected Only</b> — only the most-recent "
                            "selection participates.",
                            "<b>Delete Visibility Keys</b> — clear an object's "
                            "visibility keys when it first receives the opacity "
                            "channel.",
                        ],
                    ),
                ],
            )
        )

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    def _get_selected(self):
        """Return the effective selection, respecting 'Last Selected Only'.

        When the header checkbox is checked and the selection is non-empty,
        only the last selected object is returned.
        """
        objects = cmds.ls(selection=True) or []
        if objects and self.ui.header.menu.chk_last_selected.isChecked():
            return objects[-1:]
        return objects

    def _key_options(self):
        """The header options every key tool forwards to the facade."""
        menu = self.ui.header.menu
        return {
            "delete_visibility_keys": menu.chk_delete_vis_keys.isChecked(),
        }

    def _add_remove_action(self, widget, spec: ChannelSpec):
        """Give a key tool's option box the action that removes its channel.

        Kept apart from the option items (an action, not a setting) and
        gated by the selection job: enabled only while the selection carries
        the channel.
        """
        # Keyed by name: ChannelSpec is a frozen dataclass carrying a dict, so
        # it is not hashable.
        self._remove_actions[spec.name] = widget.option_box.set_action(
            callback=lambda spec=spec: self._remove_channel(spec),
            icon="circle_remove",
            tooltip=self.sb.tooltip.fmt(
                title=f"Remove {spec.name.title()}",
                body=f"Strip the <b>{spec.name}</b> channel from the selection: "
                "the attribute, its keys and any viewport binding (objects "
                "return to their authored material).",
            ),
        )
        self._remove_actions[spec.name].widget.setEnabled(
            bool(self._selection_snapshot()[1].get(spec.name))
        )

    # ------------------------------------------------------------------
    # WebXR preview (option-box actions)
    # ------------------------------------------------------------------

    #: Seconds a fade preview holds at each end, in the option box's animated
    #: preview and in the WebXR one alike -- one statement for both.
    PREVIEW_HOLD_SECONDS = 0.6

    #: The planner per channel, by method name: a channel that gains a preview
    #: gains a row, and this table never learns what either plan does.
    PREVIEW_PLANS = {
        "opacity": "_fade_preview_plan",
        "highlight": "_pulse_preview_plan",
    }

    def _add_webxr_preview(self, widget, spec: ChannelSpec):
        """Give a key tool's option box its **Preview in WebXR** button, LAST.

        A button inside the box rather than an icon action beside the remove
        one. The remove action acts on what the objects carry, so an eye next
        to it read as "show me the object's effect" -- and this shows the BOX:
        the fields above it, whatever the objects are keyed with. Sitting under
        those fields is what says so. It reads the box and writes nothing
        (:meth:`_preview_webxr`); named in no mode's field list, so it shows in
        both.
        """
        button = widget.option_box.menu.add(
            "QPushButton",
            setText="Preview in WebXR",
            setObjectName="btn_preview",
            setToolTip=self.sb.tooltip.fmt(
                title="Preview in WebXR",
                body=f"Push the selection to the WebXR preview with this "
                f"<b>{spec.name}</b> effect at the settings above -- the box "
                "as it stands, not the objects' own keys.",
                bullets=[
                    "Nothing in the scene is written -- no attribute, key or "
                    "colour. The effect exists only in the pushed GLB.",
                    "Whatever the objects already carry is left out, so the "
                    "page plays this effect alone. To see what they ARE keyed "
                    "with, push them through the WebXR preview itself.",
                    "Built by the deliverable's own GLB pipeline, so it plays "
                    "as the keyed effect would ship: one looping clip from its "
                    "first frame (End at Playhead does not apply).",
                    "A field the mode hides keeps its last value for the preview.",
                ],
            ),
        )
        button.clicked.connect(lambda *_, spec=spec: self._preview_webxr(spec))

    @staticmethod
    def _pulse_cadence(menu, fps: float) -> dict:
        """The pulse box's cadence as ``key_pulse`` / ``RampKeys.pulse`` kwargs.

        Every field in the box is seconds and the writers take frames: one
        conversion, shared by the key tool and its preview so the two cannot
        mean different pulses.
        """
        return {
            "period": menu.s002.value() * fps,
            "bright_fraction": menu.s003.value() / 100.0,
            "lead_in": menu.s004.value() * fps,
            "lead_out": menu.s005.value() * fps,
        }

    def _fade_preview_plan(self, fps: float):
        """``(keys, None)``: the fade as the box stands, framed by its holds."""
        menu = self._fade_menu
        keys = ptk.RampKeys.fade_loop(
            menu.s000.value(),
            hold=self.PREVIEW_HOLD_SECONDS * fps,
            direction=menu.cmb_direction.currentData(),
        )
        return keys, None

    def _pulse_preview_plan(self, fps: float):
        """``(keys, (bright, dim))``: the pulse as the box stands, from frame 0.

        An end the targets disagree on (Revise) is undecided, and a preview has
        to show SOME colour for it, so the seed stands in -- the same default
        Create would key.
        """
        menu = self._pulse_menu
        keys = ptk.RampKeys.pulse(
            0.0, menu.s001.value() * fps, **self._pulse_cadence(menu, fps)
        )
        bright, dim = self._pulse_ramp.decided()
        return keys, (bright or self.DEFAULT_BRIGHT, dim or self.DEFAULT_DIM)

    def _preview_webxr(self, spec: ChannelSpec):
        """Push the selection to the WebXR preview with *spec*'s effect as set.

        The effect rides the push as an overlay on the GLB's in-band channels
        (``WebXrPreview.push(data_export=...)``): the page plays what these
        settings would ship while the scene stays exactly as it was -- the
        preview overlays, it never writes. Unlike Apply it takes the selection
        in either mode, since there is nothing to guard.
        """
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                f"Select objects to preview the {spec.name} on."
            )
            return
        fps = float(mtk.AudioUtils.get_fps() or 30.0)
        try:
            keys, colors = getattr(self, self.PREVIEW_PLANS[spec.name])(fps)
            overlay = mtk.RenderEffects.preview_channels(
                objects, channel=spec, keys=keys, colors=colors, fps=fps
            )
            # The export selects what it writes (and restores the selection
            # after), which would re-run the selection job mid-push.
            with ScriptJobManager.instance().suppressed(self._sel_token):
                with self.sb.progress(text="WebXR preview: exporting…") as tick:
                    result = mtk.WebXrPreview().push(
                        objects=objects,
                        open_browser="auto",
                        data_export=overlay,
                        progress=lambda message: tick(text=message),
                    )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        if not result:
            self.ui.footer.setText("WebXR preview failed -- see the log for details.")
            return
        if ptk.MeshConvert.VISIBILITY_TRACKS_KEY not in (
            result.get("data_export") or ()
        ):
            # Published, but what it published is the scene, not the box: a
            # bridge that never learned the overlay knob (a pythontk imported
            # before it existed) sweeps ``data_export`` into the export bag and
            # builds as if nothing was asked, with no error anywhere. Measured
            # 2026-09-13: every push looked the same whatever the box said.
            self.ui.footer.setText(
                f"WebXR preview v{result['version']} shows the scene, "
                f"not the {spec.name} settings."
            )
            self.sb.message_box(
                "<strong>Preview not applied</strong>.<br>The push published "
                f"without the {spec.name} overlay, so the page shows the objects "
                "as they are. Usually the preview bridge in this session predates "
                "the overlay: restart Maya (or reload pythontk) and push again."
            )
            return
        self.ui.footer.setText(
            f"{spec.name.title()} preview v{result['version']}: "
            f"{self._label(objects)} at {result['url']}"
        )

    @staticmethod
    def _carrying(channel, objects):
        """Those *objects* that already carry *channel* (a spec or its name)."""
        return [o for o in objects if OpacityAttributeMode.has_channel(o, channel)]

    def _selection_snapshot(self):
        """``(selection, {channel: those that carry it})`` -- read ONCE.

        Every consumer on the selection-changed path asks the same question,
        and asking it separately made picking objects cost a scene read and a
        per-object query for each of them: two remove actions plus two Apply
        readouts, four times over.

        It reports the EFFECTIVE selection, which is what "Last Selected Only"
        narrows and what every action here operates on. The remove action used
        to be gated on the raw selection instead, so picking an object that
        carries the channel and then one that does not left the action live
        while the thing it would act on had nothing to remove.
        """
        selected = self._get_selected()
        return selected, {
            spec.name: self._carrying(spec, selected) for spec in CHANNELS.values()
        }

    # ------------------------------------------------------------------
    # Create / Revise
    # ------------------------------------------------------------------

    def _add_mode(self, widget, spec: ChannelSpec):
        """Open an option box with its mode selector and an Apply readout.

        Called FIRST in a tool's init so it lands above the fields it gates.
        The readout under it is the whole reason the divide reads as one tool
        rather than two: the tool button's label cannot say who it is about to
        act on, and that -- not which fields are on screen -- is what separates
        setting an effect up from changing one that exists.
        """
        from qtpy import QtCore
        from uitk.managers.field_visibility import FieldVisibility

        menu = widget.option_box.menu
        self._mode_menus[spec.name] = menu
        cmb = menu.add(
            "QComboBox",
            setObjectName="cmb_mode",
            setToolTip=self.sb.tooltip.fmt(
                title="What the tool button does",
                bullets=[
                    "<b>Create:</b> set this effect up on the selection, "
                    "making the channel on objects that lack it.",
                    f"<b>Revise:</b> change it where it already is — "
                    f"{self.REVISE_NOTE.get(spec.name, '')}. With nothing "
                    f"selected this reaches every {spec.name} object in the "
                    "scene, after confirming.",
                    "The box shows only what the mode writes.",
                ],
            ),
        )
        for text, data in (("Create", CREATE), ("Revise", REVISE)):
            cmb.addItem(text, data)
        menu.add(
            "QLabel",
            setObjectName="lbl_apply",
            setAlignment=QtCore.Qt.AlignCenter,
            setWordWrap=True,
            setToolTip="What this tool's button is about to do, and to how many.",
        )
        # The combo drives the layout directly; `on_change` is the mode
        # switch, which is one of the moments the tool may re-read the
        # scene (see ``_sync_mode``).
        self._mode_fields[spec.name] = FieldVisibility(
            on_change=lambda _name, c=spec: self._sync_mode(c, deep=True),
        )
        return cmb

    def _bind_mode(self, spec: ChannelSpec, fields=None, on_sync=None):
        """Declare what each mode writes, then show the opening one.

        Parameters:
            fields: ``{mode: (option-box widget name, ...)}``, resolved
                against the option box. Anything named in no list is always
                shown. ``None`` -- the usual case -- means the modes write the
                same fields and differ only in their target, which is true of
                any channel whose effect IS its keys.
            on_sync: Run whenever the mode or the selection changes, for a
                tool that has to re-read the scene (seeding a revision from
                what is authored). Takes *deep* and *snapshot*, both as
                :meth:`_sync_mode` documents them. A hook rather than a branch here, so this
                helper never learns which channel it is serving.
        """
        if on_sync is not None:
            self._mode_hooks[spec.name] = on_sync
        visibility, menu = self._mode_fields[spec.name], self._mode_menus[spec.name]
        for name in set().union(*fields.values()) if fields else ():
            field = getattr(menu, name, None)
            if field is not None:
                visibility.register(name, field)
        for mode in (CREATE, REVISE):
            visibility.define(mode, (fields or {}).get(mode, ()))
        # Binding applies the opening entry, which both shows the right fields
        # and takes the tool through its first `_sync_mode`.
        visibility.bind(menu.cmb_mode)

    def _mode(self, spec: ChannelSpec) -> str:
        """The mode *spec*'s option box is set to (``CREATE`` before it builds).

        The layout IS the mode: asking the combo separately would give two
        readers that disagree the moment anything sets the mode in code.
        """
        visibility = self._mode_fields.get(spec.name)
        return (visibility.mode if visibility is not None else None) or CREATE

    def _sync_mode(self, spec: ChannelSpec, deep: bool = False, snapshot=None):
        """Re-read the scene for *spec*'s box: its hook, then the readout.

        *deep* marks the infrequent moments -- a mode switch, a write that just
        landed -- where the hook may go past the selection and scan the scene.
        Off on the selection-changed path, which fires often enough that a scan
        there would make picking objects cost a pass over every one of them.
        """
        hook = self._mode_hooks.get(spec.name)
        if hook is not None:
            hook(deep, snapshot)
        self._update_apply_readout(spec, snapshot)

    def _update_apply_readout(self, spec: ChannelSpec, snapshot=None):
        """State what Apply is about to do, and to how many objects.

        Counts the SELECTION rather than the scene: this runs on every
        selection change, and a scene-wide scan there would make picking
        objects cost an attribute query per transform. With nothing selected
        the scope is named without a number, and Apply counts it once -- in
        the confirmation, which is where the number actually matters.

        *snapshot* is a :meth:`_selection_snapshot` pair, passed by the caller
        that already took one so this does not read the selection again.
        """
        menu = self._mode_menus.get(spec.name)
        label = getattr(menu, "lbl_apply", None) if menu is not None else None
        if label is None:
            return
        mode = self._mode(spec)
        verb = self.VERBS.get((spec.name, mode), "applies to")
        selected, carried = (
            snapshot if snapshot is not None else (self._get_selected(), None)
        )
        if mode == CREATE:
            label.setText(
                f"{verb.capitalize()} {len(selected)} selected object(s)."
                if selected
                else "Needs a selection."
            )
            return
        note = self.REVISE_NOTE.get(spec.name, "")
        if not selected:
            label.setText(
                f"{verb.capitalize()} every object in the scene that carries "
                f"{spec.name}."
            )
            return
        # Only Revise needs this, so a caller without a snapshot pays for the
        # query in the one mode that reads it.
        carrying = (
            carried.get(spec.name, ())
            if carried is not None
            else self._carrying(spec, selected)
        )
        label.setText(
            f"{verb.capitalize()} {len(carrying)} of {len(selected)} selected ({note})."
            if carrying
            else f"None of the {len(selected)} selected carry {spec.name}."
        )

    def _targets(self, spec: ChannelSpec):
        """The objects Apply acts on in the current mode, or ``None`` to stop.

        ``None`` means the artist has already been told why, or declined the
        scene-wide confirmation, so a caller returns without a second message.
        """
        selected = self._get_selected()
        if self._mode(spec) == CREATE:
            if not selected:
                self.sb.message_box(
                    "<strong>Nothing selected</strong>.<br>"
                    f"Select objects to key their {spec.name}."
                )
                return None
            return selected

        if selected:
            carrying = self._carrying(spec, selected)
            if not carrying:
                self.sb.message_box(
                    "<strong>Nothing to revise</strong>.<br>None of the "
                    f"{len(selected)} selected object(s) carry the "
                    f"{spec.name} channel."
                )
                return None
            return carrying

        everywhere = mtk.RenderEffects.objects_with_channel(spec)
        if not everywhere:
            self.sb.message_box(
                "<strong>Nothing to revise</strong>.<br>"
                f"No object in the scene carries the {spec.name} channel."
            )
            return None
        # Scene-wide is a legitimate ask and also the one shape a mis-click
        # cannot be undone by eye, so it says how many first.
        prompt = (
            f"Revise <strong>every</strong> object carrying the {spec.name} "
            f"channel ({len(everywhere)})?<br>Select objects first to narrow it."
        )
        if self.sb.message_box(prompt, "Yes", "No") != "Yes":
            return None
        return everywhere

    @staticmethod
    def _key_range(frames, ends_at_cursor):
        # Whole frames: the writers snap anyway, and a sub-frame playhead would
        # otherwise put a footer range next to keys that are not on it.
        current = float(
            ptk.MathUtils.round_value(cmds.currentTime(query=True), mode="half_up")
        )
        if ends_at_cursor:
            return current - frames, current
        return current, current + frames

    @staticmethod
    def _label(objects):
        label = ", ".join(objects[:5])
        if len(objects) > 5:
            label += f" … (+{len(objects) - 5} more)"
        return label

    # ------------------------------------------------------------------
    # Key: opacity fade
    # ------------------------------------------------------------------

    def tb000_init(self, widget):
        """Key Opacity Fade Init — configure option-box menu."""
        from pythontk.file_utils.mesh_convert.glb_fades import CHANNELS as GLTF
        from uitk.widgets.editors.color_editor import FadeWaveform, RampPreview

        widget.option_box.menu.setTitle("Key Opacity Fade")
        self._add_mode(widget, OPACITY)
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Frames: ",
            setObjectName="s000",
            setMinimum=1,
            setMaximum=1000,
            setValue=15,
            setToolTip="Number of frames over which the fade occurs.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="End at Playhead",
            setObjectName="chk000",
            setChecked=True,
            setToolTip=self.sb.tooltip.fmt(
                bullets=[
                    "<b>On:</b> Fade ends at the playhead (keys span current−frames → current).",
                    "<b>Off:</b> Fade starts at the playhead (keys span current → current+frames).",
                ],
            ),
        )
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_direction",
            setToolTip=self.sb.tooltip.fmt(
                title="Fade Direction",
                bullets=[
                    "<b>Fade In:</b> Key opacity 0 → 1.",
                    "<b>Fade Out:</b> Key opacity 1 → 0.",
                    "<b>Auto:</b> Detect from previous key — if last value is 1 → fade out; if 0 or no key → fade in.",
                ],
            ),
        )
        for text, data in [
            ("Fade In", "in"),
            ("Fade Out", "out"),
            ("Auto", "auto"),
        ]:
            cmb.addItem(text, data)
        # The fade has no colour to choose, so what its preview shows is the
        # only thing there is to get wrong: how long it takes and which way it
        # goes. `values` is the exporter's own function -- alpha on the fourth
        # lane of baseColorFactor -- so the preview cannot drift from what
        # ships, and its four components are what tell the widget to draw a
        # transparency board rather than an opaque fill.
        self._fade_menu = widget.option_box.menu
        preview = RampPreview(
            waveform=FadeWaveform(hold=self.PREVIEW_HOLD_SECONDS),
            values=GLTF[OPACITY.name].values,
            linear=True,  # a baseColorFactor is; painted encoded, as the page does
        )
        preview.setObjectName("fade_preview")
        # A stand-in albedo: the real one is per object, and what is being
        # previewed is the ALPHA.
        preview.set_base(self.PREVIEW_ALBEDO)
        widget.option_box.menu.add(
            preview,
            setToolTip=self.sb.tooltip.fmt(
                body="What the fade does to the object, at the length set above.",
                bullets=[
                    "The fill is the object's albedo at the alpha this channel "
                    "writes, over a transparency board -- the EXPORTER's own "
                    "arithmetic, so it is the deliverable's value rather than "
                    "a lookalike.",
                    "The holds either side are not padding: a curve holds its "
                    "first value backwards and its last forwards, so the "
                    "object really does sit there.",
                    "<b>Auto</b> ramps both ways, because the direction is "
                    "resolved per object from its last key when you apply.",
                ],
            ),
        )
        self._fade_preview = preview
        widget.option_box.menu.s000.valueChanged.connect(
            lambda *_: self._sync_fade_shape()
        )
        widget.option_box.menu.cmb_direction.currentIndexChanged.connect(
            lambda *_: self._sync_fade_shape()
        )
        self._sync_fade_shape()
        self._add_remove_action(widget, OPACITY)
        self._add_webxr_preview(widget, OPACITY)
        # No field gating: a fade IS its keys, so there is no part of it that
        # can be restated without re-keying and nothing for Revise to hide.
        # The mode still earns its place -- it narrows Apply to objects that
        # already fade, which is the whole of "change this, do not spread it".
        self._bind_mode(OPACITY)

    def _sync_fade_shape(self):
        """Run the fade preview at the length and direction the box is set to.

        Seconds, from the frames the box asks for: the preview animates in real
        time and the field is frames, so the rate is the one conversion. It
        declines rather than raises -- a preview must never be what stops an
        option box from building.
        """
        preview = getattr(self, "_fade_preview", None)
        menu = getattr(self, "_fade_menu", None)
        if preview is None or menu is None:
            return
        try:
            fps = float(mtk.AudioUtils.get_fps()) or 30.0
            preview.set_shape(
                duration=float(menu.s000.value()) / fps,
                direction=menu.cmb_direction.currentData(),
            )
        except (TypeError, ValueError, AttributeError, ZeroDivisionError):
            return

    @mtk.CoreUtils.undoable(name="Key Opacity Fade")
    def tb000(self, widget):
        """Key Opacity Fade — key a fade on the opacity channel (created if missing)."""
        frames = widget.option_box.menu.s000.value()
        ends_at_cursor = widget.option_box.menu.chk000.isChecked()
        direction_mode = widget.option_box.menu.cmb_direction.currentData()

        objects = self._targets(OPACITY)
        if not objects:
            return

        start, end = self._key_range(frames, ends_at_cursor)

        # Suppress the SelectionChanged callback while we modify the DG
        # to prevent reentrant evaluation (which can crash Maya). The context
        # manager defers resume past the queued idle-time dispatch — a manual
        # suppress/resume pair resumes too early to silence it.
        try:
            with ScriptJobManager.instance().suppressed(self._sel_token):
                keyed = mtk.RenderEffects.key_fade(
                    objects,
                    start=start,
                    end=end,
                    direction=direction_mode,
                    channel=OPACITY,
                    **self._key_options(),
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        dirs = {"Fade In" if d == "in" else "Fade Out" for _, d in keyed}
        direction = " / ".join(sorted(dirs)) or "Fade"
        self.ui.footer.setText(
            f"{direction}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
        )
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Key: highlight pulse
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Key Highlight Pulse Init — configure option-box menu."""
        from qtpy import QtCore
        from pythontk.file_utils.mesh_convert.glb_fades import CHANNELS as GLTF
        from uitk.widgets.editors.color_editor import ColorRampEditor

        widget.option_box.menu.setTitle("Key Highlight Pulse")
        self._add_mode(widget, HIGHLIGHT)
        # SECONDS, like every other field in this box. The panel used to ask
        # for a duration in frames next to a period and two leads in seconds,
        # so reading it meant converting in your head; the writers take frames
        # and the slot already had the rate to convert with.
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Length: ",
            setSuffix=" s",
            setObjectName="s001",
            setMinimum=0.1,
            setMaximum=3600.0,
            setSingleStep=0.5,
            setDecimals=2,
            setValue=4.0,
            setToolTip="How long the pulse runs, in seconds.",
        )
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Period: ",
            setSuffix=" s",
            setObjectName="s002",
            setMinimum=0.1,
            setMaximum=60.0,
            setSingleStep=0.1,
            setDecimals=2,
            setValue=2.86,
            setToolTip="One bright/dim cycle, in seconds (2.86 s measured on the WebXR reference).",
        )
        # "Duty", not "Bright": it is a share of TIME, and the box now also
        # carries two colours, one of which is literally called Bright.
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Duty: ",
            setSuffix=" %",
            setObjectName="s003",
            setMinimum=1,
            setMaximum=99,
            setValue=59,
            setToolTip=self.sb.tooltip.fmt(
                body="Share of each cycle spent at the bright end "
                "(59% measured on the WebXR reference).",
            ),
        )
        self._cycle_readout = widget.option_box.menu.add(
            "QLabel",
            setObjectName="lbl_cycles",
            setAlignment=QtCore.Qt.AlignCenter,
            setToolTip=self.sb.tooltip.fmt(
                body="How many whole cycles the length gives you.",
                bullets=[
                    "A length that is not a whole multiple of the period ends "
                    "MID-CYCLE: the train is cut and the tail holds whatever "
                    "value it was interrupted at.",
                    "Nothing is wrong with that -- it is just invisible "
                    "without a number, so here is the number.",
                ],
            ),
        )
        # Hold the menu rather than reach back through ``self.ui.tb001``: the
        # readout belongs to the box that built it, and a panel rebuilt under a
        # different name would otherwise silently stop updating.
        self._pulse_menu = widget.option_box.menu
        for name in ("s001", "s002"):
            getattr(self._pulse_menu, name).valueChanged.connect(
                lambda *_: self._update_cycle_readout()
            )
        self._update_cycle_readout()
        gaps = [
            widget.option_box.menu.add(
                "QDoubleSpinBox",
                setPrefix=prefix,
                setSuffix=" s",
                setObjectName=name,
                setMinimum=0.0,
                setMaximum=60.0,
                setSingleStep=0.1,
                setDecimals=2,
                setValue=self.PULSE_GAP_DEFAULT,
                setToolTip=self.sb.tooltip.fmt(
                    body=tip,
                    bullets=[
                        "The pulse starts and ends UNHIGHLIGHTED -- a curve "
                        "holds its first value backwards and its last forwards, "
                        "so a pulse that opened bright glowed for the whole "
                        "timeline before it.",
                        "The default matches one cycle's own transition, so the "
                        "ends read like every beat in between.",
                        "<b>0</b> cuts as hard as the frame grid allows -- one frame.",
                        "Unlock a field to set the two ends apart.",
                    ],
                ),
            )
            for name, prefix, tip in (
                (
                    "s004",
                    "Lead-in: ",
                    "Seconds the glow takes to come up at the start.",
                ),
                ("s005", "Lead-out: ", "Seconds it takes to fall away at the end."),
            )
        ]
        # One look, two ends: they move together unless the artist unlocks one.
        self.sb.link_spinboxes(self.ui, gaps, initial=True)
        widget.option_box.menu.add(
            "QCheckBox",
            setText="End at Playhead",
            setObjectName="chk001",
            setChecked=False,
            setToolTip=self.sb.tooltip.fmt(
                bullets=[
                    "<b>On:</b> Pulse ends at the playhead.",
                    "<b>Off:</b> Pulse starts at the playhead.",
                ],
            ),
        )
        # Both ends of the ramp, in the box, rather than a button to a modal.
        # The artist is choosing the RELATIONSHIP between them -- how far the
        # bright end reads from the dim one -- and one at a time hides it.
        #
        # The preview rides along, so this row is the whole colour editor in
        # both modes. It used to be a compact row here and a fuller window
        # elsewhere, which meant the tool had two colour sections that did not
        # agree on what they could show.
        ramp = ColorRampEditor(
            labels=("Bright", "Dim"),
            colors=(self.DEFAULT_BRIGHT, self.DEFAULT_DIM),
            advanced=("rgb",),
            preview=True,
            # The exporter's own function, so the preview and the deliverable
            # cannot disagree: additive over the material's emissive, clamped.
            values=GLTF[HIGHLIGHT.name].values,
            # The colours are LINEAR (the attribute's and the factor's space);
            # the swatches and previews show them encoded, as the page does --
            # shown raw, the page read much brighter than the box (2026-09-13).
            linear=True,
            # Light added over the object: a dim end at nothing shows the
            # board, not a black surface.
            additive=True,
        )
        ramp.setObjectName("pulse_colors")
        # Nothing is connected to the ramp's commit signals. The box stages a
        # value; the tool button writes it. An editor that wrote as it was
        # dragged is what made setting a look and changing one feel like two
        # different acts, and it also wrote to whatever happened to be selected
        # while the artist was only picking a colour for the NEXT pulse.
        widget.option_box.menu.add(
            ramp,
            setToolTip=self.sb.tooltip.fmt(
                body="The two colours the pulse rides between.",
                bullets=[
                    "<b>Bright</b> is what the object reads at intensity 1, "
                    "<b>Dim</b> at 0.",
                    "A dim end at nothing -- the board showing through -- is "
                    "the classic look: the glow fades away.",
                    "Colours are linear light, as the attribute and the GLB "
                    "factor are; the swatches show them display-encoded, which "
                    "is how the page shows them (it also tone-maps, and adds "
                    "the glow over the lit object).",
                    "The preview runs the EXPORTER's own arithmetic at the "
                    "cadence set above, so it is the deliverable's value "
                    "rather than a lookalike.",
                    "In <b>Revise</b> the look being replaced is shown beside "
                    "it, whenever the targets agree on one.",
                ],
            ),
        )
        self._pulse_ramp = ramp
        for name in ("s002", "s003"):
            getattr(self._pulse_menu, name).valueChanged.connect(
                lambda *_: self._sync_pulse_shape()
            )
        self._sync_pulse_shape()
        self._add_remove_action(widget, HIGHLIGHT)
        self._add_webxr_preview(widget, HIGHLIGHT)
        # Revise shows the colours alone: the cadence lives in keys, so a box
        # offering to re-time a signed-off pulse under the word "revise" would
        # be offering to re-key it. Re-timing IS re-keying, and that is Create.
        self._bind_mode(
            HIGHLIGHT,
            fields={
                CREATE: (
                    "s001",
                    "s002",
                    "s003",
                    "lbl_cycles",
                    "s004",
                    "s005",
                    "chk001",
                    "pulse_colors",
                ),
                REVISE: ("pulse_colors",),
            },
            on_sync=self._sync_highlight_mode,
        )

    def _update_cycle_readout(self):
        """Restate how many whole cycles the current length buys.

        The readout is an aid, so it declines rather than raises when it cannot
        read the fields: a cosmetic label must never be what stops an option
        box from finishing its build.
        """
        readout = getattr(self, "_cycle_readout", None)
        menu = getattr(self, "_pulse_menu", None)
        if readout is None or menu is None:
            return
        try:
            length = float(menu.s001.value())
            period = float(menu.s002.value())
        except (TypeError, ValueError):
            return
        cycles = (length / period) if period > 0 else 0.0
        tail = "" if abs(cycles - int(cycles)) < 0.01 else " (last one is cut)"
        readout.setText(f"≈ {cycles:.1f} cycles{tail}")

    @mtk.CoreUtils.undoable(name="Highlight Colour")
    def _apply_highlight_colors(self, objects, colors) -> list:
        """One undo chunk for the whole re-colour, however many ends it spans.

        *colors* is ``(bright, dim)``; an entry of ``None`` leaves that end
        alone, which is what lets a revision touch one end without restating
        the other.
        """
        written = []
        for stop, color in zip(("hi", "lo"), colors):
            if color is None:
                continue
            written = (
                mtk.RenderEffects.set_channel_color(
                    objects, color=color, channel=HIGHLIGHT, stop=stop
                )
                or written
            )
        return written

    def _authored_stops(self, objects):
        """``((bright, dim), mixed_flags)`` across *objects*.

        Seeding from the authored value rather than the last pick is what makes
        this a revision rather than a guess. Where the objects DISAGREE the end
        is reported mixed instead of silently taking the first one's colour --
        the old reader took ``next(iter(...))``, so a multi-object edit showed
        one object's colour and the first drag wrote it to all of them.
        """
        authored = mtk.RenderEffects.channel_color_stops(objects) if objects else {}
        seeds, mixed = [], []
        for index, fallback in enumerate((self.DEFAULT_BRIGHT, self.DEFAULT_DIM)):
            found = [
                tuple(round(float(c), 6) for c in pair[index])
                for pair in authored.values()
                if index < len(pair) and pair[index] is not None
            ]
            unique = set(found)
            mixed.append(len(unique) > 1)
            seeds.append(next(iter(unique)) if len(unique) == 1 else fallback)
        # A colour attribute may legitimately hold >1 (HDR emission); the
        # editor cannot, so the SEED is clamped while the authored value is
        # left alone.
        seeds = [tuple(min(1.0, max(0.0, c)) for c in rgb) for rgb in seeds]
        return tuple(seeds), tuple(mixed)

    def _sync_pulse_shape(self):
        """Run the preview at the cadence the box is set to.

        The preview is only worth having because it is the deliverable's own
        arithmetic; letting it animate at some other tempo than the one being
        keyed would give that away for nothing.
        """
        ramp = getattr(self, "_pulse_ramp", None)
        menu = getattr(self, "_pulse_menu", None)
        if ramp is None or menu is None:
            return
        try:
            ramp.set_shape(
                period=float(menu.s002.value()), duty=float(menu.s003.value()) / 100.0
            )
        except (TypeError, ValueError, AttributeError):
            return

    def _sync_highlight_mode(self, deep: bool = False, snapshot=None):
        """Point the colour row at whatever Revise is about to act on.

        Seeding from the authored value is what makes Revise a revision rather
        than a guess, and the same read decides the before/after: an end the
        targets DISAGREE on reads mixed, so it is neither held up as the
        current look nor written by Apply.

        Create does not reseed. Those colours are what the next pulse will be
        keyed with, and an artist who picked one must not have it replaced
        because they clicked an object.
        """
        ramp = getattr(self, "_pulse_ramp", None)
        if ramp is None:
            return
        if self._mode(HIGHLIGHT) == CREATE:
            # An end can arrive here mixed, from a revision that spanned
            # objects that disagreed. Mixed means "undecided", and Create has
            # to write something, so the default stands in -- setting it is
            # also what clears the flag.
            mixed = [editor.model.mixed for editor in ramp.editors]
            if any(mixed):
                # Through set_colors, which speaks the channel's space; only
                # when an end IS mixed, so the selection path never reseeds.
                ramp.set_colors(
                    tuple(
                        fallback if is_mixed else None
                        for is_mixed, fallback in zip(
                            mixed, (self.DEFAULT_BRIGHT, self.DEFAULT_DIM)
                        )
                    )
                )
            ramp.set_reference(None)
            return

        selected, carried = (
            snapshot if snapshot is not None else (self._get_selected(), None)
        )
        # With nothing selected the scope is the whole scene. That is too
        # expensive to read on every selection change, but reading it on the
        # switch INTO Revise costs one pass and closes a real hole: the row
        # would otherwise show colours nobody read off these objects, and
        # Apply would write them over every one of them.
        if selected:
            targets = (
                carried.get(HIGHLIGHT.name, ())
                if carried is not None
                else self._carrying(HIGHLIGHT, selected)
            )
        elif deep:
            targets = mtk.RenderEffects.objects_with_channel(HIGHLIGHT)
        else:
            targets = []
        if not targets:
            ramp.set_reference(None)
            return
        (bright, dim), mixed = self._authored_stops(targets)
        ramp.set_colors((bright, dim))
        for index, is_mixed in enumerate(mixed):
            ramp.set_mixed(index, is_mixed)
        ramp.set_reference(None if any(mixed) else (bright, dim))

    def tb001(self, widget):
        """Key Highlight Pulse — Create keys the glow, Revise re-colours it.

        Undecorated on purpose: each branch opens its OWN named undo chunk, so
        the Edit menu reads back the thing that happened rather than a generic
        name for the tool that did it.
        """
        objects = self._targets(HIGHLIGHT)
        if not objects:
            return
        if self._mode(HIGHLIGHT) == REVISE:
            self._revise_highlight(objects)
            return
        self._key_highlight_pulse(widget, objects)

    def _revise_highlight(self, objects):
        """Restate the colours on *objects*, leaving their pulse keys alone."""
        colors = self._pulse_ramp.decided()
        if not any(color is not None for color in colors):
            # Every end still reads mixed, so the artist has decided nothing.
            # Writing here would flatten a disagreement into whichever value
            # the row happened to be showing.
            self.sb.message_box(
                "<strong>Nothing decided</strong>.<br>These objects disagree on "
                "both ends. Set an end to state what it should become."
            )
            return
        written = self._apply_highlight_colors(objects, colors)
        self.ui.footer.setText(
            "Highlight colours "
            + " / ".join(
                "unchanged" if c is None else ", ".join(f"{v:.2f}" for v in c)
                for c in colors
            )
            + f" — set on {len(written)} object(s)"
        )
        # deep: a scene-wide revision has no selection to re-read, and the
        # before/after should now show what was just written.
        self._sync_mode(HIGHLIGHT, deep=True)

    @mtk.CoreUtils.undoable(name="Key Highlight Pulse")
    def _key_highlight_pulse(self, widget, objects):
        """Key the glow on *objects*, creating the channel where it is missing."""
        menu = widget.option_box.menu
        length_seconds = menu.s001.value()
        period_seconds = menu.s002.value()
        ends_at_cursor = menu.chk001.isChecked()

        # Every field in the box is seconds; the writer takes frames -- one
        # conversion, shared with the WebXR preview (``_pulse_cadence``).
        fps = float(mtk.AudioUtils.get_fps() or 30.0)
        start, end = self._key_range(length_seconds * fps, ends_at_cursor)
        bright, dim = self._pulse_ramp.decided()
        try:
            with ScriptJobManager.instance().suppressed(self._sel_token):
                keyed = mtk.RenderEffects.key_pulse(
                    objects,
                    start=start,
                    end=end,
                    color=bright,
                    dim_color=dim,
                    channel=HIGHLIGHT,
                    **self._pulse_cadence(menu, fps),
                    **self._key_options(),
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        self.ui.footer.setText(
            f"Highlight pulse: {len(keyed)} object(s), frames "
            f"{int(start)}–{int(end)} @ {period_seconds:.2f} s "
            f"({length_seconds / period_seconds:.1f} cycles)"
        )
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Remove (option-box actions)
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _remove_channel(self, spec: ChannelSpec):
        """Remove *spec*'s artifacts (attribute, binding, keys) from the selection."""
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                f"Select objects to remove {spec.name} from."
            )
            return

        try:
            with ScriptJobManager.instance().suppressed(self._sel_token):
                mtk.RenderEffects.remove(objects, channel=spec)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        self.ui.footer.setText(
            f"{spec.name.title()} removed from {len(objects)} object(s): "
            f"{self._label(objects)}"
        )
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Selection job — gate the remove actions
    # ------------------------------------------------------------------

    def _on_selection_changed(self):
        """Re-read the selection: the remove actions, and every mode readout.

        Both live on the same signal because both answer the same question --
        what does the selection already carry -- and asking it twice per change
        would double the attribute queries for nothing.
        """
        if getattr(self, "_is_updating", False):
            return
        self._is_updating = True
        try:
            # Guard: skip if the UI has been destroyed (prevents crash
            # when the callback fires after the widget is garbage-collected).
            if not self.ui or not self.ui.isVisible():
                return
            snapshot = self._selection_snapshot()
            carried = snapshot[1]
            for name, action in self._remove_actions.items():
                action.widget.setEnabled(bool(carried.get(name)))
            for spec in CHANNELS.values():
                if spec.name in self._mode_menus:
                    self._sync_mode(spec, snapshot=snapshot)
        except RuntimeError:
            pass  # Deleted C++ object — swallow to prevent crash
        except Exception:
            logging.getLogger(__name__).debug(
                "_on_selection_changed error", exc_info=True
            )
        finally:
            self._is_updating = False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("render_effects", reload=True)
    ui.show(pos="screen", app_exec=True)
