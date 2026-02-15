# !/usr/bin/python
# coding=utf-8
"""UI slots for the Attribute Manager.

Provides ``AttributeManagerSlots`` — a single-table interface for
inspecting, editing, locking, and managing Maya node attributes.
Follows the ``TexturePathEditorSlots`` pattern from ``mat_utils``.
"""
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from uitk.widgets.footer import FooterStatusController

from mayatk.node_utils.attribute_manager._attribute_manager import AttributeManager


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

    # Icon colors — active (on) vs. inactive (off)
    _CLR_LOCK_ON = "#e8c44a"  # amber
    _CLR_LOCK_OFF = "#555555"  # dim grey
    _CLR_CONN_ON = "#5cc0f0"  # cyan-blue
    _CLR_CONN_OFF = "#555555"  # dim grey

    # Maps ComboBox items → kwargs for ``pm.listAttr``.
    # Use ``_custom_filter`` key for filters that need Python-side logic
    # beyond what ``pm.listAttr`` supports natively.
    _FILTER_MAP = {
        "Custom": {"userDefined": True},
        "Keyable": {"keyable": True},
        "Channel Box": {"_custom_filter": "channel_box"},
        "Locked": {"locked": True},
        "Connected": {"connected": True},
        "Settable": {"settable": True},
        "Visible": {"visible": True},
        "Animated": {"_custom_filter": "animated"},
        "All": {},
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.attribute_manager
        self._scene_change_job_ids = None
        self._refresh_pending = False
        self._footer_controller = self._create_footer_controller()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Populate the header menu with global actions."""
        widget.menu.setTitle("Actions:")

        # --- Create Attribute ---
        widget.menu.add("Separator", setTitle="Create")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Create Attribute …",
            setToolTip="Add a new custom attribute to the selected objects.",
            setObjectName="show_create_menu",
        )

    # --- Header action handlers ---

    def show_create_menu(self, *args):
        """Show the Create Attribute popup menu."""
        from uitk import Menu

        menu = Menu(parent=self.ui, position="cursor")
        menu.setTitle("Create Attribute")

        menu.add("QLabel", setText="Name:")
        le_name = menu.add(
            "QLineEdit",
            setPlaceholderText="my_attribute",
            setObjectName="le_attr_name",
        )
        menu.add("QLabel", setText="Type:")
        cmb_type = menu.add(
            "QComboBox",
            setObjectName="cmb_attr_type",
            addItems=["float", "int", "bool", "string", "enum", "double3"],
        )
        menu.add("QLabel", setText="Default:")
        spn_default = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_default",
            setMinimum=-1e9,
            setMaximum=1e9,
        )
        menu.add("QLabel", setText="Min:")
        spn_min = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_min",
            setMinimum=-1e9,
            setMaximum=1e9,
        )
        menu.add("QLabel", setText="Max:")
        spn_max = menu.add(
            "QDoubleSpinBox",
            setObjectName="spn_max",
            setMinimum=-1e9,
            setMaximum=1e9,
            setValue=1.0,
        )
        chk_keyable = menu.add(
            "QCheckBox",
            setText="Keyable",
            setChecked=True,
            setObjectName="chk_keyable",
        )

        def _on_type_changed(text):
            is_numeric = text in ("float", "int", "double3")
            spn_default.setEnabled(is_numeric)
            spn_min.setEnabled(is_numeric)
            spn_max.setEnabled(is_numeric)

        cmb_type.currentTextChanged.connect(_on_type_changed)
        _on_type_changed(cmb_type.currentText())

        btn = menu.add("QPushButton", setText="Create")

        def _on_create():
            name = le_name.text().strip()
            if not name:
                pm.warning("Attribute name cannot be empty.")
                return
            sel = pm.ls(sl=True)
            if not sel:
                pm.warning("Nothing selected.")
                return

            attr_type = cmb_type.currentText()
            kwargs = {name: attr_type}

            pm.undoInfo(openChunk=True, chunkName=f"Create Attr: {name}")
            try:
                for obj in sel:
                    kw = {name: attr_type}
                    AttributeManager.create_or_set(
                        obj, keyable=chk_keyable.isChecked(), **kw
                    )

                    # Apply min/max/default for numeric types
                    if attr_type in ("float", "int", "double3") and obj.hasAttr(name):
                        plug = obj.attr(name)
                        min_val = spn_min.value()
                        max_val = spn_max.value()
                        default_val = spn_default.value()

                        if min_val != 0.0 or max_val != 1.0:
                            try:
                                pm.addAttr(
                                    plug, edit=True, minValue=min_val, maxValue=max_val
                                )
                            except Exception:
                                pass
                        if default_val != 0.0:
                            try:
                                pm.addAttr(plug, edit=True, defaultValue=default_val)
                                pm.setAttr(plug, default_val)
                            except Exception:
                                pass
            finally:
                pm.undoInfo(closeChunk=True)

            menu.hide()
            self._refresh_table(self.ui.tbl000)

        btn.clicked.connect(_on_create)
        menu.show()

    # ------------------------------------------------------------------
    # Filter ComboBox
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Populate filter combobox."""
        widget.addItems([k for k in self._FILTER_MAP.keys() if not k.startswith("_")])

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
            # Register Lock and Connect as action columns.
            widget.actions.add(
                self.COL_LOCK,
                states={
                    "locked": {
                        "icon": "lock",
                        "color": self._CLR_LOCK_ON,
                        "tooltip": "Locked — click to unlock",
                        "action": self._on_icon_cell_clicked,
                    },
                    "unlocked": {
                        "icon": "unlock",
                        "color": self._CLR_LOCK_OFF,
                        "tooltip": "Unlocked — click to lock",
                        "action": self._on_icon_cell_clicked,
                    },
                },
            )
            widget.actions.add(
                self.COL_CONN,
                states={
                    "connected": {
                        "icon": "connect",
                        "color": self._CLR_CONN_ON,
                        "tooltip": "Connected — click to break",
                        "action": self._on_icon_cell_clicked,
                    },
                    "disconnected": {
                        "icon": "disconnect",
                        "color": self._CLR_CONN_OFF,
                        "tooltip": "Not connected",
                        "action": self._on_icon_cell_clicked,
                    },
                },
            )
            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)

            # --- Context menu ---
            widget.menu.setTitle("Attribute Actions:")

            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton",
                setText="Lock",
                setObjectName="ctx_lock",
                setToolTip="Lock the selected attribute(s).",
            )
            widget.menu.add(
                "QPushButton",
                setText="Unlock",
                setObjectName="ctx_unlock",
                setToolTip="Unlock the selected attribute(s).",
            )
            widget.menu.add(
                "QPushButton",
                setText="Reset to Default",
                setObjectName="ctx_reset_default",
                setToolTip="Reset the attribute to its default value.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Toggle Keyable",
                setObjectName="ctx_toggle_keyable",
                setToolTip="Toggle the keyable state of the attribute.",
            )

            widget.menu.add("Separator", setTitle="Manage")
            widget.menu.add(
                "QPushButton",
                setText="Delete Attribute",
                setObjectName="ctx_delete",
                setToolTip="Delete the selected custom attribute(s).",
            )

            def _bind(action_name, method):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=self._ROW_SELECTION_COLUMNS,
                )

            _bind("ctx_lock", self._ctx_lock)
            _bind("ctx_unlock", self._ctx_unlock)
            _bind("ctx_reset_default", self._ctx_reset_default)
            _bind("ctx_toggle_keyable", self._ctx_toggle_keyable)
            _bind("ctx_delete", self._ctx_delete)

            # --- ScriptJobs ---
            self._setup_scene_change_callbacks(widget)
            try:
                widget.destroyed.connect(self.cleanup_scene_callbacks)
            except Exception:
                pass

        self._refresh_table(widget)

    # ------------------------------------------------------------------
    # Table data
    # ------------------------------------------------------------------

    def _get_selected_nodes(self):
        """Return the current Maya selection as transforms/DG nodes."""
        return pm.ls(sl=True, long=True)

    def _get_filter_kwargs(self):
        """Return the ``pm.listAttr`` kwargs for the active filter.

        Includes ``_invert=True`` when the invert checkbox is checked.
        """
        cmb = getattr(self.ui, "cmb000", None)
        key = cmb.currentText() if cmb else "Custom"
        kwargs = self._FILTER_MAP.get(key, {}).copy()

        chk = getattr(self.ui, "chk000", None)
        if chk and chk.isChecked():
            kwargs["_invert"] = True

        return kwargs

    def _query_connected_attrs(self, node):
        """Return set of attribute names on *node* that have incoming connections."""
        conns = (
            pm.listConnections(
                node, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        result = set()
        for dst_plug, _src_plug in zip(conns[0::2], conns[1::2]):
            attr_name = str(dst_plug).split(".", 1)[-1]
            result.add(attr_name)
        return result

    def _collect_attr_names(self, nodes, filter_kwargs):
        """Return the intersection of attribute names across *nodes*."""
        if not nodes:
            return []

        custom_filter = filter_kwargs.pop("_custom_filter", None)
        invert = filter_kwargs.pop("_invert", False)

        if custom_filter == "channel_box":
            # Union of keyable attrs + non-keyable attrs shown in channel box
            sets_k = [set(pm.listAttr(n, keyable=True) or []) for n in nodes]
            sets_c = [set(pm.listAttr(n, channelBox=True) or []) for n in nodes]
            common = sets_k[0] | sets_c[0]
            for sk, sc in zip(sets_k[1:], sets_c[1:]):
                common &= sk | sc
        else:
            sets = [set(pm.listAttr(n, **filter_kwargs) or []) for n in nodes]
            common = sets[0]
            for s in sets[1:]:
                common &= s

        if custom_filter == "animated":
            # Keep only attributes connected to animation curves
            animated_attrs = set()
            for attr in list(common):
                is_animated = True
                for node in nodes:
                    try:
                        conns = pm.listConnections(
                            f"{node}.{attr}",
                            source=True,
                            destination=False,
                            type="animCurve",
                        )
                        if not conns:
                            is_animated = False
                            break
                    except Exception:
                        is_animated = False
                        break
                if is_animated:
                    animated_attrs.add(attr)
            common = animated_attrs

        if invert:
            # Invert: return all attrs minus the filtered set
            all_sets = [set(pm.listAttr(n) or []) for n in nodes]
            all_common = all_sets[0]
            for s in all_sets[1:]:
                all_common &= s
            common = all_common - common

        return sorted(common)

    def _get_attr_value(self, node, attr_name):
        """Safely get an attribute value, returning ``None`` on failure."""
        try:
            return pm.getAttr(f"{node}.{attr_name}")
        except Exception:
            return None

    def _get_attr_type(self, node, attr_name):
        """Return the Maya attribute type string."""
        try:
            return pm.attributeQuery(attr_name, node=node, attributeType=True)
        except Exception:
            return "?"

    def _get_incoming_connection(self, node, attr_name):
        """Return ``'→ src.attr'`` if there is an incoming connection, else ``''``."""
        try:
            conns = pm.listConnections(
                f"{node}.{attr_name}", source=True, destination=False, plugs=True
            )
            if conns:
                return f"→ {conns[0]}"
        except Exception:
            pass
        return ""

    def _refresh_table(self, widget):
        """Rebuild the table from the current selection and filter."""
        pm.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.blockSignals(True)
            widget.clear()

            nodes = self._get_selected_nodes()
            if not nodes:
                widget.add(
                    [["No selection", "", "", "", ""]],
                    headers=["Name", "", "", "Value", "Type"],
                )
                self._configure_columns(widget)
                return

            filter_kwargs = self._get_filter_kwargs()
            attr_names = self._collect_attr_names(nodes, filter_kwargs)

            primary = nodes[0]
            multi = len(nodes) > 1

            rows = []
            attr_states = []  # parallel list: (is_locked, is_connected) per row
            for attr_name in attr_names:
                # Value
                val = self._get_attr_value(primary, attr_name)
                if multi:
                    for other in nodes[1:]:
                        other_val = self._get_attr_value(other, attr_name)
                        if other_val != val:
                            val = "*"
                            break
                val_str = self._format_value(val)

                # Type
                attr_type = self._get_attr_type(primary, attr_name)

                # Locked
                try:
                    locked = pm.getAttr(f"{primary}.{attr_name}", lock=True)
                except Exception:
                    locked = False

                # Connected
                try:
                    conns = pm.listConnections(
                        f"{primary}.{attr_name}", source=True, destination=False
                    )
                    connected = bool(conns)
                except Exception:
                    connected = False

                rows.append([attr_name, "", "", val_str, attr_type])
                attr_states.append((locked, connected))

            if not rows:
                rows = [["", "", "", "", "No attributes"]]
                attr_states = [(False, False)]

            widget.add(rows, headers=["Name", "", "", "Value", "Type"])
            self._configure_columns(widget)

            # Set action states on existing items — no widget creation
            for row_idx, (is_locked, is_connected) in enumerate(attr_states):
                widget.actions.set(
                    row_idx,
                    self.COL_LOCK,
                    "locked" if is_locked else "unlocked",
                )
                widget.actions.set(
                    row_idx,
                    self.COL_CONN,
                    "connected" if is_connected else "disconnected",
                )

        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)
            pm.waitCursor(state=False)

        if self._footer_controller:
            self._footer_controller.update()

    def _on_icon_cell_clicked(self, row, col):
        """Handle clicks on the Lock or Connect icon columns."""
        tbl = self.ui.tbl000
        name_item = tbl.item(row, self.COL_NAME)
        if not name_item or not name_item.text():
            return
        attr_name = name_item.text().strip()
        nodes = self._get_selected_nodes()
        if not nodes:
            return

        if col == self.COL_LOCK:
            # Toggle lock state
            try:
                current = pm.getAttr(f"{nodes[0]}.{attr_name}", lock=True)
            except Exception:
                return
            new_state = not current
            pm.undoInfo(openChunk=True, chunkName="Toggle Lock")
            try:
                for node in nodes:
                    try:
                        pm.setAttr(f"{node}.{attr_name}", lock=new_state)
                    except Exception:
                        pass
            finally:
                pm.undoInfo(closeChunk=True)
            self._refresh_table(tbl)

        elif col == self.COL_CONN:
            # Break incoming connections
            has_conn = False
            for node in nodes:
                try:
                    conns = pm.listConnections(
                        f"{node}.{attr_name}",
                        source=True,
                        destination=False,
                        plugs=True,
                        connections=True,
                    )
                    if conns:
                        has_conn = True
                        break
                except Exception:
                    pass
            if not has_conn:
                return
            pm.undoInfo(openChunk=True, chunkName="Break Connection")
            try:
                for node in nodes:
                    try:
                        conns = pm.listConnections(
                            f"{node}.{attr_name}",
                            source=True,
                            destination=False,
                            plugs=True,
                            connections=True,
                        )
                        if conns:
                            for dst, src in zip(conns[0::2], conns[1::2]):
                                pm.disconnectAttr(src, dst)
                    except Exception:
                        pass
            finally:
                pm.undoInfo(closeChunk=True)
            self._refresh_table(tbl)

    @staticmethod
    def _format_value(val):
        """Convert a Maya attribute value to a display string."""
        if val == "*":
            return "*"
        if val is None:
            return ""
        if isinstance(val, float):
            return f"{val:.4f}"
        if isinstance(val, (list, tuple)):
            inner = ", ".join(
                f"{v:.4f}" if isinstance(v, float) else str(v) for v in val
            )
            return f"({inner})"
        return str(val)

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

    def _handle_cell_edit(self, row, col):
        """Handle double-click inline editing of the Value column."""
        if col != self.COL_VALUE:
            return

        tbl = self.ui.tbl000
        name_item = tbl.item(row, self.COL_NAME)
        if not name_item:
            return
        attr_name = name_item.text().strip()
        if not attr_name:
            return

        new_text = tbl.item(row, col).text().strip()
        nodes = self._get_selected_nodes()
        if not nodes:
            return

        pm.undoInfo(openChunk=True, chunkName=f"Edit Attr: {attr_name}")
        try:
            for node in nodes:
                if not node.hasAttr(attr_name):
                    continue
                try:
                    attr_type = pm.attributeQuery(
                        attr_name, node=node, attributeType=True
                    )
                    value = self._parse_value(new_text, attr_type)
                    if value is not None:
                        pm.setAttr(f"{node}.{attr_name}", value)
                except Exception as e:
                    pm.warning(f"Failed to set {node}.{attr_name}: {e}")
        finally:
            pm.undoInfo(closeChunk=True)

    @staticmethod
    def _parse_value(text, attr_type):
        """Convert user-entered text to a Python value for ``pm.setAttr``."""
        if attr_type in ("double", "float", "doubleLinear", "doubleAngle"):
            return float(text)
        if attr_type in ("long", "short", "byte", "int"):
            return int(float(text))
        if attr_type == "bool":
            return text.lower() in ("1", "true", "yes", "on")
        if attr_type in ("string",):
            return text
        if attr_type == "enum":
            # Accept integer index or enum name
            try:
                return int(text)
            except ValueError:
                return text
        # Compound / unsupported — skip
        return None

    # ------------------------------------------------------------------
    # Context menu handlers
    # ------------------------------------------------------------------

    def _ctx_lock(self, selection):
        """Lock selected attributes."""
        self._set_lock(selection, lock=True)

    def _ctx_unlock(self, selection):
        """Unlock selected attributes."""
        self._set_lock(selection, lock=False)

    def _set_lock(self, selection, lock):
        """Lock or unlock attribute(s) across all selected nodes."""
        attr_names = [s["name"] for s in selection if s.get("name")]
        nodes = self._get_selected_nodes()
        if not nodes or not attr_names:
            return

        action = "Lock" if lock else "Unlock"
        pm.undoInfo(openChunk=True, chunkName=f"{action} Attrs")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        pm.setAttr(f"{node}.{attr_name}", lock=lock)
                    except Exception:
                        pass
        finally:
            pm.undoInfo(closeChunk=True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_reset_default(self, selection):
        """Reset selected attributes to their default values."""
        attr_names = [s["name"] for s in selection if s.get("name")]
        nodes = self._get_selected_nodes()
        if not nodes or not attr_names:
            return

        pm.undoInfo(openChunk=True, chunkName="Reset to Default")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        defaults = pm.attributeQuery(
                            attr_name, node=node, listDefault=True
                        )
                        if defaults:
                            pm.setAttr(f"{node}.{attr_name}", defaults[0])
                    except Exception:
                        pass
        finally:
            pm.undoInfo(closeChunk=True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_toggle_keyable(self, selection):
        """Toggle keyable state for selected attributes."""
        attr_names = [s["name"] for s in selection if s.get("name")]
        nodes = self._get_selected_nodes()
        if not nodes or not attr_names:
            return

        pm.undoInfo(openChunk=True, chunkName="Toggle Keyable")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        plug = node.attr(attr_name)
                        pm.setAttr(plug, keyable=not plug.isKeyable())
                    except Exception:
                        pass
        finally:
            pm.undoInfo(closeChunk=True)
        self._refresh_table(self.ui.tbl000)

    def _ctx_delete(self, selection):
        """Delete selected custom attributes."""
        attr_names = [s["name"] for s in selection if s.get("name")]
        nodes = self._get_selected_nodes()
        if not nodes or not attr_names:
            return

        pm.undoInfo(openChunk=True, chunkName="Delete Attrs")
        try:
            for node in nodes:
                for attr_name in attr_names:
                    try:
                        if node.hasAttr(attr_name):
                            # Disconnect incoming anim curves before deletion
                            curves = pm.listConnections(
                                node.attr(attr_name), type="animCurve"
                            )
                            if curves:
                                pm.delete(curves)
                            node.deleteAttr(attr_name)
                    except Exception as e:
                        pm.warning(f"Failed to delete {node}.{attr_name}: {e}")
        finally:
            pm.undoInfo(closeChunk=True)
        self._refresh_table(self.ui.tbl000)

    # ------------------------------------------------------------------
    # ScriptJob lifecycle
    # ------------------------------------------------------------------

    def _setup_scene_change_callbacks(self, widget):
        """Register Maya scriptJobs for selection and scene changes."""
        self.cleanup_scene_callbacks()

        events = [
            "SelectionChanged",
            "SceneOpened",
            "NewSceneOpened",
        ]

        def _callback(*args):
            self._on_scene_change(widget)

        job_ids = []
        for event in events:
            try:
                # Store the ID to clean it up later.
                # Use a lambda or partial to keep `self` bound correctly
                jid = pm.scriptJob(
                    event=[event, _callback],
                    protected=False,  # Allow it to be killed
                )
                job_ids.append(jid)
            except Exception as e:
                print(f"AttributeManager: scriptJob '{event}' failed: {e}")

        self._scene_change_job_ids = job_ids

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

        pm.evalDeferred(_do_refresh)

    def cleanup_scene_callbacks(self):
        """Kill active scriptJobs."""
        if self._scene_change_job_ids is None:
            return

        ids = list(self._scene_change_job_ids)
        self._scene_change_job_ids = None

        def _kill(job_ids):
            for jid in job_ids:
                try:
                    if pm.scriptJob(exists=jid):
                        pm.scriptJob(kill=jid, force=True)
                except Exception:
                    pass

        pm.evalDeferred(lambda: _kill(ids))

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
        nodes = self._get_selected_nodes()
        if not nodes:
            return "No selection"
        names = ", ".join(n.nodeName() for n in nodes[:3])
        suffix = f" (+{len(nodes) - 3})" if len(nodes) > 3 else ""
        return f"{names}{suffix}"


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
