# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Shot Manifest UI.

Bridges the Shot Manifest dialog to the CSV parser and
:class:`~mayatk.anim_utils.shot_manifest._shot_manifest.ShotManifest` engine.

Presentation methods (tree population, formatting, assessment colouring)
are inherited from :class:`~._table_presenter.ManifestTableMixin`.
Constants and pure helpers live in :mod:`._manifest_data`.
Range resolution is delegated to :func:`._range_resolver.resolve_ranges`.
"""
from typing import Dict, List, Optional, Tuple

import pythontk as ptk

from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
    BuilderStep,
    BuilderObject,
    ColumnMap,
    ShotManifest,
    StepStatus,
    parse_csv,
    detect_shot_regions,
    regions_from_selected_keys,
)
from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
    ERROR_COLOR,
    SETTINGS_NS,
    COL_STEP,
    COL_DESC,
    COL_START,
    COL_END,
    fmt_behavior,
)
from mayatk.anim_utils.shots.shot_manifest.range_resolver import resolve_ranges
from mayatk.anim_utils.shots._shots import (
    BatchComplete,
    SettingsChanged,
    ShotRemoved,
    StoreEvent,
)
from mayatk.anim_utils.shots.shot_manifest.table_presenter import ManifestTableMixin


class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin):
    """Business logic for the Shot Manifest UI."""

    _COLOR_SETTINGS_NS = "ShotManifest/colors"

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

        self._settings = SettingsManager(namespace=SETTINGS_NS)
        # One-shot migration: fit_mode and initial_shot_length now live on
        # ShotStore.  Purge the old manifest-namespaced keys so they don't
        # linger indefinitely in QSettings.
        _qs = self._settings.settings
        for _legacy in (
            f"{SETTINGS_NS}/fit_mode",
            f"{SETTINGS_NS}/initial_shot_length",
        ):
            if _qs.contains(_legacy):
                _qs.remove(_legacy)

        self._user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

        tree = self.ui.tbl_steps
        tree.enable_column_config()

        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QAbstractItemView

        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setExpandsOnDoubleClick(False)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_item_menu)
        tree.itemDoubleClicked.connect(self._on_range_double_clicked)
        tree.itemChanged.connect(self._on_item_changed)

        self._building = False
        self._built_this_round = False
        self._first_shown = False
        self._store_listener_bound = False
        self._cached_gaps: Optional[List[float]] = None
        self._cached_gap_ends: Optional[Dict[float, float]] = None
        self._last_resolved: List[Tuple[str, float, Optional[float], bool]] = []
        self._bind_store_listener()
        self._install_scene_jobs()
        self._column_map = ColumnMap()
        self._active_mapping = None  # loaded JSON dict from mapping/
        self._mapping_dir = None  # custom directory override
        self._setup_recent_csv()
        self._setup_csv_toggle()
        self._setup_header_menu()
        self._setup_mapping_combo()
        self._restore_color_overrides()
        self._move_action_buttons_to_footer()
        self.ui.on_first_show.connect(self._on_first_show)

    # ---- footer-hosted action buttons ------------------------------------

    def _move_action_buttons_to_footer(self) -> None:
        """Reparent the Assess/Build buttons into the footer's right side.

        The UI file still lays them out in ``action_layout`` above the
        footer so Designer remains usable; at runtime we relocate them
        onto the footer itself to consolidate the action row.  Sizes
        declared in the .ui file are preserved.
        """
        footer = getattr(self.ui, "footer", None)
        add_widget = getattr(footer, "add_widget", None) if footer else None
        if not callable(add_widget):
            return
        for name in ("b002", "b003"):
            btn = getattr(self.ui, name, None)
            if btn is None:
                continue
            add_widget(btn, side="right")

    # ---- first-show auto-populate ----------------------------------------

    def _on_first_show(self) -> None:
        """Auto-populate the table the first time the window is shown."""
        self._first_shown = True
        self._populate_from_source()

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

    def _step_is_built(self, step_id: str) -> bool:
        """True if a specific step already exists as a shot in the store."""
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            return any(s.name == step_id for s in ShotStore.active().shots)
        except Exception:
            return False

    # ---- range column editing --------------------------------------------

    @property
    def _is_detection_mode(self) -> bool:
        """True when steps were populated via scene detection (no CSV)."""
        return bool(self._steps) and not self._csv_path

    @property
    def _use_selected_keys(self) -> bool:
        """True when a selected-keys detection mode is active.

        The store's detection_mode controls this regardless of whether
        steps came from a CSV or from scene detection.  CSV defines
        step names/objects; the detection mode defines how ranges are
        inferred.
        """
        store = self._active_store()
        return store is not None and store.detection_mode != "auto"

    def _all_ranges_complete(self) -> bool:
        """True when every step has a user-supplied (start, end) pair."""
        return bool(self._steps) and all(
            (r := self._user_ranges.get(s.step_id)) is not None
            and r[0] is not None
            and r[1] is not None
            for s in self._steps
        )

    # ---- store access -----------------------------------------------------

    def _active_store(self):
        """Return the cached ShotStore, or try ShotStore.active()."""
        if self._store is not None:
            return self._store
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            return ShotStore.active()
        except Exception:
            return None

    # ---- scene detection -------------------------------------------------

    def _detect_regions(self, gap_threshold: float) -> list:
        """Return detected shot regions, respecting the detection mode.

        Returns an empty list when a selected-keys mode is active and
        no keys are selected.  Callers are responsible for showing
        appropriate user feedback (message box, footer, etc.).

        The store's detection_mode is always respected regardless of
        whether a CSV is loaded â€” CSV defines steps, detection_mode
        controls how timing boundaries are discovered.
        """
        store = self._active_store()
        mode = store.detection_mode if store is not None else "auto"
        if mode != "auto":
            return regions_from_selected_keys(
                gap_threshold=gap_threshold, key_filter=mode
            )
        return detect_shot_regions(gap_threshold=gap_threshold)

    def detect(self, gap: Optional[float] = None) -> None:
        """Detect animation regions in the scene and populate the table.

        Replaces any loaded CSV data.  Ranges are pre-filled from
        detection results (user-editable).  Section and Behaviors
        columns are minimal since detection doesn't provide that
        metadata.

        Parameters:
            gap: Minimum gap (frames) between shots.  When ``None``,
                reads from the active ShotStore's detection_threshold,
                falling back to 5.0.
        """
        store = self._active_store()
        if gap is None:
            gap = store.detection_threshold if store is not None else 5.0

        use_sel = self._use_selected_keys
        regions = self._detect_regions(gap)
        if not regions:
            if use_sel:
                self.sb.message_box(
                    "<b>No keys selected.</b><br>"
                    "Select keyframes in the Graph Editor first.",
                )
            footer = (
                "No selected keys found (select keys in the Graph Editor)."
                if use_sel
                else "No animation found in scene."
            )
            self._load_data([], footer=footer)
            return

        steps, ranges = BuilderStep.from_detection(regions)
        n_obj = sum(len(s.objects) for s in steps)
        source = "selected keys" if use_sel else "scene"
        self._load_data(
            steps,
            ranges=dict(ranges),
            footer=f"Found {len(steps)} shots, {n_obj} objects from {source}.",
        )

    def _on_range_double_clicked(self, item, column) -> None:
        """Allow editing Step, Description, Start, and End on parent rows.

        Built steps are locked; unbuilt steps remain editable even after
        other shots have been built.  For non-editable columns, toggle
        expand/collapse instead.
        """
        from qtpy.QtCore import Qt

        editable_cols = [COL_STEP, COL_DESC, COL_START, COL_END]
        is_parent = item.parent() is None
        if is_parent and column in editable_cols:
            step_data = item.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep) and self._step_is_built(
                step_data.step_id
            ):
                pass  # fall through to expand/collapse
            else:
                tree = self.ui.tbl_steps
                tree.editItem(item, column)
                return
        # Fallback: toggle expand/collapse for parent rows
        if is_parent:
            item.setExpanded(not item.isExpanded())

    def _on_item_changed(self, item, column) -> None:
        """Capture user edits to Step name, Description, Start, and End columns.

        Validation rules (Start/End):
        - Negative start values are rejected.
        - End must be > start when both are given.
        - Start must not precede the previous step's resolved end.

        After a valid range edit, downstream user ranges are cleared so
        the resolver can re-flow them from the new anchor, and the full
        table is refreshed.

        Step name edits rename the step and re-key _user_ranges.
        Description edits update the step's content field (which maps
        to ShotBlock.description) without triggering range resolution.
        """
        # Step name edit
        if column == COL_STEP:
            if item.parent() is not None:
                return
            from qtpy.QtCore import Qt

            step_data = item.data(0, Qt.UserRole)
            if not isinstance(step_data, BuilderStep):
                return
            new_name = item.text(COL_STEP).strip()
            if not new_name or new_name == step_data.step_id:
                return
            old_name = step_data.step_id
            step_data.step_id = new_name
            # Re-key user ranges
            if old_name in self._user_ranges:
                self._user_ranges[new_name] = self._user_ranges.pop(old_name)
            return

        # Description column edit
        if column == COL_DESC:
            if item.parent() is not None:
                return
            from qtpy.QtCore import Qt

            step_data = item.data(0, Qt.UserRole)
            if isinstance(step_data, BuilderStep):
                if step_data.audio:
                    step_data.audio = item.text(COL_DESC)
                else:
                    step_data.description = item.text(COL_DESC)
            return

        if column not in (COL_START, COL_END):
            return
        if item.parent() is not None:
            return
        from qtpy.QtCore import Qt

        step_data = item.data(0, Qt.UserRole)
        if not isinstance(step_data, BuilderStep):
            return

        start_raw = item.text(COL_START).strip()
        end_raw = item.text(COL_END).strip()

        # Both empty â€” clear user range
        if not start_raw and not end_raw:
            self._user_ranges.pop(step_data.step_id, None)
            self._refresh_ranges()
            return

        # Parse values
        start: Optional[float] = None
        end: Optional[float] = None
        try:
            if start_raw:
                start = float(start_raw)
        except ValueError:
            self._revert_range_cell(item, step_data.step_id)
            return
        try:
            if end_raw:
                end = float(end_raw)
        except ValueError:
            self._revert_range_cell(item, step_data.step_id)
            return

        if start is None:
            # Can't store a range without a start value
            self._revert_range_cell(item, step_data.step_id)
            return

        # Reject negative start.
        if start < 0:
            self._revert_range_cell(item, step_data.step_id)
            return

        # Reject end <= start when end is given.
        if end is not None and end <= start:
            self._revert_range_cell(item, step_data.step_id)
            return

        # Reject start before previous step's resolved end.
        step_idx = self._step_index(step_data.step_id)
        if step_idx < 0:
            return
        if step_idx > 0 and self._last_resolved:
            if step_idx <= len(self._last_resolved):
                _, _, prev_end, _ = self._last_resolved[step_idx - 1]
                if prev_end is not None and start < prev_end:
                    self._revert_range_cell(item, step_data.step_id)
                    return

        # Valid â€” store, clear downstream, and refresh.
        self._user_ranges[step_data.step_id] = (start, end)
        self._cascade_from(step_idx)
        self._refresh_ranges(from_step_idx=step_idx)

    def _step_index(self, step_id: str) -> int:
        """Return the list index for *step_id*, or -1 if not found."""
        return next((i for i, s in enumerate(self._steps) if s.step_id == step_id), -1)

    def _refresh_ranges(self, from_step_idx: int = 0) -> list:
        """Re-resolve, auto-fill, and validate all ranges.

        This is the single entry point for updating the Range column
        after any edit, cascade, or clear operation.

        Parameters
        ----------
        from_step_idx
            Passed to :meth:`_resolve_ranges` so steps before this
            index keep their last-resolved positions.

        Returns the resolved ranges list.
        """
        resolved = self._auto_fill_ranges(
            resolved=self._resolve_ranges(from_step_idx=from_step_idx)
        )
        self._validate_range_collisions(resolved)
        return resolved

    def _cascade_from(self, step_idx: int) -> None:
        """Clear user ranges on all steps after *step_idx* so they re-flow."""
        for s in self._steps[step_idx + 1 :]:
            self._user_ranges.pop(s.step_id, None)

    # ---- auto-fill logic -------------------------------------------------

    def _resolve_ranges(
        self,
        from_step_idx: int = 0,
    ) -> List[Tuple[str, float, Optional[float], bool]]:
        """Compute a resolved (start, end) for every step.

        Detects/caches animation regions, then delegates to the
        standalone :func:`._range_resolver.resolve_ranges` algorithm.
        """
        if not self._steps:
            return []

        store = self._active_store()
        gap = store.gap if store else 0.0
        det_threshold = store.detection_threshold if store else 5.0
        use_sel = self._use_selected_keys

        # Detect animation regions for auto-fill (cached per assess cycle).
        if self._cached_gaps is not None:
            gap_starts = self._cached_gaps
        else:
            regions = self._detect_regions(det_threshold)
            gap_starts = [r["start"] for r in regions] if regions else []
            self._cached_gaps = gap_starts
            self._cached_gap_ends = (
                {r["start"]: r["end"] for r in regions if r.get("end") is not None}
                if regions
                else {}
            )

        if use_sel and not gap_starts:
            return []

        # When no animation regions are detected (regardless of mode),
        # use uniform default durations so steps get sensible placeholder
        # ranges instead of behavior-derived micro-durations.  This also
        # covers the case where the scene has animation but the chosen
        # detection mode found no boundaries (e.g. skip_zero with no
        # zero-valued keys).
        default_dur = 200.0 if (not gap_starts and not use_sel) else 0

        resolved = resolve_ranges(
            steps=self._steps,
            user_ranges=self._user_ranges,
            gap_starts=gap_starts,
            gap_end_map=self._cached_gap_ends or {},
            gap=gap,
            use_selected_keys=use_sel,
            last_resolved=self._last_resolved,
            from_step_idx=from_step_idx,
            default_duration=default_dur,
        )
        self._last_resolved = resolved
        return resolved

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
        """Remove ShotStore listener and ScriptJobManager subscriptions."""
        self._unbind_store_listener()
        ScriptJobManager.instance().unsubscribe_all(self)

    # ---- Maya scene-change scriptJobs ------------------------------------

    def _install_scene_jobs(self) -> None:
        """Subscribe to SceneOpened / NewSceneOpened via ScriptJobManager."""
        mgr = ScriptJobManager.instance()
        mgr.subscribe("SceneOpened", self._on_scene_changed, owner=self)
        mgr.subscribe("NewSceneOpened", self._on_scene_changed, owner=self)
        mgr.connect_cleanup(self.ui, owner=self)

    def _on_scene_changed(self) -> None:
        """Handle a Maya scene open / new-scene event.

        Re-binds the store listener (the old store is stale) and
        re-populates the table from CSV or detection, mirroring the
        logic in ``_on_first_show``.
        """
        # The old store is invalidated by ShotStore._on_scene_changed.
        self._unbind_store_listener()
        self._store = None
        self._bind_store_listener()

        if not self._first_shown:
            return

        self._populate_from_source()

    def _populate_from_source(self) -> None:
        """Load CSV or run detection based on the current UI state.

        Shared by ``_on_first_show`` and ``_on_scene_changed`` to keep
        the populate-on-open logic in one place.
        """
        if self.ui.chk_csv.isChecked():
            path = self.ui.txt_csv_path.text().strip()
            if path:
                self._load_csv(path)
                return
        self.detect()

    def _on_store_event(self, event: StoreEvent) -> None:
        """React to ShotStore mutations â€” refresh tree timing if steps are loaded."""

        if self._building:
            return
        if isinstance(event, SettingsChanged):
            # Detection settings changed â€” invalidate cache and re-detect.
            # Guard on _first_shown to avoid triggering detection (and
            # message boxes) before the widget is visible.
            self._cached_gaps = None
            self._cached_gap_ends = None
            if self._first_shown:
                if self._csv_path:
                    # CSV defines steps â€” don't replace them with detected
                    # steps.  Just refresh auto-filled ranges so the new
                    # detection mode takes effect.
                    if self._steps:
                        self._refresh_ranges()
                else:
                    self.detect()
            return
        if not self._steps:
            return
        # Only invalidate cached assessment on structural changes
        # (shot added/removed).  Cosmetic events like ActiveShotChanged
        # or field edits (ShotUpdated) don't change object status.
        if isinstance(event, (ShotRemoved, BatchComplete)):
            self._last_results = []
        store = getattr(self, "_bound_store", None)
        # Only overwrite tree timing from the store when shots have
        # been built for the current round.  Before build, detection
        # ranges in _user_ranges are authoritative.
        if store is not None and self._built_this_round:
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
                parent.setText(COL_START, f"{shot.start:.0f}")
                parent.setText(COL_END, f"{shot.end:.0f}")
                parent.setToolTip(COL_START, f"{shot.end - shot.start:.0f}f")
                parent.setToolTip(COL_END, f"{shot.end - shot.start:.0f}f")
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

        # Right-click on empty space â€” show excluded steps menu
        if item is None:
            excluded = self._column_map.exclude_steps
            if excluded:
                menu = QMenu(tree)
                sub = menu.addMenu(f"Show Excluded ({len(excluded)})")
                for sid in sorted(excluded):
                    sub.addAction(sid, lambda n=sid: self._include_step(n))
                menu.exec_(tree.viewport().mapToGlobal(pos))
            return

        # Resolve to parent step row
        is_child = item.parent() is not None
        step_item = item.parent() if is_child else item
        step_data = step_item.data(0, Qt.UserRole)
        if not isinstance(step_data, BuilderStep):
            return

        # Collect all selected parent step IDs for multi-selection actions
        selected_step_ids = []
        for sel_item in tree.selectedItems():
            parent_item = (
                sel_item.parent() if sel_item.parent() is not None else sel_item
            )
            sel_data = parent_item.data(0, Qt.UserRole)
            if (
                isinstance(sel_data, BuilderStep)
                and sel_data.step_id not in selected_step_ids
            ):
                selected_step_ids.append(sel_data.step_id)

        # Pre-compute built-step names once for all guards in this menu.
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            built_names = {s.name for s in ShotStore.active().shots}
        except Exception:
            built_names = set()
        any_built = bool(built_names & {s.step_id for s in self._steps})

        menu = QMenu(tree)
        act_open = menu.addAction(f"Open '{step_data.step_id}' in Shot Sequencer")
        act_open_shots = menu.addAction(f"Open '{step_data.step_id}' in Shots")
        if not any_built:
            act_open.setEnabled(False)
            act_open.setToolTip("Build shots first")
            act_open_shots.setEnabled(False)
            act_open_shots.setToolTip("Build shots first")

        # Exclude step action (parent rows, pre-build only)
        act_exclude = None
        if not any_built and selected_step_ids:
            menu.addSeparator()
            if len(selected_step_ids) == 1:
                act_exclude = menu.addAction(f"Exclude '{selected_step_ids[0]}'")
            else:
                act_exclude = menu.addAction(f"Exclude {len(selected_step_ids)} Steps")

        # Show excluded submenu when exclusions exist
        excluded = self._column_map.exclude_steps
        if excluded:
            sub = menu.addMenu(f"Show Excluded ({len(excluded)})")
            for sid in sorted(excluded):
                sub.addAction(sid, lambda n=sid: self._include_step(n))

        # Range column actions (parent rows, pre-build only)
        act_set_frame = None
        act_auto_fill = None
        act_clear_range = None
        column = tree.columnAt(pos.x())
        step_is_built = step_data.step_id in built_names
        if not is_child and not step_is_built and column in (COL_START, COL_END):
            menu.addSeparator()
            act_set_frame = menu.addAction("Set Start to Current Frame")
            act_auto_fill = menu.addAction("Auto-fill from Gaps")
            if step_data.step_id in self._user_ranges:
                act_clear_range = menu.addAction("Clear Range")

        # Object-level actions (child rows only)
        act_outliner = None
        act_copy = None
        act_reapply = None
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
                act_copy = menu.addAction(f"Copy '{obj_name}' to Clipboard")
                if self._is_built and obj_data.behaviors:
                    names = ", ".join(fmt_behavior(b) for b in obj_data.behaviors)
                    act_reapply = menu.addAction(f"Apply [{names}]")

        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._open_in_shot_sequencer(step_data.step_id)
        elif chosen is act_open_shots:
            self._open_in_shots(step_data.step_id)
        elif chosen is act_exclude and act_exclude is not None:
            self._exclude_steps(selected_step_ids)
        elif chosen is not None and chosen is act_outliner:
            self._show_in_outliner(obj_name)
        elif chosen is not None and chosen is act_copy:
            from qtpy.QtWidgets import QApplication

            QApplication.clipboard().setText(obj_name)
        elif chosen is act_reapply and act_reapply is not None:
            self._reapply_behavior(step_data.step_id, obj_data)
        elif chosen is act_set_frame and act_set_frame is not None:
            self._set_range_to_current_frame(step_item, step_data.step_id)
        elif chosen is act_auto_fill and act_auto_fill is not None:
            step_idx = self._step_index(step_data.step_id)
            # Clear user ranges from clicked step onward so they re-resolve
            self._user_ranges.pop(step_data.step_id, None)
            self._cascade_from(step_idx)
            self._refresh_ranges(from_step_idx=step_idx)
        elif chosen is act_clear_range and act_clear_range is not None:
            self._user_ranges.pop(step_data.step_id, None)
            self._refresh_ranges()

    # ---- exclude / include steps -----------------------------------------

    def _exclude_steps(self, step_ids) -> None:
        """Add one or more step IDs to the exclude list."""
        if isinstance(step_ids, str):
            step_ids = [step_ids]
        current = set(self._column_map.exclude_steps)
        current.update(step_ids)
        self._column_map.exclude_steps = tuple(sorted(current))
        exclude_set = set(step_ids)
        self._steps = [s for s in self._steps if s.step_id not in exclude_set]
        for sid in step_ids:
            self._user_ranges.pop(sid, None)
        self._populate_table()
        self._update_build_button()
        n = len(self._column_map.exclude_steps)
        names = ", ".join(step_ids)
        self._set_footer(f"Excluded {names} ({n} total excluded).")

    def _include_step(self, step_id: str) -> None:
        """Remove *step_id* from the exclude list and re-parse the CSV."""
        current = set(self._column_map.exclude_steps)
        current.discard(step_id)
        self._column_map.exclude_steps = tuple(sorted(current))
        # Re-parse to restore the step
        path = self._csv_path or self.ui.txt_csv_path.text().strip()
        if path:
            self._load_csv(path)
        else:
            self._set_footer(f"Restored '{step_id}'. Reload CSV to populate.")

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

        step_idx = self._step_index(step_id)
        self._cascade_from(step_idx)
        self._refresh_ranges(from_step_idx=step_idx)

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
        """Open the Shot Sequencer UI and navigate to the shot matching *step_id*.

        The sequencer controller lazily wraps ``ShotStore.active()`` via
        its ``sequencer`` property â€” no manual wiring needed here.
        """
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        if not store.shots:
            self._set_footer("Build shots first before opening the sequencer.")
            return

        self.sb.handlers.marking_menu.show("shot_sequencer")

        seq_slots = self.sb.get_slots_instance("shot_sequencer")
        if seq_slots is None:
            return

        controller = getattr(seq_slots, "controller", None)
        if controller is None:
            return

        # Clear stale session state so prior shifted-out keys
        # and cached segments don't suppress the new display.
        controller._shifted_out_keys.clear()
        controller._segment_cache.clear()
        controller._sync_combobox()

        # Select the shot matching step_id
        cmb = getattr(seq_slots.ui, "cmb_shot", None)
        if cmb is not None:
            for i in range(cmb.count()):
                shot_id = cmb.itemData(i)
                shot = controller.sequencer.shot_by_id(shot_id) if shot_id else None
                if shot and shot.name == step_id:
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(i)
                    cmb.blockSignals(False)
                    controller._sync_to_widget(shot_id, frame=True)
                    controller._update_shot_nav_state()
                    break

    def _open_in_shots(self, step_id: str) -> None:
        """Open the Shots editor UI and navigate to the shot matching *step_id*."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        if not store.shots:
            self._set_footer("Build shots first before opening the shots editor.")
            return

        shot = store.shot_by_name(step_id)
        if shot is None:
            self._set_footer(f"Shot '{step_id}' not found in the store.")
            return

        self.sb.handlers.marking_menu.show("shots")

        # set_active_shot fires ActiveShotChanged which the ShotsController
        # listener handles â€” it syncs the combobox and editor fields.
        store.set_active_shot(shot.shot_id)

    # ---- CSV loading -----------------------------------------------------

    def _setup_recent_csv(self) -> None:
        """Attach a RecentValuesOption and BrowseOption to the CSV path widget."""
        from uitk.widgets.optionBox.options.recent_values import RecentValuesOption
        from uitk.widgets.optionBox.options.browse import BrowseOption

        txt = self.ui.txt_csv_path
        self._recent_csv_option = RecentValuesOption(
            wrapped_widget=txt,
            settings_key="shot_manifest_csv_paths",
            max_recent=10,
        )
        txt.option_box.add_option(self._recent_csv_option)

        self._browse_csv_option = BrowseOption(
            wrapped_widget=txt,
            file_types="CSV Files (*.csv);;All Files (*)",
            title="Open Sequence CSV",
            callback=lambda path: self._on_csv_browsed(path),
        )
        txt.option_box.add_option(self._browse_csv_option)

    def _setup_csv_toggle(self) -> None:
        """Connect the CSV checkbox to enable/disable the path and browse widgets."""
        chk = self.ui.chk_csv
        chk.toggled.connect(self._on_csv_toggled)
        self._sync_csv_widgets(False)

    def _setup_header_menu(self) -> None:
        """Configure the header option menu.

        Generation settings (threshold, mode) now live in the shared
        ``shots.ui`` panel, opened via the Settings button.
        """
        menu = self.ui.header.menu
        menu.setTitle("Shot Manifest:")

        chk_long = menu.add(
            "QCheckBox",
            setText="Long Names",
            setChecked=bool(self._settings.value("long_names", False)),
            setToolTip="Show full DAG paths instead of leaf node names.",
        )
        chk_long.toggled.connect(self._on_long_names_toggled)

        menu.add("Separator", setTitle="Actions")
        menu.add(
            "QPushButton",
            setText="Expand All Missing",
            setObjectName="btn_expand_missing",
            setToolTip="Expand every step row that has missing objects or behaviors.",
        )
        menu.add(
            "QPushButton",
            setText="Expand All Extra",
            setObjectName="btn_expand_extra",
            setToolTip="Expand every step row that has scene-discovered objects not in the CSV.",
        )
        menu.add(
            "QPushButton",
            setText="Colors\u2026",
            setObjectName="btn_manifest_colors",
            setToolTip="Edit manifest status colors.",
        ).released.connect(self._open_color_editor)
        menu.add(
            "QPushButton",
            setText="Audio Clips\u2026",
            setObjectName="btn_audio_clips",
            setToolTip="Open the Audio Clips editor to load, key, and\nmanage audio tracks used by this manifest.",
        ).released.connect(self._open_audio_clips)
        menu.add(
            "QPushButton",
            setText="Shots\u2026",
            setObjectName="btn_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )

        menu.add("Separator", setTitle="About")
        menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Shot Manifest \u2014 Build and validate shots from a CSV\n"
                "file or by generating from animation in the scene.\n\n"
                "Quick Start (CSV):\n"
                "  1. Check the CSV checkbox and browse to a CSV file.\n"
                "  2. Review parsed steps in the table; edit ranges\n"
                "     or exclude steps as needed.\n"
                "  3. Click Build to create shots with behaviors applied.\n"
                "  4. Click Assess to verify completeness.\n\n"
                "Quick Start (Generate from Animation):\n"
                "  1. Uncheck CSV \u2014 shots are generated from animation\n"
                "     using the settings in Shot Settings.\n"
                "  2. Refine ranges in the table if needed.\n"
                "  3. Click Build, then Assess.\n\n"
                "Table Columns:\n"
                "  Step \u2014 Step ID (e.g. A01).\n"
                "  Section \u2014 Read-only grouping label from CSV.\n"
                "  Description \u2014 Audio narration or step notes.\n"
                "  Behaviors \u2014 Per-object actions (fade in/out, etc.).\n"
                "    Click the label on a child row to toggle behaviors.\n"
                "  Start / End \u2014 Frame range per step.\n"
                "    Solid text = user-entered; dim italic = auto-filled.\n\n"
                "Editing Ranges:\n"
                "  \u2022 Double-click Start or End to type a frame or range\n"
                "    (e.g. '120-250'). Downstream steps re-flow.\n"
                "  \u2022 Right-click a range cell:\n"
                "    \u2013 Set Start to Current Frame\n"
                "    \u2013 Auto-fill from Gaps (regenerate and reflow)\n"
                "    \u2013 Clear Range (revert to auto-fill)\n\n"
                "Buttons:\n"
                "  \u2022 Assess \u2014 Read-only comparison against live shots.\n"
                "    Rows are color-tinted: red = missing, normal = valid.\n"
                "  \u2022 Build \u2014 Create or update shots from loaded steps.\n"
                "    Behaviors are applied automatically. Locked shots\n"
                "    are never modified.\n\n"
                "Right-Click Actions:\n"
                "  \u2022 Step row: Exclude step, Open in Shot Sequencer\n"
                "    (post-build), Show Excluded.\n"
                "  \u2022 Child row: Show in Outliner, Re-apply Behaviors\n"
                "    (post-build).\n\n"
                "Tip: Red tint = missing objects or behaviors,\n"
                "grey = locked shots, normal = valid steps."
            ),
        )

    @property
    def _initial_shot_length(self) -> float:
        """Read the shot-construction default from the active store."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        if store is not None:
            return float(store.initial_shot_length)
        return ShotStore.DEFAULT_INITIAL_SHOT_LENGTH

    @property
    def _fit_mode(self) -> str:
        """Read the fit-mode policy from the active store."""
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        if store is not None:
            return store.fit_mode
        return ShotStore.DEFAULT_FIT_MODE

    def _on_long_names_toggled(self, checked: bool) -> None:
        """Persist and apply the long-names display preference."""
        self._settings.setValue("long_names", checked)
        state = self._save_tree_state()
        self._populate_table()
        if self._last_results:
            self._apply_assessment(self._last_results)
        self._restore_tree_state(state)

    def _open_audio_clips(self) -> None:
        """Open the Audio Clips editor."""
        self.sb.handlers.marking_menu.show("audio_clips")

    def _open_color_editor(self) -> None:
        """Launch the status-color editor dialog."""
        from uitk.widgets.editors.color_mapping_editor import ColorMappingDialog
        from uitk.widgets.mixins.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            PASTEL_STATUS,
            BEHAVIOR_STATUS_COLORS,
        )

        # Keys with actual (fg, bg) colours â€” skip 'valid'/'csv_object' (None, None)
        editable_keys = [
            k for k, v in PASTEL_STATUS.items() if v[0] is not None or v[1] is not None
        ]

        # Build defaults dict: {key: (fg_hex, bg_hex)}
        defaults = {}
        for k in editable_keys:
            fg, bg = PASTEL_STATUS[k]
            defaults[k] = (str(fg) if fg else "#808080", str(bg) if bg else "#2A2A2A")

        sections = [("Status Colors", editable_keys)]

        color_settings = SettingsManager(namespace=self._COLOR_SETTINGS_NS)
        dlg = ColorMappingDialog(
            defaults=defaults,
            sections=sections,
            settings=color_settings,
            title="Manifest Colors",
            parent=self.ui,
        )

        def _apply(cmap):
            # Write changed colours back into the live PASTEL_STATUS palette
            for key, val in cmap.items():
                if key in PASTEL_STATUS:
                    PASTEL_STATUS[key] = val
            # Update derived constants
            BEHAVIOR_STATUS_COLORS["missing"] = PASTEL_STATUS["missing_behavior"][0]
            BEHAVIOR_STATUS_COLORS["error"] = PASTEL_STATUS["missing_object"][0]
            # Refresh the table with new colours
            state = self._save_tree_state()
            self._populate_table()
            if self._last_results:
                self._apply_assessment(self._last_results)
            self._restore_tree_state(state)

        dlg.colors_changed.connect(_apply)
        dlg.exec_()

    def _restore_color_overrides(self) -> None:
        """Apply any persisted color overrides to the live palette."""
        from uitk.widgets.mixins.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            PASTEL_STATUS,
            BEHAVIOR_STATUS_COLORS,
        )

        settings = SettingsManager(namespace=self._COLOR_SETTINGS_NS)
        changed = False
        for key in list(PASTEL_STATUS):
            fg_val = settings.value(f"{key}/fg")
            bg_val = settings.value(f"{key}/bg")
            if fg_val or bg_val:
                orig_fg, orig_bg = PASTEL_STATUS[key]
                PASTEL_STATUS[key] = (
                    fg_val or (str(orig_fg) if orig_fg else None),
                    bg_val or (str(orig_bg) if orig_bg else None),
                )
                changed = True
        if changed:
            BEHAVIOR_STATUS_COLORS["missing"] = PASTEL_STATUS["missing_behavior"][0]
            BEHAVIOR_STATUS_COLORS["error"] = PASTEL_STATUS["missing_object"][0]

    def _setup_mapping_combo(self) -> None:
        """Add a mapping-file selector combo to the header menu."""
        from mayatk.anim_utils.shots.shot_manifest.mapping import discover
        from uitk.widgets.widgetComboBox import WidgetComboBox

        menu = self.ui.header.menu
        cmb = menu.add(
            WidgetComboBox,
            setObjectName="cmb_csv_mapping",
            setToolTip="Select a JSON mapping file for CSV parsing.",
        )
        self._cmb_mapping = cmb
        self._refresh_mapping_list()
        cmb.currentTextChanged.connect(self._on_mapping_changed)

    def _refresh_mapping_list(self) -> None:
        """Refresh the mapping combo box items."""
        from mayatk.anim_utils.shots.shot_manifest.mapping import discover

        cmb = self._cmb_mapping
        cmb.blockSignals(True)
        cmb.clear()
        cmb.addItem("(none)")
        search_dir = self._mapping_dir or None
        names = list(discover(search_dir))
        for name in names:
            cmb.addItem(name)
        # Auto-select "default" mapping when available
        if "default" in names:
            cmb.setCurrentIndex(names.index("default") + 1)  # +1 for "(none)"
        cmb.blockSignals(False)
        # Sync _active_mapping with final combo text
        self._on_mapping_changed(cmb.currentText())

    def _on_mapping_changed(self, name: str) -> None:
        """Handle mapping combo selection."""
        from mayatk.anim_utils.shots.shot_manifest.mapping import load_mapping

        if not name or name == "(none)":
            self._active_mapping = None
        else:
            try:
                search_dir = self._mapping_dir or None
                self._active_mapping = load_mapping(name, search_dir)
            except Exception as exc:
                self.logger.error("Failed to load mapping '%s': %s", name, exc)
                self._set_footer(f"Mapping error: {exc}", color=ERROR_COLOR)
                self._active_mapping = None
                return

        # Re-parse the current CSV with the new mapping
        path = self._csv_path or self.ui.txt_csv_path.text().strip()
        if path:
            self._load_csv(path)

    # ---- mode switching (single source of truth) -------------------------

    def _sync_csv_widgets(self, csv_mode: bool) -> None:
        """Sync checkbox and CSV-related widgets to match the active mode."""
        chk = self.ui.chk_csv
        chk.blockSignals(True)
        chk.setChecked(csv_mode)
        chk.blockSignals(False)
        txt = self.ui.txt_csv_path
        txt.setEnabled(csv_mode)
        txt.style().unpolish(txt)
        txt.style().polish(txt)

    def _load_data(
        self,
        steps: List[BuilderStep],
        *,
        ranges: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None,
        csv_path: str = "",
        footer: str = "",
    ) -> None:
        """Single source of truth for mode switching.

        Every code path that changes table contents (detect, CSV load,
        CSV toggle-off) funnels through here so that state, widgets,
        and the table are always consistent.
        """
        self._steps = steps
        self._csv_path = csv_path
        self._user_ranges = dict(ranges) if ranges else {}
        self._last_results = []
        self._last_resolved = []
        self._built_this_round = False
        self._cached_gaps = None
        self._cached_gap_ends = None

        self._sync_csv_widgets(bool(csv_path))
        self._populate_table()
        self._update_build_button()

        if footer:
            self._set_footer(footer)

    def _on_csv_toggled(self, enabled: bool) -> None:
        """Handle the CSV checkbox toggle.

        When checked with a remembered path, reloads CSV data.
        When checked without a path, enables widgets for browsing.
        When unchecked, clears data so detection can be used.
        """
        if enabled:
            path = self.ui.txt_csv_path.text().strip()
            if path:
                self._load_csv(path)
            else:
                self._sync_csv_widgets(True)
        else:
            self._sync_csv_widgets(False)
            self.detect()

    def browse_csv(self) -> None:
        """Open a file dialog and load the selected CSV."""
        if hasattr(self, "_browse_csv_option"):
            self._browse_csv_option.browse()
            return

        from qtpy.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self.ui, "Open Sequence CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        self._on_csv_browsed(path)

    def _on_csv_browsed(self, path: str) -> None:
        """Handle a CSV path selected via browse or BrowseOption."""
        self._sync_csv_widgets(True)
        self.ui.txt_csv_path.setText(path)
        self._load_csv(path)

    def _load_csv(self, path: str) -> None:
        """Parse the CSV and load it via :meth:`_load_data`.

        When an active mapping is selected, delegates to the
        :mod:`mapping` resolver.  Otherwise falls back to
        :func:`parse_csv` with the current :attr:`_column_map`.
        """
        import os

        if not os.path.isfile(path):
            self.ui.txt_csv_path.set_action_color("invalid")
            self._set_footer(f"File not found: {path}", color=ERROR_COLOR)
            return

        try:
            if self._active_mapping is not None:
                from mayatk.anim_utils.shots.shot_manifest.mapping import resolve

                steps = resolve(path, mapping=self._active_mapping)
            else:
                steps = parse_csv(path, columns=self._column_map)
        except Exception as exc:
            self.logger.error("Failed to parse CSV: %s", exc)
            self.ui.txt_csv_path.set_action_color("invalid")
            self._set_footer(f"Error: {exc}", color=ERROR_COLOR)
            return

        self.ui.txt_csv_path.reset_action_color()
        self._recent_csv_option.record(path)
        n_obj = sum(len(s.objects) for s in steps)

        # Seed _user_ranges with existing store positions so the table
        # immediately shows correct Start/End for built steps.
        store_ranges = {}
        try:
            from mayatk.anim_utils.shots._shots import ShotStore

            store = ShotStore.active()
            step_ids = {s.step_id for s in steps}
            store_ranges = {
                s.name: (s.start, s.end)
                for s in store.sorted_shots()
                if s.name in step_ids
            }
        except Exception:
            pass

        self._load_data(
            steps,
            ranges=store_ranges or None,
            csv_path=path,
            footer=f"{len(steps)} steps, {n_obj} objects loaded.",
        )

        # Populate _last_resolved so edit validation has correct bounds
        # for new steps added between existing ones.
        if store_ranges:
            self._refresh_ranges()

    # ---- helpers ---------------------------------------------------------

    def _ensure_steps(self) -> bool:
        """Ensure steps are available, auto-detecting from scene if needed.

        Priority order:
        1. If steps are already loaded, return True immediately.
        2. If CSV checkbox is on and a path exists, load the CSV.
        3. Otherwise, run scene detection.

        Returns True if steps are now available.
        """
        if self._steps:
            return True

        # Try CSV first when enabled
        path = self.ui.txt_csv_path.text().strip()
        if path and self.ui.chk_csv.isChecked():
            self._load_csv(path)
            if self._steps:
                return True

        # Fall back to scene detection
        try:
            self.detect()
        except Exception as exc:
            self.logger.error("Auto-detect failed: %s", exc)
            self._set_footer(f"Detection error: {exc}", color=ERROR_COLOR)

        if not self._steps:
            self._set_footer("No animation detected in scene.")
            return False
        return True

    # ---- button state ----------------------------------------------------

    def _update_build_button(self) -> None:
        """Enable Build only after assess has run and unbuilt steps remain.

        Build always starts disabled.  The assess operation populates
        ``_last_results`` which determines whether build is warranted.
        If all steps are already built, or assess has not been run yet,
        the button stays disabled.
        """
        btn = getattr(self.ui, "b003", None)
        if btn is None:
            return
        if self._last_results:
            needs_build = any(not r.built for r in self._last_results)
        else:
            needs_build = False
        btn.setEnabled(needs_build)

    # ---- assess ----------------------------------------------------------

    # ---- build -----------------------------------------------------------

    def build(self) -> None:
        """Build or update shots in the store from loaded steps."""
        if not self._ensure_steps():
            return

        try:
            import pymel.core as pm
        except ImportError:
            self._set_footer("Maya is required to build shots.", color=ERROR_COLOR)
            return

        from mayatk.anim_utils.shots._shots import ShotStore

        try:
            store = ShotStore.active()
            builder = ShotManifest(store)

            # When selected-keys mode is active, verify keys exist
            # before proceeding â€” even if user ranges are complete.
            use_sel = self._use_selected_keys
            if use_sel:
                self._cached_gaps = None
                regions = self._detect_regions(
                    store.detection_threshold if store else 5.0
                )
                if not regions:
                    self.sb.message_box(
                        "<b>No keys selected.</b><br>"
                        "Select keyframes in the Graph Editor before building.",
                    )
                    self._set_footer(
                        "No selected keys found \u2014 select keyframes first.",
                        color=ERROR_COLOR,
                    )
                    return

            # Resolve ranges â€” short-circuit when all ranges are
            # already complete (detection mode provides full ranges).
            # Incremental mode: when shots already exist and we're not
            # in selected-keys mode, use the resolver's last-cascaded
            # positions so that user edits ripple downstream.  Fall
            # back to store positions when there is no resolved data.
            incremental = self._is_built and not use_sel
            if incremental:
                # Grow-only invariant: existing shots keep their store
                # positions (which may already have been grown to fit
                # audio/animation members via the sequencer). Only new
                # steps get resolver-derived positions, and user ranges
                # always win.
                range_map = {s.name: (s.start, s.end) for s in store.sorted_shots()}
                if self._last_resolved:
                    existing_ids = set(range_map)
                    for sid, s, e, _ in self._last_resolved:
                        if sid in existing_ids or e is None:
                            continue
                        range_map[sid] = (s, e)
                range_map.update(self._user_ranges)
                # Place new steps at their CSV-order predecessor's end
                # so they appear between neighbors instead of at the
                # end of the timeline.  The loop is in CSV order so
                # all predecessors are guaranteed in range_map by the
                # time each step is reached.
                for i, step in enumerate(self._steps):
                    if step.step_id not in range_map:
                        if i > 0:
                            prev_end = range_map[self._steps[i - 1].step_id][1]
                        else:
                            # New step at the very start of the CSV â€”
                            # find the first existing neighbor's start.
                            prev_end = next(
                                (
                                    range_map[s.step_id][0]
                                    for s in self._steps[1:]
                                    if s.step_id in range_map
                                ),
                                1,
                            )
                        range_map[step.step_id] = (prev_end, prev_end)
            elif self._all_ranges_complete():
                range_map = dict(self._user_ranges)
            else:
                resolved = self._resolve_ranges()
                range_map = {
                    sid: (s, e) for sid, s, e, _ in resolved if e is not None
                } or None

            # In selected-keys mode, restrict the step list to steps
            # that actually received a range from the detected regions.
            # This prevents update() from creating shots via its own
            # sequential cursor fallback for unresolved steps.
            build_steps = self._steps
            if use_sel and range_map:
                resolved_ids = set(range_map)
                build_steps = [s for s in self._steps if s.step_id in resolved_ids]
                if not build_steps:
                    self.sb.message_box(
                        "<b>No matching steps.</b><br>"
                        "Selected keys don't map to any CSV steps.",
                    )
                    self._set_footer(
                        "Selected keys don't map to any CSV steps.",
                        color=ERROR_COLOR,
                    )
                    return

            # Detection mode: don't remove existing shots not in steps
            remove = not self._is_detection_mode

            pm.undoInfo(openChunk=True, chunkName="ShotManifest_build")
            self._building = True
            try:
                with store.batch_update():
                    actions, beh, assessment = builder.sync(
                        build_steps,
                        ranges=range_map,
                        remove_missing=remove,
                        zero_duration_fallback=incremental,
                        fit_mode=self._fit_mode,
                        initial_shot_length=self._initial_shot_length,
                    )
                    # Record the source CSV for provenance on reopen.
                    csv_path = self._csv_path or self.ui.txt_csv_path.text().strip()
                    if csv_path:
                        store.source_csv = csv_path
            finally:
                self._building = False
                pm.undoInfo(closeChunk=True)

            # Store the store for later handoff to Shot Sequencer UI
            self._store = store
            self._built_this_round = True

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

            # Sync store.gap from actual shot positions so the spinbox
            # reflects the gap the manifest produced.
            actual_gap = store.compute_gap()
            if abs(actual_gap - store.gap) > 0.5:
                store.gap = actual_gap
                store.mark_dirty()
                store.notify_settings_changed()

            # Refresh tree with post-build assessment
            self._apply_post_build(assessment, store)
            self._update_build_button()
        except Exception as exc:
            self.logger.error("Build failed: %s", exc)
            self.sb.message_box(
                f"<b>Build failed.</b><br>{exc}",
            )
            self._set_footer(f"Build error: {exc}", color=ERROR_COLOR)

    def _apply_post_build(self, results: list, store) -> None:
        """Refresh tree with timing from the store and assessment results."""
        state = self._save_tree_state()
        self._populate_table()
        self._refresh_timing(store)
        self._last_results = results
        self._apply_assessment(results)
        self._restore_tree_state(state)
        self._sync_detection_widgets()

    def _sync_detection_widgets(self) -> None:
        """Refresh the Shots UI widget states via its centralized method."""
        instances = getattr(self.sb, "slot_instances", None) or {}
        shots_slots = instances.get("shots") if isinstance(instances, dict) else None
        if shots_slots is not None:
            ctrl = getattr(shots_slots, "controller", None)
            if ctrl is not None and hasattr(ctrl, "refresh_state"):
                ctrl.refresh_state()
                return
        # Fallback: direct widget manipulation if controller not available
        try:
            shots_ui = self.sb.loaded_ui.shots
        except Exception:
            return
        store = self._active_store()
        enabled = store.is_detection_relevant if store is not None else True
        for attr in ("cmb_detection_mode", "spn_detection"):
            widget = getattr(shots_ui, attr, None)
            if widget is not None:
                widget.setEnabled(enabled)

    # ---- assess ----------------------------------------------------------

    def assess(self, skip_key_check: bool = False) -> None:
        """Compare CSV steps against the live Maya shots and color the tree.

        Parameters:
            skip_key_check: When ``True``, bypass the selected-keys guard.
                Used by internal callers (e.g. re-apply behavior) that
                already know the scene state and just need a status refresh.
        """
        if not self._ensure_steps():
            return

        try:
            import pymel.core as pm
        except ImportError:
            self._set_footer("Maya is required to assess shots.", color=ERROR_COLOR)
            return

        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        builder = ShotManifest(store)
        use_sel = self._use_selected_keys

        # In selected-keys mode, verify keys exist before proceeding â€”
        # but only when shots haven't been built yet (key selection is for
        # initial range discovery, not for re-assessment of existing shots).
        if use_sel and not skip_key_check and not self._is_built:
            self._cached_gaps = None
            regions = self._detect_regions(store.detection_threshold if store else 5.0)
            if not regions:
                self.sb.message_box(
                    "<b>No keys selected.</b><br>"
                    "Select keyframes in the Graph Editor before assessing.",
                )
                self._set_footer(
                    "No selected keys found \u2014 select keyframes first.",
                    color=ERROR_COLOR,
                )
                return

        # Invalidate cached gaps so _resolve_ranges rescans the scene
        self._cached_gaps = None
        self._cached_gap_ends = None

        results = builder.assess(self._steps, skip_scene_discovery=use_sel)

        # Write per-object statuses back to shot metadata so that
        # both the manifest and sequencer share the same classification.
        built_map = {s.name: s for s in store.sorted_shots()}
        for r in results:
            shot = built_map.get(r.step_id)
            if shot is None:
                continue
            obj_status = {o.name: o.status for o in r.objects}
            for extra in r.additional_objects:
                obj_status.setdefault(extra, "additional")
            shot.metadata["object_status"] = obj_status

        # Rebuild tree and enrich with timing from store + status
        state = self._save_tree_state()
        self._populate_table()
        if not self._is_built:
            self._refresh_ranges()
        self._refresh_timing(store)
        self._last_results = results
        self._apply_assessment(results)
        self._restore_tree_state(state)

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


class ShotManifestSlots(ptk.LoggingMixin):
    """Switchboard slot class â€” routes UI events to the controller."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shot_manifest

        self.controller = ShotManifestController(self)

    # ---- header ----------------------------------------------------------

    def header_init(self, widget):
        """Header menu is configured once in controller.__init__."""
        pass

    def btn_expand_missing(self):
        """Expand all step rows that have missing objects or behaviors."""
        self.controller.expand_missing()

    def btn_expand_extra(self):
        """Expand all step rows that have scene-discovered extra objects."""
        self.controller.expand_extra()

    def btn_settings(self):
        """Open the shared shots settings panel."""
        self.sb.handlers.marking_menu.show("shots")

    # ---- buttons ---------------------------------------------------------

    def b002(self):
        """Assess shots against live Maya scene."""
        self.controller.assess()

    def b003(self):
        """Build shots from loaded steps (or auto-detect from scene)."""
        self.controller.build()
