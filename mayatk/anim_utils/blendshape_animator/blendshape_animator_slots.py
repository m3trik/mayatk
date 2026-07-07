# !/usr/bin/python
# coding=utf-8
"""Switchboard slots controller for blendshape_animator.ui."""
from typing import Dict, List, Optional

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from qtpy import QtCore, QtWidgets
from uitk.widgets.mixins.tooltip_mixin import fmt

from mayatk.anim_utils.blendshape_animator._blendshape_animator import (
    BlendshapeAnimator,
)
from mayatk.anim_utils.blendshape_animator.applicator import ApplyStatus
from mayatk.anim_utils.blendshape_animator.target import Target, Targets
from mayatk.anim_utils.blendshape_animator.weights import Weights
from mayatk.anim_utils.blendshape_animator.helpers import list_history
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.node_utils.attributes._attributes import Attributes


# Tree column indices — kept as constants so refresh logic + formatters
# stay in lockstep with the .ui column declarations.
COL_NAME = 0
COL_WEIGHT = 1
COL_FRAME = 2
COL_TOPOLOGY = 3
COL_STATUS = 4

# Edit-mode combo entries (cmb000)
MODE_WEIGHT = "Weight-based"
MODE_FRAME = "Frame-based"


class _NumericSortItem(QtWidgets.QTreeWidgetItem):
    """QTreeWidgetItem with numeric-aware sorting on Weight + Frame columns.

    Default ``QTreeWidgetItem`` sorts column text lexically, so frame "10"
    would sort before "5". This subclass reads the cell's UserRole+1 numeric
    value when present (set in ``_refresh_tree``) and falls back to text.
    """

    _NUM_ROLE = QtCore.Qt.UserRole + 1

    def __lt__(self, other: "QtWidgets.QTreeWidgetItem") -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a = self.data(col, self._NUM_ROLE)
        b = other.data(col, self._NUM_ROLE)
        if a is not None and b is not None:
            return a < b
        return self.text(col) < other.text(col)


