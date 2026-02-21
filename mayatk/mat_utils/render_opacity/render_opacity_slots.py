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

import mayatk as mtk


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
        self.ui.b001.clicked.connect(self._key_fade_in)
        self.ui.b002.clicked.connect(self._key_fade_out)
        self.ui.b003.clicked.connect(self._remove_opacity)

        # Selection-changed job to enable/disable fade controls
        self._sel_job_id = None
        try:
            cmds.evalDeferred(self._create_selection_job)
        except Exception:
            pass

        # Ensure the scriptJob is killed when the UI closes to prevent
        # callbacks firing against a dead widget.
        try:
            self.ui.destroyed.connect(self._kill_selection_job)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        widget.config_buttons("menu", "pin")
        widget.menu.setTitle("Render Opacity:")

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Render Opacity — Adds a keyable opacity attribute to objects\n"
                "for engine-ready transparency control.\n\n"
                "Workflow:\n"
                "  1. Select one or more objects.\n"
                "  2. Choose Material or Attribute mode (option box ▸).\n"
                "  3. Press 'Render Opacity' to apply.\n"
                "  4. Use Keyframe Fade to animate opacity over time.\n"
                "  5. Use Remove Opacity to clean up all artifacts."
            ),
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _apply_opacity(self):
        """Apply Render Opacity to selected objects (or create a polyCube first)."""
        mode = self.ui.cmb_mode.currentText().lower()

        objects = pm.selected()
        if not objects:
            cube = pm.polyCube(name="opacity_cube")[0]
            objects = [cube]
            pm.select(objects, replace=True)

        names = [o.name() for o in objects]
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" … (+{len(names) - 5} more)"

        try:
            results = mtk.RenderOpacity.create(objects, mode=mode)
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
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

    def _key_fade_in(self):
        """Key opacity from 0 → 1 starting at the current frame."""
        self._key_opacity_fade(fade_in=True)

    def _key_fade_out(self):
        """Key opacity from 1 → 0."""
        self._key_opacity_fade(fade_in=False)

    @mtk.CoreUtils.undoable
    def _key_opacity_fade(self, fade_in=True):
        """Key a fade-in or fade-out on the opacity attribute."""
        frames = self.ui.s000.value()
        objects = pm.selected()
        if not objects:
            self.ui.footer.setText("Select objects with an opacity attribute.")
            return

        current = pm.currentTime(query=True)
        ends_at_cursor = self.ui.chk000.isChecked()

        if fade_in:
            start_val, end_val = 0, 1
            if ends_at_cursor:
                start, end = current - frames, current
            else:
                start, end = current, current + frames
        else:
            start_val, end_val = 1, 0
            if ends_at_cursor:
                start, end = current - frames, current
            else:
                start, end = current, current + frames

        keyed = []
        for obj in objects:
            if not obj.hasAttr("opacity"):
                continue
            pm.setKeyframe(
                obj,
                attribute="opacity",
                time=start,
                value=start_val,
                inTangentType="linear",
                outTangentType="linear",
            )
            pm.setKeyframe(
                obj,
                attribute="opacity",
                time=end,
                value=end_val,
                inTangentType="linear",
                outTangentType="linear",
            )
            keyed.append(obj.name())

        direction = "Fade In" if fade_in else "Fade Out"
        if keyed:
            label = ", ".join(keyed[:5])
            if len(keyed) > 5:
                label += f" … (+{len(keyed) - 5} more)"
            self.ui.footer.setText(
                f"{direction}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
            )
        else:
            self.ui.footer.setText(f"{direction}: No objects have opacity attribute.")

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _remove_opacity(self):
        """Remove all opacity artifacts from selected objects."""
        objects = pm.selected()
        if not objects:
            self.ui.footer.setText("Select objects to remove opacity from.")
            return

        names = [o.name() for o in objects]
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" … (+{len(names) - 5} more)"

        try:
            mtk.RenderOpacity.remove(objects)
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
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

    def _create_selection_job(self):
        """Create a scriptJob to track selection changes."""
        if self._sel_job_id is not None:
            return
        self._sel_job_id = cmds.scriptJob(
            event=["SelectionChanged", self._update_fade_enabled]
        )

    def _kill_selection_job(self):
        """Kill the SelectionChanged scriptJob when the UI is destroyed."""
        if self._sel_job_id is not None:
            try:
                cmds.scriptJob(kill=self._sel_job_id, force=True)
            except Exception:
                pass
            self._sel_job_id = None

    def _update_fade_enabled(self):
        """Enable/disable fade widgets based on whether selection has opacity."""
        has_opacity = any(obj.hasAttr("opacity") for obj in pm.selected())
        self.ui.s000.setEnabled(has_opacity)
        self.ui.b001.setEnabled(has_opacity)
        self.ui.b002.setEnabled(has_opacity)
        self.ui.chk000.setEnabled(has_opacity)
