# !/usr/bin/python
# coding=utf-8
"""UI slots for the Channels UI.

``ChannelsSlots`` — a single-table Switchboard interface for
inspecting, editing, locking, and managing Maya node attributes.
Delegates all non-UI logic to :class:`Channels`.
"""
import maya.cmds as cmds
import maya.mel as mel

from qtpy import QtCore, QtWidgets
from uitk.widgets.footer import FooterStatusController
from uitk.widgets.widgetComboBox import WidgetComboBox
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.node_utils.attributes.channels._channels import (
    Channels,
)

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt


class ChannelsSlots:
    """Switchboard slots for the Channels UI.

    Layout
    ------
    - **Header menu**: Global actions (Create Attribute).
    - **ComboBox**: Filter displayed attributes (Custom, Keyable, All).
    - **Table**: One row per attribute on the primary selection.
      Columns: Name | Lock | Connect | Value | Type.
      Lock and Connect are narrow icon-only columns (clickable toggles, color-coded).
    - **Context menu**: Per-row operations (Lock/Unlock, Delete, Reset to Default).
    """

    # Column indices — Name | Lock | Connect | Value | Type
    COL_NAME = 0
    COL_LOCK = 1
    COL_CONN = 2
    COL_VALUE = 3
    COL_TYPE = 4

    _ROW_SELECTION_COLUMNS = {
        "name": 0,
        "value": 3,
        "type": 4,
    }

    # Single source of truth for all icon/state colours.
    # Desaturated Maya channel-box colour scheme:
    # - Pink for any plain incoming connection.
    # - Lighter desaturated red for an animCurve driver (no key on current frame).
    # - Deeper desaturated red for a key set on the current frame.
    ACTION_COLOR_MAP = {
        "off": "#555555",  # dim grey — inactive / default
        "active": "#6898b8",  # desat blue — toolbar toggle active state
        "locked": "#8a9bb0",  # bluish grey — lock icon
        "connected": "#c89c9c",  # desat pink — generic connection
        "keyframe": "#c86464",  # desat red — keyed (no key at current time)
        "keyframe_active": "#a83838",  # deeper desat red — key set at current time
        "expression": "#b478c8",  # desat purple — expression-driven
        "driven_key": "#6898b8",  # desat light-blue — set-driven key
        "constraint": "#5878b8",  # desat blue — constraint
        "muted": "#888850",  # olive — muted channel
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.channels
        self.controller = Channels()
        self._refresh_pending = False
        self._compact_view = False
        self._base_row_height = None  # snapshotted on first _apply_row_height
        self._footer_warning = ""
        self._current_target = None
        self._filter_invert = False  # replaces the deleted chk000 checkbox
        self._footer_controller = self._create_footer_controller()
        self._footer_compact_btn = None
        self._footer_lineedit = None
        self._footer_edit_page = -1

        # Coalescing state for MNodeMessage callbacks.  Bursts of
        # attribute-changed events (value drags, batch setAttr, playback)
        # would otherwise schedule one ``evalDeferred`` per event — the
        # idle queue saturates and Maya becomes unstable.  Instead we
        # accumulate touched attribute names into sets and flush them
        # with a single deferred pass.
        self._pending_value_attrs: set = set()
        self._pending_lock_attrs: set = set()
        self._attr_flush_pending = False
        self._destroyed = False

        # MMB scrub-edit drag state — set by ``_setup_scrub_edit``.
        self._scrub_state = None

        # Re-entry guards for ``_refresh_table``.  Filter-combobox
        # spamming (or any code path that pumps the event loop during
        # a rebuild) can otherwise start a second ``_refresh_table``
        # while the first is still tearing down / rebuilding cell
        # widgets — a known crash vector with QTableWidget + persistent
        # cell widgets (our enum comboboxes).
        self._refreshing = False
        self._refresh_queued = False

        # Force-rewire the table signals we own.  ``tbl000_init`` gates
        # its wiring on ``widget.is_initialized``, which can persist on
        # the underlying QWidget across slots-instance rebuilds.  That
        # left stale bindings pointing at a previous ``self`` whose
        # methods silently no-op — this was the root cause of "edits
        # don't set the attribute".
        #
        # ``signal.disconnect()`` with no arguments clears every handler
        # (including stale ones from dead slots instances); we then
        # connect every handler this instance needs.  Kept in sync with
        # ``tbl000_init`` (which no longer connects these — see note
        # there) so each signal has exactly the handlers listed here.
        self._wire_table_signals(self.ui.tbl000)

        # Channel Box → Table sync via Qt signal (instant, replaces polling)
        self._last_cb_selection = set()
        self._cb_signal_connected = False
        self._syncing_selection = False
        self._connect_cb_signal()

        self._combo_setting = False

        # Debounced refresh for the text filter.
        self._filter_timer = QtCore.QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(lambda: self._refresh_table(self.ui.tbl000))
        self.ui.txt000.textChanged.connect(lambda _: self._filter_timer.start())
        self.ui.txt000.option_box.clear_option = True

        # Filter on/off toggle as an action button on the option_box.
        # The action cycles two states (enabled / disabled).  Each state's
        # callback fires *before* the cycle advances, so it must set the
        # value for the NEXT state.  The "active" colour is a muted
        # blue (not red/pink) to avoid conflict with the red that the
        # table uses to signal keyed attributes.
        clr = self.ACTION_COLOR_MAP
        self._filter_enabled = True
        self.ui.txt000.option_box.add_action(
            icon="filter",
            tooltip="Toggle name filter",
            states=[
                {
                    "icon": "filter",
                    "tooltip": "Filter ON — click to disable",
                    "color": clr["active"],
                    "callback": lambda: self._set_filter_enabled(False),
                },
                {
                    "icon": "filter",
                    "tooltip": "Filter OFF — click to enable",
                    "color": clr["off"],
                    "callback": lambda: self._set_filter_enabled(True),
                },
            ],
            # No settings_key — the action's persisted icon state could
            # otherwise drift from ``_filter_enabled`` across sessions.
            settings_key=False,
        )

        # txt001 — current target display + double-click-to-rename.
        # Read-only by default; double-click enters edit mode.
        txt1 = self.ui.txt001
        txt1.setPlaceholderText("No selection")
        txt1.setToolTip("Double-click to rename.")
        txt1.setReadOnly(True)
        txt1.editingFinished.connect(self._on_target_renamed)

        def _txt001_dbl_click(event, _orig=txt1.mouseDoubleClickEvent):
            if txt1.isReadOnly() and self._current_target is not None:
                txt1.setReadOnly(False)
                txt1.setToolTip(
                    f"{self._current_target}"
                    "\n\n(Type a new name and press Enter to rename.)"
                )
                txt1.selectAll()
                txt1.setFocus()
                return
            _orig(event)

        txt1.mouseDoubleClickEvent = _txt001_dbl_click

        def _txt001_key_press(event, _orig=txt1.keyPressEvent):
            from uitk.widgets.header import QtCore

            if event.key() == QtCore.Qt.Key_Escape and not txt1.isReadOnly():
                if self._current_target:
                    txt1.blockSignals(True)
                    txt1.setText(self._current_target.rsplit("|", 1)[-1])
                    txt1.blockSignals(False)
                txt1.setReadOnly(True)
                txt1.setToolTip("Double-click to rename.")
                return
            _orig(event)

        txt1.keyPressEvent = _txt001_key_press
        # "target" icon (not "pin") — avoids visual conflict with the
        # existing PinValuesOption plugin, and reads naturally as
        # "restrict to one target object".
        txt1.option_box.add_action(
            icon="target",
            tooltip="Single-object mode",
            states=[
                {
                    "icon": "target",
                    "tooltip": (
                        "Multi-object mode — click to switch to single-object.\n"
                        "All edits are broadcast to every selected node."
                    ),
                    "color": clr["off"],
                    "callback": lambda: self._on_toggle_single_object(True),
                },
                {
                    "icon": "target",
                    "tooltip": (
                        "Single-object mode — click to switch to multi-object.\n"
                        "Only the most recently selected object is edited."
                    ),
                    "color": clr["active"],
                    "callback": lambda: self._on_toggle_single_object(False),
                },
            ],
            settings_key=False,
        )

        # Stop timer when the UI is destroyed to avoid dangling callbacks.
        self.ui.destroyed.connect(self._filter_timer.stop)

        # Wire compact-mode footer (edit page + target-toggle button).
        self._setup_compact_footer()

    def apply_launch_config(self, targets=None, filter=None, search=None):
        """Configure the window from a :func:`launch` call.

        Safe to call repeatedly — applies pin/filter/search to the
        already-constructed UI.  Pass ``targets=None`` to clear a pin.
        """
        self.controller.pin_targets(targets)

        if filter:
            if filter in Channels.FILTER_MAP:
                cmb = getattr(self.ui, "cmb000", None)
                if cmb is not None:
                    cmb.setCurrentText(filter)
            else:
                import logging

                logging.getLogger(__name__).warning(
                    "launch(filter=%r) ignored — not a FILTER_MAP key. Valid: %s",
                    filter,
                    sorted(Channels.FILTER_MAP.keys()),
                )

        if search is not None:
            self.ui.txt000.setText(search)

        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Populate the header menu with global actions."""
        # --- Create Attribute ---
        widget.menu.add("Separator", setTitle="Create")
        widget.menu.add(
            "QPushButton",
            setText="Create Attribute …",
            setToolTip="Add a new custom attribute to the selected objects.",
            setObjectName="show_create_menu",
        )

        # --- Visibility ---
        widget.menu.add("Separator", setTitle="Visibility")
        self._chk_show_type = widget.menu.add(
            "QCheckBox",
            setText="Show Type",
            setChecked=False,
            setToolTip="Toggle the Type column in the attribute table.",
            setObjectName="chk_show_type",
        )
        self._chk_show_type.toggled.connect(self._on_toggle_type_column)
        self._chk_compact = widget.menu.add(
            "QCheckBox",
            setText="Compact View",
            setChecked=False,
            setToolTip="Reduce row height and hide the Type column.",
            setObjectName="chk_compact_view",
        )
        self._chk_compact.toggled.connect(self._on_toggle_compact_view)
        self._chk_auto_fit = widget.menu.add(
            "QCheckBox",
            setText="Auto-fit Window",
            setChecked=False,
            setToolTip="Resize columns to fit contents and grow/shrink the "
            "window to match on every refresh.",
            setObjectName="chk_auto_fit",
        )
        self._chk_auto_fit.toggled.connect(self._on_toggle_auto_fit)

        # --- Selection ---
        widget.menu.add("Separator", setTitle="Selection")
        widget.menu.add(
            "QPushButton",
            setText="Select Shape Node",
            setObjectName="hdr_select_shape",
            setToolTip="Select the shape node(s) of the current selection.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Select History Node",
            setObjectName="hdr_select_history",
            setToolTip="Select the construction-history node(s) of the current selection.",
        )
        widget.menu.hdr_select_shape.clicked.connect(self._hdr_select_shape)
        widget.menu.hdr_select_history.clicked.connect(self._hdr_select_history)

        # --- Maya Editors ---
        widget.menu.add("Separator", setTitle="Maya Editors")
        widget.menu.add(
            "QPushButton",
            setText="Channel Control …",
            setObjectName="hdr_channel_control",
            setToolTip="Open Maya's Channel Control editor.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Connection Editor …",
            setObjectName="hdr_connection_editor",
            setToolTip="Open Maya's Connection Editor.",
        )
        widget.menu.hdr_channel_control.clicked.connect(
            lambda: mel.eval("ChannelControlEditor")
        )
        widget.menu.hdr_connection_editor.clicked.connect(
            lambda: mel.eval("ConnectionEditor")
        )
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=fmt(
                title="Channels",
                body="Inspect, edit, and manage Maya node attributes.",
                bullets=[
                    "Filter attributes by type: Custom, Keyable, Locked, Connected, etc.",
                    "Edit attribute values, lock/unlock, and toggle keyable state.",
                    "Create new custom attributes on selected objects.",
                    "Select Shape or History nodes from the header menu.",
                    "Open Maya's <b>Channel Control</b> or <b>Connection Editor</b>.",
                ],
            ),
        )

    # --- Header action handlers ---

    def _hdr_select_shape(self):
        """Select the shape node(s) for the current selection."""
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        shapes = self.controller.get_shape_nodes(nodes)
        if shapes:
            cmds.select(shapes, replace=True)
            self._refresh_table(self.ui.tbl000)
        else:
            self.sb.message_box("Warning: No shape nodes found.")

    def _hdr_select_history(self):
        """Select the history (construction) node(s) for the current selection."""
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        history = self.controller.get_history_nodes(nodes)
        if history:
            cmds.select(history, replace=True)
            self._refresh_table(self.ui.tbl000)
        else:
            self.sb.message_box("Warning: No history nodes found.")

    def show_create_menu(self, *args):
        """Show the *Create Attribute* popup."""
        menu = self.sb.registered_widgets.Menu(
            parent=self.ui,
            position="cursor",
            add_defaults_button=False,
            fixed_item_height=20,
        )
        menu.setTitle("Create Attribute")
        # Swap the default pin button in the header for a hide button —
        # this popup is a one-shot form, not a pinnable tool panel.
        if menu.header:
            menu.header.config_buttons("hide")

        # -- Identity -------------------------------------------------------
        menu.add("QLabel", setText="Name:", row=0, col=0)
        le_name = menu.add(
            "QLineEdit",
            setPlaceholderText="my_attribute",
            setObjectName="le_attr_name",
            row=0,
            col=1,
        )
        menu.add("QLabel", setText="Type:", row=1, col=0)
        cmb_type = menu.add(
            "QComboBox",
            setObjectName="cmb_attr_type",
            addItems=["float", "int", "bool", "string", "enum", "double3"],
            row=1,
            col=1,
        )

        # -- Numeric range --------------------------------------------------
        sep_range = menu.add("Separator", setTitle="Range", row=2)
        lbl_default = menu.add("QLabel", setText="Default:", row=3, col=0)
        spn_default = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_default",
            setMinimum=-1e9,
            setMaximum=1e9,
            row=3,
            col=1,
        )
        lbl_min = menu.add("QLabel", setText="Min:", row=4, col=0)
        spn_min = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_min",
            setMinimum=-1e9,
            setMaximum=1e9,
            row=4,
            col=1,
        )
        lbl_max = menu.add("QLabel", setText="Max:", row=5, col=0)
        spn_max = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_max",
            setMinimum=-1e9,
            setMaximum=1e9,
            setValue=1.0,
            row=5,
            col=1,
        )

        # -- Enum names -----------------------------------------------------
        sep_enum = menu.add("Separator", setTitle="Enum", row=6)
        lbl_enum = menu.add("QLabel", setText="Names:", row=7, col=0)
        le_enum = menu.add(
            "QLineEdit",
            setPlaceholderText="A:B:C",
            setToolTip="Colon-separated enum labels.",
            setObjectName="le_enum_names",
            row=7,
            col=1,
        )

        # -- Options --------------------------------------------------------
        menu.add("Separator", row=8)
        chk_keyable = menu.add(
            "QCheckBox",
            setText="Keyable",
            setChecked=True,
            setObjectName="chk_keyable",
            row=9,
        )
        btn = menu.add(
            "QPushButton",
            setText="Create",
            setMinimumHeight=28,
            setMaximumHeight=28,
            row=10,
        )

        # -- Reactive show/hide ---------------------------------------------
        _numeric_widgets = [
            sep_range,
            lbl_default,
            spn_default,
            lbl_min,
            spn_min,
            lbl_max,
            spn_max,
        ]
        _enum_widgets = [sep_enum, lbl_enum, le_enum]

        def _on_type_changed(text):
            is_numeric = text in ("float", "int", "double3")
            is_enum = text == "enum"
            for w in _numeric_widgets:
                w.setVisible(is_numeric)
            for w in _enum_widgets:
                w.setVisible(is_enum)

        cmb_type.currentTextChanged.connect(_on_type_changed)
        _on_type_changed(cmb_type.currentText())

        # -- Create handler -------------------------------------------------
        def _on_create():
            name = le_name.text().strip().replace(" ", "_")
            if not name:
                self.sb.message_box("Warning: Attribute name cannot be empty.")
                return
            sel = cmds.ls(sl=True)
            if not sel:
                self.sb.message_box("Warning: Nothing selected.")
                return

            attr_type = cmb_type.currentText()
            try:
                self.controller.create_attribute(
                    sel,
                    name,
                    attr_type,
                    keyable=chk_keyable.isChecked(),
                    min_val=spn_min.value() if spn_min.isEnabled() else None,
                    max_val=spn_max.value() if spn_max.isEnabled() else None,
                    default_val=spn_default.value(),
                    enum_names=le_enum.text().strip() if attr_type == "enum" else "",
                )
            except RuntimeError as e:
                self.sb.message_box(f"Error: {e}")
                return
            menu.hide()
            self._refresh_table(self.ui.tbl000)

        btn.clicked.connect(_on_create)
        menu.show()

    def _on_toggle_type_column(self, visible):
        """Show or hide the Type column."""
        self.ui.tbl000.setColumnHidden(self.COL_TYPE, not visible)

    def _on_toggle_auto_fit(self, _enabled):
        """Re-apply column sizing when the auto-fit toggle changes."""
        self._refresh_table(self.ui.tbl000)

    def _autofit_window(self, widget):
        """Resize the window so the table's content width fits exactly.

        Runs only when ``Auto-fit Window`` is checked.  Deferred twice:
        once to let ``_refresh_table``'s setUpdatesEnabled / signal-block
        unwind, then a second tick so ``ResizeToContents`` columns have
        applied their final widths before we measure them.
        """
        chk = getattr(self, "_chk_auto_fit", None)
        if not (chk and chk.isChecked()):
            return

        def _do_fit():
            try:
                if not self._is_widget_alive(widget):
                    return

                win = widget.window()
                if win is None:
                    return

                # Force only ResizeToContents columns to recompute — using
                # ``widget.resizeColumnsToContents()`` would override the
                # Fixed-width action columns (sized to row height by
                # TableActions) and inflate them to icon-padding width,
                # which masks the real content total.
                QHV = self.sb.QtWidgets.QHeaderView
                header = widget.horizontalHeader()
                for col in range(widget.columnCount()):
                    if (
                        not widget.isColumnHidden(col)
                        and header.sectionResizeMode(col) == QHV.ResizeToContents
                    ):
                        widget.resizeColumnToContents(col)

                lay = win.layout()
                if lay is not None:
                    lay.activate()
                cw = win.centralWidget() if hasattr(win, "centralWidget") else None
                if cw is not None and cw.layout() is not None:
                    cw.layout().activate()

                header_len = header.length()
                if header_len <= 0:
                    return

                # Width
                vbar = widget.verticalScrollBar()
                vsb_w = (
                    vbar.sizeHint().width() if vbar and vbar.isVisible() else 0
                )
                fr_w = widget.frameWidth() * 2
                chrome_w = max(win.width() - widget.viewport().width(), 0)
                target_w = header_len + fr_w + vsb_w + chrome_w
                target_w = max(target_w, win.minimumWidth())

                # Height — sum of (visible) row heights + horizontal header
                # height (when shown) + horizontal scrollbar (when shown).
                rows_h = 0
                for row in range(widget.rowCount()):
                    if not widget.isRowHidden(row):
                        rows_h += widget.rowHeight(row)
                hhdr = widget.horizontalHeader()
                hhdr_h = hhdr.height() if hhdr.isVisible() else 0
                hbar = widget.horizontalScrollBar()
                hsb_h = (
                    hbar.sizeHint().height() if hbar and hbar.isVisible() else 0
                )
                fr_h = widget.frameWidth() * 2
                chrome_h = max(win.height() - widget.viewport().height(), 0)
                target_h = rows_h + hhdr_h + fr_h + hsb_h + chrome_h
                target_h = max(target_h, win.minimumHeight())

                if target_w != win.width() or target_h != win.height():
                    win.resize(target_w, target_h)
            except RuntimeError:
                pass

        QtCore.QTimer.singleShot(
            0, lambda: QtCore.QTimer.singleShot(0, _do_fit)
        )

    def _on_toggle_compact_view(self, enabled):
        """Toggle compact view: shorter rows, hide table header, swap txt001↔footer name."""
        leaving_compact = self._compact_view and not enabled
        self._compact_view = bool(enabled)

        # The *table's* horizontal header (the column label strip), NOT
        # the window's top-banner Header widget.  The window header stays
        # visible so the title / menu button remain accessible.
        self.ui.tbl000.horizontalHeader().setVisible(not self._compact_view)
        self._apply_row_height(self.ui.tbl000)

        # Only one of (txt001, footer-name) is visible at a time.
        txt1 = self.ui.txt001
        txt1_widget = getattr(txt1, "container", None) or txt1
        try:
            txt1_widget.setVisible(not self._compact_view)
        except Exception:
            pass

        # Footer compact-mode button mirrors the txt001 option_box target button.
        if self._footer_compact_btn is not None:
            try:
                self._footer_compact_btn.setVisible(self._compact_view)
            except Exception:
                pass

        # When leaving compact mode, reset single_object_mode so the
        # option_box button (which starts in state-0 = multi-object) stays
        # consistent with the actual mode.
        if leaving_compact:
            self.controller.single_object_mode = False

        if self._footer_controller:
            try:
                self._footer_controller.update()
            except Exception:
                pass

    def _on_toggle_single_object(self, enabled):
        """Toggle single-object mode."""
        self.controller.single_object_mode = bool(enabled)
        self._update_compact_btn_state()
        self._refresh_table(self.ui.tbl000)

    def _set_filter_enabled(self, enabled):
        """Toggle whether the name filter (txt000) is applied."""
        self._filter_enabled = bool(enabled)
        self._refresh_table(self.ui.tbl000)

    def _apply_row_height(self, widget):
        """Apply the active row height.

        Normal mode uses the table's natural default; compact mode uses
        80% of that (rounded down, minimum 12 px).  The natural default
        is snapshotted per-instance on the first call and reused so
        toggling on/off always returns to the exact original height.
        ``Fixed`` resize mode is required so rows actually shrink below
        the content's natural preferred height and so that
        ``setDefaultSectionSize`` governs every row.
        """
        vh = widget.verticalHeader()
        QHV = self.sb.QtWidgets.QHeaderView
        vh.setSectionResizeMode(QHV.Fixed)

        if self._base_row_height is None:
            self._base_row_height = max(vh.defaultSectionSize(), 18)

        base = self._base_row_height
        height = max(int(base * 0.8), 12) if self._compact_view else base
        # Qt's style-dependent minimumSectionSize (~20 px on most styles)
        # silently clamps setDefaultSectionSize, so lower the floor first.
        vh.setMinimumSectionSize(min(height, vh.minimumSectionSize()))
        vh.setDefaultSectionSize(height)
        # Force-apply to existing rows — Qt doesn't retro-fit default
        # size changes onto already-laid-out sections.
        for row in range(widget.rowCount()):
            widget.setRowHeight(row, height)

        # Re-fit action-column widths and icons to the new row height —
        # without this, icons stay at their original size and clip in
        # the shorter compact rows.
        try:
            widget.actions.update_for_row_height()
        except AttributeError:
            pass

    # ------------------------------------------------------------------
    # Target display (txt001) and footer warnings
    # ------------------------------------------------------------------

    def _update_target_display(self, nodes):
        """Refresh ``txt001`` to show the current target(s)."""
        txt = self.ui.txt001
        was_blocked = txt.signalsBlocked()
        txt.blockSignals(True)
        try:
            if not nodes:
                self._current_target = None
                txt.setText("")
                txt.setReadOnly(True)
                txt.setToolTip("No selection")
            elif len(nodes) == 1:
                self._current_target = nodes[0]
                short = nodes[0].rsplit("|", 1)[-1]
                txt.setText(short)
                txt.setReadOnly(True)
                txt.setToolTip(f"{nodes[0]}\n\n(Double-click to rename.)")
            else:
                self._current_target = None
                txt.setText(f"Multi-selection ({len(nodes)})")
                txt.setReadOnly(True)
                names = "\n".join(f"  • {n.rsplit('|', 1)[-1]}" for n in nodes)
                txt.setToolTip(f"Selected objects:\n{names}")
        finally:
            txt.blockSignals(was_blocked)

    def _on_target_renamed(self):
        """Handle inline rename of the single target via ``txt001``."""
        txt = self.ui.txt001
        # Return to display mode first; prevents double-fire on Enter + focus-loss.
        was_editing = not txt.isReadOnly()
        txt.setReadOnly(True)
        txt.setToolTip("Double-click to rename.")
        if not was_editing:
            return
        old_full = self._current_target
        new_name = txt.text().strip()
        if not old_full or not new_name:
            return
        old_short = old_full.rsplit("|", 1)[-1]
        if new_name == old_short:
            return
        new_full = self.controller.rename_node(old_full, new_name)
        if new_full and new_full != old_full:
            self._current_target = new_full
            if self.controller.is_pinned:
                self.controller.pin_targets([new_full])
        self._refresh_table(self.ui.tbl000)

    def _set_footer_warning(self, message):
        """Push a warning/info message to the footer (empty clears it)."""
        self._footer_warning = message or ""
        if self._footer_controller:
            self._footer_controller.update()

    # ------------------------------------------------------------------
    # Filter ComboBox
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Populate filter combobox and wire its option_box invert action.

        The old ``chk000`` Invert checkbox has been replaced by an action
        button on the ComboBox's built-in option_box — same two-state
        cycle as the name-filter / single-object toggles.
        """
        widget.addItems(
            [k for k in Channels.FILTER_MAP.keys() if not k.startswith("_")]
        )

        clr = self.ACTION_COLOR_MAP
        widget.option_box.add_action(
            icon="invert",
            tooltip="Invert filter",
            states=[
                {
                    "icon": "invert",
                    "tooltip": "Invert OFF — click to show the complement of the filter",
                    "color": clr["off"],
                    "callback": lambda: self._set_filter_invert(True),
                },
                {
                    "icon": "invert",
                    "tooltip": "Invert ON — click to show the normal filter set",
                    "color": clr["active"],
                    "callback": lambda: self._set_filter_invert(False),
                },
            ],
            settings_key=False,
        )

    def cmb000(self, index):
        """Filter changed — refresh table."""
        self._refresh_table(self.ui.tbl000)

    def _set_filter_invert(self, enabled):
        """Toggle filter inversion (replaces the deleted chk000 checkbox)."""
        self._filter_invert = bool(enabled)
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        """One-time table setup: action columns, context menu, scriptJobs.

        ``cellChanged`` / ``itemSelectionChanged`` are re-wired on every
        call via :meth:`_wire_table_signals` (which disconnects before
        connecting, so it's idempotent).  This is necessary because the
        widget passed to ``tbl000_init`` may not be the same QWidget
        instance that ``self.ui.tbl000`` resolved to at ``__init__``
        time (e.g. after a UI reload), leaving the ``__init__`` wiring
        pointing at a dead widget.
        """
        # Always re-wire on this widget — idempotent, safe to call
        # repeatedly.  This is the authoritative wiring; the call in
        # ``__init__`` is only a best-effort first pass.
        self._wire_table_signals(widget)

        if not widget.is_initialized:
            widget.refresh_on_show = True

            self._setup_action_columns(widget)
            self._setup_context_menu(widget)
            self._setup_scene_change_callbacks(widget)
            self._setup_value_input(widget)

            try:
                widget.destroyed.connect(self.cleanup_scene_callbacks)
            except Exception:
                pass

        # Table header visibility is driven by compact mode (the .ui file
        # defaults it to hidden — we override here so normal mode shows
        # the column labels).
        widget.horizontalHeader().setVisible(not self._compact_view)

        self._refresh_table(widget)

    def _wire_table_signals(self, widget):
        """Clear stale signal bindings and wire this instance's handlers.

        Every handler for ``cellChanged`` / ``itemSelectionChanged`` /
        scrub signals that this class depends on is listed here so the
        full signal contract is visible in one place.

        Idempotent: called from ``__init__`` *and* from ``tbl000_init``
        on every UI show.  Re-wiring is mandatory because the QWidget
        can outlive the slots instance, leaving stale bindings pointing
        at a dead ``self``.  Disconnect-then-connect on every call so
        each signal is bound to exactly the live handlers.
        """
        # cellChanged → attribute-value / rename dispatch.
        try:
            widget.cellChanged.disconnect()
        except (RuntimeError, TypeError):
            pass
        widget.cellChanged.connect(self._handle_cell_edit)

        # itemSelectionChanged → CB sync + (optional) footer update.
        try:
            widget.itemSelectionChanged.disconnect()
        except (RuntimeError, TypeError):
            pass
        widget.itemSelectionChanged.connect(self._on_table_selection_changed)

        # Value-input signals (MMB scrub + wheel scroll).  Column
        # registration lives in ``_setup_scrub_edit`` (one-time per
        # widget); the connections belong here because the widget can
        # persist across slots rebuilds while ``self`` does not.
        for sig, slot in (
            (getattr(widget, "cellScrubStarted", None), self._on_scrub_started),
            (getattr(widget, "cellScrubMoved", None), self._on_scrub_moved),
            (getattr(widget, "cellScrubFinished", None), self._on_scrub_finished),
            (getattr(widget, "cellWheelScrolled", None), self._on_wheel_scrolled),
        ):
            if sig is None:
                continue  # older TableWidget without the signal
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
            sig.connect(slot)
        if self._footer_controller:
            widget.itemSelectionChanged.connect(self._footer_controller.update)

    def _setup_action_columns(self, widget):
        """Register Lock and Connect as icon-toggle action columns."""
        clr = self.ACTION_COLOR_MAP

        widget.actions.add(
            self.COL_LOCK,
            states={
                "locked": {
                    "icon": "lock",
                    "color": clr["locked"],
                    "tooltip": "Locked — click to unlock",
                    "action": self._on_icon_cell_clicked,
                },
                "unlocked": {
                    "icon": "unlock",
                    "color": clr["off"],
                    "tooltip": "Unlocked — click to lock",
                    "action": self._on_icon_cell_clicked,
                },
            },
        )

        # Connection / keyed action column.
        # Plain click → set/remove a keyframe at the current time
        #               (when the attr is unconnected or keyed).
        # Ctrl+click  → break the incoming connection (any state).
        conn_states = {
            "none": {
                "icon": "disconnect",
                "color": clr["off"],
                "tooltip": (
                    "Not connected — click to set a keyframe at the current time.\n"
                    "Ctrl+click: no-op (nothing to break)."
                ),
                "action": self._on_icon_cell_clicked,
            },
            "keyframe": {
                "icon": "connect",
                "color": clr["keyframe"],
                "tooltip": (
                    "Animated — click to set a keyframe at the current time.\n"
                    "Ctrl+click: break the connection."
                ),
                "action": self._on_icon_cell_clicked,
            },
            "keyframe_active": {
                "icon": "connect",
                "color": clr["keyframe_active"],
                "tooltip": (
                    "Key set at current time — click to remove it.\n"
                    "Ctrl+click: break the connection."
                ),
                "action": self._on_icon_cell_clicked,
            },
        }
        for key in ("connected", "expression", "driven_key", "constraint", "muted"):
            conn_states[key] = {
                "icon": "connect",
                "color": clr.get(key, clr["connected"]),
                "tooltip": (
                    f"{key.replace('_', ' ').title()} — Ctrl+click to break the connection."
                ),
                "action": self._on_icon_cell_clicked,
            }
        widget.actions.add(self.COL_CONN, states=conn_states)

    def _setup_context_menu(self, widget):
        """Build the table's right-click context menu and bind handlers."""
        menu = widget.menu

        # fmt: off
        _items = [
            ("Edit",       None),
            ("Lock",       "ctx_lock",            "Lock the selected attribute(s)."),
            ("Unlock",     "ctx_unlock",          "Unlock the selected attribute(s)."),
            ("Reset to Default", "ctx_reset_default", "Reset the attribute to its default value."),
            ("Toggle Keyable",   "ctx_toggle_keyable", "Toggle the keyable state of the attribute."),
            ("Values",     None),
            ("Copy Values",  "ctx_copy_values",   "Copy selected attribute values to clipboard."),
            ("Paste Values", "ctx_paste_values",  "Paste attribute values from clipboard."),
            ("Channel Box", None),
            ("Breakdown",  "ctx_breakdown",       "Set a breakdown key on the selected attribute(s)."),
            ("Mute",       "ctx_mute",            "Mute selected attribute(s) — suppress animation."),
            ("Unmute",     "ctx_unmute",          "Unmute selected attribute(s)."),
            ("Hide Selected",     "ctx_hide",     "Hide the attribute from the channel box."),
            ("Show Selected",     "ctx_show",     "Show (unhide) the attribute in the channel box."),
            ("Lock and Hide",     "ctx_lock_and_hide", "Lock the attribute and hide it from the channel box."),
            ("Select Connection", "ctx_select_connection", "Select the upstream node driving this attribute."),
            ("Break Connection",  "ctx_break_connection",  "Break incoming connection(s) on the selected attribute(s)."),
            ("Manage",     None),
            ("Delete Attribute",  "ctx_delete",   "Delete the selected custom attribute(s)."),
        ]
        # fmt: on

        handler_map = {
            "ctx_lock": self._ctx_lock,
            "ctx_unlock": self._ctx_unlock,
            "ctx_reset_default": self._ctx_reset_default,
            "ctx_toggle_keyable": self._ctx_toggle_keyable,
            "ctx_copy_values": self._ctx_copy_values,
            "ctx_paste_values": self._ctx_paste_values,
            "ctx_breakdown": self._ctx_breakdown,
            "ctx_mute": self._ctx_mute,
            "ctx_unmute": self._ctx_unmute,
            "ctx_hide": self._ctx_hide,
            "ctx_show": self._ctx_show,
            "ctx_lock_and_hide": self._ctx_lock_and_hide,
            "ctx_select_connection": self._ctx_select_connection,
            "ctx_break_connection": self._ctx_break_connection,
            "ctx_delete": self._ctx_delete,
        }

        for entry in _items:
            label = entry[0]
            obj_name = entry[1] if len(entry) > 1 else None
            tooltip = entry[2] if len(entry) > 2 else ""

            if obj_name is None:
                # Section separator
                menu.add("Separator", setTitle=label)
            else:
                menu.add(
                    "QPushButton",
                    setText=label,
                    setObjectName=obj_name,
                    setToolTip=tooltip,
                )
                handler = handler_map.get(obj_name)
                if handler:
                    widget.register_menu_action(
                        obj_name,
                        lambda sel, fn=handler: fn(sel),
                        columns=self._ROW_SELECTION_COLUMNS,
                    )

    # ------------------------------------------------------------------
    # Table data
    # ------------------------------------------------------------------

    def _get_filter_kwargs(self):
        """Return the ``cmds.listAttr`` kwargs for the active filter."""
        cmb = getattr(self.ui, "cmb000", None)
        key = cmb.currentText() if cmb else "Custom"
        return self.controller.get_filter_kwargs(key, self._filter_invert)

    def _refresh_table(self, widget):
        """Rebuild the table from the current selection and filter.

        Re-entry safe: if a refresh is already in progress (for example
        because a Qt event pumped during ``waitCursor`` / cell-widget
        destruction and fired a queued filter-combobox signal), we mark
        a reentry as pending and bail out.  The in-flight call picks up
        the pending flag on exit and schedules a single follow-up
        refresh via ``evalDeferred`` — so bursts of rapid filter toggles
        collapse into one extra rebuild, never overlap.
        """
        if self._destroyed:
            return

        if self._refreshing:
            self._refresh_queued = True
            return
        self._refreshing = True

        # Suppress Channel Box → table sync for the duration of the
        # rebuild.  ``_on_cb_selection_changed`` is a Qt slot on a
        # signal we don't own, so ``widget.blockSignals`` doesn't stop
        # it; without this guard the CB signal can fire mid-clear and
        # try to mutate rows that are being torn down.
        prev_syncing = self._syncing_selection
        self._syncing_selection = True

        cmds.waitCursor(state=True)
        try:
            if not self._is_widget_alive(widget):
                return

            widget.setUpdatesEnabled(False)
            widget.blockSignals(True)

            # Tear down existing enum combobox cell widgets explicitly
            # before ``clear()``.  ``clear()`` destroys them
            # synchronously — if Qt still has a queued ``activated``
            # signal for one of them (common with fast user input),
            # it fires against a half-destroyed object and can crash.
            # ``removeCellWidget`` + ``deleteLater`` defers destruction
            # to the next idle cycle, after the queued signal has
            # safely been dispatched to a disconnected slot.
            self._teardown_cell_widgets(widget)

            widget.clear()

            nodes = self.controller.get_selected_nodes()
            self._update_target_display(nodes)
            if not nodes:
                self._footer_warning = ""
                widget.add(
                    [["No selection", "", "", "", ""]],
                    headers=["Name", "", "", "Value", "Type"],
                )
                self._configure_columns(widget)
                self._apply_row_height(widget)
                return

            self._footer_warning = ""

            filter_kwargs = self._get_filter_kwargs()
            rows, attr_states = self.controller.build_table_data(nodes, filter_kwargs)

            # Apply wildcard text filter when the toggle is on and a
            # pattern is present.  Filtering itself is delegated to
            # ``pythontk.IterUtils.filter_list``, which already handles
            # comma-separated patterns, wildcards, and case-insensitivity.
            pattern = getattr(self.ui, "txt000", None)
            if self._filter_enabled and pattern and pattern.text().strip():
                text = pattern.text().strip()
                names = [r[0] for r in rows]
                filtered = ptk.IterUtils.filter_list(names, inc=text, ignore_case=True)
                keep = set(filtered)
                zipped = [(r, s) for r, s in zip(rows, attr_states) if r[0] in keep]
                if zipped:
                    rows, attr_states = zip(*zipped)
                    rows, attr_states = list(rows), list(attr_states)
                else:
                    rows, attr_states = [], []

            widget.add(rows, headers=["Name", "", "", "Value", "Type"])
            self._configure_columns(widget)

            # Set action states (icon colours are handled by the action column config)
            for row_idx, (is_locked, conn_type) in enumerate(attr_states):
                widget.actions.set(
                    row_idx,
                    self.COL_LOCK,
                    "locked" if is_locked else "unlocked",
                )
                widget.actions.set(
                    row_idx,
                    self.COL_CONN,
                    conn_type,  # "none", "keyframe", "expression", etc.
                )

            # Make name cells editable for user-defined attrs and
            # store the original name so renames can be detected.
            self._set_name_editability(widget, nodes)
            self._apply_row_height(widget)

            # Replace enum value cells with comboboxes.
            self._setup_enum_combos(widget, nodes)

            # Sync table selection with channel box selection.
            # Fetch fresh CB data *before* syncing so the table reflects
            # the current state rather than a stale cache.
            self._last_cb_selection = self._normalize_cb_attrs(
                set(self.controller.get_channel_box_selection())
            )
            self._sync_table_to_channel_box(widget)

        except RuntimeError:
            # Widget (or a child) destroyed mid-refresh.
            self.cleanup_scene_callbacks()
            return
        except Exception:
            # Don't let a rebuild error take down the panel — log and
            # continue so the next refresh has a chance to recover.
            import logging

            logging.getLogger(__name__).debug(
                "channels refresh failed", exc_info=True
            )
        finally:
            try:
                widget.blockSignals(False)
                widget.setUpdatesEnabled(True)
            except RuntimeError:
                pass
            cmds.waitCursor(state=False)
            self._syncing_selection = prev_syncing
            self._refreshing = False

            # If a re-entry was requested while we were busy, fire a
            # single follow-up on the next idle tick so the user's
            # latest filter/state wins without overlapping rebuilds.
            if self._refresh_queued and not self._destroyed:
                self._refresh_queued = False
                cmds.evalDeferred(lambda w=widget: self._deferred_refresh(w))

        # Restore Type column visibility from the Show Type checkbox.
        chk = getattr(self, "_chk_show_type", None)
        if chk is not None:
            try:
                self.ui.tbl000.setColumnHidden(self.COL_TYPE, not chk.isChecked())
            except RuntimeError:
                pass

        if self._footer_controller:
            try:
                self._footer_controller.update()
            except Exception:
                pass

        self._autofit_window(widget)

    def _teardown_cell_widgets(self, widget):
        """Defer-destroy enum-combobox cell widgets before ``clear()``.

        Only touches ``COL_VALUE`` — that's the single column where we
        explicitly install cell widgets (``_setup_enum_combos``).  The
        action columns (``COL_LOCK`` / ``COL_CONN``) are managed by
        uitk's ``widget.actions`` subsystem, which may cache its own
        cell widget references; tearing those down here would
        invalidate that cache.

        Detaches via ``removeCellWidget`` and schedules ``deleteLater``
        so Qt can finish delivering any queued ``activated`` signal to
        the (now disconnected) combo before the C++ object vanishes —
        safer than letting ``clear()`` destroy them synchronously.
        """
        try:
            row_count = widget.rowCount()
        except RuntimeError:
            return

        for row in range(row_count):
            try:
                w = widget.cellWidget(row, self.COL_VALUE)
            except RuntimeError:
                return
            if w is None:
                continue
            try:
                # Block first so any already-queued signal that slips
                # past removeCellWidget is discarded rather than
                # dispatched during teardown.
                w.blockSignals(True)
                widget.removeCellWidget(row, self.COL_VALUE)
                w.deleteLater()
            except Exception:
                pass

    def _deferred_refresh(self, widget):
        """Gated ``_refresh_table`` for ``evalDeferred`` scheduling.

        Bails silently if the slots instance was torn down or the
        widget destroyed between scheduling and dispatch.
        """
        if self._destroyed:
            return
        if not self._is_widget_alive(widget):
            return
        self._refresh_table(widget)

    def _sync_table_to_channel_box(self, widget):
        """Select table rows matching the current channel box selection.

        Uses ``QItemSelectionModel.select()`` with ``Select | Rows`` so
        that multiple matching rows are highlighted additively after an
        initial clear.

        An empty CB selection is treated as "no intent" and does *not*
        clear the table — Maya transiently clears the CB on focus shifts
        (viewport clicks, panel hover), which would otherwise wipe the
        user's deliberate table selection.  To deselect in the table,
        click empty space in the table itself.
        """
        cb_attrs = self._last_cb_selection
        if not cb_attrs:
            return

        # Block signals to prevent loop with _on_table_selection_changed
        was_blocked = widget.signalsBlocked()
        widget.blockSignals(True)
        try:
            widget.clearSelection()

            sel_model = widget.selectionModel()
            model = widget.model()
            QSel = self.sb.QtCore.QItemSelectionModel
            for row_idx in range(widget.rowCount()):
                name_item = widget.item(row_idx, self.COL_NAME)
                if name_item and name_item.text().strip() in cb_attrs:
                    sel_model.select(
                        model.index(row_idx, 0),
                        QSel.Select | QSel.Rows,
                    )
        finally:
            widget.blockSignals(was_blocked)

    def _on_table_selection_changed(self):
        """Push table selection to the Maya Channel Box.

        Sends table-selected attribute names (long names) to
        ``ChannelBox.select_visual`` and updates the cache
        with the same long names so the CB signal handler
        doesn’t fight us.
        """
        if self.ui.tbl000.signalsBlocked() or self._syncing_selection:
            return

        selected_items = self.ui.tbl000.selectedItems()
        attr_names = []

        # Gather unique names from selected rows (COLUMN 0)
        for item in selected_items:
            if item.column() == self.COL_NAME:
                name = item.text().strip()
                if name and name not in attr_names:
                    attr_names.append(name)

        self._syncing_selection = True
        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            ChannelBox.select_visual(attr_names)
            self._last_cb_selection = set(attr_names)
        except Exception:
            pass
        finally:
            self._syncing_selection = False

    def _on_icon_cell_clicked(self, row, col):
        """Handle clicks on the Lock or Connect/Key icon columns.

        Connect column behaviour:
          - Plain click on ``none`` / ``keyframe`` → set keyframe at current time.
          - Plain click on ``keyframe_active`` → remove the key at current time.
          - Plain click on other connection states (expression, constraint,
            driven_key, connected, muted) → no-op (use Ctrl+click instead).
          - Ctrl+click on any non-``none`` state → break the connection.
        """
        tbl = self.ui.tbl000
        name_item = tbl.item(row, self.COL_NAME)
        if not name_item or not name_item.text():
            return
        attr_name = name_item.text().strip()
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        if col == self.COL_LOCK:
            self.controller.toggle_lock(nodes, attr_name)
            self._refresh_table(tbl)
            return

        if col != self.COL_CONN:
            return

        # cellClicked carries no modifier info; query the current state instead.
        Qt = self.sb.QtCore.Qt
        modifiers = self.sb.QtWidgets.QApplication.keyboardModifiers()
        ctrl = bool(modifiers & Qt.ControlModifier)

        state = tbl.actions.get(row, col)
        if ctrl:
            if state and state != "none":
                self.controller.break_connections(nodes, attr_name)
                self._refresh_table(tbl)
            return

        if state in (None, "none", "keyframe", "keyframe_active"):
            self.controller.toggle_key_at_current_time(nodes, attr_name)
            self._refresh_table(tbl)

    def _configure_columns(self, widget):
        """Set column resize modes and widths."""
        header = widget.horizontalHeader()
        header.setSectionsMovable(False)
        QHV = self.sb.QtWidgets.QHeaderView

        chk = getattr(self, "_chk_auto_fit", None)
        auto_fit = bool(chk and chk.isChecked())

        # Name fits its content (right-aligned text appears flush within
        # whatever width the longest name demands) regardless of mode.
        header.setSectionResizeMode(self.COL_NAME, QHV.ResizeToContents)

        if auto_fit:
            header.setSectionResizeMode(self.COL_VALUE, QHV.ResizeToContents)
            header.setSectionResizeMode(self.COL_TYPE, QHV.ResizeToContents)
        else:
            header.setSectionResizeMode(self.COL_VALUE, QHV.Stretch)
            header.setSectionResizeMode(self.COL_TYPE, QHV.Interactive)
            widget.setColumnWidth(self.COL_TYPE, 80)

    # ------------------------------------------------------------------
    # Inline editing
    # ------------------------------------------------------------------

    def _set_name_editability(self, widget, nodes):
        """Set per-cell flags: right-align names, gate Name-column editing.

        Value-column editing is always allowed — edits are broadcast
        across every selected node so users can batch-change multiple
        objects at once (Maya channel-box behaviour).

        The Name column (rename) is editable only in single-selection
        and only for user-defined attributes, since renaming across a
        multi-selection doesn't have a sensible single outcome.
        """
        Qt = self.sb.QtCore.Qt
        primary = nodes[0] if nodes else None
        user_attrs = (
            set(cmds.listAttr(primary, userDefined=True) or []) if primary else set()
        )
        multi = len(nodes) > 1

        for row_idx in range(widget.rowCount()):
            name_item = widget.item(row_idx, self.COL_NAME)
            if name_item:
                name_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                attr_name = name_item.text().strip()
                name_item.setData(Qt.UserRole, attr_name)
                if not multi and attr_name in user_attrs:
                    name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
                else:
                    name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

    # Sentinel labels for enum combobox action items.
    _ENUM_ACTION_RENAME = "Rename"
    _ENUM_ACTION_ADD = "Add"
    _ENUM_ACTION_DELETE = "Delete"

    def _setup_enum_combos(self, widget, nodes):
        """Replace value cells with comboboxes for enum-type rows.

        Each combobox is populated with the attribute's enum labels,
        followed by a separator and Rename / Add / Delete action items
        using ``combo.actions.add()``.
        The ``activated`` signal (user interaction only) is used so that
        programmatic index changes never trigger side-effects.
        """
        if not nodes:
            return
        primary = nodes[0]
        Qt = self.sb.QtCore.Qt

        for row in range(widget.rowCount()):
            type_item = widget.item(row, self.COL_TYPE)
            if not type_item or type_item.text() != "enum":
                continue

            name_item = widget.item(row, self.COL_NAME)
            if not name_item:
                continue
            attr_name = name_item.text().strip()

            labels = self.controller.get_enum_fields(primary, attr_name)
            if not labels:
                continue

            pairs = self.controller._parse_enum_def(primary, attr_name)
            maya_indices = [idx for _, idx in pairs]

            try:
                current_maya_idx = cmds.getAttr(f"{primary}.{attr_name}")
            except Exception:
                current_maya_idx = 0

            combo = WidgetComboBox()
            combo.setSizeAdjustPolicy(WidgetComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.setStyleSheet(
                "QComboBox { padding: 0; margin: 0; border: none; }"
                "QComboBox::drop-down { subcontrol-position: right center; }"
            )
            combo.add(labels)

            # --- persistent action items via actions namespace ---
            actions = combo.actions.add(
                {
                    self._ENUM_ACTION_RENAME: lambda checked=False, c=combo: self._on_enum_action_rename(
                        c
                    ),
                    self._ENUM_ACTION_ADD: lambda checked=False, c=combo: self._on_enum_action_add(
                        c
                    ),
                    self._ENUM_ACTION_DELETE: lambda checked=False, c=combo: self._on_enum_action_delete(
                        c
                    ),
                }
            )
            # Apply SVG icons to each action (edit / add / trash).
            try:
                from uitk.widgets.mixins.icon_manager import IconManager

                _icon_names = ("edit", "add", "trash")
                for action, icon_name in zip(actions, _icon_names):
                    action.setIcon(IconManager.get(icon_name, size=(14, 14)))
                combo._rebuild_actions_section()
            except Exception:
                pass

            # Map Maya int value to combo position.
            if current_maya_idx in maya_indices:
                combo.setCurrentIndex(maya_indices.index(current_maya_idx))
            else:
                combo.setCurrentIndex(0)

            # Store metadata for the value handler.
            combo.setProperty("_attr_name", attr_name)
            combo.setProperty("_table_row", row)
            combo.setProperty("_maya_indices", maya_indices)

            # Disable the text-item underneath so double-click can't
            # open a line-edit behind the combobox.
            val_item = widget.item(row, self.COL_VALUE)
            if val_item:
                val_item.setFlags(val_item.flags() & ~Qt.ItemIsEditable)

            # Use ``activated`` (user-click only) instead of
            # ``currentIndexChanged`` to avoid re-entrancy when the
            # index is changed programmatically or the widget is removed.
            combo.activated.connect(
                lambda idx, c=combo: self._on_enum_combo_activated(c, idx)
            )
            widget.setCellWidget(row, self.COL_VALUE, combo)

    def _on_enum_combo_activated(self, combo, index):
        """Handle user-initiated enum combobox value selection.

        Action items (Rename / Add / Delete) are handled by callbacks
        wired through ``combo.actions.add()``, so this handler only
        needs to process real enum value selections.
        """
        attr_name = combo.property("_attr_name")
        maya_indices = combo.property("_maya_indices") or []
        nodes = self.controller.get_selected_nodes()
        if not nodes or not attr_name:
            return

        # Translate combo position to Maya integer index.
        maya_idx = maya_indices[index] if index < len(maya_indices) else index
        self._combo_setting = True
        cmds.undoInfo(openChunk=True, chunkName=f"Set Enum: {attr_name}")
        try:
            for node in nodes:
                try:
                    cmds.setAttr(f"{node}.{attr_name}", maya_idx)
                except Exception:
                    pass
        finally:
            cmds.undoInfo(closeChunk=True)
            self._combo_setting = False

    def _on_enum_action_rename(self, combo):
        """Handle Rename action from enum combobox."""
        attr_name = combo.property("_attr_name")
        nodes = self.controller.get_selected_nodes()
        if not nodes or not attr_name:
            return
        current_label = self.controller.get_enum_label(nodes[0], attr_name)
        if current_label:
            cmds.evalDeferred(
                lambda: self._enum_rename_dialog(nodes, attr_name, current_label)
            )

    def _on_enum_action_add(self, combo):
        """Handle Add action from enum combobox."""
        attr_name = combo.property("_attr_name")
        nodes = self.controller.get_selected_nodes()
        if not nodes or not attr_name:
            return
        cmds.evalDeferred(lambda: self._enum_add_dialog(nodes, attr_name))

    def _on_enum_action_delete(self, combo):
        """Handle Delete action from enum combobox."""
        attr_name = combo.property("_attr_name")
        nodes = self.controller.get_selected_nodes()
        if not nodes or not attr_name:
            return
        current_label = self.controller.get_enum_label(nodes[0], attr_name)
        if current_label:
            cmds.evalDeferred(
                lambda: self._deferred_delete_enum(nodes, attr_name, current_label)
            )

    def _deferred_delete_enum(self, nodes, attr_name, label):
        """Delete an enum field and refresh (called via evalDeferred)."""
        self.controller.delete_enum_field(nodes, attr_name, label)
        self._refresh_table(self.ui.tbl000)

    def _handle_cell_edit(self, row, col):
        """Handle inline editing of the Name or Value column.

        Multi-selection is supported: value edits are broadcast to every
        selected node.  Name edits (rename) are silently skipped when
        more than one node is selected because renaming across
        heterogeneous objects doesn't have a sensible single outcome.
        """
        tbl = self.ui.tbl000
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        if col == self.COL_NAME:
            item = tbl.item(row, col)
            if not item:
                return
            Qt = self.sb.QtCore.Qt
            old_name = item.data(Qt.UserRole)
            new_name = item.text().strip()
            if not old_name or not new_name or new_name == old_name:
                return
            try:
                self.controller.rename_attribute(nodes, old_name, new_name)
            except Exception as exc:
                self.sb.message_box(f"Warning: Cannot rename '{old_name}': {exc}")
            self._refresh_table(tbl)
            return

        if col != self.COL_VALUE:
            return

        name_item = tbl.item(row, self.COL_NAME)
        if not name_item:
            return
        attr_name = name_item.text().strip()
        if not attr_name:
            return

        val_item = tbl.item(row, col)
        # Skip only when the cell owns a *persistent* widget (our enum
        # combobox).  Delegate editors (QLineEdit) are also returned by
        # ``cellWidget()`` under PySide6, so a blanket non-None check
        # short-circuits every normal text edit.
        cell_w = tbl.cellWidget(row, col)
        QComboBox = self.sb.QtWidgets.QComboBox
        if val_item is None or isinstance(cell_w, QComboBox):
            return
        new_text = val_item.text().strip()

        self.controller.set_attribute_value(nodes, attr_name, new_text)

        # Read back the actual value Maya stored (it may have been clamped
        # or rejected) and update the cell so the table never shows a
        # value that differs from the real attribute.
        attr_type = self.controller.get_attr_type(nodes[0], attr_name)
        if attr_type == "enum":
            actual_str = self.controller.get_enum_label(nodes[0], attr_name) or ""
        else:
            actual = self.controller.get_attr_value(nodes[0], attr_name)
            actual_str = self.controller.format_value(actual)
        cell = tbl.item(row, col)
        if cell and cell.text() != actual_str:
            tbl.blockSignals(True)
            cell.setText(actual_str)
            tbl.blockSignals(False)

    # ------------------------------------------------------------------
    # Sync with Channel Box
    # ------------------------------------------------------------------

    def _connect_cb_signal(self):
        """Connect to the Channel Box's QItemSelectionModel signal.

        Safe to call repeatedly — disconnects any previous connection
        first.  Called from ``__init__`` and after every scene change
        (which may invalidate the C++ pointer).
        """
        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            ChannelBox.disconnect_selection_changed(self._on_cb_selection_changed)
        except Exception:
            pass

        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            self._cb_signal_connected = ChannelBox.connect_selection_changed(
                self._on_cb_selection_changed
            )
        except Exception:
            self._cb_signal_connected = False

    def _on_cb_selection_changed(self, selected, deselected):
        """Slot for Channel Box ``selectionModel().selectionChanged``.

        Translates the Qt signal into table row highlights.
        """
        if self._syncing_selection or self._destroyed:
            return

        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            raw_sel = set(ChannelBox.get_selected_attrs(sections="all"))
        except Exception:
            return

        current_sel = self._normalize_cb_attrs(raw_sel)

        if current_sel != self._last_cb_selection:
            self._syncing_selection = True
            try:
                tbl = self.ui.tbl000
                if not self._is_widget_alive(tbl):
                    return
                self._last_cb_selection = current_sel
                self._sync_table_to_channel_box(tbl)
            except RuntimeError:
                self.cleanup_scene_callbacks()
            except Exception:
                pass
            finally:
                self._syncing_selection = False

    def _normalize_cb_attrs(self, cb_attrs):
        """Resolve channel-box attribute names to long names.

        The channel box may return short names (``tx``) or long names
        (``translateX``) depending on how the selection was made
        (Qt ``select_visual`` produces short names; manual clicks produce
        long names).  Normalising to long names via
        ``cmds.attributeQuery(longName=True)`` lets us compare against the
        table, which always shows long names from ``cmds.listAttr``.
        """
        if not cb_attrs:
            return set()
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return set(cb_attrs)
        node = nodes[0]
        result = set()
        for attr in cb_attrs:
            try:
                long = cmds.attributeQuery(attr, node=node, longName=True)
                result.add(long)
            except Exception:
                result.add(attr)
        return result

    # ------------------------------------------------------------------
    # Context menu handlers
    # ------------------------------------------------------------------

    def _selected_attrs_and_nodes(self, selection):
        """Extract attribute names and nodes from a menu *selection* payload.

        Returns ``(attr_names, nodes)`` or ``(None, None)`` if either is empty.
        """
        attr_names = [s["name"] for s in selection if s.get("name")]
        nodes = self.controller.get_selected_nodes()
        if not nodes or not attr_names:
            return None, None
        return attr_names, nodes

    def _ctx_lock(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.set_lock(nodes, attrs, lock=True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_unlock(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.set_lock(nodes, attrs, lock=False)
        self._refresh_table(self.ui.tbl000)

    def _ctx_reset_default(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.reset_to_default(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_toggle_keyable(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.toggle_keyable(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_copy_values(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        copied = self.controller.copy_attr_values(nodes, attrs)
        if copied:
            self.sb.message_box(f"Result: Copied {len(copied)} attribute value(s).")

    def _ctx_paste_values(self, selection):
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        self.controller.paste_attr_values(nodes)
        self._refresh_table(self.ui.tbl000)

    def _ctx_breakdown(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.set_breakdown_key(nodes, attrs)

    def _ctx_mute(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.mute_attrs(nodes, attrs)

    def _ctx_unmute(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.unmute_attrs(nodes, attrs)

    def _ctx_hide(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.hide_attrs(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_show(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.show_attrs(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_lock_and_hide(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.lock_and_hide_attrs(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    def _ctx_select_connection(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        if not self.controller.select_connections(nodes, attrs[0]):
            self.sb.message_box(f"Warning: No incoming connection on '{attrs[0]}'.")

    def _ctx_break_connection(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        any_broken = any(self.controller.break_connections(nodes, a) for a in attrs)
        if any_broken:
            self._refresh_table(self.ui.tbl000)
        else:
            self.sb.message_box("Warning: No connections to break.")

    # ------------------------------------------------------------------
    # Enum field editing
    # ------------------------------------------------------------------

    def _enum_rename_dialog(self, nodes, attr_name, old_label):
        """Show an input dialog to rename the current enum label."""
        new_label = self.sb.input_dialog(
            title="Rename Enum Value",
            label=f"Rename '{old_label}' to:",
            text=old_label,
            parent=self.ui.tbl000,
        )
        if new_label and new_label != old_label:
            self.controller.rename_enum_field(nodes, attr_name, old_label, new_label)
            self._refresh_table(self.ui.tbl000)

    def _enum_add_dialog(self, nodes, attr_name):
        """Show an input dialog to add a new enum label."""
        new_label = self.sb.input_dialog(
            title="Add Enum Value",
            label="New enum label:",
            placeholder="e.g. Footstep",
            parent=self.ui.tbl000,
        )
        if new_label:
            self.controller.add_enum_field(nodes, attr_name, new_label)
            self._refresh_table(self.ui.tbl000)

    def _ctx_delete(self, selection):
        attrs, nodes = self._selected_attrs_and_nodes(selection)
        if not attrs:
            return
        self.controller.delete_attributes(nodes, attrs)
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Value-cell input (MMB scrub, wheel scroll, single-click edit)
    # ------------------------------------------------------------------

    # Attribute storage types that accept numeric input (scrub or wheel).
    # Includes both the user-facing "float"/"int" aliases and the raw
    # Maya types returned by ``cmds.getAttr(plug, type=True)``.
    _SCRUBBABLE_TYPES = frozenset({
        "float", "int",
        "double", "doubleAngle", "doubleLinear", "time",
        "long", "short", "byte",
    })

    # Pixels per unit for the default (unmodified) scrub.  Modifiers
    # multiply: Ctrl=0.1 (fine), Shift=10 (coarse).
    _SCRUB_FLOAT_STEP = 0.01
    _SCRUB_INT_PIXELS_PER_UNIT = 4

    # Wheel-scroll step per Qt notch.  Modifiers select between three
    # step sizes — see ``WHEEL_MOD_FINE`` and ``WHEEL_MOD_SMALLEST``
    # below for the modifier mapping.  ``_WHEEL_SMALLEST_STEP`` should
    # match the controller's display precision so the user can step
    # the *visible* last decimal place — currently 4 decimals
    # (``Channels._fmt_float`` default), so ``1e-4``.
    _WHEEL_FLOAT_STEP = 0.1  # default (no modifier)
    _WHEEL_SMALLEST_STEP = 0.0001  # ``WHEEL_MOD_SMALLEST`` modifier

    # ------------------------------------------------------------------
    # Wheel-scroll hotkeys (overrideable per app / per instance)
    # ------------------------------------------------------------------
    # Apps embedding this slots class can reassign these to fit their
    # hotkey scheme — either as class-level overrides in a subclass or
    # as instance assignment after construction.  Both are
    # ``Qt.KeyboardModifier`` masks; matching uses ``(mods & MASK) ==
    # MASK`` so combined modifiers (Ctrl+Alt) take priority over the
    # less-specific Ctrl-only mapping.
    WHEEL_MOD_FINE = QtCore.Qt.ControlModifier  # whole-number step
    WHEEL_MOD_SMALLEST = (
        QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier
    )  # smallest-decimal step

    def _setup_value_input(self, widget):
        """Enable MMB-drag scrub, wheel-scroll, and click-to-edit on the
        value column.

        Idempotent — registers per-column behaviors on the widget.
        Signal connections are wired in :meth:`_wire_table_signals`
        because the widget can outlive the slots instance.
        """
        widget.set_scrub_columns([self.COL_VALUE])
        widget.set_wheel_scrub_columns([self.COL_VALUE])
        widget.set_single_click_edit_columns([self.COL_VALUE])

    def _on_scrub_started(self, row, col):
        """Capture starting values for an MMB scrub-drag."""
        if self._destroyed:
            return
        widget = self.ui.tbl000
        name_item = widget.item(row, self.COL_NAME)
        if not name_item:
            return
        attr_name = name_item.text().strip()
        if not attr_name:
            return

        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        primary = nodes[0]
        attr_type = self.controller.get_attr_type(primary, attr_name)
        if attr_type not in self._SCRUBBABLE_TYPES:
            return

        # Snapshot current value per node so each drag step computes
        # ``start + delta`` (avoids cumulative-rounding drift).
        starts = {}
        for node in nodes:
            try:
                if not cmds.attributeQuery(attr_name, node=node, exists=True):
                    continue
                if cmds.getAttr(f"{node}.{attr_name}", lock=True):
                    continue
                val = cmds.getAttr(f"{node}.{attr_name}")
                if isinstance(val, (int, float)):
                    starts[node] = float(val)
            except Exception:
                continue

        if not starts:
            return

        cmds.undoInfo(openChunk=True, chunkName=f"Scrub {attr_name}")
        self._scrub_state = {
            "attr": attr_name,
            "type": attr_type,
            "starts": starts,
            "is_int": attr_type in {"long", "short", "byte", "int"},
        }

    def _on_scrub_moved(self, row, col, dx, dy):
        """Apply a delta to all snapshotted nodes for the active scrub."""
        state = self._scrub_state
        if not state or self._destroyed:
            return

        # Sensitivity modifiers — Ctrl=fine, Shift=coarse.  Read modifiers
        # live from the application so the user can switch mid-drag.
        mods = QtWidgets.QApplication.keyboardModifiers()
        scale = 1.0
        if mods & QtCore.Qt.ControlModifier:
            scale *= 0.1
        if mods & QtCore.Qt.ShiftModifier:
            scale *= 10.0

        if state["is_int"]:
            delta = int(dx / self._SCRUB_INT_PIXELS_PER_UNIT * scale)
        else:
            delta = dx * self._SCRUB_FLOAT_STEP * scale

        attr = state["attr"]
        for node, start in state["starts"].items():
            new_val = start + delta
            if state["is_int"]:
                new_val = int(round(new_val))
            try:
                cmds.setAttr(f"{node}.{attr}", new_val)
            except Exception:
                continue

    def _on_scrub_finished(self, row, col):
        """Close the undo chunk and clear scrub state."""
        if self._scrub_state is not None:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
            self._scrub_state = None

    def _wheel_step(self, mods, is_int):
        """Per-notch step size given the active modifier state.

        Modifier precedence (most-specific first):
        - ``WHEEL_MOD_SMALLEST`` (default Ctrl+Alt) → smallest decimal,
          ignored for int attrs (already whole).
        - ``WHEEL_MOD_FINE`` (default Ctrl) → whole-number step.
        - none → ``_WHEEL_FLOAT_STEP`` for floats, ``1.0`` for ints.
        """
        if (
            not is_int
            and (mods & self.WHEEL_MOD_SMALLEST) == self.WHEEL_MOD_SMALLEST
        ):
            return self._WHEEL_SMALLEST_STEP
        if (mods & self.WHEEL_MOD_FINE) == self.WHEEL_MOD_FINE:
            return 1.0
        return 1.0 if is_int else self._WHEEL_FLOAT_STEP

    def _on_wheel_scrolled(self, row, col, steps):
        """Adjust a numeric attribute by *steps* notches (signed).

        Two paths.

        **Edit mode** — when the cell has an open editor, it is the
        user-visible source of truth.  Read its text, parse, add the
        delta, write the new text back, and call ``cmds.setAttr`` to
        match.  This single-direction flow avoids the model→editor
        sync problems that plague the alternative (model.setText
        followed by ``setEditorData`` is brittle on focused editors;
        repaints are inconsistent across Qt styles).

        **Display mode** — no editor open; read current value via
        ``cmds.getAttr``, add delta, ``cmds.setAttr``, then refresh
        the cell text via ``_on_attr_value_set`` (the async callback
        path would also do this but only on the next idle, leaving
        a visible lag).

        Locked attrs and non-numeric types skip silently.
        """
        if self._destroyed or steps == 0:
            return

        widget = self.ui.tbl000
        name_item = widget.item(row, self.COL_NAME)
        if not name_item:
            return
        attr_name = name_item.text().strip()
        if not attr_name:
            return

        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        primary = nodes[0]
        attr_type = self.controller.get_attr_type(primary, attr_name)
        if attr_type not in self._SCRUBBABLE_TYPES:
            return

        is_int = attr_type in {"long", "short", "byte", "int"}
        mods = QtWidgets.QApplication.keyboardModifiers()
        delta = self._wheel_step(mods, is_int) * steps

        # Detect an open editor on the wheel-scrubbed cell.  If found,
        # treat its text as the source of truth.
        editor = None
        if hasattr(widget, "active_editor"):
            candidate = widget.active_editor()
            cur_idx = widget.currentIndex()
            if (
                isinstance(candidate, QtWidgets.QLineEdit)
                and cur_idx.isValid()
                and cur_idx.row() == row
                and cur_idx.column() == col
            ):
                editor = candidate

        if editor is not None:
            # ----- Edit-mode path -----
            # Editor provides primary's source-of-truth value (it may
            # include un-committed user edits).  Other selected nodes
            # apply the delta to their *own* current Maya values so
            # multi-select divergence is preserved — same per-node
            # semantics as the display-mode path below.
            try:
                primary_cur = float(editor.text())
            except (TypeError, ValueError):
                # Editor text isn't numeric (mid-typing, etc.) — fall
                # back to Maya's current value for primary.
                cur_raw = self.controller.get_attr_value(primary, attr_name)
                try:
                    primary_cur = float(cur_raw) if cur_raw is not None else 0.0
                except (TypeError, ValueError):
                    primary_cur = 0.0

            new_primary = primary_cur + delta
            if is_int:
                new_primary = int(round(new_primary))
            new_text = self.controller.format_value(new_primary)

            # Update the editor first so the user sees the change
            # immediately, regardless of what happens to the model.
            cursor_from_end = len(editor.text()) - editor.cursorPosition()
            editor.blockSignals(True)
            try:
                editor.setText(new_text)
            finally:
                editor.blockSignals(False)
            editor.setCursorPosition(max(0, len(new_text) - cursor_from_end))

            # Apply per-node — primary takes the editor-derived value,
            # others get ``their_cur + delta`` so divergence between
            # selected nodes is retained across wheel notches.
            cmds.undoInfo(openChunk=True, chunkName=f"Wheel {attr_name}")
            try:
                for node in nodes:
                    try:
                        if not cmds.attributeQuery(attr_name, node=node, exists=True):
                            continue
                        if cmds.getAttr(f"{node}.{attr_name}", lock=True):
                            continue
                        if node == primary:
                            new_val = new_primary
                        else:
                            cur = cmds.getAttr(f"{node}.{attr_name}")
                            if not isinstance(cur, (int, float)):
                                continue
                            new_val = cur + delta
                            if is_int:
                                new_val = int(round(new_val))
                        cmds.setAttr(f"{node}.{attr_name}", new_val)
                    except Exception:
                        continue
            finally:
                cmds.undoInfo(closeChunk=True)
            return

        # ----- Display-mode path -----
        # Pre-resolve targets so we don't open an empty undo chunk if
        # nothing is going to change.
        targets = []  # list of (node, current_value)
        for node in nodes:
            try:
                if not cmds.attributeQuery(attr_name, node=node, exists=True):
                    continue
                if cmds.getAttr(f"{node}.{attr_name}", lock=True):
                    continue
                cur = cmds.getAttr(f"{node}.{attr_name}")
                if not isinstance(cur, (int, float)):
                    continue
                targets.append((node, cur))
            except Exception:
                continue

        if not targets:
            return

        cmds.undoInfo(openChunk=True, chunkName=f"Wheel {attr_name}")
        try:
            for node, cur in targets:
                new_val = cur + delta
                if is_int:
                    new_val = int(round(new_val))
                try:
                    cmds.setAttr(f"{node}.{attr_name}", new_val)
                except Exception:
                    continue
        finally:
            cmds.undoInfo(closeChunk=True)

        # Refresh the cell text synchronously so the user sees the
        # change without waiting for the async callback to flush.
        try:
            self._on_attr_value_set(widget, attr_name)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ScriptJob lifecycle
    # ------------------------------------------------------------------

    def _setup_scene_change_callbacks(self, widget):
        """Register all event subscriptions through ``ScriptJobManager``.

        SJM unifies cleanup for both ``pm.scriptJob`` events and
        ``MMessage`` callbacks, so a single ``unsubscribe_all(owner=self)``
        call (triggered by widget destruction or
        :meth:`cleanup_scene_callbacks`) tears them all down.

        Per-node attribute callbacks are tracked separately via
        :attr:`_attr_change_tokens` because they are re-registered after
        every selection change.
        """
        self.cleanup_scene_callbacks()
        # ``cleanup_scene_callbacks`` raises the ``_destroyed`` guard so
        # any in-flight deferreds short-circuit; re-arm it here so the
        # freshly-registered callbacks actually run.
        self._destroyed = False

        mgr = ScriptJobManager.instance()
        for event in ("SelectionChanged", "SceneOpened", "NewSceneOpened"):
            mgr.subscribe(
                event,
                lambda w=widget: self._on_scene_change(w),
                owner=self,
            )
        # Time changes don't fire AttributeChanged callbacks for animated
        # attrs, so subscribe separately and run the lightweight values-only
        # updater (no full table rebuild — preserves selection / scroll).
        mgr.subscribe(
            "timeChanged",
            lambda w=widget: self._on_time_changed(w),
            owner=self,
        )
        mgr.connect_cleanup(widget, owner=self)

        # Global DG connection-changed callback — managed by SJM so it
        # tears down with the rest of our subscriptions.
        try:
            import maya.api.OpenMaya as om2

            def _on_connection_change(src_plug, dst_plug, made, *args):
                # Defensive wrap: any exception escaping here enters
                # Maya's DG callback pipeline and can destabilize it.
                try:
                    if self._destroyed:
                        return
                    self._on_scene_change(widget)
                except Exception:
                    pass

            mgr.add_om_callback(
                om2.MDGMessage.addConnectionCallback,
                _on_connection_change,
                owner=self,
            )
        except ImportError:
            pass

        # Per-node attribute-added/removed and value-changed callbacks.
        self._attr_change_tokens = []
        self._register_attr_change_callbacks(widget)

    def _register_attr_change_callbacks(self, widget):
        """Register per-node attribute callbacks via ``ScriptJobManager``.

        - ``MNodeMessage.addAttributeAddedOrRemovedCallback`` detects when
          custom attributes are added/removed on the selected nodes.
        - ``MNodeMessage.addAttributeChangedCallback`` detects value
          changes (e.g. from the channel box) so the table stays in sync.

        Re-called after every selection change.  Tokens are tracked in
        :attr:`_attr_change_tokens` so they can be cleared independently
        of the persistent global subscriptions.
        """
        self._cleanup_attr_change_callbacks()

        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        try:
            import maya.api.OpenMaya as om2
        except ImportError:
            return

        # Message bits we care about on the value/lock callback.
        # Lock changes use distinct bits from kAttributeSet — without
        # these, external lock toggles (channel box, other tools) never
        # reach the table and the icon goes stale.
        lock_mask = (
            om2.MNodeMessage.kAttributeLocked | om2.MNodeMessage.kAttributeUnlocked
        )
        set_mask = om2.MNodeMessage.kAttributeSet

        def _on_attr_added_removed(msg, plug, *args):
            # Wrap defensively: exceptions escaping into Maya's API 2.0
            # callback pipeline can destabilize the DG.
            try:
                if self._destroyed:
                    return
                self._on_scene_change(widget)
            except Exception:
                pass

        def _on_attr_value_changed(msg, plug, other_plug, *args):
            # Mutating Qt widgets synchronously from an MNodeMessage
            # callback (which fires on the DG evaluation path) is a
            # known crash vector during playback / batch edits.  We
            # enqueue the touched attribute name and schedule a single
            # coalesced flush on the idle loop — bursts of hundreds of
            # kAttributeSet messages (value drags) collapse into one
            # UI pass instead of flooding the deferred queue.
            try:
                if self._destroyed:
                    return
                try:
                    attr_name = plug.partialName(useLongNames=True)
                except Exception:
                    return
                touched = False
                if msg & lock_mask:
                    self._pending_lock_attrs.add(attr_name)
                    touched = True
                if msg & set_mask:
                    self._pending_value_attrs.add(attr_name)
                    touched = True
                if touched:
                    self._schedule_attr_flush(widget)
            except Exception:
                pass

        # Matrix-modified fires during interactive viewport manipulator
        # drags (transform tools bypass setAttr and write the data block
        # directly, so kAttributeSet only arrives on mouse-release).
        # Routed through a values-only refresh — preserves selection /
        # scroll and coalesces drag bursts into one UI pass per idle.
        def _on_matrix_modified(*_args):
            try:
                if self._destroyed:
                    return
                self._schedule_values_only_refresh(widget)
            except Exception:
                pass

        mgr = ScriptJobManager.instance()
        sel = om2.MSelectionList()
        for node_name in nodes:
            try:
                sel.clear()
                sel.add(node_name)
                mobj = sel.getDependNode(0)
            except Exception:
                continue
            for register_fn, callback in (
                (
                    om2.MNodeMessage.addAttributeAddedOrRemovedCallback,
                    _on_attr_added_removed,
                ),
                (om2.MNodeMessage.addAttributeChangedCallback, _on_attr_value_changed),
            ):
                token = mgr.add_om_callback(register_fn, mobj, callback, owner=self)
                if token is not None:
                    self._attr_change_tokens.append(token)

            # MDagMessage.addMatrixModifiedCallback wants an MDagPath;
            # only DAG nodes have one.  Non-DAG nodes (shaders, utility
            # nodes, …) skip silently.
            try:
                dag_path = om2.MDagPath.getAPathTo(mobj)
            except Exception:
                continue
            token = mgr.add_om_callback(
                om2.MDagMessage.addMatrixModifiedCallback,
                dag_path,
                _on_matrix_modified,
                owner=self,
            )
            if token is not None:
                self._attr_change_tokens.append(token)

    def _is_widget_alive(self, widget):
        """Return ``True`` if *widget*'s C++ pointer is still valid.

        Qt wrappers survive their underlying QObject destruction; any
        access then raises ``RuntimeError``.  We probe a cheap attribute
        (``rowCount``) so the check never mutates state.
        """
        if widget is None:
            return False
        try:
            widget.rowCount()
            return True
        except RuntimeError:
            return False
        except Exception:
            return False

    def _schedule_attr_flush(self, widget):
        """Schedule a single coalesced flush of pending attribute updates.

        Repeated calls while a flush is already pending are no-ops —
        hundreds of kAttributeSet bursts collapse into one UI pass.
        """
        if self._attr_flush_pending or self._destroyed:
            return
        self._attr_flush_pending = True
        cmds.evalDeferred(lambda w=widget: self._flush_attr_updates(w))

    def _flush_attr_updates(self, widget):
        """Drain pending value / lock updates in a single pass.

        Called from ``evalDeferred``; defensive throughout because the
        widget may have been destroyed and because we never want an
        exception to reach Maya's main event loop.

        If a full ``_refresh_table`` is currently running, drop the
        pending queues and bail — the rebuild reads fresh state from
        Maya for every visible attr, so any queued updates are
        superseded.  Running the flush concurrently with the rebuild
        would race on cell-widget state (``widget.actions.set`` calls
        from both paths on the same rows).
        """
        self._attr_flush_pending = False

        if self._destroyed:
            return
        if self._refreshing:
            # Refresh will pick up fresh values — drop superseded
            # updates.  New callbacks firing after refresh completes
            # will schedule their own flush.
            self._pending_value_attrs.clear()
            self._pending_lock_attrs.clear()
            return
        if not self._is_widget_alive(widget):
            self.cleanup_scene_callbacks()
            return

        # Snapshot-and-clear so late-arriving callbacks can queue up
        # again without being lost mid-iteration.
        lock_attrs = list(self._pending_lock_attrs)
        self._pending_lock_attrs.clear()
        value_attrs = list(self._pending_value_attrs)
        self._pending_value_attrs.clear()

        for name in lock_attrs:
            try:
                self._on_attr_lock_changed(widget, name)
            except RuntimeError:
                self.cleanup_scene_callbacks()
                return
            except Exception:
                pass

        for name in value_attrs:
            try:
                self._on_attr_value_set(widget, name)
            except RuntimeError:
                self.cleanup_scene_callbacks()
                return
            except Exception:
                pass

    def _on_attr_value_set(self, widget, attr_name):
        """Update the table cell for a single attribute whose value just changed.

        For enum attributes with a combobox widget this updates the
        combobox index directly (no full rebuild).  For other types it
        updates the cell text.

        Invoked via ``evalDeferred`` from an MNodeMessage callback, so
        the widget may have been destroyed between schedule and dispatch;
        all widget access is guarded.
        """
        # Skip echo when we ourselves just set the value from the combobox.
        if getattr(self, "_combo_setting", False):
            return

        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        primary = nodes[0]

        try:
            row_count = widget.rowCount()
        except RuntimeError:
            self.cleanup_scene_callbacks()
            return

        # Find the table row for this attribute.
        try:
            for row in range(row_count):
                name_item = widget.item(row, self.COL_NAME)
                if not name_item or name_item.text().strip() != attr_name:
                    continue

                combo = widget.cellWidget(row, self.COL_VALUE)
                if combo is not None:
                    # Enum combobox — update index without re-firing our signal.
                    try:
                        maya_idx = cmds.getAttr(f"{primary}.{attr_name}")
                        maya_indices = combo.property("_maya_indices") or []
                        if maya_idx in maya_indices:
                            pos = maya_indices.index(maya_idx)
                        else:
                            pos = 0
                        combo.blockSignals(True)
                        combo.setCurrentIndex(pos)
                        combo.blockSignals(False)
                    except Exception:
                        pass
                else:
                    # Plain text cell — update displayed value.
                    attr_type = self.controller.get_attr_type(primary, attr_name)
                    if attr_type == "enum":
                        val_str = (
                            self.controller.get_enum_label(primary, attr_name) or ""
                        )
                    else:
                        val = self.controller.get_attr_value(primary, attr_name)
                        val_str = self.controller.format_value(val)
                    cell = widget.item(row, self.COL_VALUE)
                    if cell:
                        widget.blockSignals(True)
                        cell.setText(val_str)
                        widget.blockSignals(False)
                break
        except RuntimeError:
            self.cleanup_scene_callbacks()

    def _on_attr_lock_changed(self, widget, attr_name):
        """Update the Lock action-column icon for *attr_name*.

        Driven by ``kAttributeLocked`` / ``kAttributeUnlocked`` messages so
        external lock toggles (channel box, scripts, other tools) keep
        the Channels UI's lock icon in sync.  No full rebuild —
        preserves selection and scroll.
        """
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        primary = nodes[0]

        try:
            locked = cmds.getAttr(f"{primary}.{attr_name}", lock=True)
        except Exception:
            return

        state = "locked" if locked else "unlocked"
        try:
            row_count = widget.rowCount()
            for row in range(row_count):
                name_item = widget.item(row, self.COL_NAME)
                if name_item and name_item.text().strip() == attr_name:
                    if widget.actions.get(row, self.COL_LOCK) != state:
                        widget.actions.set(row, self.COL_LOCK, state)
                    break
        except RuntimeError:
            self.cleanup_scene_callbacks()

    def _cleanup_attr_change_callbacks(self):
        """Remove per-node attribute callbacks (SJM-managed)."""
        tokens = getattr(self, "_attr_change_tokens", None)
        if tokens:
            mgr = ScriptJobManager.instance()
            for token in tokens:
                mgr.unsubscribe(token)
        self._attr_change_tokens = []

    def _on_time_changed(self, widget):
        """Lightweight refresh for ``timeChanged`` — values + key state only.

        Skips type/lock detection and does NOT rebuild the table; updates
        existing rows in place so the user's selection and scroll position
        are preserved during scrubbing.
        """
        self._schedule_values_only_refresh(widget)

    def _schedule_values_only_refresh(self, widget):
        """Schedule a single deferred values-only update of the table.

        Shared by ``timeChanged`` and viewport matrix-modified callbacks
        — both want the same in-place refresh that preserves selection /
        scroll.  Coalesces repeated calls (drag bursts collapse into one
        UI pass per Qt event-loop tick).

        Uses ``QTimer.singleShot(0, ...)`` rather than
        ``cmds.evalDeferred`` because Maya's idle queue does *not* drain
        during interactive viewport tool drags (Move/Rotate/Scale
        manipulators) — values stay frozen until mouse-release.  Qt's
        event loop *does* tick during drags (the viewport repaints
        through it), so the Qt path keeps cells live during the drag.
        """
        if getattr(self, "_values_refresh_pending", False) or self._destroyed:
            return
        self._values_refresh_pending = True

        def _do():
            self._values_refresh_pending = False
            if self._destroyed:
                return
            if not self._is_widget_alive(widget):
                self.cleanup_scene_callbacks()
                return
            try:
                self._update_values_only(widget)
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _do)

    def _update_values_only(self, widget):
        """Update value cells and connection-state icons for current rows.

        All widget access is wrapped because a stale C++ pointer (the Qt
        wrapper survives but the underlying object is gone) raises
        ``RuntimeError`` rather than evaluating falsy — so a simple
        ``if not widget`` check would let the failure escape.
        """
        try:
            row_count = widget.rowCount()
        except RuntimeError:
            # Widget was destroyed between schedule and dispatch.
            self.cleanup_scene_callbacks()
            return

        nodes = self.controller.get_selected_nodes()
        if not nodes or row_count == 0:
            return

        try:
            # Collect attribute names already shown in the table.
            attr_names = []
            for row in range(row_count):
                name_item = widget.item(row, self.COL_NAME)
                if name_item:
                    name = name_item.text().strip()
                    if name:
                        attr_names.append(name)
            if not attr_names:
                return

            data = self.controller.collect_value_strings(nodes, attr_names)

            widget.blockSignals(True)
            try:
                for row in range(row_count):
                    name_item = widget.item(row, self.COL_NAME)
                    if not name_item:
                        continue
                    attr_name = name_item.text().strip()
                    if attr_name not in data:
                        continue
                    val_str, conn_type = data[attr_name]

                    # Value cell — only update plain text cells; enum
                    # comboboxes are kept current via the
                    # AttributeChanged callback path.
                    if widget.cellWidget(row, self.COL_VALUE) is None:
                        cell = widget.item(row, self.COL_VALUE)
                        if cell and cell.text() != val_str:
                            cell.setText(val_str)

                    # Connection / key state — refresh the action icon so
                    # the "key at current time" colour follows the time
                    # slider.
                    if widget.actions.get(row, self.COL_CONN) != conn_type:
                        widget.actions.set(row, self.COL_CONN, conn_type)
            finally:
                widget.blockSignals(False)
        except RuntimeError:
            # Widget destroyed mid-update — silently drop.
            self.cleanup_scene_callbacks()

    def _on_scene_change(self, widget):
        """Debounced callback for scriptJob events."""
        if self._refresh_pending or self._destroyed:
            return
        self._refresh_pending = True

        def _do_refresh():
            self._refresh_pending = False
            if self._destroyed:
                return
            if not self._is_widget_alive(widget):
                self.cleanup_scene_callbacks()
                return

            try:
                self._refresh_table(widget)
                # Re-register per-node callbacks for the (possibly new)
                # selection.
                self._register_attr_change_callbacks(widget)
                # Reconnect the CB signal — the C++ pointer may have
                # changed.
                self._connect_cb_signal()
            except RuntimeError:
                self.cleanup_scene_callbacks()
            except Exception:
                # Defensive — anything escaping here is a no-op on the
                # idle loop; surfacing traces from a scriptJob is
                # unhelpful to the user.
                pass

        cmds.evalDeferred(_do_refresh)

    def cleanup_scene_callbacks(self):
        """Tear down every event subscription owned by this slots instance.

        SJM unifies both ``pm.scriptJob`` events and ``MMessage`` callbacks
        under one ``owner=self`` grouping, so a single
        :meth:`ScriptJobManager.unsubscribe_all` call handles them all.
        The Channel Box's Qt selection signal is the only thing managed
        outside SJM and is disconnected separately.
        """
        # Flip the guard *first* — any deferred callbacks that fire
        # between now and the actual unsubscribe will early-return.
        self._destroyed = True

        # Disconnect the Channel Box Qt signal (not an SJM event).
        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            ChannelBox.disconnect_selection_changed(self._on_cb_selection_changed)
        except Exception:
            pass
        self._cb_signal_connected = False

        # Clear per-node attr token list so a stale list doesn't linger.
        self._attr_change_tokens = []

        # Drop any buffered attribute updates so a late flush doesn't
        # fire against a torn-down widget.
        self._pending_value_attrs.clear()
        self._pending_lock_attrs.clear()

        # One call removes every script-job event AND every MMessage
        # callback registered with ``owner=self``.
        try:
            ScriptJobManager.instance().unsubscribe_all(self)
        except Exception:
            pass

    def __del__(self):
        self.cleanup_scene_callbacks()

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _setup_compact_footer(self):
        """Wire the footer for compact-mode use.

        - Adds a QLineEdit as a third page in the footer's stacked widget
          so double-clicking the footer label allows inline node rename.
        - Adds a target-object toggle button visible only in compact mode.
        """
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return

        # -- Inline rename QLineEdit (page 2 of footer stacked widget) --------
        try:
            from qtpy import QtWidgets as _QW, QtCore as _QC

            le = _QW.QLineEdit()
            le.setFrame(False)
            le.setStyleSheet("QLineEdit { background: transparent; border: none; }")
            footer._stacked_widget.addWidget(le)
            self._footer_lineedit = le
            self._footer_edit_page = footer._stacked_widget.indexOf(le)
            le.editingFinished.connect(self._on_footer_edit_finished)

            def _footer_le_key_press(event, _orig=le.keyPressEvent):
                if event.key() == _QC.Qt.Key_Escape:
                    # Block signals so clearFocus() doesn't fire editingFinished
                    # (which would commit as a rename — the opposite of cancel).
                    le.blockSignals(True)
                    try:
                        footer._stacked_widget.setCurrentIndex(0)
                    except Exception:
                        pass
                    le.clearFocus()
                    le.blockSignals(False)
                    return
                _orig(event)

            le.keyPressEvent = _footer_le_key_press
        except Exception:
            pass

        # -- Double-click on footer label enters rename mode ------------------
        try:

            _orig_lbl_dbl = footer._status_label.mouseDoubleClickEvent

            def _footer_label_dbl_click(event, _orig=_orig_lbl_dbl):
                if self._compact_view and self._current_target is not None:
                    self._enter_footer_edit_mode()
                    return
                _orig(event)

            footer._status_label.mouseDoubleClickEvent = _footer_label_dbl_click
        except Exception:
            pass

        # -- Compact target-object toggle button ------------------------------
        try:
            self._footer_compact_btn = footer.add_action_button(
                icon_name="target",
                tooltip=(
                    "Multi-object mode — click to switch to single-object.\n"
                    "All edits are broadcast to every selected node."
                ),
                callback=self._on_footer_compact_btn_clicked,
            )
            self._footer_compact_btn.setVisible(False)
        except Exception:
            self._footer_compact_btn = None

    def _enter_footer_edit_mode(self):
        """Switch the footer to an inline QLineEdit for node rename."""
        footer = getattr(self.ui, "footer", None)
        if not footer or self._footer_lineedit is None or self._footer_edit_page < 0:
            return
        short = (self._current_target or "").rsplit("|", 1)[-1]
        self._footer_lineedit.blockSignals(True)
        self._footer_lineedit.setText(short)
        self._footer_lineedit.blockSignals(False)
        footer._stacked_widget.setCurrentIndex(self._footer_edit_page)
        self._footer_lineedit.selectAll()
        self._footer_lineedit.setFocus()

    def _on_footer_edit_finished(self):
        """Commit (or discard) a rename initiated via the footer QLineEdit."""
        footer = getattr(self.ui, "footer", None)
        if footer:
            try:
                footer._stacked_widget.setCurrentIndex(0)
            except Exception:
                pass

        if not self._current_target or self._footer_lineedit is None:
            return
        new_name = self._footer_lineedit.text().strip()
        old_short = self._current_target.rsplit("|", 1)[-1]
        if not new_name or new_name == old_short:
            if self._footer_controller:
                self._footer_controller.update()
            return

        new_full = self.controller.rename_node(self._current_target, new_name)
        if new_full and new_full != self._current_target:
            self._current_target = new_full
            if self.controller.is_pinned:
                self.controller.pin_targets([new_full])
        self._refresh_table(self.ui.tbl000)

    def _on_footer_compact_btn_clicked(self):
        """Toggle single/multi-object mode from the compact footer button."""
        self._on_toggle_single_object(not self.controller.single_object_mode)

    def _update_compact_btn_state(self):
        """Refresh the compact footer button icon colour to reflect the current mode."""
        btn = self._footer_compact_btn
        if btn is None:
            return
        clr = self.ACTION_COLOR_MAP
        try:
            from uitk.widgets.mixins.icon_manager import IconManager

            color = clr["active"] if self.controller.single_object_mode else clr["off"]
            h = max(btn.height(), 14)
            size = (int(h * 0.7), int(h * 0.7))
            IconManager.set_icon(btn, "target", size=size, color=color)
        except Exception:
            pass
        tip = (
            "Single-object mode — click to switch to multi-object.\n"
            "Only the most recently selected object is edited."
            if self.controller.single_object_mode
            else "Multi-object mode — click to switch to single-object.\n"
            "All edits are broadcast to every selected node."
        )
        btn.setToolTip(tip)

    def _create_footer_controller(self):
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        return FooterStatusController(
            footer=footer,
            resolver=self._resolve_footer_text,
            default_text="",
            truncate_kwargs={"length": 96, "mode": "middle"},
        )

    def _resolve_footer_text(self) -> str:
        """In compact mode show the node name (mirrors txt001); otherwise show warnings."""
        if self._footer_warning:
            return self._footer_warning
        if self._compact_view:
            try:
                return self.ui.txt001.text()
            except Exception:
                pass
        return ""
