# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Opacity UI.

Provides ``RenderOpacitySlots`` — a standalone window for creating,
keying fades, and removing per-object opacity in Maya.
"""
try:
    import pymel.core as pm
    import maya.cmds as cmds
except ImportError:
    pass

import logging

import mayatk as mtk
from mayatk.core_utils.script_job_manager import ScriptJobManager
from uitk.widgets.mixins.tooltip_mixin import fmt


class RenderOpacitySlots:
    """Switchboard slots for the Render Opacity UI.

    Layout
    ------
    - **Header**: Title bar.
    - **Apply**: Mode combo (Material/Attribute) + apply button.
    - **Keyframe Fade**: Frames spinner, fade in/out, end-at-playhead.
    - **Manage**: Remove opacity artifacts.
    - **Footer**: Status messages.
    """

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_opacity

        # Wire plain QPushButton widgets (not auto-connected by switchboard)
        self.ui.b000.clicked.connect(self._apply_opacity)
        self.ui.b003.clicked.connect(self._remove_opacity)

        # Selection-changed job to enable/disable fade controls
        self._is_updating = False  # Reentrancy guard
        mgr = ScriptJobManager.instance()
        self._sel_token = mgr.subscribe(
            "SelectionChanged",
            self._update_fade_enabled,
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
            setToolTip=fmt(
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
            setToolTip=fmt(
                bullets=[
                    "<b>On:</b> Existing visibility keyframes are deleted before applying opacity.",
                    "<b>Off:</b> Objects with visibility keys are skipped with a warning.",
                ],
            ),
        )
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=fmt(
                title="Render Opacity",
                body="Adds a keyable opacity attribute to objects for engine-ready transparency control.",
                steps=[
                    "Select one or more objects.",
                    "Choose Material or Attribute mode (option box ▸).",
                    "Press <b>Create</b> to apply.",
                    "Press <b>Key Render Opacity</b> to animate fades. Use the option box to set frames, direction (Fade In / Fade Out / Auto), and timing.",
                    "Use <b>Remove Opacity</b> to clean up all artifacts.",
                ],
            ),
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _get_selected(self):
        """Return the effective selection, respecting 'Last Selected Only'.

        When the header checkbox is checked and the selection is non-empty,
        only the last selected object is returned.
        """
        objects = pm.selected()
        if objects and self.ui.header.menu.chk_last_selected.isChecked():
            return objects[-1:]
        return objects

    @mtk.CoreUtils.undoable
    def _apply_opacity(self):
        """Apply Render Opacity to selected objects (or create a polyCube first)."""
        mode = self.ui.cmb_mode.currentText().lower()

        objects = self._get_selected()
        if not objects:
            cube = pm.polyCube(name="opacity_cube")[0]
            objects = [cube]
            pm.select(objects, replace=True)

        names = [o.name() for o in objects]
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" … (+{len(names) - 5} more)"

        delete_vis = self.ui.header.menu.chk_delete_vis_keys.isChecked()

        try:
            results = mtk.RenderOpacity.create(
                objects, mode=mode, delete_visibility_keys=delete_vis
            )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            pm.select(objects, replace=True)

        self.ui.footer.setText(
            f"{mode.title()} opacity → {len(results)} object(s): {label}"
        )
        self._update_fade_enabled()

    # ------------------------------------------------------------------
    # Keyframe Fade
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
            setToolTip=fmt(
                bullets=[
                    "<b>On:</b> Fade ends at the playhead (keys span current\u2212frames \u2192 current).",
                    "<b>Off:</b> Fade starts at the playhead (keys span current \u2192 current+frames).",
                ],
            ),
        )
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_direction",
            setToolTip=fmt(
                title="Fade Direction",
                bullets=[
                    "<b>Fade In:</b> Key opacity 0 \u2192 1.",
                    "<b>Fade Out:</b> Key opacity 1 \u2192 0.",
                    "<b>Auto:</b> Detect from previous key \u2014 if last value is 1 \u2192 fade out; if 0 or no key \u2192 fade in.",
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
        """Key Render Opacity \u2014 key a fade on the opacity attribute."""
        frames = widget.option_box.menu.s000.value()
        ends_at_cursor = widget.option_box.menu.chk000.isChecked()
        cmb = widget.option_box.menu.cmb_direction
        direction_mode = cmb.currentData()

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                "Select objects with an <hl>opacity</hl> attribute."
            )
            return

        auto_create = widget.option_box.menu.chk_auto_create.isChecked()

        current = pm.currentTime(query=True)

        if ends_at_cursor:
            start, end = current - frames, current
        else:
            start, end = current, current + frames

        # Suppress the SelectionChanged callback while we modify the DG
        # to prevent reentrant evaluation (which can crash Maya).
        mgr = ScriptJobManager.instance()
        mgr.suppress(self._sel_token)
        try:
            keyed = mtk.RenderOpacity.key_fade(
                objects,
                start=start,
                end=end,
                direction=direction_mode,
                auto_create=auto_create,
            )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            mgr.resume(self._sel_token)

        if keyed:
            dirs = {"Fade In" if d == "in" else "Fade Out" for _, d in keyed}
            direction = " / ".join(sorted(dirs))
            self.ui.footer.setText(
                f"{direction}: {len(keyed)} object(s), frames {int(start)}\u2013{int(end)}"
            )
        else:
            self.sb.message_box(
                "Warning: Selected objects have no <hl>opacity</hl> attribute.<br>"
                "Use <b>Create</b> first."
            )

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _remove_opacity(self):
        """Remove all opacity artifacts from selected objects."""
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                "Select objects to remove opacity from."
            )
            return

        names = [o.name() for o in objects]
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" … (+{len(names) - 5} more)"

        try:
            mtk.RenderOpacity.remove(objects)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            pm.select(objects, replace=True)

        self.ui.footer.setText(
            f"Opacity removed from {len(objects)} object(s): {label}"
        )
        self._update_fade_enabled()

    # ------------------------------------------------------------------
    # Selection job — enable/disable fade controls
    # ------------------------------------------------------------------

    def _update_fade_enabled(self):
        """Enable/disable fade widgets based on whether selection has opacity.

        Also re-establishes driver connections that may have been lost
        (e.g. after a Duplicate operation) so the user always operates
        on a healthy object.
        """
        # Reentrancy guard — ensure_connections modifies the DG, which
        # can fire additional callbacks and crash Maya.
        if self._is_updating:
            return
        self._is_updating = True
        try:
            # Guard: skip if the UI has been destroyed (prevents crash
            # when the callback fires after the widget is garbage-collected).
            if not self.ui or not self.ui.isVisible():
                return

            selected = pm.selected()
            if not selected:
                for item in self.ui.tb000.option_box.menu.get_items():
                    item.setEnabled(False)
                return

            # Defer scene-modifying work out of the SelectionChanged
            # callback context to prevent reentrant DG evaluation.
            cmds.evalDeferred(
                lambda sel=list(selected): mtk.RenderOpacity.ensure_connections(sel)
            )

            has_opacity = any(obj.hasAttr("opacity") for obj in selected)
            for item in self.ui.tb000.option_box.menu.get_items():
                item.setEnabled(has_opacity)
        except RuntimeError:
            pass  # Deleted C++ object — swallow to prevent crash
        except Exception:
            logging.getLogger(__name__).debug(
                "_update_fade_enabled error", exc_info=True
            )
        finally:
            self._is_updating = False
