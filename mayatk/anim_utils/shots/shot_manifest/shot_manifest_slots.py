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
    Detection,
    BuilderStep,
    BuilderObject,
    ColumnMap,
    ShotManifest,
    ManifestModel,
    ShotPairing,
)
from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
    ManifestData,
    ERROR_COLOR,
    SETTINGS_NS,
    COL_STEP,
    COL_DESC,
    COL_START,
    COL_END,
)
from mayatk.anim_utils.shots.shot_manifest.range_resolver import RangeResolver
from mayatk.anim_utils.shots._shots import (
    BatchComplete,
    SettingsChanged,
    ShotDefined,
    ShotRemoved,
    ShotUpdated,
    StoreEvent,
)
from mayatk.anim_utils.shots.shot_manifest.table_presenter import ManifestTableMixin
from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors


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
        # Where the loaded steps came from: "csv" (a manifest), "scene" (the
        # store's own shots) or "detect" (animation regions, nothing built yet).
        self._source: str = ""
        self._store = None  # ShotStore from last build
        self._last_results: list = []  # Last assessment results

        from uitk.managers.settings_manager import SettingsManager

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
        # connect_cleanup (in _install_scene_jobs) tears down only the SJM
        # subscriptions; the ShotStore listener needs remove_callbacks, or it
        # keeps the dead controller alive and firing after the panel closes.
        self.ui.destroyed.connect(lambda *_: self.remove_callbacks())
        self._column_map = ColumnMap()
        self._active_mapping = None  # effective template (options applied)
        self._mapping_template = None  # the template as loaded, options block and all
        self._mapping_name = None
        self._option_rows: list = []  # header-menu rows built from its options
        self._mapping_dir = None  # custom directory override
        self._setup_recent_csv()
        self._setup_csv_path_editing()
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
            add_widget(btn, side="right", background=True)

    # ---- first-show auto-populate ----------------------------------------

    def _on_first_show(self) -> None:
        """Auto-populate the table the first time the window is shown."""
        self._first_shown = True
        self._open_scene_source()

    # ---- built state -----------------------------------------------------

    @property
    def _is_built(self) -> bool:
        """True if any loaded step has a shot (``_pairing``)."""
        try:
            return bool(self._pairing().shots)
        except Exception:
            return False

    def _step_is_built(self, step_id: str) -> bool:
        """True if step *step_id* has a shot (``_pairing``)."""
        try:
            return step_id in self._pairing().shots
        except Exception:
            return False

    # ---- range column editing --------------------------------------------

    @property
    def _is_detection_mode(self) -> bool:
        """True when the steps came from the scene (its shots or detected
        animation), not a CSV -- so a build never removes a shot."""
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
        return ptk.RangeResolver.all_ranges_complete(self._steps, self._user_ranges)

    # ---- store access -----------------------------------------------------

    @staticmethod
    def _store_cls():
        """This host's ``ShotStore`` class: the one name the twins spell
        differently, so every method that reaches the store is shared text."""
        from mayatk.anim_utils.shots._shots import ShotStore

        return ShotStore

    @staticmethod
    def _manifest_cls():
        """This host's manifest engine class (the twins' other spelling)."""
        return ShotManifest

    @property
    def _match(self) -> str:
        """How the selected template pairs steps with shots (``match``)."""
        return (self._active_mapping or {}).get("match", "name")

    def _manifest(self, store=None):
        """This host's manifest engine over *store* (default: the active one),
        pairing steps with shots the way the selected template says."""
        store = store if store is not None else self._active_store()
        return self._manifest_cls()(store, match=self._match)

    def _pairing(self, steps=None, store=None) -> ShotPairing:
        """Which shot each step (default: the loaded ones) is -- the pure
        ``ShotManifest.pair``, the one place a step finds its shot (binding,
        name, then order); it reads only the store, so no host engine."""
        store = store if store is not None else self._active_store()
        steps = self._steps if steps is None else steps
        if store is None or not steps:
            return ShotPairing()
        return ptk.ShotManifest(store, match=self._match).pair(steps)

    def _orphan_shots(self, pairing: Optional[ShotPairing] = None) -> list:
        """Shots no step of the loaded sheet pairs with ("not in doc" rows);
        only a sheet can leave a shot out, so none in the other modes."""
        if self._source != "csv":
            return []
        return (pairing if pairing is not None else self._pairing()).orphans

    def _remove_orphan(self, shot) -> None:
        """Remove one shot no doc step pairs with -- the only way a shot leaves
        the store from this panel: explicit, one at a time, undoable."""
        store = self._active_store()
        if store is None:
            return
        with store.scene_edit("manifest_remove_shot"):
            store.remove_shot(shot.shot_id)
        self._populate_table()
        self._set_footer(f"Removed shot '{shot.name}'; its keys stay in the scene.")

    def _active_store(self):
        """Return the cached ShotStore, or try ShotStore.active()."""
        if self._store is not None:
            return self._store
        try:
            return self._store_cls().active()
        except Exception:
            return None

    # ---- scene detection -------------------------------------------------

    def _detect_regions(self, gap_threshold: float) -> list:
        """Return detected shot regions, respecting the detection mode.

        Returns an empty list when a selected-keys mode is active and
        no keys are selected.  Callers are responsible for showing
        appropriate user feedback (message box, footer, etc.).

        The store's detection_mode is always respected regardless of
        whether a CSV is loaded — CSV defines steps, detection_mode
        controls how timing boundaries are discovered.
        """
        store = self._active_store()
        mode = store.detection_mode if store is not None else "auto"
        if mode != "auto":
            return Detection.regions_from_selected_keys(
                gap_threshold=gap_threshold, key_filter=mode
            )
        return Detection.detect_shot_regions(gap_threshold=gap_threshold)

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
            # Reject a rename colliding with another step's id — duplicate
            # step_ids clobber each other's ranges at build time (parse_csv
            # guards the same way).
            if any(s.step_id == new_name for s in self._steps if s is not step_data):
                import maya.cmds as cmds

                cmds.warning(f"Duplicate step name '{new_name}' — rename reverted.")
                tree = self.ui.tbl_steps
                tree.blockSignals(True)
                item.setText(COL_STEP, step_data.step_id)
                tree.blockSignals(False)
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
                # The cell renders display_text (the description) — writing
                # the edit to .audio would silently destroy the narration
                # while the visible description stayed unchanged.
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

        # The edit rules (numbers, a start, start >= 0, end > start) are
        # RangeResolver.parse_range_edit's; both cells empty clears the range.
        try:
            user_range = ptk.RangeResolver.parse_range_edit(
                item.text(COL_START), item.text(COL_END)
            )
        except ValueError:
            self._revert_range_cell(item, step_data.step_id)
            return
        if user_range is None:
            self._user_ranges.pop(step_data.step_id, None)
            self._refresh_ranges()
            return
        start, end = user_range

        # Reject start before the previous step's resolved end.
        step_idx = self._step_index(step_data.step_id)
        if step_idx < 0:
            return
        prev_end = ptk.RangeResolver.previous_end(
            self._steps, self._last_resolved, step_idx
        )
        if prev_end is not None and start < prev_end:
            self._revert_range_cell(item, step_data.step_id)
            return

        # Valid — store, clear downstream, and refresh.
        self._user_ranges[step_data.step_id] = (start, end)
        self._cascade_from(step_idx)
        self._refresh_ranges(from_step_idx=step_idx)

    def _step_index(self, step_id: str) -> int:
        """Return the list index for *step_id*, or -1 if not found."""
        return ptk.RangeResolver.step_index(self._steps, step_id)

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
        ptk.RangeResolver.cascade_from(self._steps, self._user_ranges, step_idx)

    # ---- auto-fill logic -------------------------------------------------

    def _placement_on_regions(self) -> Optional[bool]:
        """Whether new shots go on the detected animation regions (``True``),
        one after another (``False``), or the build stops (``None``).

        Steps meet regions IN ORDER, which is right only when there is one
        region per step; when the counts differ that pairing is a guess, so
        the user decides with both counts in front of them.  Selected keys
        are the user's own boundaries and never ask.
        """
        regions = len(self._cached_gaps or [])
        auto = [s for s in self._steps if s.step_id not in self._user_ranges]
        if self._use_selected_keys or not regions or len(auto) in (0, regions):
            return True
        answer = self.sb.message_box(
            f"<b>{len(auto)} steps, {regions} animation regions.</b><br>"
            "New shots are placed on the scene's animation regions in order, "
            "which only lines up with one region per step.<br><br>"
            "<b>Yes</b> \u2014 place them on the regions anyway<br>"
            "<b>No</b> \u2014 place them one after another (adjust later)<br>"
            "<b>Cancel</b> \u2014 set the steps' ranges first",
            "Yes",
            "No",
            "Cancel",
        )
        if answer == "Yes":
            return True
        return False if answer == "No" else None

    def _resolve_ranges(
        self,
        from_step_idx: int = 0,
        regions: bool = True,
    ) -> List[Tuple[str, float, Optional[float], bool]]:
        """Compute a resolved (start, end) for every step.

        Detects/caches animation regions, then delegates to the
        standalone :func:`._range_resolver.resolve_ranges` algorithm.
        ``regions=False`` ignores them: steps are placed one after another.
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
            gap_starts, self._cached_gap_ends = ptk.RangeResolver.gaps_from_regions(
                self._detect_regions(det_threshold)
            )
            self._cached_gaps = gap_starts

        if not regions:
            gap_starts = []
        if use_sel and not gap_starts:
            return []

        # When no animation regions are detected (regardless of mode),
        # use uniform default durations so steps get sensible placeholder
        # ranges instead of behavior-derived micro-durations.  This also
        # covers the case where the scene has animation but the chosen
        # detection mode found no boundaries (e.g. skip_zero with no
        # zero-valued keys).  The store's initial_shot_length is the
        # user-facing policy for new-shot sizing (default 200f).
        default_dur = (
            self._initial_shot_length if (not gap_starts and not use_sel) else 0
        )

        resolved = RangeResolver.resolve_ranges(
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
            store = self._store_cls().active()
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

        self._open_scene_source()

    def _open_scene_source(self) -> None:
        """Point the source field at this scene's manifest, then populate.

        The scene remembers the CSV it was built from (``store.source_csv``,
        recorded at build), so a scene built from a sheet re-opens checked
        against that sheet and one built any other way opens on its own shots.
        Shared by ``_on_first_show`` and ``_on_scene_changed``.
        """
        store = self._active_store()
        self.ui.txt_csv_path.setText(store.source_csv if store is not None else "")
        self._populate_from_source()

    def _populate_from_source(self) -> None:
        """Load the manifest named in the source field, else the scene's shots.

        No mode switch: a path or link checks the scene against that file; an
        empty field (or one that fails to load) shows the scene's own shots,
        and a scene with none yet falls through to animation detection.
        """
        path = self.ui.txt_csv_path.text().strip()
        if path and self._load_csv(path):
            return
        self._load_scene_shots(manifest_failed=bool(path))

    def _load_scene_shots(self, manifest_failed: bool = False) -> None:
        """Show the store's shots as the steps; detect when there are none.

        The steps carry each shot's description, section, members and (for a
        manifest-built shot) its behaviors (``BuilderStep.from_shots``), so
        Assess checks the scene against itself: missing objects, broken
        behaviors, unlisted animated objects.  A build from this source only
        patches -- it never removes a shot (``_is_detection_mode``).

        Parameters:
            manifest_failed: The source field named a manifest that did not
                load; its reason stays on the field and the footer says so.
        """
        store = self._active_store()
        shots = store.sorted_shots() if store is not None else []
        if not shots:
            if manifest_failed:
                self._load_data([])  # never leave a previous scene's rows up
            else:
                self.detect()
            return
        steps, ranges = BuilderStep.from_shots(shots)
        steps = self._drop_excluded(steps)
        n_obj = sum(len(s.objects) for s in steps)
        footer = f"{len(steps)} shots, {n_obj} objects from the scene."
        self._load_data(steps, ranges=ranges, source="scene", footer=footer)
        if manifest_failed:
            self._set_footer(
                f"Manifest not loaded (see the field) \u2014 showing {footer}",
                color=ERROR_COLOR,
            )

    def _drop_excluded(self, steps: List[BuilderStep]) -> List[BuilderStep]:
        """*steps* less the context-menu exclusions (case-insensitive)."""
        if not self._column_map.exclude_steps:
            return steps
        excluded = {e.upper() for e in self._column_map.exclude_steps}
        return [s for s in steps if s.step_id.upper() not in excluded]

    def _on_store_event(self, event: StoreEvent) -> None:
        """React to ShotStore mutations — refresh tree timing if steps are loaded."""

        if self._building:
            return
        if isinstance(event, SettingsChanged):
            # Detection settings changed — invalidate cache and re-detect.
            # Guard on _first_shown to avoid triggering detection (and
            # message boxes) before the widget is visible.
            self._cached_gaps = None
            self._cached_gap_ends = None
            if self._first_shown:
                if self._csv_path:
                    # CSV defines steps — don't replace them with detected
                    # steps.  Just refresh auto-filled ranges so the new
                    # detection mode takes effect.
                    if self._steps:
                        self._refresh_ranges()
                elif self._source != "scene":
                    # The scene's own shots don't depend on detection settings.
                    self.detect()
            return
        if (
            self._source == "scene"
            and self._first_shown
            and isinstance(
                event, (ShotDefined, ShotUpdated, ShotRemoved, BatchComplete)
            )
        ):
            # The store IS the source: follow its edits (renames, descriptions,
            # ranges, added/removed shots), keeping the user's expansion.
            state = self._save_tree_state()
            self._load_scene_shots()
            self._restore_tree_state(state)
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

        timing_map = self._pairing().shots
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

        # Right-click on empty space — show excluded steps menu
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
            if getattr(step_data, "shot_id", None) is not None:  # "not in doc" row
                menu = QMenu(tree)
                act_remove = menu.addAction(f"Remove Shot '{step_data.name}'")
                act_remove.setToolTip(
                    "No doc step pairs with this shot. Removes its record; "
                    "its keys stay in the scene."
                )
                if menu.exec_(tree.viewport().mapToGlobal(pos)) is act_remove:
                    self._remove_orphan(step_data)
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

        # Pre-compute the pairing once for all guards in this menu.
        try:
            built_names = set(self._pairing().shots)
        except Exception:
            built_names = set()
        any_built = bool(built_names)

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
        act_audio = None
        effect_actions = {}
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
                    names = ", ".join(
                        ManifestData.fmt_behavior(b) for b in obj_data.behaviors
                    )
                    act_reapply = menu.addAction(f"Apply [{names}]")
                # The panels that say HOW its behaviors are keyed, opened on
                # this object alone (the recipe there is the build's).
                for channel, verb in self._effect_pages(obj_data):
                    action = menu.addAction(f"{verb} '{obj_name}'\u2026")
                    effect_actions[action] = channel
                if obj_data.kind == "audio":
                    act_audio = menu.addAction(f"Open '{obj_name}' in Audio Clips")

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
        elif chosen is not None and chosen in effect_actions:
            self._open_effect(step_data.step_id, obj_data, effect_actions[chosen])
        elif chosen is act_audio and act_audio is not None:
            self._open_audio_clip(obj_name)
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
        """Remove *step_id* from the exclude list and re-load the source."""
        current = set(self._column_map.exclude_steps)
        current.discard(step_id)
        self._column_map.exclude_steps = tuple(sorted(current))
        self._populate_from_source()

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
            import maya.cmds as cmds
        except ImportError:
            return
        store = self._active_store()
        if store is not None:
            obj_name = store.resolve_member(obj_name)[0]
        if not cmds.objExists(obj_name):
            self._set_footer(f"'{obj_name}' not found in scene.", color="#D4908F")
            return
        # A view mirror, not an edit: recording it costs the user a Ctrl+Z
        # per click before they reach their own work (same guard as the
        # sequencer's ``_select_and_show``).
        from mayatk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_disabled():
            cmds.select(obj_name, replace=True)
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.reveal_in_outliner([obj_name])

    def _open_in_shot_sequencer(self, step_id: str) -> None:
        """Open the Shot Sequencer UI and navigate to the shot matching *step_id*.

        The sequencer controller lazily wraps ``ShotStore.active()`` via
        its ``sequencer`` property — no manual wiring needed here.
        """
        store = self._store_cls().active()
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

        # Select the shot paired with step_id
        target = self._pairing().shots.get(step_id)
        cmb = getattr(seq_slots.ui, "cmb_shot", None)
        if cmb is not None and target is not None:
            for i in range(cmb.count()):
                shot_id = cmb.itemData(i)
                if shot_id == target.shot_id:
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(i)
                    cmb.blockSignals(False)
                    controller._sync_to_widget(shot_id, frame=True)
                    controller._update_shot_nav_state()
                    break

    #: The Render Effects page each recipe effect is keyed from, and the verb
    #: its row entry reads with.
    EFFECT_PAGES = {
        "fade_in": ("opacity", "Fade"),
        "fade_out": ("opacity", "Fade"),
        "pulse": ("highlight", "Highlight"),
    }

    @classmethod
    def _effect_pages(cls, obj) -> list:
        """``[(channel, verb)]`` -- the Render Effects pages *obj*'s behaviors
        are keyed from, each once, in behavior order."""
        pages = []
        for behavior in obj.behaviors or ():
            page = cls.EFFECT_PAGES.get(Behaviors.effect_of(behavior))
            if page is not None and page not in pages:
                pages.append(page)
        return pages

    def _open_effect(self, step_id: str, obj, channel: str) -> None:
        """Open Render Effects focused on *obj*'s *channel* effect.

        The picker hides and the header names the object and its step; the
        object is selected. Once its step is built, the panel's Key re-applies
        the object's behaviors where the build places them
        (:meth:`_reapply_behavior`) -- the recipe the page edits is the one
        that keys them.
        """
        store = self._active_store()
        node = store.resolve_member(obj.name)[0] if store is not None else obj.name
        leaf = str(node).split("|")[-1].split(":")[-1]
        self.sb.handlers.marking_menu.show("render_effects")
        slots = self.sb.get_slots_instance("render_effects")
        if slots is None or not hasattr(slots, "focus"):
            return
        apply = None
        if self._pairing().shots.get(step_id) is not None:

            def apply():
                self._reapply_behavior(step_id, obj)
                return f"Re-applied {leaf}'s behaviors in {step_id}."

        slots.focus(
            channel,
            [node],
            title=f"{leaf} \u00b7 {step_id}",
            apply=apply,
            apply_text=f"Apply to '{leaf}' in {step_id}" if apply else "",
        )

    def _open_audio_clip(self, name: str) -> None:
        """Open Audio Clips on *name*'s track."""
        self.sb.handlers.marking_menu.show("audio_clips")
        slots = self.sb.get_slots_instance("audio_clips")
        if slots is not None and hasattr(slots, "select_track"):
            slots.select_track(name)

    def _open_in_shots(self, step_id: str) -> None:
        """Open the Shots editor UI and navigate to the shot matching *step_id*."""
        store = self._store_cls().active()
        if not store.shots:
            self._set_footer("Build shots first before opening the shots editor.")
            return

        shot = self._pairing().shots.get(step_id)
        if shot is None:
            self._set_footer(f"No shot pairs with '{step_id}' yet.")
            return

        self.sb.handlers.marking_menu.show("shots")

        # set_active_shot fires ActiveShotChanged which the ShotsController
        # listener handles — it syncs the combobox and editor fields.
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
        # Picking a recent path should load it (parity with Browse).  The
        # signal fires only on an explicit user selection, never on record,
        # so this can't loop with _load_csv's record() call.
        self._recent_csv_option.value_selected.connect(self._on_csv_recent_selected)

        self._browse_csv_option = BrowseOption(
            wrapped_widget=txt,
            file_types="CSV Files (*.csv);;All Files (*)",
            title="Open Sequence CSV",
            callback=lambda path: self._on_csv_browsed(path),
        )
        txt.option_box.add_option(self._browse_csv_option)

    def _setup_csv_path_editing(self) -> None:
        """Make the CSV path field typeable/pasteable with live validation.

        The field ships read-only (browse-only) in the .ui.  Here we allow
        direct entry, attach uitk's ``"file_or_url"`` validator (an existing
        file, or a URL probed off the UI thread; red + reason otherwise), and load
        the CSV on commit (Enter / focus-out) via :meth:`_on_csv_path_edited`.
        """
        txt = self.ui.txt_csv_path
        txt.setReadOnly(False)
        # The scene owns its source (ShotStore.source_csv); a path restored
        # from the last session would check this scene against another's sheet.
        # Past paths stay one click away in the recent-values list.
        txt.restore_state = False
        # uitk's "file_or_url" preset: an existing file passes synchronously; a
        # URL passes on shape, then uitk's deferred probe (off the UI thread)
        # settles reachability and hands its reason to the callable tooltip.
        txt.set_validator(
            "file_or_url",
            invalid_tooltip=lambda problem: self._csv_source_tooltip(
                problem or "CSV file not found."
            ),
            valid_tooltip=self._csv_source_tooltip(),
            empty_tooltip=self._csv_source_tooltip(),
            pending_tooltip=self._csv_source_tooltip("Checking the link\u2026"),
            empty_is_valid=True,
        )
        txt.editingFinished.connect(self._on_csv_path_edited)

    def _csv_source_tooltip(self, problem: Optional[str] = None) -> str:
        """The CSV field's tooltip; *problem* (when given) leads as the body."""
        tt = self.sb.tooltip
        return tt.fmt(
            title="CSV Source",
            body=problem
            or "A local CSV file, or a web address that serves one.  Leave it "
            "empty to review the scene's own shots.",
            sections=[
                (
                    "Local file",
                    ["Browse from the option box, or paste a path."],
                ),
                (
                    "Web address",
                    [
                        "Paste an <b>http(s)</b> link to a CSV and press "
                        f"{tt.kbd('Enter')}.",
                        "A <b>Google Sheets</b> share link works as-is: File > "
                        "Share > <b>Anyone with the link</b> (Viewer), then copy "
                        "the link.  The tab named in the link is the one used.",
                        "Reload re-fetches, so edits made in the sheet arrive on "
                        "the next load.",
                    ],
                ),
            ],
            notes=[
                "A link is checked in the background and turns red when it "
                "can't be fetched; the reason shows here and in the footer.",
                "Sheets that require sign-in are not supported.",
            ],
        )

    def _mark_csv_invalid(self, reason: str) -> None:
        """Red field, tooltip and footer all carrying the same *reason*."""
        self.ui.txt_csv_path.set_action_color("invalid")
        self.ui.txt_csv_path.setToolTip(self._csv_source_tooltip(reason))
        self._set_footer(reason, color=ERROR_COLOR)

    def _on_csv_path_edited(self) -> None:
        """Load the CSV when a typed/pasted path is committed (Enter / focus-out).

        Skips an unchanged path (a bare re-commit on focus-out); a cleared
        field returns to the scene's own shots.  Any other changed path is
        handed to _load_csv -- the
        single authority on validity -- which strips it, reports a missing
        file, surfaces an unreadable cloud placeholder, and keeps the field
        editable on failure.
        """
        path = self.ui.txt_csv_path.text().strip()
        if path == self._csv_path:
            return
        if not path:
            self._load_scene_shots()
            return
        self._load_csv(path)

    def _on_csv_recent_selected(self, _value=None) -> None:
        """Load the CSV when a path is chosen from the recent-values list."""
        path = self.ui.txt_csv_path.text().strip()
        if path:
            self._on_csv_browsed(path)

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
            setText="Copy Asset Names",
            setObjectName="btn_copy_asset_names",
            setToolTip=(
                "Copy the sheet's Asset Names column with the names the panel\n"
                "found -- read from the description, or auto-filled from the\n"
                "scene -- written in. Paste it over that column, starting at the\n"
                "sheet's first row; every other cell keeps its value."
            ),
        ).released.connect(self._copy_asset_names)
        menu.add(
            "QPushButton",
            setText="Audio Clips\u2026",
            setObjectName="btn_audio_clips",
            setToolTip="Open the Audio Clips editor to load, key, and\nmanage audio tracks used by this manifest.",
        ).released.connect(self._open_audio_clips)
        menu.add(
            "QPushButton",
            setText="Render Effects\u2026",
            setObjectName="btn_render_effects",
            setToolTip="Open Render Effects to key or revise the opacity and highlight\nchannels this manifest's fade and highlight behaviors key.",
        ).released.connect(self._open_render_effects)
        menu.add(
            "QPushButton",
            setText="Shots\u2026",
            setObjectName="btn_settings",
            setToolTip="Open shared shot generation, gap, and editing settings.",
        )

        self.ui.header.set_help_text(
            self.sb.tooltip.fmt(
                title="Shot Manifest",
                body="Check the scene's shots against a build sheet, or review the shots the scene already has.",
                sections=[
                    (
                        "Quick Start \u2014 Build Sheet",
                        [
                            "Browse to a CSV file, or paste a web address (a Google "
                            "Sheets share link works) and press Enter.",
                            "Review parsed steps in the table; edit ranges or exclude steps as needed.",
                            "Click <b>Build</b> to create shots with behaviors applied.",
                            "Click <b>Assess</b> to verify completeness.",
                            "The template's options (under its picker in the header "
                            "menu) adapt it to the sheet: step-ID style, audio, and "
                            "<b>Auto-fill Missing Assets</b> -- then <b>Copy Asset "
                            "Names</b> pastes those back into the sheet.",
                        ],
                    ),
                    (
                        "No Build Sheet",
                        [
                            "Leave the field empty (or clear it) \u2014 the table shows the "
                            "scene's own shots with their descriptions; <b>Assess</b> checks "
                            "them for missing objects and behaviors.",
                            "A scene with no shots yet is generated from animation using "
                            "the settings in Shot Settings; refine ranges, then <b>Build</b>.",
                            "A scene built from a sheet re-opens checked against that sheet.",
                        ],
                    ),
                    (
                        "Table Columns",
                        [
                            "<b>Step</b> \u2014 Step ID (e.g. A01).",
                            "<b>Section</b> \u2014 Read-only grouping label from the sheet.",
                            "<b>Description</b> \u2014 Audio narration or step notes.",
                            "<b>Behaviors</b> \u2014 Per-object actions; click the child row label to toggle.",
                            "<b>Start / End</b> \u2014 Frame range. Solid text = user-entered; dim italic = auto-filled.",
                        ],
                    ),
                    (
                        "Editing &amp; Actions",
                        [
                            "Double-click Start or End to type a frame. Downstream steps re-flow.",
                            "Right-click a range cell: Set Start to Current Frame, Auto-fill from Gaps, Clear Range.",
                            "<b>Assess</b> \u2014 Read-only comparison; red tint = missing, grey = locked, normal = valid.",
                            "<b>Build</b> \u2014 Create or update shots from loaded steps. Locked shots are never modified. "
                            "Fades and highlights are keyed from the scene's effect recipe (Render Effects); "
                            "Build re-keys those an older recipe made and removes the keys of behaviors the "
                            "doc dropped, so it stays enabled while either is pending.",
                            "Right-click a step row: Open in Shot Sequencer or Shots (once built), "
                            "Exclude (before a build), Show Excluded. An object row adds Show in "
                            "Outliner, Copy, Apply its behaviors, and Fade / Highlight '...' -- Render "
                            "Effects on that object alone, where Key re-applies its behaviors; an audio "
                            "row opens its clip in Audio Clips; a 'not in doc' row offers Remove Shot "
                            "-- its keys stay in the scene.",
                        ],
                    ),
                ],
            )
        )

    @property
    def _initial_shot_length(self) -> float:
        """Read the shot-construction default from the active store."""
        store = self._store_cls().active()
        if store is not None:
            return float(store.initial_shot_length)
        return self._store_cls().DEFAULT_INITIAL_SHOT_LENGTH

    @property
    def _fit_mode(self) -> str:
        """Read the fit-mode policy from the active store."""
        store = self._store_cls().active()
        if store is not None:
            return store.fit_mode
        return self._store_cls().DEFAULT_FIT_MODE

    def _fill_missing_assets(self) -> None:
        """Give the loaded sheet's asset-less steps the scene's objects.

        ``ShotManifest.fill_missing_assets``: what each step's paired shot
        holds (its members and what animates in its range) -- a step with no
        shot stays empty (nothing links it to scene time).  The added objects are ``generated``: marked in the
        table, built like any other, and what :meth:`_copy_asset_names` writes
        back to the sheet.
        """
        store = self._active_store()
        mapping = self._active_mapping or {}
        if not (
            mapping.get("fill_missing_assets") and self._steps and store is not None
        ):
            return
        filled = self._manifest(store).fill_missing_assets(self._steps)
        unfilled = [
            s.step_id
            for s in self._steps
            if not any(o.kind != "audio" for o in s.objects)
        ]
        if filled:
            self._populate_table()
            self._refresh_ranges()
        n_obj = sum(len(names) for names in filled.values())
        footer = f"{len(self._steps)} steps loaded; auto-filled {len(filled)} ({n_obj} objects)"
        if unfilled:
            footer += (
                f"; {len(unfilled)} have no shot to take objects from -- "
                "build or match shots first"
            )
        self._set_footer(footer + ".")

    def _copy_asset_names(self) -> None:
        """Put the sheet's Asset Names column, with the names the sheet's asset
        cells didn't list (read from the description or auto-filled) written
        into their steps' empty cells, on the clipboard
        (``ManifestModel.asset_column``)."""
        from qtpy.QtCore import QMimeData
        from qtpy.QtWidgets import QApplication

        fills = {
            s.step_id: [o.name for o in s.objects if o.origin != "column"]
            for s in self._steps
        }
        fills = {sid: names for sid, names in fills.items() if names}
        if not self._csv_path or not fills:
            self._set_footer(
                "Nothing to copy: no step takes its objects from its description "
                "or the scene (the template's Objects and Auto-fill Missing "
                "Assets options).",
                color=ERROR_COLOR,
            )
            return
        columns = (
            ColumnMap.from_dict(self._active_mapping.get("columns", {}))
            if self._active_mapping is not None
            else self._column_map
        )
        try:
            column = ManifestModel.asset_column(self._csv_path, fills, columns=columns)
        except (OSError, ValueError) as exc:
            self._set_footer(f"Couldn't copy asset names: {exc}", color=ERROR_COLOR)
            return
        tsv, html = ManifestModel.column_clipboard(column)
        mime = QMimeData()
        mime.setText(tsv)
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)
        self._set_footer(
            f"Copied {len(column)} rows ({len(fills)} steps filled). In the sheet, "
            "select the Asset Names cell in row 1 and paste."
        )

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

    def _open_render_effects(self) -> None:
        """Open the Render Effects panel."""
        self.sb.handlers.marking_menu.show("render_effects")

    def _open_color_editor(self) -> None:
        """Launch the status-color editor dialog."""
        from uitk.widgets.editors.color_mapping_editor import ColorMappingDialog
        from uitk.managers.settings_manager import SettingsManager
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
            PASTEL_STATUS,
            BEHAVIOR_STATUS_COLORS,
        )

        # Keys with actual (fg, bg) colours — skip 'valid'/'csv_object' (None, None)
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
            preset_dir="mayatk/shot_manifest_colors",
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
        from uitk.managers.settings_manager import SettingsManager
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
        """Add the mapping selector to the header menu as an option-box template.

        A titled divider introduces the control, then a
        :class:`~uitk.widgets.comboBox.ComboBox` whose ``option_box`` carries two
        icon buttons to its right — *Refresh* (rescan the folder) and *Open
        folder*.  Mapping files aren't edited in-place from the UI; the workflow
        is to open the folder, manage the files externally, then refresh.
        """
        from uitk.widgets.comboBox import ComboBox

        menu = self.ui.header.menu
        menu.add("Separator", setTitle="CSV Mapping")
        cmb = menu.add(
            ComboBox,
            setObjectName="cmb_csv_mapping",
            setToolTip=(
                "Select a CSV mapping. Built-in mappings ship with the tool;\n"
                "your own live in the mappings folder — open it to add or edit\n"
                "files, then click Refresh."
            ),
        )
        self._cmb_mapping = cmb
        self._refresh_mapping_list()
        cmb.currentIndexChanged.connect(self._on_mapping_changed)
        # Pin the combo (and thus its square icon buttons) to the header menu's
        # row height so the template row lines up with the sibling buttons.
        self._wire_mapping_option_box(
            cmb, target_h=getattr(menu, "fixed_item_height", None)
        )

    def _wire_mapping_option_box(self, cmb, target_h=None) -> None:
        """Attach the option-box toolbar to *cmb*: *Refresh* then *Open folder*.

        Factored out so the construction is unit-testable on a real ComboBox.
        A no-op when *cmb* isn't a real widget — the controller's logic tests
        run against a mocked UI where the toolbar is irrelevant.

        *target_h* pins the combo to the header row height; the option-box sizes
        its square icon buttons to the combo's height, so both end up matching
        the sibling buttons.  Falls back to the combo's natural height hint.
        """
        from qtpy import QtWidgets

        if not isinstance(cmb, QtWidgets.QWidget):
            return

        cmb.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        h = (
            target_h
            if isinstance(target_h, int) and target_h > 0
            else cmb.sizeHint().height()
        )
        cmb.setFixedHeight(h)
        # Give the combo its row height *now*, before the synchronous option-box
        # wrap below: the option-box sizes its icon buttons from the wrapped
        # widget's current height, which is otherwise 0 until the still-hidden
        # header menu is first laid out.
        cmb.resize(cmb.width() or cmb.sizeHint().width(), h)

        cmb.option_box.add_action(
            callback=self._refresh_mapping_list,
            icon="refresh",
            tooltip="Rescan the mappings folder for files you've added, removed, or edited.",
        )
        cmb.option_box.add_action(
            callback=self._open_mappings_folder,
            icon="folder",
            tooltip="Open the mappings folder to add or edit files (manage them here, then Refresh).",
        )

    def _refresh_mapping_list(self, select: Optional[str] = None) -> None:
        """Rebuild the mapping combo, tagging each item by source.

        The item *text* carries a built-in/user tag for the user; the real
        mapping name is stored as item *data* so selection stays robust.
        Selection priority: *select* arg > last-used > ``default`` > ``(none)``.
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        cmb = self._cmb_mapping
        cmb.blockSignals(True)
        cmb.clear()
        cmb.addItem("(none)", None)
        if self._mapping_dir:
            names = list(Mapping.discover(self._mapping_dir))
            for name in names:
                cmb.addItem(name, name)
        else:
            ts = Mapping.templates()
            names = ts.names()
            for name in names:
                tag = "user" if ts.source(name) == "user" else "built-in"
                cmb.addItem(f"{name}  ·  {tag}", name)

        target = select
        if target is None and not self._mapping_dir:
            target = Mapping.templates().active
            retired = Mapping.retired(target) if target else None
            if retired is not None:
                target = self._migrate_retired_mapping(target, *retired)
        idx = self._combo_data_index(target) if target else -1
        if idx < 0 and "default" in names:
            idx = self._combo_data_index("default")
        cmb.setCurrentIndex(idx if idx >= 0 else 0)
        cmb.blockSignals(False)
        # Programmatic refresh: apply but don't persist — rebuilding the list
        # must not overwrite the user's last-used pointer.
        self._apply_mapping(cmb.currentData(), persist=False)

    def _migrate_retired_mapping(
        self, name: str, replacement: str, values: dict
    ) -> str:
        """Point a saved selection of retired template *name* at its
        replacement, carrying the option *values* it stood for (unless the
        replacement already has saved values).  Returns the replacement."""
        import json
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        key = f"mapping_options/{replacement}"
        if not self._settings.value(key, ""):
            self._settings.setValue(key, json.dumps(values))
        try:
            Mapping.templates().active = replacement
        except Exception:
            pass
        self.logger.info(
            "Mapping %r is retired; using %r with %s.", name, replacement, values
        )
        return replacement

    def _combo_data_index(self, name) -> int:
        """Index of the combo item whose data == *name*, or -1."""
        cmb = self._cmb_mapping
        for i in range(cmb.count()):
            if cmb.itemData(i) == name:
                return i
        return -1

    def _on_mapping_changed(self, arg=None) -> None:
        """Handle a mapping selection.

        Connected to ``currentIndexChanged`` (passes an int) — the real mapping
        name is read from the current item's data. A ``str`` *arg* is also
        accepted (legacy/tests) and used directly as the name.
        """
        if isinstance(arg, str):
            name = None if (not arg or arg == "(none)") else arg
        else:
            name = self._cmb_mapping.currentData()
        self._apply_mapping(name, persist=True)

    def _apply_mapping(self, name, persist: bool) -> None:
        """Load *name* into ``_active_mapping`` and re-parse the current CSV.

        *persist* records the choice as last-used (skipped in directory-override
        mode, whose pointer belongs to a different store).
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        self._mapping_name = name or None
        self._mapping_template = None
        if name:
            try:
                self._mapping_template = Mapping.load_mapping(
                    name, self._mapping_dir or None
                )
            except Exception as exc:
                self.logger.error("Failed to load mapping '%s': %s", name, exc)
                self._set_footer(f"Mapping error: {exc}", color=ERROR_COLOR)
                self._active_mapping = None
                self._build_option_rows()
                return
            if persist and not self._mapping_dir:
                try:
                    Mapping.templates().active = (
                        name  # remember last-used across sessions
                    )
                except Exception:
                    pass
        self._build_option_rows()
        self._reapply_mapping()

    # ---- template options -------------------------------------------------

    def _option_values(self) -> Dict[str, object]:
        """The saved option values of the current template (``{}`` if none)."""
        import json

        if not self._mapping_name:
            return {}
        raw = self._settings.value(f"mapping_options/{self._mapping_name}", "")
        try:
            values = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return {}
        return values if isinstance(values, dict) else {}

    def _reapply_mapping(self) -> None:
        """Rebuild the effective template from its options, then re-load the sheet."""
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        self._active_mapping = (
            Mapping.apply_options(self._mapping_template, self._option_values())
            if self._mapping_template is not None
            else None
        )
        path = self._csv_path or self.ui.txt_csv_path.text().strip()
        if path:
            self._load_csv(path)

    def _on_option_changed(self, key: str, value) -> None:
        """Save one option of the current template and re-apply it."""
        import json

        values = self._option_values()
        values[key] = value
        self._settings.setValue(
            f"mapping_options/{self._mapping_name}", json.dumps(values)
        )
        self._reapply_mapping()

    def _build_option_rows(self) -> None:
        """Show the current template's options under its picker in the header menu.

        One row per option (``Mapping.option_specs``), built by uitk's widget
        factory from an ``AttributeSpec`` -- the template declares its own
        settings, so a new option needs no code here.  Values persist per
        template; the template, not the widget state, owns them.
        """
        from qtpy import QtCore, QtWidgets
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping
        from uitk.bridge.spec import AttributeSpec, KindFactory
        from uitk.bridge.tooltip import Tooltip

        menu = self.ui.header.menu
        for row in self._option_rows:
            menu.remove_widget(row)
            row.deleteLater()
        self._option_rows = []
        if not isinstance(menu, QtWidgets.QWidget):
            return  # mocked UI (logic tests)
        values = self._option_values()
        for opt in Mapping.option_specs(self._mapping_template):
            spec = AttributeSpec(
                key=opt["key"],
                label=opt["label"],
                kind=opt["kind"],
                default=values.get(opt["key"], opt["default"]),
                choices=tuple(opt.get("choices", ())),
                tooltip=opt["tooltip"],
            )
            row = QtWidgets.QWidget()
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(2)
            label = QtWidgets.QLabel(f"{spec.display_label}:", row)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            widget = KindFactory.make_widget(spec, row)
            widget.setObjectName(f"opt_{spec.key}")
            widget.restore_state = False  # the template owns the value
            tip = Tooltip.format_param_tooltip(spec)
            label.setToolTip(tip)
            widget.setToolTip(tip)
            hbox.addWidget(label)
            hbox.addWidget(widget, 1)
            KindFactory.connect_changed(
                widget, lambda value, key=spec.key: self._on_option_changed(key, value)
            )
            menu.add(row)
            self._option_rows.append(row)

    def _open_mappings_folder(self) -> None:
        """Open the writable folder where user mapping files live.

        Mapping files are managed externally, not edited in-place from the UI:
        the user opens this folder, adds/edits/removes files, then clicks
        *Refresh*.  On first use (empty folder) it's seeded with a documented
        example and the format reference, so there's a model to copy and a spec
        to read.
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        ts = Mapping.templates()
        d = ts.user_dir
        d.mkdir(parents=True, exist_ok=True)
        self._seed_mappings_folder(ts)
        ptk.FileUtils.open_explorer(str(d), logger=self.logger)

    @staticmethod
    def _seed_mappings_folder(ts) -> None:
        """Seed *ts*'s empty user folder with an example mapping + format
        reference (``Mapping.seed_user_folder``: a no-op once it holds anything).
        """
        from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

        Mapping.seed_user_folder(ts)

    # ---- mode switching (single source of truth) -------------------------

    def _load_data(
        self,
        steps: List[BuilderStep],
        *,
        ranges: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None,
        csv_path: str = "",
        source: str = "",
        footer: str = "",
    ) -> None:
        """Single source of truth for mode switching.

        Every code path that changes table contents (detect, CSV load, the
        scene's shots) funnels through here so that state and the table are
        always consistent.  *source* defaults to ``"csv"`` with a
        *csv_path*, else ``"detect"``.
        """
        self._steps = steps
        self._csv_path = csv_path
        self._source = source or ("csv" if csv_path else "detect")
        self._user_ranges = dict(ranges) if ranges else {}
        self._last_results = []
        self._last_resolved = []
        self._built_this_round = False
        self._cached_gaps = None
        self._cached_gap_ends = None

        self._populate_table()
        self._update_build_button()

        if footer:
            self._set_footer(footer)

    def _on_csv_browsed(self, path: str) -> None:
        """Handle a CSV path selected via browse or BrowseOption."""
        self.ui.txt_csv_path.setText(path)
        self._load_csv(path)

    def _load_csv(self, path: str) -> bool:
        """Parse the CSV (a path or URL) and load it via :meth:`_load_data`.

        Returns ``True`` once loaded; a failure marks the field invalid with
        its reason and returns ``False``.

        When an active mapping is selected, delegates to the
        :mod:`mapping` resolver.  Otherwise falls back to
        :func:`parse_csv` with the current :attr:`_column_map`.
        """
        import os

        # Settle the path field's live validator now (sync check only) so
        # neither its debounce nor an in-flight URL probe can re-color the
        # field after the load below sets the authoritative result (e.g.
        # flip an unreadable cloud file back to "valid" 300ms later).
        self.ui.txt_csv_path.validate_now(run_deferred=False)

        # A URL is a source too (RemoteFile fetches it inside parse_csv).  It
        # can't be probed without a round trip, so only a local path is gated.
        if not ptk.RemoteFile.is_url(path) and not os.path.isfile(path):
            self._mark_csv_invalid(f"File not found: {path}")
            return False

        try:
            if self._active_mapping is not None:
                from pythontk.core_utils.engines.shots.manifest.mapping import Mapping

                steps = Mapping.resolve(path, mapping=self._active_mapping)
            else:
                steps = ManifestModel.parse_csv(path, columns=self._column_map)
        except ptk.RemoteFile.Error as exc:
            # A URL that didn't yield a CSV: no network, an HTTP error, or a
            # sign-in page where a file was expected.  RemoteFile's message
            # already names the remedy (share the sheet, check the link); the
            # disk/sync diagnosis below would be wrong for a URL, and Error
            # subclasses OSError, so this branch must come first.
            self.logger.error("Failed to fetch CSV %r: %s", path, exc)
            self._mark_csv_invalid(str(exc))
            return False
        except OSError as exc:
            # isfile() passed but the bytes can't be read.  Don't assume a
            # single cause -- _describe_read_failure enumerates the likely
            # culprits (full disk / stopped sync client / locked / disconnected),
            # always surfaces the raw error, and appends the free space if low.
            self.logger.error("Failed to read CSV %r: %s", path, exc)
            self._mark_csv_invalid(self._describe_read_failure(path, exc))
            return False
        except Exception as exc:
            self.logger.error("Failed to parse CSV: %s", exc)
            self._mark_csv_invalid(f"Error: {exc}")
            return False

        # Honor context-menu exclusions on BOTH branches.  parse_csv already
        # applies them on the no-mapping branch (idempotent here); resolve()
        # builds a fresh ColumnMap from the mapping JSON and never sees
        # self._column_map, so without this a reload while a mapping is active
        # resurrects every context-menu-excluded step.
        steps = self._drop_excluded(steps)

        self.ui.txt_csv_path.reset_action_color()
        self._recent_csv_option.record(path)
        n_obj = sum(len(s.objects) for s in steps)

        # Seed _user_ranges with existing store positions so the table
        # immediately shows correct Start/End for built steps.
        store_ranges = {}
        try:
            store = self._store_cls().active()
            store_ranges = {
                sid: (shot.start, shot.end)
                for sid, shot in self._pairing(steps, store).shots.items()
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
        self._fill_missing_assets()
        return True

    @staticmethod
    def _describe_read_failure(path: str, exc: OSError) -> str:
        """Explain an unreadable CSV by its likely causes, never one asserted
        (``ManifestModel.describe_read_failure``)."""
        return ptk.ManifestModel.describe_read_failure(path, exc)

    # ---- helpers ---------------------------------------------------------

    def _ensure_steps(self) -> bool:
        """Ensure steps are available, auto-detecting from scene if needed.

        Priority order:
        1. If steps are already loaded, return True immediately.
        2. Load the source: the CSV in the field, else the scene's shots.
        3. Otherwise, run scene detection.

        Returns True if steps are now available.
        """
        if self._steps:
            return True

        self._populate_from_source()
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
        """Enable Build once Assess has run and a build would change something.

        Build always starts disabled. It is warranted while a step is unbuilt,
        an object is one a build fixes -- not in its shot, its behavior keys
        missing or made under an older effect recipe -- a shot holds keys of
        behaviors the doc dropped (``StepStatus.needs_build``), or a behavior
        was ticked on or off since that Assess.
        """
        btn = getattr(self.ui, "b003", None)
        if btn is None:
            return
        if self._last_results:
            needs_build = getattr(self, "_behaviors_edited", False) or any(
                r.needs_build for r in self._last_results
            )
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
            import maya.cmds  # noqa: F401 — availability check
        except ImportError:
            self._set_footer("Maya is required to build shots.", color=ERROR_COLOR)
            return

        from mayatk.anim_utils.shots._shots import ShotStore

        try:
            store = ShotStore.active()
            builder = self._manifest(store)

            # When selected-keys mode is active, verify keys exist
            # before proceeding — even if user ranges are complete.
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

            # Resolve ranges — short-circuit when all ranges are
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
                range_map = {
                    sid: (shot.start, shot.end)
                    for sid, shot in self._pairing(store=store).shots.items()
                }
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
                            # New step at the very start of the CSV —
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
                placement = self._placement_on_regions()
                if placement is None:
                    self._set_footer("Build cancelled -- set the steps' ranges first.")
                    return
                if not placement:
                    resolved = self._resolve_ranges(regions=False)
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

            self._building = True
            try:
                with store.scene_edit("manifest_build"), store.batch_update():
                    actions, beh, assessment = builder.sync(
                        build_steps,
                        ranges=range_map,
                        # A build never removes a shot: one no step pairs with
                        # is listed ("not in doc") for the user to remove.
                        remove_missing=False,
                        zero_duration_fallback=incremental,
                        fit_mode=self._fit_mode,
                        initial_shot_length=self._initial_shot_length,
                        skip_scene_discovery=use_sel,
                    )
                    # Record the source CSV for provenance on reopen -- only
                    # the one these steps came from: a path still in the field
                    # after a failed load was never used.
                    csv_path = self._csv_path
                    if csv_path and store.source_csv != csv_path:
                        store.source_csv = csv_path
                        store.mark_dirty()
            finally:
                self._building = False

            # Store the store for later handoff to Shot Sequencer UI
            self._store = store
            self._built_this_round = True

            n_created = sum(1 for a in actions.values() if a == "created")
            n_patched = sum(1 for a in actions.values() if a == "patched")
            n_skipped = sum(1 for a in actions.values() if a == "skipped")
            n_refused = sum(1 for a in actions.values() if a == "refused")
            n_beh_applied = len(beh.get("applied", []))
            n_beh_skipped = len(beh.get("skipped", []))
            n_beh_failed = len(beh.get("failed", []))
            parts = []
            if n_created:
                parts.append(f"{n_created} created")
            if n_patched:
                parts.append(f"{n_patched} patched")
            if n_skipped:
                parts.append(f"{n_skipped} unchanged")
            if n_refused:
                parts.append(f"{n_refused} not built (name taken, see log)")
            if n_beh_applied:
                parts.append(f"{n_beh_applied} behaviors applied")
            if n_beh_skipped:
                parts.append(f"{n_beh_skipped} behaviors kept (animator keys)")
            if n_beh_failed:
                parts.append(f"{n_beh_failed} behaviors failed (see log)")
            self._set_footer(
                f"Build complete: {', '.join(parts)}.",
                color=ERROR_COLOR if n_beh_failed or n_refused else "",
            )

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
            import maya.cmds as cmds  # noqa: F401 — availability check
        except ImportError:
            self._set_footer("Maya is required to assess shots.", color=ERROR_COLOR)
            return

        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore.active()
        builder = self._manifest(store)
        use_sel = self._use_selected_keys

        # In selected-keys mode, verify keys exist before proceeding —
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
        built_map = self._pairing(store=store).shots
        status_changed = False
        for r in results:
            shot = built_map.get(r.step_id)
            if shot is None:
                continue
            obj_status = {o.name: o.status for o in r.objects}
            for extra in r.additional_objects:
                obj_status.setdefault(extra, "additional")
            if shot.metadata.get("object_status") != obj_status:
                shot.metadata["object_status"] = obj_status
                status_changed = True
        if status_changed:
            store.mark_dirty()

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
        stale_names = {
            o.name for r in results for o in r.objects if o.status == "stale_behavior"
        }
        n_dropped = sum(len(r.dropped_behaviors) for r in results)
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
        if stale_names:
            parts.append(f"{len(stale_names)} to re-key (older recipe)")
        if n_dropped:
            parts.append(f"{n_dropped} dropped behavior(s) to remove")
        if n_additional:
            parts.append(f"{n_additional} scene objects")
        if n_shrinkable:
            parts.append(f"{n_shrinkable} shrinkable")
        self._set_footer(f"Assessment: {', '.join(parts)}")
        self._update_build_button()


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
