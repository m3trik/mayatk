# !/usr/bin/python
# coding=utf-8
"""UI slots for the Attribute Manager.

``AttributeManagerSlots`` — a single-table Switchboard interface for
inspecting, editing, locking, and managing Maya node attributes.
Delegates all non-UI logic to :class:`AttributeManager`.
"""
import maya.cmds as cmds
import maya.mel as mel

from uitk.widgets.footer import FooterStatusController
from uitk.widgets.widgetComboBox import WidgetComboBox
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.node_utils.attributes.attribute_manager._attribute_manager import (
    AttributeManager,
)

import pythontk as ptk


class AttributeManagerSlots:
    """Switchboard slots for the Attribute Manager UI.

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
    # Desaturated Maya channel-box colour scheme.
    ACTION_COLOR_MAP = {
        "off": "#555555",  # dim grey — inactive / default
        "locked": "#8a9bb0",  # bluish grey — lock icon
        "keyframe": "#c86464",  # desaturated red — keyed
        "connected": "#c8b448",  # desaturated yellow — generic connection
        "expression": "#b478c8",  # desaturated purple — expression-driven
        "driven_key": "#6898b8",  # desaturated light-blue — set-driven key
        "constraint": "#5878b8",  # desaturated blue — constraint
        "muted": "#888850",  # olive — muted channel
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.attribute_manager
        self.controller = AttributeManager()
        self._refresh_pending = False
        self._footer_controller = self._create_footer_controller()

        # Force-connect table selection signal (in case tbl000_init guard skipped it)
        try:
            self.ui.tbl000.itemSelectionChanged.disconnect(
                self._on_table_selection_changed
            )
        except Exception:
            pass
        self.ui.tbl000.itemSelectionChanged.connect(self._on_table_selection_changed)

        # Channel Box → Table sync via Qt signal (instant, replaces polling)
        self._last_cb_selection = set()
        self._cb_signal_connected = False
        self._syncing_selection = False
        self._connect_cb_signal()

        self._combo_setting = False

        # Debounced refresh for the text filter.
        from uitk.widgets.header import QtCore

        self._filter_timer = QtCore.QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(lambda: self._refresh_table(self.ui.tbl000))
        self.ui.txt000.textChanged.connect(lambda _: self._filter_timer.start())
        self.ui.txt000.option_box.clear_option = True

        # Stop timer when the UI is destroyed to avoid dangling callbacks.
        self.ui.destroyed.connect(self._filter_timer.stop)

    def apply_launch_config(self, targets=None, filter=None, search=None):
        """Configure the window from a :func:`launch` call.

        Safe to call repeatedly — applies pin/filter/search to the
        already-constructed UI.  Pass ``targets=None`` to clear a pin.
        """
        self.controller.pin_targets(targets)

        if filter:
            if filter in AttributeManager.FILTER_MAP:
                cmb = getattr(self.ui, "cmb000", None)
                if cmb is not None:
                    cmb.setCurrentText(filter)
            else:
                import logging

                logging.getLogger(__name__).warning(
                    "launch(filter=%r) ignored — not a FILTER_MAP key. Valid: %s",
                    filter,
                    sorted(AttributeManager.FILTER_MAP.keys()),
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
            self.sb.registered_widgets.Label,
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
            setToolTip=(
                "Attribute Manager — Inspect, edit, and manage Maya node attributes.\n\n"
                "• Filter attributes by type: Custom, Keyable, Locked, Connected, etc.\n"
                "• Edit attribute values, lock/unlock, and toggle keyable state.\n"
                "• Create new custom attributes on selected objects.\n"
                "• Select Shape or History nodes from the header menu.\n"
                "• Open Maya's Channel Control or Connection Editor."
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
        """Show or hide the Type column in the attribute table."""
        self.ui.tbl000.setColumnHidden(self.COL_TYPE, not visible)

    # ------------------------------------------------------------------
    # Filter ComboBox
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Populate filter combobox."""
        widget.addItems(
            [
                k
                for k in AttributeManager.FILTER_MAP.keys()
                if not k.startswith("_")
            ]
        )

    def cmb000(self, index):
        """Filter changed — refresh table."""
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Invert Checkbox
    # ------------------------------------------------------------------

    def chk000(self, state):
        """Invert checkbox toggled — refresh table."""
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        """One-time table setup: signals, context menu, scriptJobs."""
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self._handle_cell_edit)

            self._setup_action_columns(widget)
            self._setup_context_menu(widget)
            self._setup_scene_change_callbacks(widget)

            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)
            widget.itemSelectionChanged.connect(self._on_table_selection_changed)

            try:
                widget.destroyed.connect(self.cleanup_scene_callbacks)
            except Exception:
                pass

        self._refresh_table(widget)

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

        conn_states = {
            "none": {
                "icon": "disconnect",
                "color": clr["off"],
                "tooltip": "Not connected",
                "action": self._on_icon_cell_clicked,
            },
        }
        for key in (
            "connected",
            "keyframe",
            "expression",
            "driven_key",
            "constraint",
            "muted",
        ):
            conn_states[key] = {
                "icon": "connect",
                "color": clr.get(key, clr["connected"]),
                "tooltip": f"{key.replace('_', ' ').title()} — click to break",
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

        chk = getattr(self.ui, "chk000", None)
        invert = bool(chk and chk.isChecked())

        return self.controller.get_filter_kwargs(key, invert)

    def _refresh_table(self, widget):
        """Rebuild the table from the current selection and filter."""
        cmds.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.blockSignals(True)
            widget.clear()

            nodes = self.controller.get_selected_nodes()
            if not nodes:
                widget.add(
                    [["No selection", "", "", "", ""]],
                    headers=["Name", "", "", "Value", "Type"],
                )
                self._configure_columns(widget)
                return

            filter_kwargs = self._get_filter_kwargs()
            rows, attr_states = self.controller.build_table_data(nodes, filter_kwargs)

            # Apply wildcard text filter if the user typed something.
            pattern = getattr(self.ui, "txt000", None)
            if pattern and pattern.text().strip():
                text = pattern.text().strip()
                # Build name list from rows for filtering.
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

            # Replace enum value cells with comboboxes.
            self._setup_enum_combos(widget, nodes)

            # Sync table selection with channel box selection.
            # Fetch fresh CB data *before* syncing so the table reflects
            # the current state rather than a stale cache.
            self._last_cb_selection = self._normalize_cb_attrs(
                set(self.controller.get_channel_box_selection())
            )
            self._sync_table_to_channel_box(widget)

        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)
            cmds.waitCursor(state=False)

        # Restore column visibility from the Show Type checkbox.
        chk = getattr(self, "_chk_show_type", None)
        if chk is not None:
            widget.setColumnHidden(self.COL_TYPE, not chk.isChecked())

        if self._footer_controller:
            self._footer_controller.update()

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
        """Handle clicks on the Lock or Connect icon columns."""
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

        elif col == self.COL_CONN:
            # Only attempt to break if the attr is actually connected.
            state = tbl.actions.get(row, col)
            if state and state != "none":
                self.controller.break_connections(nodes, attr_name)
                self._refresh_table(tbl)

    def _configure_columns(self, widget):
        """Set column resize modes and widths."""
        header = widget.horizontalHeader()
        header.setSectionsMovable(False)
        QHV = self.sb.QtWidgets.QHeaderView

        # Data columns
        header.setSectionResizeMode(self.COL_NAME, QHV.Interactive)
        widget.setColumnWidth(self.COL_NAME, 160)

        # Remaining data columns
        header.setSectionResizeMode(self.COL_VALUE, QHV.Stretch)
        header.setSectionResizeMode(self.COL_TYPE, QHV.Interactive)
        widget.setColumnWidth(self.COL_TYPE, 80)

    # ------------------------------------------------------------------
    # Inline editing
    # ------------------------------------------------------------------

    def _set_name_editability(self, widget, nodes):
        """Make name cells editable for user-defined attrs.

        Stores the original attribute name in ``Qt.UserRole`` so
        ``_handle_cell_edit`` can detect rename attempts.
        """
        Qt = self.sb.QtCore.Qt
        primary = nodes[0] if nodes else None
        user_attrs = (
            set(cmds.listAttr(primary, userDefined=True) or []) if primary else set()
        )

        for row_idx in range(widget.rowCount()):
            item = widget.item(row_idx, self.COL_NAME)
            if not item:
                continue
            attr_name = item.text().strip()
            # Store original name for rename detection.
            item.setData(Qt.UserRole, attr_name)
            if attr_name in user_attrs:
                item.setFlags(item.flags() | Qt.ItemIsEditable)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

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
        """Handle inline editing of the Name or Value column."""
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
        # If a cell widget (combobox) owns this cell, skip text handling.
        if val_item is None or tbl.cellWidget(row, col) is not None:
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
        if self._syncing_selection:
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
                self._last_cb_selection = current_sel
                self._sync_table_to_channel_box(self.ui.tbl000)
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
    # ScriptJob lifecycle
    # ------------------------------------------------------------------

    def _setup_scene_change_callbacks(self, widget):
        """Register scene-change subscriptions and OpenMaya callbacks.

        Uses ``MDGMessage.addConnectionCallback`` to detect connection
        changes (make / break) so the table updates its color-coded
        icons immediately.
        """
        self.cleanup_scene_callbacks()

        mgr = ScriptJobManager.instance()
        for event in ("SelectionChanged", "SceneOpened", "NewSceneOpened"):
            mgr.subscribe(
                event,
                lambda w=widget: self._on_scene_change(w),
                owner=self,
            )
        mgr.connect_cleanup(widget, owner=self)

        # --- OpenMaya connection callback ---
        try:
            import maya.api.OpenMaya as om2

            def _on_connection_change(src_plug, dst_plug, made, *args):
                """Fires when any DG connection is made or broken."""
                self._on_scene_change(widget)

            cb_id = om2.MDGMessage.addConnectionCallback(_on_connection_change)
            self._om_callback_ids = [cb_id]
        except Exception:
            self._om_callback_ids = []

        # --- Per-node attribute-added/removed callbacks ---
        self._node_attr_callback_ids = []
        self._register_attr_change_callbacks(widget)

    def _register_attr_change_callbacks(self, widget):
        """Register per-node attribute-added/removed and value-changed callbacks.

        Uses ``MNodeMessage.addAttributeAddedOrRemovedCallback`` to detect
        when custom attributes are created or deleted on the selected nodes,
        and ``MNodeMessage.addAttributeChangedCallback`` to detect value
        changes (e.g. via channel box) so enum comboboxes stay in sync.
        Re-called after every selection change to track the new selection.
        """
        self._cleanup_attr_change_callbacks()

        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return

        try:
            import maya.api.OpenMaya as om2

            def _on_attr_added_removed(msg, plug, *args):
                self._on_scene_change(widget)

            def _on_attr_value_changed(msg, plug, other_plug, *args):
                # Only react to value-set messages.
                if not (msg & om2.MNodeMessage.kAttributeSet):
                    return
                self._on_attr_value_set(widget, plug)

            sel = om2.MSelectionList()
            for node_name in nodes:
                try:
                    sel.clear()
                    sel.add(node_name)
                    mobj = sel.getDependNode(0)
                    cb_id = om2.MNodeMessage.addAttributeAddedOrRemovedCallback(
                        mobj, _on_attr_added_removed
                    )
                    self._node_attr_callback_ids.append(cb_id)
                    cb_id2 = om2.MNodeMessage.addAttributeChangedCallback(
                        mobj, _on_attr_value_changed
                    )
                    self._node_attr_callback_ids.append(cb_id2)
                except Exception:
                    pass
        except ImportError:
            pass

    def _on_attr_value_set(self, widget, plug):
        """Update the table cell for a single attribute whose value just changed.

        For enum attributes with a combobox widget this updates the
        combobox index directly (no full rebuild).  For other types it
        updates the cell text.
        """
        # Skip echo when we ourselves just set the value from the combobox.
        if getattr(self, "_combo_setting", False):
            return

        attr_name = plug.partialName(useLongNames=True)
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return
        primary = nodes[0]

        # Find the table row for this attribute.
        for row in range(widget.rowCount()):
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
                    val_str = self.controller.get_enum_label(primary, attr_name) or ""
                else:
                    val = self.controller.get_attr_value(primary, attr_name)
                    val_str = self.controller.format_value(val)
                cell = widget.item(row, self.COL_VALUE)
                if cell:
                    widget.blockSignals(True)
                    cell.setText(val_str)
                    widget.blockSignals(False)
            break

    def _cleanup_attr_change_callbacks(self):
        """Remove per-node attribute-added/removed callbacks."""
        ids = getattr(self, "_node_attr_callback_ids", [])
        if not ids:
            return
        try:
            import maya.api.OpenMaya as om2

            for cb_id in ids:
                try:
                    om2.MMessage.removeCallback(cb_id)
                except Exception:
                    pass
        except ImportError:
            pass
        self._node_attr_callback_ids = []

    def _on_scene_change(self, widget):
        """Debounced callback for scriptJob events."""
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def _do_refresh():
            self._refresh_pending = False
            try:
                # If widget is dead or hidden, skip refresh
                if not widget:  # or not widget.isVisible():
                    return
            except Exception:
                # Widget likely destroyed
                self.cleanup_scene_callbacks()
                return

            self._refresh_table(widget)

            # Re-register per-node callbacks for the (possibly new) selection.
            self._register_attr_change_callbacks(widget)

            # Reconnect the CB signal — the C++ pointer may have changed.
            self._connect_cb_signal()

        cmds.evalDeferred(_do_refresh)

    def cleanup_scene_callbacks(self):
        """Remove ScriptJobManager subscriptions, OpenMaya callbacks, and
        disconnect the Channel Box selection signal."""
        # Disconnect CB signal
        try:
            from mayatk.ui_utils.channel_box import ChannelBox

            ChannelBox.disconnect_selection_changed(self._on_cb_selection_changed)
        except Exception:
            pass
        self._cb_signal_connected = False

        # Clean up per-node attr callbacks first (synchronous)
        self._cleanup_attr_change_callbacks()

        # Unsubscribe from centralized manager
        ScriptJobManager.instance().unsubscribe_all(self)

        # Clean up OpenMaya callbacks
        om_ids = list(getattr(self, "_om_callback_ids", []))
        self._om_callback_ids = []

        if om_ids:

            def _kill_om(cb_ids):
                try:
                    import maya.api.OpenMaya as om2

                    for cb_id in cb_ids:
                        om2.MMessage.removeCallback(cb_id)
                except Exception:
                    pass

            cmds.evalDeferred(lambda: _kill_om(om_ids))

    def __del__(self):
        self.cleanup_scene_callbacks()

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

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
        nodes = self.controller.get_selected_nodes()
        if not nodes:
            return "No selection"
        names = ", ".join(n.rsplit("|", 1)[-1] for n in nodes[:3])
        suffix = f" (+{len(nodes) - 3})" if len(nodes) > 3 else ""
        return f"{names}{suffix}"
