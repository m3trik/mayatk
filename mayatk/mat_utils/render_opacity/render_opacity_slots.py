# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Effects UI.

Provides ``RenderOpacitySlots`` — a standalone window for creating, keying and
removing per-object render-effect channels (``opacity`` fades, ``highlight``
pulses) in Maya. The class keeps its historical name because the ``.ui`` it
drives (``render_opacity.ui``) is a frozen header.
"""

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import logging

import mayatk as mtk
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.mat_utils.render_opacity.channels import CHANNELS, HIGHLIGHT, OPACITY


class RenderOpacitySlots:
    """Switchboard slots for the Render Effects UI.

    Layout
    ------
    - **Header**: Title bar.
    - **Create**: Channel combo (Opacity/Highlight) + mode combo
      (Attribute/Material) + create button.
    - **Key**: Key Render Opacity (fade) and Key Highlight Pulse, each with an
      option box.
    - **Manage**: Remove the selected channel's artifacts.
    - **Footer**: Status messages.
    """

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_opacity
        self._pulse_color = None  # (r, g, b) picked in the pulse option box

        # Wire plain QPushButton widgets (not auto-connected by switchboard)
        self.ui.b000.clicked.connect(self._apply_channel)
        self.ui.b003.clicked.connect(self._remove_channel)

        # Selection-changed job to enable/disable key controls
        self._is_updating = False  # Reentrancy guard
        mgr = ScriptJobManager.instance()
        self._sel_token = mgr.subscribe(
            "SelectionChanged",
            self._update_key_enabled,
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
                body="Applies to Create, Key, and Remove operations.",
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
                bullets=[
                    "<b>On:</b> Existing visibility keyframes are deleted before applying opacity.",
                    "<b>Off:</b> Objects with visibility keys are skipped with a warning.",
                ],
            ),
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Render Effects",
                body="Add keyable per-object render-effect channels for "
                "engine-ready control: an <b>Opacity</b> fade (alpha in the "
                "GLB, a Unity controller via FBX) or a <b>Highlight</b> pulse "
                "(an additive emissive glow with a per-object colour). The "
                "<b>Mode</b> combo picks attribute-only or a material binding "
                "that shows the effect in the viewport.",
                steps=[
                    "Select one or more objects.",
                    "Pick a <b>Channel</b> (Opacity / Highlight) and a "
                    "<b>Mode</b>: <i>Attribute</i> (engine-only) or "
                    "<i>Material</i> (also previews in the viewport; each "
                    "object gets its own material).",
                    "Press <b>Create</b>.",
                    "Press <b>Key Render Opacity</b> to key a fade, or "
                    "<b>Key Highlight Pulse</b> to key a repeating glow. Each "
                    "option box (▸) configures timing; the pulse box also "
                    "sets the colour.",
                ],
                sections=[
                    (
                        "Header menu",
                        [
                            "<b>Last Selected Only</b> — only the most-recent "
                            "selection participates in Create / Key / Remove.",
                            "<b>Delete Visibility Keys</b> — when on, existing "
                            "visibility keys are removed before Create; when off, "
                            "objects with vis keys are skipped with a warning.",
                        ],
                    ),
                ],
                notes=[
                    "Material bindings are suspended for the duration of every "
                    "export and re-bound after, so the deliverable always "
                    "carries the authored material.",
                    "Use <b>Remove Channel</b> to clean up every artifact "
                    "(attribute, binding, keys) the tool added for that channel.",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def _channel(self):
        """The channel the combo names (its ``ChannelSpec``)."""
        return CHANNELS.get(self.ui.cmb_channel.currentText().lower(), OPACITY)

    def _get_selected(self):
        """Return the effective selection, respecting 'Last Selected Only'.

        When the header checkbox is checked and the selection is non-empty,
        only the last selected object is returned.
        """
        objects = cmds.ls(selection=True) or []
        if objects and self.ui.header.menu.chk_last_selected.isChecked():
            return objects[-1:]
        return objects

    @mtk.CoreUtils.undoable
    def _apply_channel(self):
        """Create the selected channel on selected objects (or a polyCube first)."""
        mode = self.ui.cmb_mode.currentText().lower()
        spec = self._channel()

        objects = self._get_selected()
        if not objects:
            cube = cmds.polyCube(name=f"{spec.name}_cube")[0]
            objects = [cube]
            cmds.select(objects, replace=True)
            mtk.DisplayUtils.add_to_isolation_set(cube)

        label = ", ".join(objects[:5])
        if len(objects) > 5:
            label += f" … (+{len(objects) - 5} more)"

        delete_vis = self.ui.header.menu.chk_delete_vis_keys.isChecked()

        try:
            results = mtk.RenderEffects.create(
                objects, mode=mode, delete_visibility_keys=delete_vis, channel=spec
            )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            cmds.select(objects, replace=True)

        self.ui.footer.setText(
            f"{spec.name.title()} ({mode}) → {len(results)} object(s): {label}"
        )
        self._update_key_enabled()

    # ------------------------------------------------------------------
    # Key: fade
    # ------------------------------------------------------------------

    def tb000_init(self, widget):
        """Key Render Opacity Init — configure option-box menu."""
        widget.option_box.menu.setTitle("Key Render Opacity")
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
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Create if Missing",
            setObjectName="chk_auto_create",
            setChecked=True,
            setToolTip=(
                "When checked, automatically creates the opacity\n"
                "attribute on selected objects that don't have one\n"
                "(using the mode set in the Create section)."
            ),
        )

    @mtk.CoreUtils.undoable
    def tb000(self, widget):
        """Key Render Opacity — key a fade on the opacity attribute."""
        frames = widget.option_box.menu.s000.value()
        ends_at_cursor = widget.option_box.menu.chk000.isChecked()
        direction_mode = widget.option_box.menu.cmb_direction.currentData()

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                "Select objects with an <hl>opacity</hl> attribute."
            )
            return

        auto_create = widget.option_box.menu.chk_auto_create.isChecked()
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
                    auto_create=auto_create,
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        if keyed:
            dirs = {"Fade In" if d == "in" else "Fade Out" for _, d in keyed}
            direction = " / ".join(sorted(dirs))
            self.ui.footer.setText(
                f"{direction}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
            )
        else:
            self.sb.message_box(
                "Warning: Selected objects have no <hl>opacity</hl> attribute.<br>"
                "Use <b>Create</b> first."
            )

    # ------------------------------------------------------------------
    # Key: highlight pulse
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Key Highlight Pulse Init — configure option-box menu."""
        widget.option_box.menu.setTitle("Key Highlight Pulse")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Frames: ",
            setObjectName="s001",
            setMinimum=1,
            setMaximum=100000,
            setValue=120,
            setToolTip="Length of the pulse, in frames.",
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
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Bright: ",
            setSuffix=" %",
            setObjectName="s003",
            setMinimum=1,
            setMaximum=99,
            setValue=59,
            setToolTip="Share of each cycle spent bright (59% measured on the reference).",
        )
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
        btn = widget.option_box.menu.add(
            "QPushButton",
            setText="Colour…",
            setObjectName="b_pulse_color",
            setToolTip="Pick the highlight colour written to the objects' highlightColor.",
        )
        btn.clicked.connect(self._pick_pulse_color)
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Create if Missing",
            setObjectName="chk_auto_create_hl",
            setChecked=True,
            setToolTip="Create the highlight channel on selected objects that lack it.",
        )

    def _pick_pulse_color(self):
        """Open a colour dialog; remember the pick for the next Key Highlight Pulse."""
        from qtpy import QtGui, QtWidgets

        initial = QtGui.QColor.fromRgbF(*(self._pulse_color or (0.2, 0.5, 1.0)))
        color = QtWidgets.QColorDialog.getColor(initial, self.ui, "Highlight Colour")
        if color.isValid():
            self._pulse_color = (color.redF(), color.greenF(), color.blueF())
            self.ui.footer.setText(
                "Highlight colour: " + ", ".join(f"{c:.2f}" for c in self._pulse_color)
            )

    @mtk.CoreUtils.undoable
    def tb001(self, widget):
        """Key Highlight Pulse — key a repeating glow on the highlight attribute."""
        menu = widget.option_box.menu
        frames = menu.s001.value()
        period_seconds = menu.s002.value()
        bright = menu.s003.value() / 100.0
        ends_at_cursor = menu.chk001.isChecked()
        auto_create = menu.chk_auto_create_hl.isChecked()

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                "Select objects with a <hl>highlight</hl> attribute."
            )
            return

        fps = float(mtk.AudioUtils.get_fps() or 30.0)
        start, end = self._key_range(frames, ends_at_cursor)
        try:
            with ScriptJobManager.instance().suppressed(self._sel_token):
                keyed = mtk.RenderEffects.key_pulse(
                    objects,
                    start=start,
                    end=end,
                    period=period_seconds * fps,
                    bright_fraction=bright,
                    color=self._pulse_color,
                    auto_create=auto_create,
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        if keyed:
            self.ui.footer.setText(
                f"Highlight pulse: {len(keyed)} object(s), frames "
                f"{int(start)}–{int(end)} @ {period_seconds:.2f} s"
            )
        else:
            self.sb.message_box(
                "Warning: Selected objects have no <hl>highlight</hl> attribute.<br>"
                "Use <b>Create</b> first."
            )

    @staticmethod
    def _key_range(frames, ends_at_cursor):
        current = cmds.currentTime(query=True)
        if ends_at_cursor:
            return current - frames, current
        return current, current + frames

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _remove_channel(self):
        """Remove the selected channel's artifacts from selected objects."""
        spec = self._channel()
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                f"Select objects to remove {spec.name} from."
            )
            return

        label = ", ".join(objects[:5])
        if len(objects) > 5:
            label += f" … (+{len(objects) - 5} more)"

        try:
            mtk.RenderEffects.remove(objects, channel=spec)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            cmds.select(objects, replace=True)

        self.ui.footer.setText(
            f"{spec.name.title()} removed from {len(objects)} object(s): {label}"
        )
        self._update_key_enabled()

    # Previous names, kept for their callers (one release).
    _apply_opacity = _apply_channel
    _remove_opacity = _remove_channel

    def _update_fade_enabled(self):
        """Previous name of :meth:`_update_key_enabled` (one release)."""
        self._update_key_enabled()

    # ------------------------------------------------------------------
    # Selection job — enable/disable key controls
    # ------------------------------------------------------------------

    def _update_key_enabled(self):
        """Enable/disable each key widget by whether the selection carries its channel.

        Also re-establishes driver connections that may have been lost
        (e.g. after a Duplicate operation) so the user always operates
        on a healthy object.
        """
        # Reentrancy guard — ensure_connections modifies the DG, which
        # can fire additional callbacks and crash Maya. ``getattr``: a slot
        # built without ``__init__`` (tests) has no guard yet.
        if getattr(self, "_is_updating", False):
            return
        self._is_updating = True
        try:
            # Guard: skip if the UI has been destroyed (prevents crash
            # when the callback fires after the widget is garbage-collected).
            if not self.ui or not self.ui.isVisible():
                return
            selected = cmds.ls(selection=True) or []
            widgets = ((self.ui.tb000, OPACITY), (self.ui.tb001, HIGHLIGHT))
            if not selected:
                for widget, _spec in widgets:
                    for item in widget.option_box.menu.get_items():
                        item.setEnabled(False)
                return

            # Defer scene-modifying work out of the SelectionChanged
            # callback context to prevent reentrant DG evaluation.
            cmds.evalDeferred(
                lambda sel=list(selected): mtk.RenderEffects.ensure_connections(sel)
            )
            for widget, spec in widgets:
                has = any(
                    cmds.attributeQuery(spec.name, node=obj, exists=True)
                    for obj in selected
                )
                for item in widget.option_box.menu.get_items():
                    item.setEnabled(has)
        except RuntimeError:
            pass  # Deleted C++ object — swallow to prevent crash
        except Exception:
            logging.getLogger(__name__).debug(
                "_update_key_enabled error", exc_info=True
            )
        finally:
            self._is_updating = False