class BlendshapeAnimatorSlots(BlendshapeAnimator):
    """Controller wiring blendshape_animator.ui to the BlendshapeAnimator domain class.

    Inherits BlendshapeAnimator so domain methods (``create``, ``edit_weight_based``,
    ``apply_all_edits``, etc.) are available as ``self.<method>``. UI events
    translate into direct method calls plus a tree refresh.

    ``LoggingMixin`` is provided transitively via ``BlendshapeAnimator`` — no
    need to redeclare it as a base.
    """

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def __init__(self, switchboard):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.blendshape_animator

        # Per-row last-apply status, keyed by mesh name. Survives refreshes
        # so a tween that errored on apply stays red until the next apply
        # attempt or scene-state change.
        self._row_status: Dict[str, ApplyStatus] = {}

        # Filter toggle from the tree's header action bar
        self._show_only_mismatches = False

        # Selection-sync state
        self._sel_token: Optional[int] = None
        self._syncing_selection = False

        self._wire_tree()
        self._wire_dynamic_tooltips()

        self.ui.on_first_show.connect(self._on_first_show)

    def _on_first_show(self) -> None:
        """Auto-load existing setup from selection on first display, if any."""
        bound = self._try_bind_from_selection()
        self._refresh_tree()
        self._update_setup_active()
        self._install_selection_sync()
        if bound and self._has_setup():
            self._set_status(f"Loaded existing setup on {self.base_mesh}")
        else:
            self._set_status(
                "Ready. Select 2 meshes (source, target) and click Create Setup."
            )

    # =========================================================================
    # Setup-state gating + Maya selection sync
    # =========================================================================

    _GATED_GROUPS = ("edit_group", "diagnostics_group", "export_group")

    def _has_setup(self) -> bool:
        """True when a base mesh + blendShape are bound and exist in scene."""
        return bool(
            self.base_mesh
            and self.blendshape
            and cmds.objExists(self.base_mesh)
            and cmds.objExists(self.blendshape)
        )

    def _update_setup_active(self) -> None:
        """Enable/disable edit/diagnostics/export groups based on setup state."""
        active = self._has_setup()
        for name in self._GATED_GROUPS:
            grp = getattr(self.ui, name, None)
            if grp is not None:
                grp.setEnabled(active)

    def _install_selection_sync(self) -> None:
        """Subscribe to Maya SelectionChanged so the UI tracks the user's pick.

        Persistent (not ephemeral) — the callback re-resolves all node names
        from selection on every fire, so it stays valid across scene changes.
        """
        if self._sel_token is not None:
            return
        try:
            mgr = ScriptJobManager.instance()
            self._sel_token = mgr.subscribe(
                "SelectionChanged",
                self._on_maya_selection_changed,
                owner=self,
            )
            mgr.connect_cleanup(self.ui, owner=self)
        except RuntimeError:
            pass

    def _try_bind_from_selection(self) -> bool:
        """Bind to the first selected base mesh that has a blendShape.

        Returns ``True`` only when state actually changed (a different mesh
        was adopted). Returns ``False`` when nothing matched OR when the
        already-bound mesh is still the right one.
        """
        sel = cmds.ls(selection=True, long=False) or []
        if not sel:
            return False
        for node in sel:
            if not cmds.objExists(node):
                continue
            # Skip tween meshes — they are bs targets, not base meshes
            if Attributes.has_attr(node, "isInbetweenTarget"):
                continue
            try:
                history = list_history(node, type_filter="blendShape")
            except RuntimeError:
                continue
            if not history:
                continue
            if self.base_mesh == node and self._has_setup():
                # No-op rebind: same mesh, state still valid.
                return False
            return self._adopt_state(BlendshapeAnimator.from_existing(node))
        return False

    def _on_maya_selection_changed(self) -> None:
        """SelectionChanged callback — rebind UI to the selected mesh's bs setup."""
        # Tree-driven selection uses cmds.select(...) too, so guard against
        # bouncing the rebind back when the user just clicked a tree row.
        if self._syncing_selection:
            return
        if self._try_bind_from_selection():
            self._set_status(f"Loaded existing setup on {self.base_mesh}")
            self._refresh_tree()
            self._update_setup_active()

    # =========================================================================
    # Header
    # =========================================================================

    def header_init(self, widget) -> None:
        """Configure header buttons + about menu."""
        widget.config_buttons("menu", "minimize", "hide")

        widget.set_help_text(
            fmt(
                title="Blendshape Animator",
                body="Build a morph between two meshes, add in-between "
                "(tween) shapes for custom curve control, edit them, and "
                "apply the edits back to the rig.",
                sections=[
                    ("Setup", [
                        "Select two meshes — <b>source</b> first, <b>target</b> "
                        "second.",
                        "Click <b>Create Setup</b>. The option box (▸) offers "
                        "<i>Load From Existing</i> for resuming an in-progress "
                        "setup.",
                    ]),
                    ("Edit", [
                        "Add tweens by <b>weight</b> (count or comma-separated "
                        "values) or by <b>frame</b>.",
                        "Click rows in the tween list to select; right-click "
                        "for per-tween actions (re-apply, delete, etc.) — "
                        "supports multi-select.",
                    ]),
                    ("Apply &amp; export", [
                        "<b>Apply All Edits</b> — bulk-apply every queued edit "
                        "back to the blendshape rig.",
                        "<b>Diagnostics</b> — flag topology mismatches and "
                        "recover lost keys.",
                        "<b>Export</b> — finalize the scene for baking / FBX.",
                    ]),
                ],
            )
        )

    # =========================================================================
    # Setup section (b000)
    # =========================================================================

    def b000_init(self, widget) -> None:
        """Create Setup button — option_box exposes alternative entrypoints."""
        widget.option_box.menu.setTitle("Create / Load")
        widget.option_box.menu.add(
            "QPushButton",
            setText="Load From Existing",
            setObjectName="btn_from_existing",
            setToolTip=fmt(
                title="Load From Existing",
                body=(
                    "Bind to the blendShape on the selected base mesh "
                    "instead of creating a new one."
                ),
            ),
        )
        widget.option_box.menu.btn_from_existing.clicked.connect(
            self._action_from_existing
        )

        widget.option_box.menu.add(
            "QPushButton",
            setText="Recover Setup",
            setObjectName="btn_recover_setup",
            setToolTip=fmt(
                title="Recover Setup",
                body=(
                    "Rebuild a corrupted blendShape, preserving keyframes "
                    "and re-applying tagged tween meshes."
                ),
            ),
        )
        widget.option_box.menu.btn_recover_setup.clicked.connect(
            self._action_recover_setup
        )

    def b000(self, widget) -> None:
        """Create Setup."""
        ok = self.create(
            start_frame=self.ui.s000.value(),
            end_frame=self.ui.s001.value(),
            name=self.ui.le000.text() or "morph",
            test_setup=False,
        )
        if ok:
            self._set_status(f"Setup created: {self.base_mesh} -> {self.target_mesh}")
        else:
            self._set_status("Create Setup failed — see Script Editor.")
        self._refresh_tree()
        self._update_setup_active()

    # ``BlendshapeAnimator.from_existing`` and ``recover_setup`` are
    # classmethods that return a NEW instance — we transplant their state
    # onto ``self`` so the controller's UI bindings stay intact.
    _STATE_FIELDS = (
        "base_mesh",
        "target_mesh",
        "blendshape",
        "keyframes",
        "tween_creator",
        "tween_applicator",
    )

    def _adopt_state(self, loaded: Optional[BlendshapeAnimator]) -> bool:
        """Copy domain state from ``loaded`` onto ``self``. Returns success."""
        if loaded is None:
            return False
        for attr in self._STATE_FIELDS:
            setattr(self, attr, getattr(loaded, attr))
        return True

    def _action_from_existing(self) -> None:
        ok = self._adopt_state(BlendshapeAnimator.from_existing())
        self._set_status(
            f"Loaded existing setup on {self.base_mesh}" if ok
            else "Load From Existing failed — see Script Editor."
        )
        self._refresh_tree()
        self._update_setup_active()

    def _action_recover_setup(self) -> None:
        ok = self._adopt_state(BlendshapeAnimator.recover_setup())
        self._set_status(
            f"Recovered setup on {self.base_mesh}" if ok
            else "Recover Setup failed — see Script Editor."
        )
        self._refresh_tree()
        self._update_setup_active()

    # =========================================================================
    # Edit section (cmb000, le001, b001)
    # =========================================================================

    def cmb000_init(self, widget) -> None:
        """Populate the edit-mode combo."""
        widget.clear()
        widget.addItems([MODE_WEIGHT, MODE_FRAME])
        widget.currentIndexChanged.connect(self._on_mode_changed)
        self._on_mode_changed(0)

    def _on_mode_changed(self, _index: int) -> None:
        """Show only the inputs relevant to the selected mode."""
        mode = self.ui.cmb000.currentText()
        weight_mode = mode == MODE_WEIGHT
        # Weight-mode inputs
        self.ui.le001.setVisible(weight_mode)
        # Frame-mode inputs
        self.ui.s003.setVisible(not weight_mode)

    def le001_init(self, widget) -> None:
        """CSV weights field — option_box menu offers preset lists."""
        widget.option_box.menu.setTitle("Weight Presets")
        widget.option_box.clear_option = True

        for label, csv in (
            ("Quarters (0.25, 0.5, 0.75)", "0.25, 0.5, 0.75"),
            ("Thirds (0.33, 0.67)", "0.33, 0.67"),
            ("Quintiles (0.2 .. 0.8)", "0.2, 0.4, 0.6, 0.8"),
            ("Easing-in (0.1, 0.3, 0.7, 0.9)", "0.1, 0.3, 0.7, 0.9"),
        ):
            btn = widget.option_box.menu.add(
                "QPushButton",
                setText=label,
                setToolTip=f"Set Weights field to: {csv}",
            )
            btn.clicked.connect(lambda _checked=False, c=csv: widget.setText(c))

    def b001_init(self, widget) -> None:
        """Add Tweens — option_box exposes count + group / prefix overrides."""
        widget.option_box.menu.setTitle("Add Tweens")
        widget.option_box.menu.add(
            "QSpinBox",
            setObjectName="count",
            setMinimum=1,
            setMaximum=20,
            setValue=3,
            setPrefix="Count:  ",
            setToolTip=(
                "Number of evenly-spaced weight-based tweens to create when no\n"
                "explicit weights are provided. Ignored in Frame-based mode."
            ),
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setText="_morphInbetweens_GRP",
            setObjectName="group_name",
            setToolTip="Group to parent newly-created tween meshes under.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setText="morph_ib",
            setObjectName="name_prefix",
            setToolTip="Name prefix for newly-created tween meshes.",
        )

    def b001(self, widget) -> None:
        """Add Tweens — dispatches by mode."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return

        mode = self.ui.cmb000.currentText()
        if mode == MODE_WEIGHT:
            csv = (self.ui.le001.text() or "").strip()
            if csv:
                try:
                    weights = [float(p.strip()) for p in csv.split(",") if p.strip()]
                except ValueError:
                    self._set_status(
                        "Invalid CSV in Weights — expected comma-separated floats."
                    )
                    return
            else:
                count = 3
                try:
                    count = widget.option_box.menu.count.value()
                except (AttributeError, RuntimeError):
                    pass
                weights = Weights.generate_weights(count)

            kwargs = {}
            try:
                kwargs["group_name"] = (
                    widget.option_box.menu.group_name.text() or "_morphInbetweens_GRP"
                )
                kwargs["name_prefix"] = (
                    widget.option_box.menu.name_prefix.text() or "morph_ib"
                )
            except (AttributeError, RuntimeError):
                pass

            # Pre-rounding handled by the domain method.
            tweens = self.tween_creator.create_weight_based_tweens(weights, **kwargs)
            self._set_status(f"Added {len(tweens)} weight-based tween(s).")
        else:
            frame = self.ui.s003.value()
            tween = self.tween_creator.create_frame_based_tween(frame)
            self._set_status(
                f"Added frame-based tween at frame {frame}" if tween
                else "Frame-based tween creation failed — see Script Editor."
            )

        self._refresh_tree()

    # =========================================================================
    # Diagnostics section (b003, b004, b005)
    # =========================================================================

    def b003(self, widget) -> None:
        """Diagnose Topology."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return
        ok = self.diagnose_topology_issues()
        self._set_status("Topology OK" if ok else "Topology mismatches detected.")
        self._refresh_tree()

    def b004_init(self, widget) -> None:
        """Cleanup Topology Mismatches — option_box for the two flags."""
        widget.option_box.menu.setTitle("Cleanup Topology Mismatches")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Apply valid first",
            setChecked=True,
            setObjectName="apply_valid_only",
            setToolTip="Re-apply tweens that DO match base topology before deleting bad ones.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Delete mismatched",
            setChecked=True,
            setObjectName="delete_mismatched",
            setToolTip="Delete tween meshes whose vertex count no longer matches the base.",
        )

    def b004(self, widget) -> None:
        """Clean up blendshape targets whose topology doesn't match the base mesh."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return
        self.cleanup_topology_mismatches(
            delete_mismatched=widget.option_box.menu.delete_mismatched.isChecked(),
            apply_valid_only=widget.option_box.menu.apply_valid_only.isChecked(),
        )
        self._set_status("Cleanup complete.")
        self._refresh_tree()

    def b005(self, widget) -> None:
        """Recover Animation."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return
        ok = self.recover_animation()
        self._set_status("Animation recovered." if ok else "Animation recovery failed.")

    # =========================================================================
    # Export section (b006, b007, b008)
    # =========================================================================

    def b006_init(self, widget) -> None:
        """Apply All Edits — option_box for skip_duplicates, validate_topology."""
        widget.option_box.menu.setTitle("Apply All Edits")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Skip duplicates",
            setChecked=True,
            setObjectName="skip_duplicates",
            setToolTip="Treat 'Weights must be unique' as a skip, not an error.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Validate topology first",
            setChecked=False,
            setObjectName="validate_topology",
            setToolTip=(
                "Filter out tweens whose vertex count no longer matches the base.\n"
                "Off by default to avoid silently masking problems."
            ),
        )

    def b006(self, widget) -> None:
        """Apply All Edits — bulk apply with optional flags from the option_box."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return
        try:
            skip = widget.option_box.menu.skip_duplicates.isChecked()
            validate = widget.option_box.menu.validate_topology.isChecked()
        except (AttributeError, RuntimeError):
            skip, validate = True, False
        results = self.tween_applicator.apply_tweens(
            None, skip_duplicates=skip, validate_topology=validate
        )
        for tween, status in results:
            self._row_status[tween.mesh] = status
        applied = sum(1 for _, s in results if s is ApplyStatus.APPLIED)
        skipped = sum(1 for _, s in results if s is ApplyStatus.SKIPPED_DUPLICATE)
        errors = sum(1 for _, s in results if s is ApplyStatus.ERROR)
        if results:
            self._set_status(
                f"Applied {applied}/{len(results)} — skipped {skipped}, errors {errors}"
            )
        else:
            self._set_status("No tweens to apply.")
        self._refresh_tree()

    def b007(self, widget) -> None:
        """Remove Target Mesh."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return
        ok = self.remove_target_for_export()
        self._set_status(
            "Target mesh removed." if ok else "Removal failed — see Script Editor."
        )

    def b008_init(self, widget) -> None:
        """Finalize for Export — option_box for the four boolean flags."""
        widget.option_box.menu.setTitle("Finalize for Export")
        for name, label, default, tip in (
            ("cleanup_scene", "Cleanup scene", True, "Hide target mesh and tween meshes."),
            (
                "delete_construction_history",
                "Delete construction history",
                True,
                "Delete construction history except the blendShape itself.",
            ),
            ("hide_target_mesh", "Hide target mesh", True, "Hide the target instead of leaving it visible."),
            (
                "delete_inbetween_meshes",
                "Delete in-between meshes",
                True,
                "Delete the tween meshes after applying their edits.",
            ),
        ):
            widget.option_box.menu.add(
                "QCheckBox",
                setText=label,
                setChecked=default,
                setObjectName=name,
                setToolTip=tip,
            )

    def b008(self, widget) -> None:
        """Finalize the blendshape setup for export (scene cleanup, bake history, hide source)."""
        if not self._validate_setup():
            self._set_status("Setup not complete — Create or Load first.")
            return
        ok = self.finalize_for_export(
            cleanup_scene=widget.option_box.menu.cleanup_scene.isChecked(),
            delete_construction_history=widget.option_box.menu.delete_construction_history.isChecked(),
            hide_target_mesh=widget.option_box.menu.hide_target_mesh.isChecked(),
            delete_inbetween_meshes=widget.option_box.menu.delete_inbetween_meshes.isChecked(),
        )
        self._set_status("Finalized for export." if ok else "Finalize failed.")
        self._refresh_tree()

    # =========================================================================
    # Tree wiring (tree000)
    # =========================================================================

    def _wire_tree(self) -> None:
        """One-time tree configuration: column formatters, header actions, signals."""
        tree = self.ui.tree000

        # Per-column color formatters: read the cell's value and apply
        # semantic action color from TreeFormatMixin.ACTION_COLOR_MAP.
        topology_map = {
            "match": ("#3C8D3C", None),    # green text
            "mismatch": ("#B97A7A", None), # red text
            "unknown": ("#AAAAAA", None),  # gray
        }
        status_map = {
            "applied": ("#3C8D3C", None),
            "pending": ("#B49B5C", None),
            "skipped": ("#6D9BAA", None),
            "error": ("#B97A7A", None),
        }
        tree.set_column_formatter(
            COL_TOPOLOGY, tree.make_color_map_formatter(topology_map)
        )
        tree.set_column_formatter(
            COL_STATUS, tree.make_color_map_formatter(status_map)
        )

        # Selection sync: clicking a row selects the tween mesh in Maya.
        tree.itemSelectionChanged.connect(self._on_tree_selection_changed)

        # Right-click context menu
        tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_tree_context_menu)

        # Header overlay action bar
        try:
            tree.header_actions.add(
                "refresh",
                "refresh",
                tooltip="Re-scan tween meshes",
                callback=self._refresh_tree,
            )
            tree.header_actions.add(
                "filter",
                "filter",
                tooltip="Show only topology mismatches",
                callback=self._toggle_filter_mismatches,
                toggle=True,
            )
        except Exception:
            self.logger.debug("Header action bar unavailable on this tree widget")

        # Subtle tints to differentiate the numeric columns
        try:
            from qtpy.QtGui import QColor
            tree.set_column_tint(COL_WEIGHT, QColor(255, 255, 255, 8))
            tree.set_column_tint(COL_FRAME, QColor(255, 255, 255, 8))
        except Exception:
            pass

        tree.selection_style = "tint"
        # Default-sort by Weight ascending so newly-added tweens appear in the
        # order users expect (low to high). Header click can override.
        tree.sortByColumn(COL_WEIGHT, QtCore.Qt.AscendingOrder)

    def _wire_dynamic_tooltips(self) -> None:
        """Bind dynamic tooltips that reflect current state on hover."""
        try:
            self.ui.tree000.tooltip.bind(self._tree_tooltip_provider)
            self.ui.footer.tooltip.bind(
                lambda: f"Setup: {self.blendshape or '(none)'} on {self.base_mesh or '(none)'}"
            )
        except (AttributeError, RuntimeError):
            pass

    def _tree_tooltip_provider(self) -> str:
        if not self.blendshape:
            return "No setup loaded. Click Create Setup or load via the option box."
        tweens = Targets.find_all_targets()
        n = len(tweens)
        n_mismatch = sum(
            1 for t in tweens
            if self.base_mesh
            and cmds.objExists(t.mesh)
            and cmds.polyEvaluate(t.mesh, vertex=True)
            != cmds.polyEvaluate(self.base_mesh, vertex=True)
        )
        return f"{n} tween(s), {n_mismatch} topology mismatch(es)"

    def _refresh_tree(self) -> None:
        """Rebuild the tree from the current scene state."""
        tree = self.ui.tree000
        tree.blockSignals(True)
        tree.setSortingEnabled(False)
        tree.clear()

        if not self.base_mesh or not cmds.objExists(self.base_mesh):
            tree.setSortingEnabled(True)
            tree.blockSignals(False)
            return

        base_vert_count = cmds.polyEvaluate(self.base_mesh, vertex=True)
        tweens = Targets.find_all_targets()

        for tween in tweens:
            try:
                vert_count = cmds.polyEvaluate(tween.mesh, vertex=True)
            except RuntimeError:
                vert_count = None

            if vert_count is None:
                topology = "unknown"
            elif vert_count == base_vert_count:
                topology = "match"
            else:
                topology = "mismatch"

            if self._show_only_mismatches and topology == "match":
                continue

            # Tweens with no recorded apply result are pending — defaulting
            # to APPLIED would show green for edits never applied back.
            status_enum = self._row_status.get(tween.mesh)
            status = (
                "pending"
                if status_enum is None
                else {
                    ApplyStatus.APPLIED: "applied",
                    ApplyStatus.SKIPPED_DUPLICATE: "skipped",
                    ApplyStatus.ERROR: "error",
                }[status_enum]
            )

            frame = tween.target_frame
            item = _NumericSortItem(
                [
                    tween.mesh,
                    f"{tween.weight:.3f}",
                    "" if frame is None else str(frame),
                    "Match" if topology == "match" else (
                        f"Mismatch ({vert_count} vs {base_vert_count})"
                        if vert_count is not None else "Unknown"
                    ),
                    status.capitalize(),
                ]
            )
            # Stash the lowercase keys in UserRole so the column formatters can
            # read them without parsing display text.
            item.setData(COL_TOPOLOGY, QtCore.Qt.UserRole, topology)
            item.setData(COL_STATUS, QtCore.Qt.UserRole, status)
            # Also stash the Target itself for handler convenience
            item.setData(COL_NAME, QtCore.Qt.UserRole, tween)
            # Numeric sort keys for Weight + Frame columns (UserRole+1)
            item.setData(COL_WEIGHT, _NumericSortItem._NUM_ROLE, tween.weight)
            if frame is not None:
                item.setData(COL_FRAME, _NumericSortItem._NUM_ROLE, frame)
            # Right-align numeric cells (display only)
            item.setTextAlignment(COL_WEIGHT, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            item.setTextAlignment(COL_FRAME, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            tree.addTopLevelItem(item)

        tree.apply_formatting()
        tree.setSortingEnabled(True)
        tree.blockSignals(False)

    def _on_tree_selection_changed(self) -> None:
        """Sync tree selection -> Maya selection."""
        meshes = [t.mesh for t in self._selected_tweens() if cmds.objExists(t.mesh)]
        if not meshes:
            return
        # Guard against rebinding the UI to a tween mesh that obviously
        # doesn't carry its own blendShape — we just selected its tween.
        self._syncing_selection = True
        try:
            cmds.select(meshes, replace=True)
        finally:
            self._syncing_selection = False

    def _selected_tweens(self) -> List[Target]:
        items = self.ui.tree000.selectedItems()
        out: List[Target] = []
        for item in items:
            t = item.data(COL_NAME, QtCore.Qt.UserRole)
            if isinstance(t, Target):
                out.append(t)
        return out

    def _show_tree_context_menu(self, pos) -> None:
        tree = self.ui.tree000
        clicked = tree.itemAt(pos)
        if clicked is None:
            return

        # Right-click on an unselected row promotes it to the selection so
        # the context-menu actions operate on what the user clicked. Right-
        # click on a selected row leaves multi-selection intact.
        if not clicked.isSelected():
            tree.setCurrentItem(clicked)

        tweens = self._selected_tweens()
        if not tweens:
            tween_one = clicked.data(COL_NAME, QtCore.Qt.UserRole)
            if isinstance(tween_one, Target):
                tweens = [tween_one]
        if not tweens:
            return

        multi = len(tweens) > 1
        # Jump-to-frame is single-row only — a multi-row jump is ambiguous.
        single = tweens[0]

        menu = QtWidgets.QMenu(tree)
        act_select = menu.addAction(
            f"Select {len(tweens)} in Maya" if multi else "Select in Maya"
        )
        act_jump = menu.addAction("Jump to Frame")
        act_jump.setEnabled(not multi and single.target_frame is not None)
        menu.addSeparator()
        act_reapply = menu.addAction(
            f"Re-apply {len(tweens)} Tweens" if multi else "Re-apply This Tween"
        )
        menu.addSeparator()
        act_delete = menu.addAction(
            f"Delete {len(tweens)} Tween Meshes" if multi else "Delete Tween Mesh"
        )

        chosen = menu.exec_(tree.viewport().mapToGlobal(pos))
        if chosen is act_select:
            meshes = [t.mesh for t in tweens if cmds.objExists(t.mesh)]
            if meshes:
                cmds.select(meshes, replace=True)
                self._set_status(f"Selected {len(meshes)} mesh(es)")
        elif chosen is act_jump:
            cmds.currentTime(single.target_frame)
            self._set_status(f"Jumped to frame {single.target_frame}")
        elif chosen is act_reapply:
            with CoreUtils.undo_chunk("Re-apply Tweens"):
                results = self.tween_applicator.apply_tweens(list(tweens))
            for t, s in results:
                self._row_status[t.mesh] = s
            applied = sum(1 for _, s in results if s is ApplyStatus.APPLIED)
            self._set_status(
                f"Re-applied {applied}/{len(results)} tween(s)"
            )
            self._refresh_tree()
        elif chosen is act_delete:
            deleted = 0
            with CoreUtils.undo_chunk("Delete Tween Meshes"):
                for tween in tweens:
                    try:
                        cmds.delete(tween.mesh)
                        self._row_status.pop(tween.mesh, None)
                        deleted += 1
                    except RuntimeError as e:
                        self.logger.error(f"Could not delete {tween.mesh}: {e}")
            self._set_status(
                f"Deleted {deleted}/{len(tweens)} tween mesh(es)"
            )
            self._refresh_tree()

    def _toggle_filter_mismatches(self, checked: bool) -> None:
        self._show_only_mismatches = bool(checked)
        self._refresh_tree()

    # =========================================================================
    # Footer / status
    # =========================================================================

    def _set_status(self, text: str) -> None:
        try:
            self.ui.footer.set_status(text)
        except (AttributeError, RuntimeError):
            self.logger.info(text)
