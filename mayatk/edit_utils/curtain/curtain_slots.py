# !/usr/bin/python
# coding=utf-8
"""Curtain panel — the Switchboard slots for ``curtain.ui``.

Drives the curtain engine (:mod:`._curtain`: :class:`Rail`,
:class:`CurtainMesh`) through the hermetic
:class:`~mayatk.core_utils.preview.Preview`, with an in-panel preset combo
over the shipped ``presets/`` (the built-in tier; identical to blendertk's).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import BoundingBox
from mayatk.core_utils.preview import Preview
from mayatk.edit_utils.curtain._curtain import CurtainMesh, Rail, Vec

# Shipped, read-only presets (loaded via PresetManager's built-in tier).
_PRESETS_DIR = Path(__file__).resolve().parent / "presets"


class CurtainSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the curtain UI (hermetic preview + presets)."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curtain
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curtain] ")
        self.last_curtain: Optional[str] = None
        self.presets = None
        # Auto-rail state: when nothing usable is selected we own a generated
        # driver curve (``_driver``) built from the Width/Curvature/Hanging-
        # Points/Closed fields. ``_generated`` flags that mode; ``_driver_sig``
        # is the field tuple the current driver was built from, so it's only
        # rebuilt when one of those fields actually changes.
        self._driver: Optional[str] = None
        self._driver_sig: Optional[tuple] = None
        self._generated: bool = False

        # Ensure a rail exists the moment Preview is toggled on (and clean the
        # auto-rail when it's toggled off). Connected BEFORE Preview so it runs
        # first and Preview.enable() finds a selection — you never have to
        # select an unrelated object.
        self.ui.chk000.toggled.connect(self._ensure_rail)

        # Per-parameter reset button (uitk option-box plugin): a small icon
        # button beside each field that resets it to its default on click, or
        # bypasses it to default (greyed, restorable) on Alt/Ctrl+click. The
        # X/Y/Z Position triplet is skipped — it already shares a tight row with
        # the Get button.
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui, skip=("s025", "s026", "s027"))

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            finalize_func=self._finalize,
            message_func=self.sb.message_box,
            # Select Result is first-class in Preview: it (de)selects the
            # curtain on every preview build and on commit (after _finalize
            # discards the auto-rail), and wires chk005 live.
            select_result_checkbox=self.ui.chk005,
            result_provider=lambda: self.last_curtain,
        )
        # Re-drape live as any numeric field changes; rail-shaping fields also
        # resync the generated driver. Closed reshapes the rail; Invert is a
        # pure re-drape.
        self.sb.connect_multi(
            self.ui, "s000-27", "valueChanged", self._on_param_changed
        )
        self.ui.chk001.toggled.connect(self._on_param_changed)
        self.ui.chk004.toggled.connect(self.preview.refresh)

        # The Position fields dropped their "X "/"Y "/"Z " prefixes; color-code
        # the values red/green/blue instead (axis convention) so the row stays
        # compact while still reading per-axis at a glance.
        self._color_code_position_fields()

        # Footer doubles as a stats readout (the result's tri count) once a
        # curtain is built; show a hint until then.
        try:
            self.ui.footer.setDefaultStatusText("Toggle Preview to drape a curtain.")
        except Exception:
            pass

    # --------------------------------------------------------------- header

    def header_init(self, widget):
        """Configure header help text (the preset combo lives in the panel)."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Curtain",
                body="Drape a pleated cloth curtain from a <b>rail</b> — a "
                "selected NURBS curve, polygon edge loop, or chain of locators, "
                "or a generated straight rail when nothing usable is selected.",
                steps=[
                    "Toggle <b>Preview</b> (a rail is auto-created from "
                    "Width/Curvature if you haven't selected your own).",
                    "Set <b>Hanging Points</b> (the pleats/pins) and <b>Fullness</b>.",
                    "Dial <b>Gravity</b> — how far the fabric falls between "
                    "hanging points.",
                    "Press <b>Create</b> to commit.",
                ],
                sections=[
                    (
                        "Model",
                        [
                            "Each <b>Hanging Point</b> is a pleat where the fabric "
                            "pins to the rail — one clean gather at the rail — and "
                            "bellies into a full fold between consecutive points, so "
                            "the count maps roughly 1:1 to the folds you see. The "
                            "spans sag down a real <b>catenary</b> (cosh).",
                            "<b>Gravity</b> sets the sag depth (wider gaps fall "
                            "further); <b>Catenary Tension</b> shapes that curve.",
                            "<b>Taper</b> gathers the pleats at the top and flares "
                            "them toward the hem.",
                            "<b>Mid Folds</b> fork V-folds down from some hang "
                            "points (seed varies which), breaking the plain in/out "
                            "belly; <b>Creases</b> add diagonal V break-lines; "
                            "<b>Sway</b> randomly leans a subset of the folds left "
                            "or right along the rail (not just in/out); the "
                            "<b>Ends</b> group bends each end; <b>Round</b> softens "
                            "the hooks.",
                        ],
                    ),
                ],
                notes=[
                    "The <b>preset</b> combo loads built-in looks "
                    "(Stage Swag, Shower Curtain) and saves your own.",
                    "<b>Select Result</b> selects the finished curtain on "
                    "<b>Create</b> so you can see the result.",
                ],
            )
        )
        # Align every spinbox's value column once the panel's fonts/styles are
        # settled (deferred a tick so QFontMetrics sees the themed font).
        try:
            from qtpy import QtCore

            QtCore.QTimer.singleShot(0, self._align_spinbox_prefixes)
        except Exception as e:
            self.logger.debug(f"Prefix alignment deferral failed: {e}")

    def cmb000_init(self, widget):
        """Wire the in-panel preset selector (built-in + user tiers)."""
        try:
            from uitk.managers.preset_manager import PresetManager

            self.presets = PresetManager(
                parent=self.ui,
                state=self.ui.state,
                preset_dir="mayatk/curtain",
                builtin_dir=str(_PRESETS_DIR),
            )
            # on_loaded resyncs the generated driver to the loaded fields, then
            # refreshes the preview in one shot.
            self.presets.wire_combo(widget, on_loaded=self._on_param_changed)
        except Exception as e:
            self.logger.warning(f"Preset combo unavailable: {e}")

    # ------------------------------------------------- spinbox value alignment

    def _color_code_position_fields(self) -> None:
        """Tint the rail Position values red/green/blue for X/Y/Z.

        The fields dropped their "X "/"Y "/"Z " prefixes (see curtain.ui); the
        axis-coded value text now carries that meaning at a glance, with the
        tooltips naming the axis as a textual fallback. Colors come from the
        shared ``pythontk.Palette.axes()`` (Maya/3D RGB convention), applied via
        the uitk ``SpinBox``/``DoubleSpinBox`` ``set_text_color`` helper.
        """
        try:
            axes = ptk.Palette.axes()
        except Exception as e:
            self.logger.debug(f"Position color-coding unavailable: {e}")
            return
        for name, key in (("s025", "x"), ("s026", "y"), ("s027", "z")):
            setter = getattr(getattr(self.ui, name, None), "set_text_color", None)
            if callable(setter):
                setter(axes[key].hex)

    def _align_spinbox_prefixes(self) -> None:
        """Pad each spinbox prefix so the values line up within each group.

        The custom spin widgets add a single ``\\t`` after the prefix, which
        only lands on one tab stop — long prefixes ("Catenary Tension:") then
        overflow past short ones ("Seed:"), so the value columns don't align.
        Here we measure the widest prefix per section (with the widget's own
        font metrics) and right-pad the rest with spaces to match (font-correct
        to within a space width), bypassing the ``\\t``. Aligning per group —
        keyed on each spinbox's container — keeps short-labelled sections tight
        instead of indenting them to clear a long label elsewhere.
        """
        try:
            from qtpy import QtWidgets, QtGui
        except Exception:
            return

        # Bucket the spinboxes by their titled group. Walk up to the nearest
        # CollapsableGroup rather than using the immediate parent, since the
        # option-box "disable" wrapping reparents each spinbox into its own
        # container — grouping on that would defeat the per-section alignment.
        try:
            from uitk.widgets.collapsableGroup import CollapsableGroup
        except Exception:
            CollapsableGroup = ()

        def _group_of(w):
            p = w.parentWidget()
            while p is not None:
                if CollapsableGroup and isinstance(p, CollapsableGroup):
                    return p
                p = p.parentWidget()
            return w.parentWidget()

        groups = {}
        for sb in self.ui.findChildren(QtWidgets.QAbstractSpinBox):
            # The AUTHORED label, not the rendered one: uitk's PrefixColumnMixin
            # collapses (and past a point elides) the prefix to keep the value
            # visible in a narrow field, so prefix() can be a truncated form.
            # Falls back for a plain QSpinBox and for a re-run over prefixes
            # this method already padded (which the mixin leaves verbatim).
            base = getattr(sb, "prefix_label", lambda: "")() or sb.prefix().rstrip()
            if not base:
                continue
            groups.setdefault(_group_of(sb), []).append(
                (sb, base, QtGui.QFontMetrics(sb.font()))
            )

        for entries in groups.values():
            max_w = max(fm.horizontalAdvance(base) for _, base, fm in entries)
            for sb, base, fm in entries:
                space_w = fm.horizontalAdvance(" ") or 1
                gap = max_w + 2 * space_w - fm.horizontalAdvance(base)
                text = base + " " * max(1, round(gap / space_w))
                # Bypass the custom setPrefix: an exact, hand-composed prefix is
                # the one form PrefixColumnMixin leaves alone, and routing it
                # through the override would strip this padding back off.
                if isinstance(sb, QtWidgets.QDoubleSpinBox):
                    QtWidgets.QDoubleSpinBox.setPrefix(sb, text)
                else:
                    QtWidgets.QSpinBox.setPrefix(sb, text)

    # ----------------------------------------------------------- rail / driver

    def _on_param_changed(self, *_):
        """A field changed: resync the generated driver (if any) and re-drape.

        The driver only resyncs while a preview is live — otherwise a slider
        nudge after committing (preview off) would spawn a stray rail.
        """
        if self.preview.is_enabled:
            self._sync_driver()
        self.preview.refresh()

    def _field_rail(self) -> Tuple[List[Vec], bool]:
        """The generated rail from the Width / Curvature / Position / Closed fields."""
        return Rail.make(
            width=self.ui.s001.value(),
            curvature=self.ui.s002.value(),
            closed=self.ui.chk001.isChecked(),
            center=(self.ui.s025.value(), self.ui.s026.value(), self.ui.s027.value()),
        )

    def _build_driver(self, points: Sequence[Vec], closed: bool) -> str:
        """Build a low-CV rail curve whose CVs sit at the hanging points.

        Used as the preview's visible rail — resampled to ``hanging_points``
        control points so it reads as the line of pins the cloth gathers on.
        """
        n = max(2, int(self.ui.s003.value()))
        ctrl = Rail.resample(points, n)
        crv = cmds.curve(point=ctrl, degree=min(3, max(1, len(ctrl) - 1)))
        if closed:
            cmds.closeCurve(crv, ch=False, replaceOriginal=True)
        return cmds.rename(crv, "curtain_rail")

    def _sync_driver(self, force: bool = False) -> None:
        """Rebuild the owned driver curve when a rail-shaping field changed.

        No-op unless we're in generated mode. The signature
        (width/curvature/position/closed/hanging-points) gates the rebuild so
        dragging a drape-only field (gravity, taper, hang spacing…) doesn't churn
        the curve.
        """
        if not self._generated:
            return
        sig = (
            self.ui.s001.value(),
            self.ui.s002.value(),
            self.ui.chk001.isChecked(),
            int(self.ui.s003.value()),
            self.ui.s025.value(),
            self.ui.s026.value(),
            self.ui.s027.value(),
        )
        have = bool(self._driver and cmds.objExists(self._driver))
        if have and not force and sig == self._driver_sig:
            return
        if have:
            cmds.delete(self._driver)
        points, closed = self._field_rail()
        self._driver = self._build_driver(points, closed)
        self._driver_sig = sig
        cmds.select(self._driver)

    def _discard_driver(self) -> None:
        """Delete the generated driver curve we own (orphan-rail cleanup)."""
        if self._driver and cmds.objExists(self._driver):
            try:
                cmds.delete(self._driver)
            except Exception:
                pass
        self._driver = None
        self._driver_sig = None

    def _user_selection(self):
        """Current selection minus our own driver curve."""
        return [s for s in (cmds.ls(selection=True) or []) if s != self._driver]

    def _ensure_rail(self, state: bool) -> None:
        """On preview-enable, guarantee a usable rail; on disable, clean ours.

        If the user has their own rail selected we hang on that (Width/Curvature
        are ignored). Otherwise we enter *generated* mode and build/select a
        driver curve — both to satisfy Preview's selection gate and to show the
        rail the cloth hangs on (dropped on commit).
        """
        if not state:
            self._discard_driver()
            return
        if Rail.from_selection(self._user_selection()) is not None:
            # Hang on the user's own rail; drop any auto-rail we still own.
            self._discard_driver()
            self._generated = False
            return
        self._generated = True
        self._sync_driver(force=True)

    def _resolve_rail(self, objects) -> Tuple[List[Vec], bool]:
        """Rail points for the current drape.

        Generated mode reads the Width/Curvature/Closed fields live (so they
        take effect on every refresh); selected mode resolves the user's rail.
        """
        if not self._generated:
            rail = Rail.from_selection([o for o in objects if o != self._driver])
            if rail is not None:
                points, closed = rail
                return points, closed or self.ui.chk001.isChecked()
        return self._field_rail()

    # --------------------------------------------------------------- buttons

    def b001_init(self, widget):
        """Reset to Defaults, on uitk's shared reset grammar (Shift+Click saves the
        current values as the defaults, Ctrl+Shift+Click forgets them)."""
        from uitk.managers.reset_gesture import ResetGesture

        self._reset_gesture = ResetGesture(widget)

    def b002(self):
        """Set Position to the bounding-box center of the selected object(s).

        Centers the generated rail on whatever is selected (its combined world
        bounding box). Ignores the panel's own auto-rail driver and the curtain
        it's building, so Get centers on the *external* target. The three
        Position fields are set in one shot (signals blocked) and a single
        re-drape is fired, so the curtain re-centers immediately.
        """
        ours = {self._driver, self.last_curtain}
        sel = [
            s for s in (cmds.ls(selection=True, flatten=True) or []) if s not in ours
        ]
        if not sel:
            self.sb.message_box("Select object(s) to center the rail on.")
            return
        bb = cmds.exactWorldBoundingBox(sel)  # combined; accepts one or many
        center = BoundingBox(bb[:3], bb[3:]).center
        for widget, value in (
            (self.ui.s025, center.x),
            (self.ui.s026, center.y),
            (self.ui.s027, center.z),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._on_param_changed()

    # ------------------------------------------------------------- operation

    def perform_operation(self, objects, contract):
        """Build the curtain from the resolved rail (Preview entry point)."""
        points, closed = self._resolve_rail(objects)

        self.last_curtain = CurtainMesh(
            points,
            height=self.ui.s000.value(),
            hanging_points=self.ui.s003.value(),
            hang_jitter=self.ui.s023.value(),
            hang_seed=self.ui.s024.value(),
            gravity=self.ui.s004.value(),
            tension=self.ui.s005.value(),
            round_points=self.ui.s013.value(),
            round_gather=self.ui.s022.value(),
            fullness=self.ui.s006.value(),
            taper=self.ui.s007.value(),
            mid_folds=self.ui.s019.value(),
            mid_fold_seed=self.ui.s010.value(),
            creases=self.ui.s014.value(),
            crease_seed=self.ui.s015.value(),
            sway=self.ui.s020.value(),
            sway_seed=self.ui.s021.value(),
            end_bend_left=self.ui.s016.value(),
            end_bend_right=self.ui.s017.value(),
            end_bend_falloff=self.ui.s018.value(),
            irregularity=self.ui.s008.value(),
            density=self.ui.s009.value(),
            reduce=self.ui.s012.value(),
            thickness=self.ui.s011.value(),
            invert=self.ui.chk004.isChecked(),
            closed=closed,
        ).build()
        self._update_footer()
        # Select Result is applied by Preview itself (it owns the checkbox +
        # result_provider) after this build and on commit -- see __init__.

    def _update_footer(self):
        """Show the result's triangle count in the footer; clears to the default
        hint when there is no result. Updates live as the preview re-drapes."""
        try:
            footer = self.ui.footer
        except Exception:
            return
        curtain = self.last_curtain
        if not curtain or not cmds.objExists(curtain):
            footer.setStatusText("")  # falls back to the default hint
            return
        tris = cmds.polyEvaluate(curtain, triangle=True) or 0
        footer.setStatusText(f"{tris:,} tris")

    def _finalize(self):
        """On commit, drop the preview's auto-rail.

        The auto-rail is only a preview aid (it shows where the cloth hangs and
        satisfies Preview's selection gate); it isn't wanted in the committed
        scene. Wrapped in its own undo chunk (finalize_func runs outside
        Preview's commit chunk). The next preview recomputes the rail mode from
        the live selection, so the mode flag is cleared here. Preview applies
        the Select Result toggle *after* this runs (the discard can change the
        active selection), so the result wins.
        """
        self._generated = False
        cmds.undoInfo(openChunk=True)
        try:
            self._discard_driver()
        finally:
            cmds.undoInfo(closeChunk=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("curtain", reload=True)
    ui.show(pos="screen", app_exec=True)
