# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Scene Builder UI.

Bridges the Scene Builder dialog to the CSV parser and
:class:`~mayatk.anim_utils.scene_builder._scene_builder.SceneBuilder` engine.
"""
from typing import List, Optional

import pythontk as ptk

from mayatk.anim_utils.scene_builder._scene_builder import (
    BuilderStep,
    SceneBuilder,
    parse_csv,
    detect_behavior,
)
from mayatk.anim_utils.behavior_keys import list_behaviors


class SceneBuilderController(ptk.LoggingMixin):
    """Business logic for the Scene Builder UI."""

    def __init__(self, slots_instance):
        super().__init__()
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._steps: List[BuilderStep] = []
        self._csv_path: str = ""

    # ---- CSV loading -----------------------------------------------------

    def browse_csv(self) -> None:
        """Open a file dialog and load the selected CSV."""
        from qtpy.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self.ui, "Open Sequence CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        self._csv_path = path
        self.ui.txt_csv_path.setText(path)
        self._load_csv(path)

    def _load_csv(self, path: str) -> None:
        """Parse the CSV, populate the table, and update the summary."""
        try:
            self._steps = parse_csv(path)
        except Exception as exc:
            self.logger.error("Failed to parse CSV: %s", exc)
            self.ui.lbl_summary.setText(f"Error: {exc}")
            return

        self._populate_table()
        n_obj = sum(len(s.objects) for s in self._steps)
        self.ui.lbl_summary.setText(
            f"{len(self._steps)} steps, {n_obj} objects loaded."
        )

    # ---- table population ------------------------------------------------

    _HEADERS = ["Step", "Section", "Content", "Objects", "Behaviors"]
    _PREVIEW_HEADERS = [
        "Step",
        "Section",
        "Start",
        "End",
        "Objects",
        "Behaviors",
    ]

    def _populate_table(self) -> None:
        """Fill the TreeWidget with parsed steps and expandable object rows."""
        tree = self.ui.tbl_steps
        tree.clear()
        tree.setHeaderLabels(self._HEADERS)
        tree.setColumnCount(len(self._HEADERS))

        for step in self._steps:
            unique_behaviors = ", ".join(
                dict.fromkeys(o.behavior for o in step.objects if o.behavior)
            )
            section = (
                f"{step.section}: {step.section_title}"
                if step.section_title
                else step.section
            )
            obj_summary = f"{len(step.objects)} object(s)" if step.objects else ""

            parent = tree.create_item(
                [step.step_id, section, step.content, obj_summary, unique_behaviors],
                data=step,
            )
            # Add child rows for each object
            for obj in step.objects:
                tree.create_item(
                    ["", "", "", obj.name, obj.behavior or ""],
                    data=obj,
                    parent=parent,
                )

        tree.set_stretch_column(2)  # Stretch "Content" column

    # ---- helpers ---------------------------------------------------------

    def _ensure_steps(self) -> bool:
        """Load CSV from the text field if steps are empty. Returns True if steps are available."""
        if not self._steps:
            path = self.ui.txt_csv_path.text().strip()
            if path:
                self._load_csv(path)
        if not self._steps:
            self.ui.lbl_summary.setText("Load a CSV first.")
            return False
        return True

    # ---- preview ---------------------------------------------------------

    def preview(self) -> None:
        """Populate the tree with planned scene layout (no Maya changes)."""
        if not self._ensure_steps():
            return

        from mayatk.anim_utils.sequencer._sequencer import Sequencer

        builder = SceneBuilder(
            Sequencer(),
            step_duration=self.ui.spn_duration.value(),
            gap=self.ui.spn_gap.value(),
            start_frame=self.ui.spn_start_frame.value(),
        )
        layout = builder.preview(self._steps)

        tree = self.ui.tbl_steps
        tree.clear()
        tree.setHeaderLabels(self._PREVIEW_HEADERS)
        tree.setColumnCount(len(self._PREVIEW_HEADERS))

        for entry in layout:
            unique_behaviors = ", ".join(
                dict.fromkeys(o["behavior"] for o in entry["objects"] if o["behavior"])
            )
            obj_summary = (
                f"{len(entry['objects'])} object(s)" if entry["objects"] else ""
            )

            parent = tree.create_item(
                [
                    entry["step_id"],
                    entry.get("section", ""),
                    f"{entry['start']:.0f}",
                    f"{entry['end']:.0f}",
                    obj_summary,
                    unique_behaviors,
                ],
                data=entry,
            )
            for obj in entry["objects"]:
                tree.create_item(
                    ["", "", "", "", obj["name"], obj["behavior"] or ""],
                    data=obj,
                    parent=parent,
                )

        tree.set_stretch_column(4)  # Stretch "Objects" column

        total_frames = layout[-1]["end"] - layout[0]["start"] if layout else 0
        self.ui.lbl_summary.setText(
            f"Preview: {len(layout)} scenes, {total_frames:.0f} frames"
        )

    # ---- build -----------------------------------------------------------

    def build(self) -> None:
        """Build scenes in the sequencer from loaded steps."""
        if not self._ensure_steps():
            return

        try:
            import pymel.core as pm
        except ImportError:
            self.ui.lbl_summary.setText("Maya is required to build scenes.")
            return

        from qtpy.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self.ui,
            "Build Scenes",
            f"Create {len(self._steps)} scenes in the Maya timeline?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from mayatk.anim_utils.sequencer._sequencer import Sequencer

        try:
            seq = Sequencer.load() or Sequencer()
            builder = SceneBuilder(
                seq,
                step_duration=self.ui.spn_duration.value(),
                gap=self.ui.spn_gap.value(),
                start_frame=self.ui.spn_start_frame.value(),
            )

            pm.undoInfo(openChunk=True, chunkName="SceneBuilder_build")
            try:
                builder.build(self._steps)
            finally:
                pm.undoInfo(closeChunk=True)

            seq.save()

            if seq.scenes:
                pm.displayInfo(
                    f"Scene Builder: created {len(self._steps)} scenes "
                    f"({seq.scenes[0].start:.0f}-{seq.scenes[-1].end:.0f})"
                )

            self.ui.lbl_summary.setText(
                f"Built {len(self._steps)} scenes. Saved to Maya scene."
            )
        except Exception as exc:
            self.logger.error("Build failed: %s", exc)
            self.ui.lbl_summary.setText(f"Build error: {exc}")


class SceneBuilderSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.scene_builder

        self.controller = SceneBuilderController(self)

    # ---- header ----------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        widget.config_buttons("menu", "pin")
        widget.menu.setTitle("Scene Builder:")

    # ---- buttons ---------------------------------------------------------

    def b001(self):
        """Browse for CSV file."""
        self.controller.browse_csv()

    def b002(self):
        """Preview planned layout."""
        self.controller.preview()

    def b003(self):
        """Build scenes from loaded CSV."""
        self.controller.build()
