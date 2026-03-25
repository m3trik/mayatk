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
        self._sel_job_id = None
        self._is_updating = False  # Reentrancy guard
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
        widget.menu.add("Separator", setTitle="Options")
        widget.menu.add(
            "QCheckBox",
            setText="Last Selected Only",
            setObjectName="chk_last_selected",
            setChecked=False,
            setToolTip=(
                "When checked, only the last selected object\n"
                "is processed instead of all selected objects.\n"
                "Applies to Create, Key, and Remove operations."
            ),
        )
        widget.menu.add(
            "QCheckBox",
            setText="Delete Visibility Keys",
            setObjectName="chk_delete_vis_keys",
            setChecked=False,
            setToolTip=(
                "When checked, existing visibility keyframes are\n"
                "automatically deleted before applying opacity.\n"
                "When unchecked, objects with visibility keys are\n"
                "skipped with a warning."
            ),
        )
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
                "  3. Press 'Create' to apply.\n"
                "  4. Press 'Key Render Opacity' to animate fades.\n"
                "     • Use the option box to set frames, direction\n"
                "       (Fade In / Fade Out / Auto), and timing.\n"
                "  5. Use 'Remove Opacity' to clean up all artifacts."
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
            setToolTip=(
                "When checked, the fade ends at the playhead\n"
                "(keys span current\u2212frames \u2192 current).\n"
                "When unchecked, the fade starts at the playhead\n"
                "(keys span current \u2192 current+frames)."
            ),
        )
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_direction",
            setToolTip=(
                "Fade direction:\n"
                "\u2022 Fade In: Key opacity 0 \u2192 1.\n"
                "\u2022 Fade Out: Key opacity 1 \u2192 0.\n"
                "\u2022 Auto: Detect from previous key \u2014\n"
                "  if last keyed value is 1 \u2192 fade out,\n"
                "  if 0 or no key \u2192 fade in."
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

        # Auto-create opacity on objects that lack it, if the option is on.
        auto_create = widget.option_box.menu.chk_auto_create.isChecked()
        if auto_create:
            missing = [o for o in objects if not o.hasAttr("opacity")]
            if missing:
                mode = self.ui.cmb_mode.currentText().lower()
                delete_vis = self.ui.header.menu.chk_delete_vis_keys.isChecked()
                try:
                    mtk.RenderOpacity.create(
                        missing, mode=mode, delete_visibility_keys=delete_vis
                    )
                except Exception as e:
                    self.sb.message_box(f"Error: {e}")
                    return

        current = pm.currentTime(query=True)

        if ends_at_cursor:
            start, end = current - frames, current
        else:
            start, end = current, current + frames

        # Suppress the SelectionChanged callback while we modify the DG
        # to prevent reentrant evaluation (which can crash Maya).
        self._kill_selection_job()
        try:
            keyed = []
            for obj in objects:
                if not obj.hasAttr("opacity"):
                    continue

                # Resolve fade direction per object
                if direction_mode == "auto":
                    fade_in = self._resolve_auto_fade(obj, current)
                else:
                    fade_in = direction_mode == "in"

                start_val, end_val = (0, 1) if fade_in else (1, 0)

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
                keyed.append((obj.name(), fade_in))
        finally:
            self._create_selection_job()

        if keyed:
            dirs = {"Fade In" if fi else "Fade Out" for _, fi in keyed}
            direction = " / ".join(sorted(dirs))
            self.ui.footer.setText(
                f"{direction}: {len(keyed)} object(s), frames {int(start)}\u2013{int(end)}"
            )
        else:
            self.sb.message_box(
                "Warning: Selected objects have no <hl>opacity</hl> attribute.<br>"
                "Use <b>Create</b> first."
            )

    @staticmethod
    def _resolve_auto_fade(obj, current_time):
        """Return True for fade-in, False for fade-out based on previous key.

        Looks at the most recent opacity keyframe at or before
        ``current_time``.  If its value is >= 0.5 (opaque), the object
        needs a fade-out; otherwise a fade-in.  Defaults to fade-in
        when no previous key exists.
        """
        key_times = (
            pm.keyframe(obj, attribute="opacity", query=True, timeChange=True) or []
        )

        # Find the latest key at or before current_time
        prev_time = None
        for t in sorted(key_times):
            if t <= current_time:
                prev_time = t
            else:
                break

        if prev_time is None:
            return True  # No previous key \u2014 fade in

        vals = pm.keyframe(
            obj,
            attribute="opacity",
            query=True,
            time=(prev_time, prev_time),
            valueChange=True,
        )
        if vals:
            return vals[0] < 0.5  # Opaque \u2192 fade out; transparent \u2192 fade in

        return True  # Fallback \u2014 fade in

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

    def _create_selection_job(self):
        """Create a scriptJob to track selection changes."""
        if self._sel_job_id is not None:
            return
        self._sel_job_id = cmds.scriptJob(
            event=["SelectionChanged", self._update_fade_enabled],
            killWithScene=True,
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
