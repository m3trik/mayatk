# !/usr/bin/python
# coding=utf-8
"""UI slots for the Macro Manager panel.

``MacroManagerSlots`` — a single-table Switchboard interface for assigning a
hotkey and category to any ``mayatk`` macro, filtering the list, and saving /
loading named binding sets as presets. All non-UI logic is delegated to the
:class:`mayatk.edit_utils.macros.MacroManager` management API, which is the
single source of truth and is fully usable without this panel.
"""
import os

from qtpy import QtCore, QtWidgets, QtGui
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt, kbd

from mayatk.edit_utils.macros import Macros

_PRESETS_DIR = os.path.join(os.path.dirname(__file__), "presets")
_CONFLICT_COLOR = "#c86464"  # desaturated red — matches the channels panel


class MacroManagerSlots:
    """Switchboard slots for the Macro Manager UI.

    Layout
    ------
    - **Header menu**: global actions (Clear All / Reset to Default) and the
      preset selector (save / load named binding sets).
    - **ComboBox** (cmb000): filter the table by category.
    - **LineEdit** (txt000): wildcard filter by macro name or description.
    - **Table** (tbl000): one row per discoverable macro.
      Columns: Macro | Hotkey | Category | Description.
      Double-click a Hotkey cell to capture a chord; double-click a Category
      cell to pick / type a category from a dropdown.
    - **Context menu**: per-row Assign / Clear / Reset to Default.
    """

    COL_MACRO = 0
    COL_HOTKEY = 1
    COL_CATEGORY = 2
    COL_DESC = 3

    # Fallback category options; the live set is derived from the controller's
    # mixins in __init__ so the dropdown never drifts from the macro code.
    CATEGORIES = ["Display", "Edit", "Selection", "Animation", "UI"]

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.macro_manager
        self.controller = Macros
        self.CATEGORIES = self.controller.list_categories() or type(self).CATEGORIES

        # NOTE: no fit_to_content_on_show opt-out needed -- uitk's MainWindow now
        # treats a restored geometry as authoritative (skips the on-show fit when
        # a saved size was restored), so this resizable table panel keeps its
        # hand-expanded height across sessions automatically.

        self.presets = None
        self._category_delegate = None  # in-cell Category dropdown delegate
        self._available = {}  # macro_name -> annotation
        self._bindings = {}  # macro_name -> {"key": str, "cat": str}
        self._row_names = []  # row index -> macro_name (filtered view)

        # Debounced wildcard name/description filter.
        self._filter_timer = QtCore.QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self._refresh_table)
        self.ui.txt000.textChanged.connect(lambda _: self._filter_timer.start())
        self.ui.txt000.option_box.clear_option = True
        self.ui.destroyed.connect(self._filter_timer.stop)

        # Re-query Maya's live hotkey registry every time the panel is shown so
        # the table reflects bindings changed elsewhere (Maya's own Hotkey
        # Editor, other tools) — the source of truth is Maya, not a preset.
        self.ui.on_show.connect(self._reload_from_maya)

    # ------------------------------------------------------------------
    # Header + presets
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Populate the header menu (global actions + preset selector)."""
        widget.menu.add("Separator", setTitle="Actions")
        widget.menu.add(
            "QPushButton",
            setText="Clear All Hotkeys",
            setObjectName="hdr_clear_all",
            setToolTip="Unbind the hotkey of every macro (categories are kept).",
        )
        widget.menu.add(
            "QPushButton",
            setText="Reset to Default",
            setObjectName="hdr_reset_default",
            setToolTip="Reload the shipped 'default' binding set.",
        )
        widget.menu.hdr_clear_all.clicked.connect(self._clear_all)
        widget.menu.hdr_reset_default.clicked.connect(self._reset_to_default)

        self._init_presets(widget)

        widget.set_help_text(
            fmt(
                title="Macro Manager",
                body="Assign a hotkey and category to any mayatk macro, filter "
                "the list, and save / load named binding sets as presets.",
                sections=[
                    ("Table", [
                        "Each row is one discoverable macro.",
                        "Double-click a <b>Hotkey</b> cell to capture a chord "
                        f"(e.g. {kbd('Ctrl', 'Shift', 'I')}).",
                        "Edit the <b>Category</b> dropdown to re-group a macro.",
                        "Conflicting hotkeys are shown in red.",
                    ]),
                    ("Filter", [
                        "<b>Category</b> combo — restrict to one category.",
                        "<b>Filter</b> field — wildcard match on name or "
                        "description (<b>m_*select*</b>, <b>*wireframe</b>).",
                    ]),
                    ("Presets (header menu)", [
                        "Save the current bindings as a named set, or load one.",
                        "The shipped <b>default</b> preset is the startup set "
                        "applied by <b>apply_saved_macros()</b>.",
                    ]),
                ],
            )
        )

    def _init_presets(self, header_widget):
        """Create + wire the header preset selector (semantic-mode)."""
        try:
            from uitk.widgets.mixins.preset_manager import PresetManager
            from uitk.widgets.comboBox import ComboBox

            header_widget.menu.add("Separator", setTitle="Presets")
            combo = header_widget.menu.add(
                ComboBox,
                setObjectName="cmb_presets",
                setToolTip="Save / load a named macro binding set.",
            )
            # Semantic mode: presets store the binding dict, not widget state —
            # the same file format the headless MacroManager reads.
            self.presets = PresetManager(
                parent=self.ui,
                preset_dir="mayatk/macro_manager",
                builtin_dir=_PRESETS_DIR,
                value_provider=self._export_bindings,
                value_applier=self._import_bindings,
            )
            self.presets.wire_combo(combo, on_loaded=self._on_preset_loaded)
        except Exception as error:  # noqa: BLE001 - presets are non-critical
            self.presets = None
            print(f"# Warning: macro_manager preset combo unavailable: {error} #")

    def _export_bindings(self):
        """value_provider — capture the persist-worthy state.

        Every macro with a hotkey, *plus* any whose category has been changed
        from its mixin-derived default — so a re-categorization survives a
        save/load even when the macro is unbound (the common case). Macros at
        their default category and with no key are omitted to keep presets lean.
        """
        out = {}
        for name, spec in self._bindings.items():
            key = spec.get("key", "")
            cat = spec.get("cat", "")
            if key or (cat and cat != self.controller.macro_category(name)):
                out[name] = {"key": key, "cat": cat}
        return out

    def _import_bindings(self, data):
        """value_applier — apply a loaded binding set to Maya + memory."""
        data = data or {}
        # Unbind anything currently bound that the incoming set won't overwrite.
        for name, spec in self._bindings.items():
            if spec.get("key") and name not in data:
                self.controller.clear_hotkey(name, key=spec["key"])
        self.controller.apply_bindings(data)

        self._bindings = self._blank_bindings()
        for name, spec in data.items():
            if name in self._bindings and isinstance(spec, dict):
                self._bindings[name] = {
                    "key": spec.get("key", "") or "",
                    # Fall back to the mixin default so a preset entry that
                    # carries only a key (older format) doesn't blank the cat.
                    "cat": (spec.get("cat", "") or "")
                    or self.controller.macro_category(name),
                }
        return len(data)

    def _on_preset_loaded(self):
        """on_loaded — refresh the table after a preset is applied."""
        self._refresh_table()

    # ------------------------------------------------------------------
    # Category filter
    # ------------------------------------------------------------------

    def _all_categories(self):
        """Canonical categories plus any custom ones present in the bindings."""
        return sorted(
            set(self.CATEGORIES)
            | {s.get("cat") for s in self._bindings.values() if s.get("cat")}
        )

    def cmb000_init(self, widget):
        """Populate the category filter combobox."""
        cats = self._all_categories()
        previous = widget.currentText() if widget.count() else "All"
        widget.blockSignals(True)
        widget.clear()
        widget.addItems(["All"] + cats)
        idx = widget.findText(previous)  # keep the filter across repopulation
        widget.setCurrentIndex(idx if idx >= 0 else 0)
        widget.blockSignals(False)

    def cmb000(self, index):
        """Category filter changed — refresh the table."""
        self._refresh_table()

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        """One-time table setup, then (re)populate."""
        if not widget.is_initialized:
            # Hotkey + Category cells are both captured in-cell on double-click
            # (chord for Hotkey, dropdown for Category); NoEditTriggers keeps
            # the other columns read-only — each capture delegate opens its own
            # column explicitly regardless of triggers.
            from uitk.widgets.hotkey_capture_delegate import install_hotkey_capture
            from uitk.widgets.choice_capture_delegate import install_choice_capture

            widget.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            install_hotkey_capture(
                widget, self.COL_HOTKEY, self._on_hotkey_captured
            )
            self._category_delegate = install_choice_capture(
                widget, self.COL_CATEGORY, self.CATEGORIES, self._on_category_captured
            )
            self._setup_context_menu(widget)

        self._load_bindings()
        self.cmb000_init(self.ui.cmb000)
        self._refresh_table()

    def _load_bindings(self):
        """Seed the available macros + live bindings queried from Maya."""
        self._available = self.controller.list_available_macros()
        self._bindings = self.controller.get_current_bindings()
        # Ensure every available macro has an entry (unbound -> empty).
        for name in self._available:
            self._bindings.setdefault(name, {"key": "", "cat": ""})

    def _reload_from_maya(self):
        """Re-query Maya's live hotkeys and rebuild the table (on panel show)."""
        self._load_bindings()
        self.cmb000_init(self.ui.cmb000)
        self._refresh_table()
        self._mark_modified()

    def _blank_bindings(self):
        """A fresh, hotkey-less entry for every macro, each keeping its
        mixin-derived default category — so a preset load that omits a macro
        leaves its category at the default instead of blanking it."""
        return {
            name: {"key": "", "cat": self.controller.macro_category(name)}
            for name in self._available
        }

    def _filtered_names(self):
        """Macro names passing the category + name/description filters."""
        names = list(self._available.keys())

        cmb = getattr(self.ui, "cmb000", None)
        cat = cmb.currentText() if cmb else "All"
        if cat and cat != "All":
            names = [n for n in names if self._bindings.get(n, {}).get("cat") == cat]

        text = self.ui.txt000.text().strip()
        if text:
            name_hits = set(ptk.IterUtils.filter_list(names, inc=text, ignore_case=True))
            needle = text.lower().strip("*")

            def matches(n):
                return (
                    n in name_hits
                    or needle in self._available.get(n, "").lower()
                    or needle in self.controller.macro_label(n).lower()
                )

            names = [n for n in names if matches(n)]
        return sorted(names, key=lambda n: self.controller.macro_label(n).lower())

    def _conflict_peers(self):
        """``{macro: [other macros sharing its hotkey]}`` for clashing bindings."""
        peers = {}
        for names in self.controller.find_conflicts(self._bindings).values():
            for name in names:
                peers[name] = [other for other in names if other != name]
        return peers

    def _refresh_table(self):
        """Rebuild the table rows from the current bindings + filters."""
        tbl = self.ui.tbl000
        names = self._filtered_names()
        conflict_peers = self._conflict_peers()
        Qt = self.sb.QtCore.Qt

        # Keep the in-cell dropdown's options in step with any custom
        # categories the user has typed (parity with the filter combo).
        if self._category_delegate:
            self._category_delegate.set_choices(self._all_categories())

        tbl.blockSignals(True)
        tbl.setUpdatesEnabled(False)
        try:
            # setRowCount(0) drops every row cleanly before we rebuild.
            tbl.setRowCount(0)
            tbl.setRowCount(len(names))
            self._row_names = list(names)

            for row, name in enumerate(names):
                spec = self._bindings.get(name, {})
                key = spec.get("key", "")
                cat = spec.get("cat", "")
                disp = self.controller.maya_key_to_qt_sequence(key) if key else ""
                label = self.controller.macro_label(name)
                summary = self._available.get(name, "")
                tip = self._macro_tooltip(name)

                # Macro — nice user-facing label (raw name kept in UserRole).
                macro_item = QtWidgets.QTableWidgetItem(label)
                macro_item.setData(Qt.UserRole, name)
                macro_item.setToolTip(tip)
                tbl.setItem(row, self.COL_MACRO, macro_item)

                # Hotkey — red + peer list when the chord collides.
                hk_item = QtWidgets.QTableWidgetItem(disp)
                peers = conflict_peers.get(name)
                if peers:
                    hk_item.setForeground(QtGui.QColor(_CONFLICT_COLOR))
                    hk_item.setToolTip(
                        "Hotkey conflict — also bound to: "
                        + ", ".join(self.controller.macro_label(p) for p in peers)
                    )
                else:
                    hk_item.setToolTip("Double-click to assign a hotkey.")
                tbl.setItem(row, self.COL_HOTKEY, hk_item)

                # Category — plain editable cell; the choice-capture delegate
                # opens a dropdown on double-click (mirrors the Hotkey column),
                # so there's no persistent combo widget grabbing hover/select.
                # (No UserRole: the row→macro map is _row_names, and a stray
                # UserRole would be drag-propagated by the table to peer cells.)
                cat_item = QtWidgets.QTableWidgetItem(cat)
                cat_item.setToolTip("Double-click to choose a category.")
                tbl.setItem(row, self.COL_CATEGORY, cat_item)

                desc_item = QtWidgets.QTableWidgetItem(summary)
                desc_item.setToolTip(tip)
                tbl.setItem(row, self.COL_DESC, desc_item)
        finally:
            tbl.setUpdatesEnabled(True)
            tbl.blockSignals(False)

        self._configure_columns()
        self._update_footer()

    def _macro_tooltip(self, name):
        """The macro's docstring, for the Macro / Description cells — but only
        when it adds detail beyond the first line the Description column already
        shows. A single-line docstring *is* that column's content, so it gets
        no tooltip (no echoing the row). Returned as plain text so Qt keeps the
        docstring's own line breaks; ``fmt``'s HTML ``body`` collapses them.
        """
        help_text = self.controller.macro_help(name)
        return help_text if (help_text and "\n" in help_text) else ""

    def _configure_columns(self):
        """Set column resize behaviour."""
        tbl = self.ui.tbl000
        header = tbl.horizontalHeader()
        header.setSectionsMovable(False)
        QHV = self.sb.QtWidgets.QHeaderView
        header.setSectionResizeMode(self.COL_MACRO, QHV.ResizeToContents)
        header.setSectionResizeMode(self.COL_HOTKEY, QHV.Interactive)
        tbl.setColumnWidth(self.COL_HOTKEY, 120)
        header.setSectionResizeMode(self.COL_CATEGORY, QHV.Interactive)
        tbl.setColumnWidth(self.COL_CATEGORY, 110)
        header.setSectionResizeMode(self.COL_DESC, QHV.Stretch)

    def _update_footer(self):
        """Show a macro / binding / conflict summary in the footer."""
        bound = sum(1 for s in self._bindings.values() if s.get("key"))
        conflicts = self.controller.find_conflicts(self._bindings)
        msg = f"{bound} bound · {len(self._available)} macros"
        if conflicts:
            msg += f" · {len(conflicts)} conflict(s)"
        try:
            self.ui.footer.setText(msg, level="warning" if conflicts else None)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cell interaction
    # ------------------------------------------------------------------

    def _on_hotkey_captured(self, row, _col, sequence):
        """Apply a chord captured in-cell to the row's macro.

        ``sequence`` is a Qt NativeText shortcut string (``""`` to clear);
        convert it to Maya's hotkey notation before persisting.
        """
        name = self._row_macro(row)
        if not name:
            return
        new_key = self.controller.qt_sequence_to_maya_key(sequence)
        self._set_binding_key(name, new_key)

    def _capture_hotkey(self, row):
        """Open the in-cell capture editor for ``row`` (context-menu entry).

        Routes through the same delegate the double-click path uses, so the
        captured chord lands in :meth:`_on_hotkey_captured`.
        """
        tbl = self.ui.tbl000
        item = tbl.item(row, self.COL_HOTKEY)
        if item is not None:
            tbl.editItem(item)

    def _set_binding_key(self, name, new_key):
        """Apply a new (possibly empty) hotkey to ``name`` and refresh."""
        spec = self._bindings.setdefault(name, {"key": "", "cat": ""})
        old_key = spec.get("key", "")
        cat = spec.get("cat", "")
        if old_key and old_key != new_key:
            self.controller.clear_hotkey(name, key=old_key)
        spec["key"] = new_key
        if new_key:
            self.controller.set_macro(name, key=new_key, cat=cat or None)
        self._mark_modified()
        self._refresh_table()

    def _on_category_captured(self, row, _col, new_cat):
        """Category dropdown committed — update + (if bound) re-register macro.

        ``new_cat`` is the value chosen / typed in the in-cell dropdown. The
        emit is deferred one tick by the delegate, so the editor is already
        closed: rebuilding the table here is safe (unlike the old persistent
        combo) and keeps the category filter + filtered view in sync.
        """
        name = self._row_macro(row)
        if not name:
            return
        new_cat = (new_cat or "").strip()
        spec = self._bindings.setdefault(name, {"key": "", "cat": ""})
        if spec.get("cat", "") == new_cat:
            return
        spec["cat"] = new_cat
        if spec.get("key"):
            self.controller.set_macro(name, key=spec["key"], cat=new_cat or None)
        self._mark_modified()
        # A newly-typed category should appear in the filter combo; rebuild it
        # (it preserves the active selection) and the table view.
        self.cmb000_init(self.ui.cmb000)
        self._refresh_table()

    def _row_macro(self, row):
        """Macro name for a view row, or ``None``."""
        if 0 <= row < len(self._row_names):
            return self._row_names[row]
        return None

    def _mark_modified(self):
        """Refresh the preset dirty marker (no-op without presets)."""
        if self.presets:
            try:
                self.presets.refresh_modified_state()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _setup_context_menu(self, widget):
        """Build the per-row right-click menu."""
        menu = widget.menu
        menu.add("Separator", setTitle="Hotkey")
        menu.add(
            "QPushButton",
            setText="Assign Hotkey…",
            setObjectName="ctx_assign",
            setToolTip="Capture a hotkey for the selected macro.",
        )
        menu.add(
            "QPushButton",
            setText="Clear Hotkey",
            setObjectName="ctx_clear",
            setToolTip="Unbind the selected macro(s).",
        )
        menu.add(
            "QPushButton",
            setText="Reset to Default",
            setObjectName="ctx_reset",
            setToolTip="Restore the selected macro(s) from the 'default' preset.",
        )
        menu.ctx_assign.clicked.connect(self._ctx_assign)
        menu.ctx_clear.clicked.connect(self._ctx_clear)
        menu.ctx_reset.clicked.connect(self._ctx_reset)

    def _selected_macros(self):
        """Macro names for the currently selected rows."""
        tbl = self.ui.tbl000
        rows = sorted({idx.row() for idx in tbl.selectedIndexes()})
        return [self._row_names[r] for r in rows if r < len(self._row_names)]

    def _ctx_assign(self):
        """Assign a hotkey to the (single) selected macro."""
        tbl = self.ui.tbl000
        rows = sorted({idx.row() for idx in tbl.selectedIndexes()})
        if len(rows) != 1:
            self.sb.message_box("Select a single macro to assign a hotkey.")
            return
        self._capture_hotkey(rows[0])

    def _ctx_clear(self):
        """Clear the hotkey of every selected macro."""
        changed = False
        for name in self._selected_macros():
            spec = self._bindings.get(name, {})
            if spec.get("key"):
                self.controller.clear_hotkey(name, key=spec["key"])
                spec["key"] = ""
                changed = True
        if changed:
            self._mark_modified()
            self._refresh_table()

    def _ctx_reset(self):
        """Restore the selected macros from the shipped 'default' preset."""
        names = self._selected_macros()
        if not names:
            return
        try:
            defaults = self.controller.load_preset(self.controller.DEFAULT_PRESET)
        except Exception as error:  # noqa: BLE001
            self.sb.message_box(f"Could not load default preset: {error}")
            return
        for name in names:
            spec = self._bindings.setdefault(name, {"key": "", "cat": ""})
            old_key = spec.get("key", "")
            default = defaults.get(name)
            if default:
                if old_key and old_key != default.get("key"):
                    self.controller.clear_hotkey(name, key=old_key)
                spec.update(
                    {"key": default.get("key", ""), "cat": default.get("cat", "")}
                )
                if spec["key"]:
                    self.controller.set_macro(
                        name, key=spec["key"], cat=spec["cat"] or None
                    )
            elif old_key:  # not in default -> unbind
                self.controller.clear_hotkey(name, key=old_key)
                spec["key"] = ""
        self._mark_modified()
        self._refresh_table()

    # ------------------------------------------------------------------
    # Header actions
    # ------------------------------------------------------------------

    def _clear_all(self):
        """Unbind the hotkey of every macro (categories are kept)."""
        for name, spec in self._bindings.items():
            if spec.get("key"):
                self.controller.clear_hotkey(name, key=spec["key"])
                spec["key"] = ""
        self._mark_modified()
        self._refresh_table()

    def _reset_to_default(self):
        """Reload the shipped 'default' binding set."""
        try:
            defaults = self.controller.load_preset(self.controller.DEFAULT_PRESET)
        except Exception as error:  # noqa: BLE001
            self.sb.message_box(f"Could not load default preset: {error}")
            return
        self._import_bindings(defaults)
        self.controller.set_active_preset(self.controller.DEFAULT_PRESET)
        self._mark_modified()
        self._refresh_table()
