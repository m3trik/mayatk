# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Manifest UI.

Bridges the Shot Manifest dialog to the CSV parser and
:class:`~mayatk.anim_utils.shot_manifest._shot_manifest.ShotManifest` engine.
"""
from typing import Dict, List, Optional, Tuple

import pythontk as ptk

from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderStep,
    BuilderObject,
    ShotManifest,
    StepStatus,
    parse_csv,
    detect_behavior,
    detect_animation_gaps,
)
from mayatk.anim_utils.shots.behaviors import list_behaviors


# Pastel red used for error labels in the footer
_ERROR_COLOR = "#D4908F"


class ShotManifestController(ptk.LoggingMixin):
    """Business logic for the Shot Manifest UI."""

    _SETTINGS_NS = "ShotManifest"

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui
        self._steps: List[BuilderStep] = []
        self._csv_path: str = ""
        self._store = None  # ShotStore from last build
        self._last_results: list = []  # Last assessment results

        from uitk.widgets.mixins.settings_manager import SettingsManager

        self._settings = SettingsManager(namespace=self._SETTINGS_NS)

        self._user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

        tree = self.ui.tbl_steps
        tree.enable_column_config()

        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QAbstractItemView

        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_item_menu)
        tree.itemDoubleClicked.connect(self._on_range_double_clicked)
        tree.itemChanged.connect(self._on_item_changed)

        self._building = False
        self._store_listener_bound = False
        self._cached_gaps: Optional[List[float]] = None
        self._last_resolved: List[Tuple[str, float, Optional[float], bool]] = []
        self._bind_store_listener()

    # ---- built state -----------------------------------------------------

    @property
    def _is_built(self) -> bool:
        """True if any CSV step already exists as a shot in the store."""
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            built_map = {s.name for s in ShotStore.active().shots}
        except Exception:
            return False
        return any(step.step_id in built_map for step in self._steps)

    # ---- range column editing --------------------------------------------

    def _on_range_double_clicked(self, item, column) -> None:
        """Allow editing only the Range column on parent rows pre-build."""
        if column != self._COL_RANGE:
            return
        if item.parent() is not None:
            return  # child row
        if self._is_built:
            return  # read-only post-build
        tree = self.ui.tbl_steps
        tree.editItem(item, column)

    def _on_item_changed(self, item, column) -> None:
        """Capture user edits to the Range column into ``_user_ranges``."""
        if column != self._COL_RANGE:
            return
        if item.parent() is not None:
            return
        from qtpy.QtCore import Qt

        step_data = item.data(0, Qt.UserRole)
        if not isinstance(step_data, BuilderStep):
            return

        raw = item.text(column).strip()
        if not raw:
            self._user_ranges.pop(step_data.step_id, None)
            self._validate_range_collisions()
            return

        self._parse_and_store_range(step_data.step_id, raw)
        self._validate_range_collisions()

    def _parse_and_store_range(self, step_id: str, raw: str) -> None:
        """Parse a range string and store it in ``_user_ranges``.

        Accepts ``"120"`` (start only) or ``"120-250"`` / ``"120\u2013250"``.
        """
        raw = raw.replace("\u2013", "-")  # en-dash to hyphen
        parts = [p.strip() for p in raw.split("-", 1)]
        try:
            start = float(parts[0])
        except (ValueError, IndexError):
            return
        end: Optional[float] = None
        if len(parts) == 2 and parts[1]:
            try:
                end = float(parts[1])
            except ValueError:
                pass
        self._user_ranges[step_id] = (start, end)

    def _restore_user_ranges(self, tree) -> None:
        """Write ``_user_ranges`` values back into Range cells after a table rebuild."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        dim = QBrush(QColor("#888888"))
        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                user_range = self._user_ranges.get(step_data.step_id)
                if user_range is None:
                    # Auto-filled values appear dim (set by assess/auto-fill later)
                    if parent.text(self._COL_RANGE):
                        parent.setForeground(self._COL_RANGE, dim)
                    continue
                start, end = user_range
                if end is not None:
                    parent.setText(self._COL_RANGE, f"{start:.0f}\u2013{end:.0f}")
                else:
                    parent.setText(self._COL_RANGE, f"{start:.0f}")
        finally:
            tree.blockSignals(False)

    # ---- auto-fill logic -------------------------------------------------

    @staticmethod
    def _prune_to_top_boundaries(
        region_starts: List[float], n_steps: int
    ) -> List[float]:
        """Keep only *n_steps* region starts by selecting the largest gaps.

        Picks the *n_steps - 1* largest consecutive differences in
        *region_starts* as the primary shot boundaries, then returns
        the first region plus the region after each selected boundary.
        """
        if len(region_starts) <= n_steps:
            return region_starts
        diffs = [
            (region_starts[i + 1] - region_starts[i], i)
            for i in range(len(region_starts) - 1)
        ]
        diffs.sort(key=lambda x: -x[0])
        top_indices = sorted(d[1] for d in diffs[: n_steps - 1])
        selected = [region_starts[0]]
        for idx in top_indices:
            selected.append(region_starts[idx + 1])
        return selected

    def _resolve_ranges(
        self,
        from_step_idx: int = 0,
    ) -> List[Tuple[str, float, Optional[float], bool]]:
        """Compute a resolved (start, end) for every step.

        Merges user-entered ranges with gap-detected auto-fill.

        Parameters:
            from_step_idx: Only re-resolve from this step index onward.
                Steps before this index retain their last-resolved
                positions (stored in ``_last_resolved``).

        Returns:
            List of ``(step_id, start, end_or_None, is_user)`` in CSV order.
        """
        if not self._steps:
            return []

        from mayatk.anim_utils.shots.behaviors import compute_duration

        # Gap value from the store (single source of truth)
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            gap = ShotStore.active().gap
        except Exception:
            gap = 0.0

        # Detect animation gaps for auto-fill (cached per assess cycle).
        # Two-phase detection: fast pass first, then motion-aware retry
        # when the scene has baked keys hiding the real shot boundaries.
        if self._cached_gaps is not None:
            gap_starts = self._cached_gaps
        else:
            min_gap_val = max(gap + 1, 2.0)
            gap_starts = detect_animation_gaps(min_gap=min_gap_val)
            n_steps = len(self._steps)
            if len(gap_starts) < n_steps:
                # Not enough regions for all steps — try motion-based
                # detection (handles baked / flat-key scenes).
                motion_gaps = detect_animation_gaps(
                    min_gap=min_gap_val, ignore_flat_keys=True
                )
                if len(motion_gaps) > len(gap_starts):
                    gap_starts = motion_gaps
                    if gap_starts:
                        self._set_footer(
                            f"Baked animation detected — "
                            f"{len(gap_starts) - 1} gaps found after "
                            f"ignoring flat keys.  Consider running "
                            f"Optimize Keys to clean the curves.",
                            color="#D4B878",
                        )
            self._cached_gaps = gap_starts

        # When more regions than steps, keep only the largest
        # boundaries so each step maps to a major animation section.
        if len(gap_starts) > len(self._steps):
            gap_starts = self._prune_to_top_boundaries(gap_starts, len(self._steps))

        # Build the resolved list
        resolved: List[Tuple[str, float, Optional[float], bool]] = []
        gap_idx = 0
        cursor = 1.0  # default start when no animation or no gaps

        # Frozen prefix: reuse last-resolved values for steps before from_step_idx
        last = getattr(self, "_last_resolved", None) or []
        if from_step_idx > 0 and last:
            for i in range(min(from_step_idx, len(last), len(self._steps))):
                resolved.append(last[i])
            # Advance cursor past the frozen prefix
            if resolved:
                _, _, prev_end, _ = resolved[-1]
                if prev_end is not None:
                    cursor = prev_end + gap
            # Advance gap_idx past gaps consumed by the frozen prefix
            for gs in gap_starts:
                if gs < cursor:
                    gap_idx += 1
                else:
                    break

        for i, step in enumerate(self._steps):
            if i < len(resolved):
                continue  # already in frozen prefix

            user = self._user_ranges.get(step.step_id)
            if user is not None:
                start, end = user
                resolved.append((step.step_id, start, end, True))
                # Advance cursor past this user-defined range
                if end is not None:
                    cursor = end + gap
                else:
                    cursor = start + compute_duration(step.objects) + gap
            elif gap_starts and gap_idx < len(gap_starts):
                # Use next region start for this step
                start = gap_starts[gap_idx]
                gap_idx += 1
                resolved.append((step.step_id, start, None, False))
                cursor = start + compute_duration(step.objects) + gap
            else:
                # Sequential placement from cursor
                start = cursor
                resolved.append((step.step_id, start, None, False))
                cursor = start + compute_duration(step.objects) + gap

        # Second pass: resolve None ends as next_start - gap (or last key)
        for i in range(len(resolved)):
            step_id, start, end, is_user = resolved[i]
            if end is None:
                if i + 1 < len(resolved):
                    end = resolved[i + 1][1] - gap
                else:
                    end = start + compute_duration(self._steps[i].objects)
            resolved[i] = (step_id, start, end, is_user)

        self._last_resolved = resolved
        return resolved

    def _auto_fill_ranges(self, resolved=None) -> list:
        """Auto-fill the Range column using resolved ranges.

        User-entered values are preserved; auto-filled values appear dim
        and italic.

        Parameters:
            resolved: Pre-computed resolved ranges.  When ``None``,
                :meth:`_resolve_ranges` is called internally.

        Returns:
            The resolved ranges list (for reuse by collision validation).
        """
        if resolved is None:
            resolved = self._resolve_ranges()
        if not resolved:
            return resolved

        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush, QFont

        tree = self.ui.tbl_steps
        dim = QBrush(QColor("#888888"))
        step_map = {r[0]: r for r in resolved}

        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                entry = step_map.get(step_data.step_id)
                if entry is None:
                    continue
                step_id, start, end, is_user = entry
                if end is not None:
                    parent.setText(self._COL_RANGE, f"{start:.0f}\u2013{end:.0f}")
                else:
                    parent.setText(self._COL_RANGE, f"{start:.0f}")
                font = parent.font(self._COL_RANGE)
                if not is_user:
                    parent.setForeground(self._COL_RANGE, dim)
                    font.setItalic(True)
                else:
                    font.setItalic(False)
                parent.setFont(self._COL_RANGE, font)
        finally:
            tree.blockSignals(False)
        return resolved

    def _validate_range_collisions(self, resolved=None) -> int:
        """Check adjacent ranges for ordering violations and color conflicts.

        Resets Range-column foreground on all items, then recolors
        collision participants in pastel red.

        Parameters:
            resolved: Pre-computed resolved ranges.  When ``None``,
                :meth:`_resolve_ranges` is called internally.

        Returns the number of collisions found.
        """
        if resolved is None:
            resolved = self._resolve_ranges()
        if len(resolved) < 2:
            return 0

        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        collision_brush = QBrush(QColor("#D4908F"))  # pastel red
        dim = QBrush(QColor("#888888"))
        collisions = 0

        # Build a map of step_id → tree item for quick lookup
        item_map: dict = {}
        resolved_map: dict = {}
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep):
                item_map[step_data.step_id] = parent
        for r in resolved:
            resolved_map[r[0]] = r

        # Block signals to prevent _on_item_changed from firing
        # recursively while we update foregrounds/tooltips.
        tree.blockSignals(True)
        try:
            # First pass: reset foreground and tooltip for all range cells
            for sid, item in item_map.items():
                entry = resolved_map.get(sid)
                is_user = entry[3] if entry else False
                item.setForeground(self._COL_RANGE, QBrush() if is_user else dim)
                item.setToolTip(self._COL_RANGE, "")

            # Second pass: mark collision items
            for i in range(len(resolved) - 1):
                curr_id, curr_start, curr_end, _ = resolved[i]
                next_id, next_start, _, _ = resolved[i + 1]
                effective_end = curr_end if curr_end is not None else curr_start
                if effective_end >= next_start:
                    collisions += 1
                    for sid in (curr_id, next_id):
                        item = item_map.get(sid)
                        if item is not None:
                            item.setForeground(self._COL_RANGE, collision_brush)
                            item.setToolTip(
                                self._COL_RANGE,
                                "Range collision: overlaps with adjacent step",
                            )
        finally:
            tree.blockSignals(False)

        return collisions

    # ---- ShotStore observer ----------------------------------------------

    def _bind_store_listener(self) -> None:
        """Register as a listener on the active ShotStore."""
        if self._store_listener_bound:
            return
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            store = ShotStore.active()
            store.add_listener(self._on_store_event)
            self._bound_store = store
            self._store_listener_bound = True
        except Exception:
            pass

    def _unbind_store_listener(self) -> None:
        """Remove the ShotStore listener."""
        if not self._store_listener_bound:
            return
        try:
            store = getattr(self, "_bound_store", None)
            if store is not None:
                store.remove_listener(self._on_store_event)
                self._bound_store = None
        except Exception:
            pass
        self._store_listener_bound = False

    def remove_callbacks(self) -> None:
        """Remove ShotStore listener (call on teardown)."""
        self._unbind_store_listener()

    def _on_store_event(self, event: str, payload=None) -> None:
        """React to ShotStore mutations — refresh tree timing if steps are loaded."""
        if not self._steps or self._building:
            return
        self._last_results = []  # invalidate stale assessment
        store = getattr(self, "_bound_store", None)
        if store is not None:
            self._refresh_timing(store)
        self._update_build_button()

    def _refresh_timing(self, store) -> None:
        """Update Start/End columns in the tree from the store."""
        from qtpy.QtCore import Qt

        timing_map = {s.name: s for s in store.sorted_shots()}
        tree = self.ui.tbl_steps
        tree.blockSignals(True)
        try:
            for i in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(i)
                step_data = parent.data(0, Qt.UserRole)
                if not isinstance(step_data, BuilderStep):
                    continue
                shot = timing_map.get(step_data.step_id)
                if shot is None:
                    continue
                parent.setText(self._COL_RANGE, f"{shot.start:.0f}\u2013{shot.end:.0f}")
                parent.setToolTip(self._COL_RANGE, f"{shot.end - shot.start:.0f}f")
        finally:
            tree.blockSignals(False)

    # ---- footer helpers --------------------------------------------------

    def _set_footer(self, text: str, *, color: str = "") -> None:
        """Set footer text with an optional foreground color."""
        label = self.ui.footer._status_label
        if color:
            label.setStyleSheet(
                f"background: transparent; border: none; color: {color};"
            )
        else:
            label.setStyleSheet("background: transparent; border: none;")
        self.ui.footer.setText(text)

    # ---- context menu ----------------------------------------------------

    def _show_item_menu(self, pos) -> None:
        """Show a context menu for the clicked tree item."""
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QMenu

        tree = self.ui.tbl_steps
        item = tree.itemAt(pos)
        if item is None:
            return

        # Resolve to parent step row
        is_child = item.parent() is not None
        step_item = item.parent() if is_child else item
        step_data = step_item.data(0, Qt.UserRole)
        if not isinstance(step_data, BuilderStep):
            return

        menu = QMenu(tree)
        act_open = menu.addAction(f"Open '{step_data.step_id}' in Shot Sequencer")
        if not self._is_built:
            act_open.setEnabled(False)
            act_open.setToolTip("Build shots first")

        # Range column actions (parent rows, pre-build only)
        act_set_frame = None
        act_auto_fill = None
        act_clear_range = None
        column = tree.columnAt(pos.x())
        if not is_child and not self._is_built and column == self._COL_RANGE:
            menu.addSeparator()
            act_set_frame = menu.addAction("Set Start to Current Frame")
            act_auto_fill = menu.addAction("Auto-fill from Gaps")
            if step_data.step_id in self._user_ranges:
                act_clear_range = menu.addAction("Clear Range")

        # Object-level actions (child rows only)
        act_outliner = None
        if is_child:
            obj_data = item.data(0, Qt.UserRole)
            obj_name = (
                getattr(obj_data, "name", None)
                if isinstance(obj_data, BuilderObject)
                else None
            )
            if obj_name:
                menu.addSeparator()
                act_outliner = menu.addAction(f"Show '{obj_name}' in Outliner")

        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._open_in_shot_sequencer(step_data.step_id)
        elif chosen is not None and chosen is act_outliner:
            self._show_in_outliner(obj_name)
        elif chosen is act_set_frame and act_set_frame is not None:
            self._set_range_to_current_frame(step_item, step_data.step_id)
        elif chosen is act_auto_fill and act_auto_fill is not None:
            step_idx = next(
                (
                    i
                    for i, s in enumerate(self._steps)
                    if s.step_id == step_data.step_id
                ),
                0,
            )
            # Clear user ranges from clicked step onward so they re-resolve
            for s in self._steps[step_idx:]:
                self._user_ranges.pop(s.step_id, None)
            resolved = self._auto_fill_ranges(
                resolved=self._resolve_ranges(from_step_idx=step_idx)
            )
            self._validate_range_collisions(resolved)
        elif chosen is act_clear_range and act_clear_range is not None:
            self._user_ranges.pop(step_data.step_id, None)
            tree.blockSignals(True)
            step_item.setText(self._COL_RANGE, "")
            tree.blockSignals(False)
            resolved = self._auto_fill_ranges()
            self._validate_range_collisions(resolved)

    def _set_range_to_current_frame(self, item, step_id: str) -> None:
        """Set the range start for *step_id* to the current Maya timeline frame.

        Clears user ranges on subsequent steps so they cascade from the
        new anchor point.
        """
        try:
            import maya.cmds as _cmds
        except ImportError:
            return
        frame = float(_cmds.currentTime(q=True))
        self._user_ranges[step_id] = (frame, None)

        # Clear subsequent user ranges so they re-flow from this anchor
        step_idx = next(
            (i for i, s in enumerate(self._steps) if s.step_id == step_id),
            0,
        )
        for s in self._steps[step_idx + 1 :]:
            self._user_ranges.pop(s.step_id, None)

        tree = self.ui.tbl_steps
        tree.blockSignals(True)
        item.setText(self._COL_RANGE, f"{frame:.0f}")
        tree.blockSignals(False)
        resolved = self._auto_fill_ranges()
        self._validate_range_collisions(resolved)

    def _show_in_outliner(self, obj_name: str) -> None:
        """Select *obj_name* and reveal it in Maya's Outliner."""
        try:
            import pymel.core as pm
        except ImportError:
            return
        if not pm.objExists(obj_name):
            self._set_footer(f"'{obj_name}' not found in scene.", color="#D4908F")
            return
        pm.select(obj_name, replace=True)
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.reveal_in_outliner([obj_name])

    def _open_in_shot_sequencer(self, step_id: str) -> None:
        """Open the Shot Sequencer UI and navigate to the shot matching *step_id*."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        if not store.shots:
            self._set_footer("Build shots first before opening the sequencer.")
            return

        self.sb.handlers.marking_menu.show("shot_sequencer")

        seq_slots = self.sb.slot_instances.get("shot_sequencer")
        if seq_slots is None:
            return

        controller = getattr(seq_slots, "controller", None)
        if controller is None:
            return

        # The shot sequencer wraps the shared store
        from mayatk.anim_utils.shots.shot_sequencer._shot_sequencer import ShotSequencer

        controller.sequencer = ShotSequencer(store=store)

        # Do NOT auto-respace here.  _sync_header_settings (called by
        # _sync_to_widget) will flow the spinner value into store.gap
        # for display/metadata, but actual keyframe respacing must be
        # explicitly triggered by the user via the gap spinner.

        controller._sync_combobox()

        # Select the shot matching step_id
        cmb = getattr(seq_slots.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                shot_id = cmb.itemData(i)
                shot = controller.sequencer.shot_by_id(shot_id) if shot_id else None
                if shot and shot.name == step_id:
                    # Clear stale session state so prior shifted-out keys
                    # and cached segments don't suppress the new display.
                    controller._shifted_out_keys.clear()
                    controller._segment_cache.clear()
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(i)
                    cmb.blockSignals(False)
                    controller._sync_to_widget(shot_id, frame=True)
                    controller._update_shot_nav_state()
                    break

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
        import os

        if not os.path.isfile(path):
            self.ui.txt_csv_path.set_action_color("invalid")
            self._set_footer(f"File not found: {path}", color=_ERROR_COLOR)
            return

        try:
            self._steps = parse_csv(path)
        except Exception as exc:
            self.logger.error("Failed to parse CSV: %s", exc)
            self.ui.txt_csv_path.set_action_color("invalid")
            self._set_footer(f"Error: {exc}", color=_ERROR_COLOR)
            return

        self.ui.txt_csv_path.reset_action_color()
        self._populate_table()
        self._last_results = []  # stale after new CSV
        self._update_build_button()
        n_obj = sum(len(s.objects) for s in self._steps)
        self._set_footer(f"{len(self._steps)} steps, {n_obj} objects loaded.")

    # ---- table population ------------------------------------------------

    _HEADERS = ["Step", "Section", "Content", "Behaviors", "Range"]

    # Foreground colors for behavior names on child rows (pastel)
    _BEHAVIOR_COLORS = {
        "fade_in": ("#8ECFBF", None),  # soft teal
        "fade_out": ("#E0B880", None),  # soft amber
        "fade_in_out": ("#A3C4E0", None),  # soft sky blue
    }

    _STEP_ICON_COLOR = "#8E8E8E"  # neutral dark grey for parent step rows

    @staticmethod
    def _fmt_behavior(name: str) -> str:
        """'fade_in_out' -> 'Fade In Out'."""
        return name.replace("_", " ").title() if name else ""

    @staticmethod
    def _try_load_maya_icons():
        """Return the :class:`NodeIcons` class if Maya is available, else ``None``."""
        try:
            from mayatk.ui_utils.node_icons import NodeIcons
            import maya.cmds as cmds  # noqa: F401 — availability check
        except ImportError:
            return None
        return NodeIcons

    # Fixed column indices for the unified 5-column layout
    _COL_STEP = 0
    _COL_SECTION = 1
    _COL_CONTENT = 2  # parent: description, child: object name
    _COL_BEHAVIORS = 3
    _COL_RANGE = 4

    def _apply_formatting(self, tree) -> None:
        """Set column/row tints, behavior colors, icons, and column widths."""
        from qtpy.QtGui import QColor

        content_col = self._COL_CONTENT
        beh_col = self._COL_BEHAVIORS

        # Row tints via delegate (fillRect bypasses Maya QSS stripping).
        tree._child_row_color = QColor(0, 0, 0, 55)

        # Column tints — darken Step and Behaviors columns
        tree.clear_column_tints()
        tree.set_column_tint(self._COL_STEP, QColor(0, 0, 0, 45))
        tree.set_column_tint(self._COL_BEHAVIORS, QColor(0, 0, 0, 45))

        # Behavior column formatter
        display_colors = {
            self._fmt_behavior(k).lower(): v for k, v in self._BEHAVIOR_COLORS.items()
        }
        formatter = tree.make_color_map_formatter(display_colors)
        tree.set_column_formatter(beh_col, formatter)

        # Icons: step icon on parents, type-coded icon on child Content column
        node_icons_cls = self._try_load_maya_icons()
        not_found_color = self._PASTEL_STATUS.get("missing_object", ("#D4908F",))[0]
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            tree.set_item_icon(parent, "step", color=self._STEP_ICON_COLOR)
            for j in range(parent.childCount()):
                child = parent.child(j)
                obj_name = child.text(content_col)
                if not obj_name:
                    continue
                maya_icon = (
                    node_icons_cls.get_icon(obj_name) if node_icons_cls else None
                )
                if maya_icon is not None:
                    child.setIcon(content_col, maya_icon)
                else:
                    tree.set_item_type_icon(
                        child, "close", column=content_col, color=not_found_color
                    )

        # Column widths
        header = tree.header()
        header.resizeSection(self._COL_STEP, 60)
        header.resizeSection(beh_col, 110)
        header.resizeSection(self._COL_RANGE, 80)

        # Run registered formatters
        tree.apply_formatting()

    def _populate_table(self) -> None:
        """Fill the TreeWidget with parsed steps and expandable object rows."""
        tree = self.ui.tbl_steps
        tree.clear()
        tree.setHeaderLabels(self._HEADERS)
        tree.setColumnCount(len(self._HEADERS))

        for step in self._steps:
            section = (
                f"{step.section}: {step.section_title}"
                if step.section_title
                else step.section
            )

            parent = tree.create_item(
                [step.step_id, section, step.content, "", ""],
                data=step,
            )
            # Child rows: object name in Content column, behavior in Behaviors
            for obj in step.objects:
                tree.create_item(
                    ["", "", obj.name, self._fmt_behavior(obj.behavior), ""],
                    data=obj,
                    parent=parent,
                )

        # Restrict editability: only Range column on parent rows, pre-build
        from qtpy.QtCore import Qt as _Qt

        editable = not self._is_built
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            if editable:
                parent.setFlags(parent.flags() | _Qt.ItemIsEditable)
            else:
                parent.setFlags(parent.flags() & ~_Qt.ItemIsEditable)
            for j in range(parent.childCount()):
                child = parent.child(j)
                child.setFlags(child.flags() & ~_Qt.ItemIsEditable)

        # Restore user-entered range values that survive table rebuilds
        self._restore_user_ranges(tree)

        self._apply_formatting(tree)
        tree.set_stretch_column(2)  # Stretch "Content" column
        tree.restore_column_state()  # Persist user header changes

    # ---- helpers ---------------------------------------------------------

    def _ensure_steps(self) -> bool:
        """Load CSV from the text field if steps are empty. Returns True if steps are available."""
        if not self._steps:
            path = self.ui.txt_csv_path.text().strip()
            if path:
                self._load_csv(path)
        if not self._steps:
            self._set_footer("Load a CSV first.")
            return False
        return True

    # ---- button state ----------------------------------------------------

    def _update_build_button(self) -> None:
        """Enable Build when sync would produce created/patched actions.

        Scenarios that re-enable the button:
        - New CSV with step names absent from the store ("created").
        - Updated CSV where objects or behaviors differ ("patched").
        - Store mutation (undo, external removal) invalidates prior results.
        """
        btn = getattr(self.ui, "b003", None)
        if btn is None:
            return
        if self._last_results:
            needs_build = any(not r.built for r in self._last_results)
        elif self._steps:
            needs_build = self._needs_sync()
        else:
            needs_build = False
        btn.setEnabled(needs_build)

    def _needs_sync(self) -> bool:
        """Return True if any step would be created or patched by sync."""
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            built_map = {s.name: s for s in ShotStore.active().shots}
        except Exception:
            return True
        # Shots in store but not in CSV → would be "removed"
        csv_ids = {step.step_id for step in self._steps}
        if any(name not in csv_ids for name in built_map):
            return True
        for step in self._steps:
            shot = built_map.get(step.step_id)
            if shot is None:
                return True  # new shot
            # Check for object or behavior changes (same logic as update())
            csv_obj_map = {o.name: o.behavior for o in step.objects}
            if set(csv_obj_map) != set(shot.objects):
                return True  # objects added or removed
            old_beh = {
                e["name"]: e.get("behavior", "")
                for e in shot.metadata.get("behaviors", [])
            }
            if any(csv_obj_map.get(n, "") != old_beh.get(n, "") for n in csv_obj_map):
                return True  # behavior changed
        return False

    # ---- assess ----------------------------------------------------------

    # ---- build -----------------------------------------------------------

    def build(self) -> None:
        """Build or update shots in the store from loaded steps."""
        if not self._ensure_steps():
            return

        try:
            import pymel.core as pm
        except ImportError:
            self._set_footer("Maya is required to build shots.", color=_ERROR_COLOR)
            return

        from mayatk.anim_utils.shots._shots import ShotStore

        try:
            store = ShotStore.active()
            builder = ShotManifest(store)

            # Resolve ranges (gap-detected + user overrides) into a map
            resolved = self._resolve_ranges()
            range_map = {
                sid: (s, e) for sid, s, e, _ in resolved if e is not None
            } or None

            pm.undoInfo(openChunk=True, chunkName="ShotManifest_build")
            self._building = True
            try:
                with store.batch_update():
                    actions, beh, assessment = builder.sync(
                        self._steps, ranges=range_map
                    )
            finally:
                self._building = False
                pm.undoInfo(closeChunk=True)

            # Store the store for later handoff to Shot Sequencer UI
            self._store = store

            n_created = sum(1 for a in actions.values() if a == "created")
            n_patched = sum(1 for a in actions.values() if a == "patched")
            n_skipped = sum(1 for a in actions.values() if a == "skipped")
            n_removed = sum(1 for a in actions.values() if a == "removed")
            n_beh_applied = len(beh.get("applied", []))
            n_beh_skipped = len(beh.get("skipped", []))
            parts = []
            if n_created:
                parts.append(f"{n_created} created")
            if n_patched:
                parts.append(f"{n_patched} patched")
            if n_skipped:
                parts.append(f"{n_skipped} unchanged")
            if n_removed:
                parts.append(f"{n_removed} removed from CSV")
            if n_beh_applied:
                parts.append(f"{n_beh_applied} behaviors applied")
            if n_beh_skipped:
                parts.append(f"{n_beh_skipped} behaviors kept (existing keys)")
            self._set_footer(f"Build complete: {', '.join(parts)}.")

            # Refresh tree with post-build assessment
            self._apply_post_build(assessment, store)
            self._update_build_button()
        except Exception as exc:
            self.logger.error("Build failed: %s", exc)
            self._set_footer(f"Build error: {exc}", color=_ERROR_COLOR)

    def _apply_post_build(self, results: list, store) -> None:
        """Refresh tree with timing from the store and assessment results."""
        self._populate_table()
        self._refresh_timing(store)
        self._apply_assessment(results)
        self._last_results = results

    # ---- assess ----------------------------------------------------------

    # Soft pastel assessment colors: (foreground, background)
    # Distinct hues on dark themes — visible but not harsh.
    _PASTEL_STATUS = {
        "valid": (None, None),  # no change — default appearance
        "missing_shot": ("#D4B878", "#3D3528"),  # warm gold fg + subtle amber bg
        "missing_object": ("#E0A0A0", "#3D2828"),  # pastel rose fg + subtle rose bg
        "missing_behavior": ("#80C8E8", "#28323D"),  # sky fg + subtle blue bg
        "user_animated": ("#C8A8E8", "#32283D"),  # lavender fg + subtle purple bg
        "locked": ("#888888", None),  # dimmed grey fg, no bg
        "additional": ("#A8C8A0", "#2D3D28"),  # soft green fg + subtle green bg
    }

    def assess(self) -> None:
        """Compare CSV steps against the live Maya shots and color the tree."""
        if not self._ensure_steps():
            return

        try:
            import pymel.core as pm
        except ImportError:
            self._set_footer("Maya is required to assess shots.", color=_ERROR_COLOR)
            return

        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        builder = ShotManifest(store)

        # Invalidate cached gaps so _resolve_ranges rescans the scene
        self._cached_gaps = None

        results = builder.assess(self._steps)

        # Rebuild tree and enrich with timing from store + status
        self._populate_table()
        if not self._is_built:
            resolved = self._auto_fill_ranges()
            self._validate_range_collisions(resolved)
        self._refresh_timing(store)
        self._apply_assessment(results)
        self._last_results = results

        # Summary counts
        n_built = sum(1 for r in results if r.built)
        missing_obj_names = {
            o.name for r in results for o in r.objects if o.status == "missing_object"
        }
        missing_beh_names = {
            o.name for r in results for o in r.objects if o.status == "missing_behavior"
        }
        n_additional = sum(len(r.additional_objects) for r in results)
        n_shrinkable = sum(1 for r in results if r.shrinkable_frames > 0)
        sorted_shots = store.sorted_shots()
        total_frames = (
            (sorted_shots[-1].end - sorted_shots[0].start) if sorted_shots else 0
        )
        parts = [f"{n_built}/{len(results)} steps built, {total_frames:.0f} frames"]
        if missing_obj_names:
            parts.append(f"{len(missing_obj_names)} missing objects")
        if missing_beh_names:
            parts.append(f"{len(missing_beh_names)} missing behaviors")
        if n_additional:
            parts.append(f"{n_additional} scene objects")
        if n_shrinkable:
            parts.append(f"{n_shrinkable} shrinkable")
        self._set_footer(f"Assessment: {', '.join(parts)}")
        self._update_build_button()

    def expand_missing(self) -> None:
        """Expand all step rows that have missing objects, behaviors, or additional objects."""
        from qtpy.QtCore import Qt

        if not self._last_results:
            self._set_footer("Build first to detect issues.")
            return

        problem_ids = set()
        lines: list[str] = []
        for r in self._last_results:
            issues: list[str] = []
            if r.status == "missing_shot":
                issues.append("shot not built")
            elif r.status == "missing_object":
                missing = [o.name for o in r.objects if not o.exists]
                if missing:
                    issues.append(f"missing objects: {', '.join(missing)}")
            elif r.status == "missing_behavior":
                no_beh = [o.name for o in r.objects if o.status == "missing_behavior"]
                if no_beh:
                    issues.append(f"missing behaviors: {', '.join(no_beh)}")
            if r.additional_objects:
                issues.append(f"additional objects: {', '.join(r.additional_objects)}")
            if issues:
                problem_ids.add(r.step_id)
                lines.append(f"  {r.step_id}: {'; '.join(issues)}")

        if not problem_ids:
            self._set_footer("No issues found.")
            return

        print(f"\n--- Expand Missing ({len(lines)} steps) ---")
        for line in lines:
            print(line)
        print("---")

        tree = self.ui.tbl_steps
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and step_data.step_id in problem_ids:
                parent.setExpanded(True)

    def expand_extra(self) -> None:
        """Expand all step rows that have scene-discovered extra objects."""
        from qtpy.QtCore import Qt

        if not self._last_results:
            self._set_footer("Assess or build first to detect extra objects.")
            return

        extra_ids = {r.step_id for r in self._last_results if r.additional_objects}
        if not extra_ids:
            self._set_footer("No extra objects found.")
            return

        tree = self.ui.tbl_steps
        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and step_data.step_id in extra_ids:
                parent.setExpanded(True)

    def _apply_assessment(self, results: list) -> None:
        """Walk tree items and apply pastel colors + tooltips from results."""
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QColor, QBrush

        tree = self.ui.tbl_steps
        col_count = tree.columnCount()
        content_col = self._COL_CONTENT
        beh_col = self._COL_BEHAVIORS

        status_map = {r.step_id: r for r in results}

        for i in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(i)
            step_data = parent.data(0, Qt.UserRole)
            if not isinstance(step_data, BuilderStep):
                continue
            step_status = status_map.get(step_data.step_id)
            if step_status is None:
                continue

            # Parent tooltip
            if step_status.status == "missing_shot":
                parent.setToolTip(0, "Shot not built in sequencer")
            elif step_status.status == "missing_object":
                names = [o.name for o in step_status.objects if not o.exists]
                parent.setToolTip(0, f"Missing: {', '.join(names)}")
            elif step_status.status == "missing_behavior":
                names = [
                    o.name
                    for o in step_status.objects
                    if o.status == "missing_behavior"
                ]
                parent.setToolTip(0, f"Missing behavior keys: {', '.join(names)}")

            if step_status.shrinkable_frames > 0:
                existing_tip = parent.toolTip(0) or ""
                shrink_tip = f"{step_status.shrinkable_frames:.0f}f unused"
                parent.setToolTip(
                    0, f"{existing_tip}\n{shrink_tip}" if existing_tip else shrink_tip
                )

            # Recolor step icon to reflect status
            fg_hex, _ = self._PASTEL_STATUS.get(step_status.status, (None, None))
            icon_color = fg_hex or self._STEP_ICON_COLOR
            tree.set_item_icon(parent, "step", color=icon_color)

            # Color parent behavior column if any child has a behavior issue
            beh_issues = [
                o for o in step_status.objects if o.status == "missing_behavior"
            ]
            if beh_issues:
                b_fg, b_bg = self._PASTEL_STATUS["missing_behavior"]
                if b_fg:
                    parent.setForeground(beh_col, QBrush(QColor(b_fg)))
                if b_bg:
                    parent.setBackground(beh_col, QBrush(QColor(b_bg)))
                parent.setText(
                    beh_col,
                    f"{len(beh_issues)} missing",
                )
                lines = [
                    f"{o.name}  \u2192  {self._fmt_behavior(o.behavior)}"
                    for o in beh_issues
                ]
                parent.setToolTip(beh_col, "\n".join(lines))

            # Color child rows — only problem statuses
            obj_status_map = {o.name: o for o in step_status.objects}
            for j in range(parent.childCount()):
                child = parent.child(j)
                child_data = child.data(0, Qt.UserRole)
                if not isinstance(child_data, BuilderObject):
                    continue
                obj_st = obj_status_map.get(child_data.name)
                if obj_st is None or obj_st.status == "valid":
                    continue

                c_fg, c_bg = self._PASTEL_STATUS.get(obj_st.status, (None, None))
                if c_fg:
                    brush = QBrush(QColor(c_fg))
                    for c in range(col_count):
                        child.setForeground(c, brush)
                if c_bg:
                    bg = QBrush(QColor(c_bg))
                    for c in range(col_count):
                        child.setBackground(c, bg)

                if obj_st.status == "missing_object":
                    child.setToolTip(content_col, "Object not found in Maya")
                elif obj_st.status == "missing_behavior":
                    child.setToolTip(
                        content_col, f"Expected '{obj_st.behavior}' keys not found"
                    )
                elif obj_st.status == "user_animated" and obj_st.key_range:
                    child.setToolTip(
                        content_col,
                        f"User-animated: keys {obj_st.key_range[0]:.0f}-{obj_st.key_range[1]:.0f}",
                    )

            # Additional objects (in shot but not in CSV)
            if step_status.additional_objects:
                a_fg, a_bg = self._PASTEL_STATUS.get("additional", (None, None))
                node_icons_cls = self._try_load_maya_icons()
                for extra_name in step_status.additional_objects:
                    extra_item = tree.create_item(
                        ["", "", extra_name, "scene", ""],
                        parent=parent,
                    )
                    extra_item.setToolTip(
                        content_col,
                        "Scene-discovered: this object is in the shot but not listed in the CSV.",
                    )
                    # Italic font to visually distinguish from CSV objects
                    font = extra_item.font(content_col)
                    font.setItalic(True)
                    for c in range(col_count):
                        extra_item.setFont(c, font)
                    if a_fg:
                        brush = QBrush(QColor(a_fg))
                        for c in range(col_count):
                            extra_item.setForeground(c, brush)
                    if a_bg:
                        bg = QBrush(QColor(a_bg))
                        for c in range(col_count):
                            extra_item.setBackground(c, bg)
                    if node_icons_cls:
                        maya_icon = node_icons_cls.get_icon(extra_name)
                        if maya_icon is not None:
                            extra_item.setIcon(content_col, maya_icon)


class ShotManifestSlots(ptk.LoggingMixin):
    """Switchboard slot class — routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shot_manifest

        self.controller = ShotManifestController(self)

    # ---- header ----------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.setTitle("Shot Manifest:")

        widget.menu.add(
            "QPushButton",
            setText="Expand All Missing",
            setObjectName="btn_expand_missing",
            setToolTip="Expand every step row that has missing objects or behaviors.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Expand All Extra",
            setObjectName="btn_expand_extra",
            setToolTip="Expand every step row that has scene-discovered objects not in the CSV.",
        )

    def btn_expand_missing(self):
        """Expand all step rows that have missing objects or behaviors."""
        self.controller.expand_missing()

    def btn_expand_extra(self):
        """Expand all step rows that have scene-discovered extra objects."""
        self.controller.expand_extra()

    # ---- buttons ---------------------------------------------------------

    def b001(self):
        """Browse for CSV file."""
        self.controller.browse_csv()

    def b002(self):
        """Assess shots against live Maya scene."""
        self.controller.assess()

    def b003(self):
        """Build shots from loaded CSV."""
        self.controller.build()
