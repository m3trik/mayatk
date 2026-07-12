# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    cmds = None

import os

from pythontk.img_utils._img_utils import ImgUtils
from pythontk.core_utils.engines.textures.map_factory import MapFactory
from pythontk.str_utils.fuzzy_matcher import FuzzyMatcher
from uitk.widgets.footer import FooterStatusController
from uitk.widgets.mixins.tooltip_mixin import fmt

# From this package:
from mayatk.core_utils.script_job_manager import ScriptJobManager
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils


class TexturePathEditorSlots:
    _ROW_SELECTION_COLUMNS = {
        "shader": 0,
        "path": 1,
        "file_node": 2,
    }

    # Resolve-Missing cascade order: safest first. Same order is used in the
    # header-menu checkbox listing so the visual matches the run order.
    _RESOLVE_STRATEGY_ORDER = ("stem", "texture", "fuzzy")

    # Normalize-Paths combobox items. Order is the contract: the menu's
    # combobox is populated in this order, and ``_read_normalize_external_mode``
    # maps ``currentIndex()`` back to the mode key. Reordering breaks the read.
    _NORMALIZE_MODE_ITEMS = (
        ("Leave external textures untouched", "rewrite"),
        ("Copy external textures to sourceimages", "copy"),
        ("Move external textures to sourceimages", "move"),
    )

    # Set-Directory / Find-&-Copy relocate combobox items.
    _RELOCATE_MODE_ITEMS = (
        ("Leave textures in place (path only)", "rewrite"),
        ("Copy textures to new directory", "copy"),
        ("Move textures to new directory", "move"),
    )

    # Find-&-Copy relocate combobox items (no "rewrite" — the operation
    # always relocates files; the only choice is copy vs. move).
    _FIND_MODE_ITEMS = (
        ("Copy", "copy"),
        ("Move", "move"),
    )

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.texture_path_editor
        self._refresh_pending = False
        self._footer_controller = self._create_footer_controller()
        self._previous_paths = {}  # node_name -> path before last in-session repath
        self._browse_in_progress = False  # re-entry guard
        self._find_copy_in_progress = False  # re-entry guard

    # ------------------------------------------------------------------
    # Header menu
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Initialize the header menu.

        Plain action items are QPushButtons wired via ``clicked.connect``.
        The two items with per-button option-box flyouts (Normalize Paths,
        Resolve Missing Textures) are uitk ``PushButton`` (``tb_*``)
        auto-wired by name; their flyout contents are populated by matching
        ``_init`` methods.
        """
        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self.refresh_texture_table)

        widget.menu.add("Separator", setTitle="General")
        btn_open_si = widget.menu.add(
            "QPushButton",
            setText="Open Source Images",
            setObjectName="btn_open_source_images",
            setToolTip="Open the project's sourceimages directory in the file explorer.",
        )
        btn_open_si.clicked.connect(self.open_source_images)

        btn_reload = widget.menu.add(
            "QPushButton",
            setText="Reload Scene Textures",
            setObjectName="btn_reload_scene_textures",
            setToolTip=(
                "Force Maya to re-read every scene texture from disk "
                "(file / aiImage / pxrTexture / imagePlane). Useful after "
                "editing textures externally or after Find & Copy / Normalize "
                "Paths relocates them."
            ),
        )
        btn_reload.clicked.connect(self.reload_scene_textures)

        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Set Directory…",
            setObjectName="tb_set_texture_directory",
            setToolTip=(
                "Repath every (selected, or all) file node so its texture lives "
                "under the chosen directory. Subdirectories are flattened. Paths "
                "become relative when the chosen directory is inside sourceimages."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Find && Copy Textures…",
            setObjectName="tb_find_and_copy_textures",
            setToolTip=(
                "Search recursively from a source directory for textures used by "
                "(selected, or all) file nodes, relocate them to a destination, "
                "and repath. Paths become relative when destination is inside "
                "sourceimages.\n\nNote: Arnold texture nodes are not supported."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Normalize Paths",
            setObjectName="tb_normalize_paths",
            setToolTip=(
                "Rewrite (selected, or all) absolute paths under sourceimages "
                "to relative. UDIM tokens are preserved."
            ),
        )
        widget.menu.add(
            self.sb.registered_widgets.PushButton,
            setText="Resolve Missing Textures",
            setObjectName="tb_resolve_missing_textures",
            setToolTip=(
                "Search sourceimages (recursively, all subfolders) for "
                "replacement files for missing (selected, or all) textures. "
                "Enabled strategies run in order: Stem → Texture → Fuzzy "
                "(safest first); stops at first hit."
            ),
        )

        widget.menu.add("Separator", setTitle="Selection")
        btn_sel_for_obj = widget.menu.add(
            "QPushButton",
            setText="Select Textures for Selected Objects",
            setObjectName="btn_select_textures_for_objects",
            setToolTip=(
                "Highlight the texture-path cells for textures used by the "
                "currently selected scene objects."
            ),
        )
        btn_sel_for_obj.clicked.connect(self.select_textures_for_objects)

        btn_sel_broken = widget.menu.add(
            "QPushButton",
            setText="Select Broken Paths",
            setObjectName="btn_select_broken_paths",
            setToolTip="Highlight rows whose texture file is missing.",
        )
        btn_sel_broken.clicked.connect(self.select_broken_paths)

        btn_sel_abs = widget.menu.add(
            "QPushButton",
            setText="Select Absolute Paths",
            setObjectName="btn_select_absolute_paths",
            setToolTip=(
                "Highlight rows whose path is absolute (regardless of validity). "
                "These are candidates for Normalize Paths."
            ),
        )
        btn_sel_abs.clicked.connect(self.select_absolute_paths)

        widget.set_help_text(
            fmt(
                title="Texture Path Editor",
                body="Inspect and fix file-node texture paths. Path commands "
                "operate on selected rows if any, otherwise on all file "
                "nodes in the scene.",
                sections=[
                    ("Path management (header menu)", [
                        "<b>Set Directory…</b> — repath to a chosen folder "
                        "(subdirs flatten). Option box (▸) chooses leave / "
                        "copy / move.",
                        "<b>Find &amp; Copy Textures…</b> — search an external "
                        "folder for matching textures, copy or move them into "
                        "a destination. Option box (▸) toggles Copy / Move.",
                        "<b>Normalize Paths</b> — rewrite absolute paths under "
                        "<i>sourceimages</i> to relative. Option box (▸) "
                        "controls external textures: leave / copy / move into "
                        "sourceimages.",
                        "<b>Resolve Missing Textures</b> — search sourceimages "
                        "using strategy cascade <i>Stem → Texture → Fuzzy</i> "
                        "(safest first; stops at first hit). Option box (▸) "
                        "enables/disables individual strategies.",
                    ]),
                    ("General (header menu)", [
                        "<b>Open Source Images</b> — Explorer shortcut.",
                        "<b>Reload Scene Textures</b> — force Maya to re-read "
                        "all textures from disk (useful after relocations).",
                    ]),
                    ("Selection helpers (header menu)", [
                        "<b>Select Textures for Selected Objects</b> — "
                        "highlight rows for textures used by the current "
                        "scene selection.",
                        "<b>Select Broken Paths</b> — rows whose file is "
                        "missing on disk.",
                        "<b>Select Absolute Paths</b> — rows with absolute "
                        "paths (candidates for Normalize Paths).",
                    ]),
                ],
                notes=[
                    "<b>Right-click</b> any row for per-texture actions: "
                    "Browse for File, scene selection, Hypershade graph, "
                    "delete.",
                    "Collision policy on Copy / Move: same-name + same-size "
                    "files rebind without overwriting; different-size hits "
                    "skip with a warning (never silently rebinds to a wrong "
                    "texture, never destroys the external).",
                ],
            )
        )

    def tb_set_texture_directory_init(self, widget):
        """Populate the Set Directory option-box with the relocate-mode combobox."""
        widget.option_box.menu.setTitle("Set Directory")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_relocate_mode",
            setToolTip=(
                "Behavior for texture files when the directory changes:\n\n"
                "• Leave in place — only rewrite the file-node path.\n"
                "• Copy — duplicate each texture into the chosen directory.\n"
                "• Move — relocate each texture into the chosen directory.\n\n"
                "Collision policy: same-name + same-size at destination is a "
                "safe rebind (no overwrite). Different size is skipped + "
                "warned — never silently rebind to a wrong texture."
            ),
            addItems=[label for label, _key in self._RELOCATE_MODE_ITEMS],
        )

    def tb_find_and_copy_textures_init(self, widget):
        """Populate the Find & Copy option-box with the copy/move combobox.

        Also wires the combobox to swap the button text between
        ``Find & Copy Textures…`` and ``Find & Move Textures…`` so the
        active mode is visible on the menu item itself.
        """
        widget.option_box.menu.setTitle("Find & Copy Textures")
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_relocate_mode",
            setToolTip=(
                "How to relocate matched textures into the destination:\n\n"
                "• Copy — duplicate each match into the destination.\n"
                "• Move — relocate each match into the destination "
                "(removes the source file after successful copy)."
            ),
            addItems=[label for label, _key in self._FIND_MODE_ITEMS],
        )

        def _sync_text(idx):
            label, _key = self._FIND_MODE_ITEMS[idx] if 0 <= idx < len(self._FIND_MODE_ITEMS) else self._FIND_MODE_ITEMS[0]
            widget.setText(f"Find && {label} Textures…")

        cmb.currentIndexChanged.connect(_sync_text)
        _sync_text(cmb.currentIndex())  # initial sync

    def tb_normalize_paths_init(self, widget):
        """Populate the Normalize Paths option-box with the external-mode combobox."""
        widget.option_box.menu.setTitle("Normalize Paths")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_external_mode",
            setToolTip=(
                "Behavior for external textures (absolute paths outside "
                "sourceimages) whose file exists on disk:\n\n"
                "• Leave untouched — only rewrite paths already under "
                "sourceimages.\n"
                "• Copy to sourceimages — duplicate the file in, then rebind.\n"
                "• Move to sourceimages — relocate the file in, then rebind.\n\n"
                "Collision policy: same-name + same-size in sourceimages is "
                "a safe rebind (no overwrite). Different size is skipped + "
                "warned — never silently rebind to a wrong texture."
            ),
            addItems=[label for label, _key in self._NORMALIZE_MODE_ITEMS],
        )

    def tb_resolve_missing_textures_init(self, widget):
        """Populate the Resolve Missing option-box with the strategy checkboxes."""
        widget.option_box.menu.setTitle("Resolve Missing Textures")
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Stem  — exact name, different extension",
            setObjectName="chk_stem",
            setChecked=True,
            setToolTip=(
                "Match files in sourceimages whose stem equals the missing "
                "texture's stem (extension may differ)."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Texture  — same map type + base name (safest fuzzy)",
            setObjectName="chk_texture",
            setChecked=True,
            setToolTip=(
                "Restrict candidates to files of the same map type "
                "(AO/DIFF/NORM/SPEC/…) and fuzzy-match on the map-stripped "
                "base name."
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Fuzzy  — similar name (loose; may mismatch)",
            setObjectName="chk_fuzzy",
            setChecked=True,
            setToolTip=(
                "Loose name matching across all candidates. May mismatch on "
                "map-type boundaries."
            ),
        )

    # ------------------------------------------------------------------
    # Table context menu
    # ------------------------------------------------------------------

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)
            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)

            widget.menu.add("Separator", setTitle="Path Management")
            widget.menu.add(
                "QPushButton",
                setText="Browse for File...",
                setObjectName="row_browse_for_file",
                setToolTip=(
                    "Open a file browser and pick a texture file to repath this "
                    "row to. Single selection only."
                ),
            )

            widget.menu.add("Separator", setTitle="Selection")
            widget.menu.add(
                "QPushButton",
                setText="Select In Scene",
                setObjectName="select_material",
                setToolTip=(
                    "Select all scene objects currently assigned to this material."
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Select File Node",
                setObjectName="select_file_node",
                setToolTip="Select the file node in Maya.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Show in Hypershade",
                setObjectName="row_show_in_hypershade",
                setToolTip="Graph the selected file node in the Hypershade editor.",
            )

            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton",
                setText="Delete File Node",
                setObjectName="delete_file_node",
                setToolTip="Delete the selected file node from Maya.",
            )

            def _bind_menu_action(action_name, method, columns=None):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=columns or self._ROW_SELECTION_COLUMNS,
                )

            _bind_menu_action("row_browse_for_file", self.row_browse_for_file)
            _bind_menu_action("select_material", self.select_material)
            _bind_menu_action("select_file_node", self.select_file_node)
            _bind_menu_action("row_show_in_hypershade", self.row_show_in_hypershade)
            _bind_menu_action("delete_file_node", self.delete_file_node)

            self._setup_scene_change_callback(widget)

        self._refresh_table_content(widget)

    # ------------------------------------------------------------------
    # Smart scope
    # ------------------------------------------------------------------

    def _get_scope_nodes(self):
        """Return (nodes, scope_label).

        Selection-aware: returns selected rows' file nodes if any, otherwise
        all file nodes in the scene. ``scope_label`` is a human-readable
        descriptor used in dialog titles and info logs.

        Distinguishes "no selection" (fall through to all) from "selection
        with no valid file nodes" (warn + return empty) so a user with a
        broken selection isn't silently escalated to scene-wide scope.
        """
        contexts = self._get_selected_contexts(
            warn_on_empty=False,
            require_file_nodes=False,
        )
        if contexts:
            selected_nodes = []
            for ctx in contexts:
                selected_nodes.extend(ctx.get("file_nodes") or [])
            selected_nodes = list(dict.fromkeys(selected_nodes))
            if selected_nodes:
                return selected_nodes, f"{len(selected_nodes)} selected row(s)"
            cmds.warning(
                "Selected row(s) contain no valid file nodes; nothing to do."
            )
            return [], "selected (no valid file nodes)"

        all_nodes = cmds.ls(type="file") or []
        return all_nodes, f"all {len(all_nodes)} file node(s)"

    # ------------------------------------------------------------------
    # Header slots — General
    # ------------------------------------------------------------------

    def open_source_images(self):
        """Open the project's sourceimages directory."""
        path = EnvUtils.get_env_info("sourceimages")
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            cmds.warning(f"Source images directory not found: {path}")

    def reload_scene_textures(self):
        """Force Maya to re-read all scene textures from disk."""
        MatUtils.reload_textures(refresh_viewport=True)
        om.MGlobal.displayInfo("Reloaded scene textures from disk.")
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------
    # Header slots — Path Management
    # ------------------------------------------------------------------

    def tb_set_texture_directory(self, widget=None):
        """Repath file nodes (selection or all) under a chosen directory.

        The option-box combobox selects whether files are also relocated to
        the new directory (copy / move) or only the path attribute changes
        (rewrite, default).
        """
        nodes, scope_label = self._get_scope_nodes()
        if not nodes:
            cmds.warning("No file nodes to process.")
            return

        relocate_mode = self._read_relocate_mode(widget, self._RELOCATE_MODE_ITEMS)

        # Surface the active mode in the dialog title — last interaction before
        # any file ops fire. Matches the dynamic-text intent in Find & Copy.
        mode_hint = {
            "rewrite": "path only",
            "copy": "copy files",
            "move": "move files",
        }.get(relocate_mode, relocate_mode)

        target_dir = self.sb.dir_dialog(
            title=f"Set Texture Directory — {mode_hint} — {scope_label}",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not target_dir:
            return

        om.MGlobal.displayInfo(
            f"Setting texture paths to: {target_dir} (mode: {relocate_mode})"
        )
        count = self._set_texture_dir_flat(nodes, target_dir, relocate_mode=relocate_mode)
        om.MGlobal.displayInfo(f"Updated {count}/{len(nodes)} file nodes.")
        self.ui.tbl000.init_slot()

    def tb_find_and_copy_textures(self, widget=None):
        """Find textures from a source dir, copy or move to a destination, repath."""
        nodes, _scope_label = self._get_scope_nodes()
        if not nodes:
            cmds.warning("No file nodes to process.")
            return
        relocate_mode = self._read_relocate_mode(widget, self._FIND_MODE_ITEMS)
        self._find_and_copy_workflow(nodes, relocate_mode=relocate_mode)

    def _read_relocate_mode(self, button, mode_items) -> str:
        """Read a relocate combobox (``cmb_relocate_mode``) via index lookup."""
        combo = button.option_box.menu.cmb_relocate_mode
        idx = combo.currentIndex()
        if 0 <= idx < len(mode_items):
            return mode_items[idx][1]
        return mode_items[0][1]  # safe default

    def tb_normalize_paths(self, widget=None):
        """Rewrite paths under sourceimages to relative.

        External-mode is read from this button's own option_box combobox.
        ``widget`` is the button itself, passed by the switchboard auto-wire.
        """
        nodes, _scope_label = self._get_scope_nodes()
        if not nodes:
            cmds.warning("No file nodes to process.")
            return

        external_mode = self._read_normalize_external_mode(widget)
        self._normalize_to_relative(nodes, external_mode=external_mode)
        self.ui.tbl000.init_slot()

    def _read_normalize_external_mode(self, button) -> str:
        """Read the Normalize Paths external-mode combobox via index lookup."""
        combo = button.option_box.menu.cmb_external_mode
        idx = combo.currentIndex()
        if 0 <= idx < len(self._NORMALIZE_MODE_ITEMS):
            return self._NORMALIZE_MODE_ITEMS[idx][1]
        return self._NORMALIZE_MODE_ITEMS[0][1]  # safe default

    def tb_resolve_missing_textures(self, widget=None):
        """Resolve missing textures with configurable cascade strategies.

        Strategy selection is read from this button's own option_box
        checkboxes. ``widget`` is the button itself, passed by auto-wire.
        """
        nodes, _scope_label = self._get_scope_nodes()
        if not nodes:
            cmds.warning("No file nodes to process.")
            return

        modes = self._read_resolve_modes(widget)
        if not modes:
            cmds.warning(
                "No Resolve Missing strategies enabled in the option-box."
            )
            return

        self._resolve_missing_textures(modes=modes, file_nodes=nodes)

    def _read_resolve_modes(self, button):
        """Read the Resolve Missing strategy checkboxes; preserve safest-first order."""
        menu = button.option_box.menu
        attr_by_mode = {
            "stem": "chk_stem",
            "texture": "chk_texture",
            "fuzzy": "chk_fuzzy",
        }
        return [
            mode for mode in self._RESOLVE_STRATEGY_ORDER
            if getattr(menu, attr_by_mode[mode]).isChecked()
        ]

    # ------------------------------------------------------------------
    # Header slots — Selection
    # ------------------------------------------------------------------

    def select_textures_for_objects(self):
        """Select table rows whose textures are used by the scene selection."""
        selection = cmds.ls(sl=True, flatten=True)
        if not selection:
            self.sb.message_box("Select object(s) first.")
            return

        mats = MatUtils.get_mats(selection)
        if not mats:
            cmds.warning("No materials found on selected objects.")
            return

        target_node_names = set()
        for mat in mats:
            try:
                file_nodes = cmds.ls(cmds.listHistory(mat) or [], type="file") or []
                for fn in file_nodes:
                    target_node_names.add(fn.split("|")[-1].split(":")[-1])
            except Exception:
                pass

        if not target_node_names:
            cmds.warning("No file nodes found for selected objects.")
            return

        table = self.ui.tbl000
        table.clearSelection()
        selected_count = 0
        for row in range(table.rowCount()):
            node_data = table.item_data(row, 2)
            if not node_data:
                continue
            node_name = str(node_data).split("|")[-1].split(":")[-1]
            if node_name in target_node_names:
                path_item = table.item(row, 1)
                if path_item:
                    path_item.setSelected(True)
                    selected_count += 1
                    if selected_count == 1:
                        table.scrollToItem(path_item)
        if selected_count > 0:
            om.MGlobal.displayInfo(f"Selected {selected_count} rows in the table.")

    def select_broken_paths(self):
        """Select rows whose texture file is missing."""
        self._select_rows_by_predicate(
            predicate=lambda path, abs_path: not os.path.exists(abs_path),
            empty_message="No broken paths found.",
            count_message="broken paths",
        )

    def select_absolute_paths(self):
        """Select rows whose path is absolute (regardless of validity)."""
        self._select_rows_by_predicate(
            predicate=lambda path, abs_path: os.path.isabs(path),
            empty_message="No absolute paths found.",
            count_message="absolute paths",
        )

    def _select_rows_by_predicate(self, predicate, empty_message, count_message):
        """Select rows whose ``(path, abs_path)`` satisfies the predicate."""
        widget = self.ui.tbl000
        source_root = EnvUtils.get_env_info("workspace")
        widget.clearSelection()

        selection_mode = widget.selectionMode()
        widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)
        rows_to_select = []
        try:
            for row in range(widget.rowCount()):
                item = widget.item(row, 1)
                if not item:
                    continue
                path = str(item.text()).strip()
                if not path:
                    continue
                abs_path = (
                    os.path.normpath(os.path.join(source_root, path))
                    if not os.path.isabs(path)
                    else os.path.normpath(path)
                )
                if predicate(path, abs_path):
                    rows_to_select.append(row)

            for row in rows_to_select:
                path_item = widget.item(row, 1)
                if path_item:
                    path_item.setSelected(True)

            if rows_to_select:
                widget.scrollToItem(widget.item(rows_to_select[0], 1))
                om.MGlobal.displayInfo(
                    f"Selected {len(rows_to_select)} {count_message}."
                )
            else:
                om.MGlobal.displayInfo(empty_message)
        finally:
            widget.setSelectionMode(selection_mode)

    # ------------------------------------------------------------------
    # Row-only context slots
    # ------------------------------------------------------------------

    def row_browse_for_file(self, selection=None):
        """Open a file dialog and repath the selected row's file node."""
        if getattr(self, "_browse_in_progress", False):
            return
        self._browse_in_progress = True
        try:
            self._do_browse_for_file(selection)
        finally:
            from qtpy.QtCore import QTimer
            QTimer.singleShot(
                250, lambda: setattr(self, "_browse_in_progress", False)
            )

    def _do_browse_for_file(self, selection):
        nodes = self._file_nodes_from_selection(selection)
        if not nodes:
            return
        if len(nodes) > 1:
            cmds.warning("Browse for File: select a single row.")
            return

        node_name = nodes[0]
        sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        try:
            current = cmds.getAttr(f"{node_name}.fileTextureName") or ""
        except Exception:
            current = ""

        start_dir = sourceimages
        if current:
            workspace = EnvUtils.get_env_info("workspace") or ""
            current_abs = (
                current if os.path.isabs(current)
                else os.path.normpath(os.path.join(workspace, current))
            )
            current_dir = os.path.dirname(current_abs)
            if current_dir and os.path.isdir(current_dir):
                start_dir = current_dir

        chosen = self.sb.file_dialog(
            file_types=[
                "*.png", "*.jpg", "*.jpeg", "*.tga", "*.tif", "*.tiff",
                "*.exr", "*.hdr", "*.bmp", "*.psd", "*.iff", "*.tx", "*.*",
            ],
            title=f"Select texture file for {node_name}",
            start_dir=start_dir,
            filter_description="Texture Files",
            allow_multiple=False,
        )
        if not chosen:
            return

        new_path = self._project_relative_converter()(chosen)
        cmds.undoInfo(openChunk=True, chunkName="Browse Texture File")
        try:
            cmds.setAttr(f"{node_name}.fileTextureName", new_path, type="string")
            if current and current != new_path:
                self._previous_paths[node_name] = current
            om.MGlobal.displayInfo(f"{node_name}: '{current}' -> '{new_path}'")
        except Exception as e:
            cmds.warning(f"{node_name}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)
        self.ui.tbl000.init_slot()

    def select_material(self, selection=None):
        """Select scene objects assigned to the materials of selected rows."""
        contexts = self._get_selected_contexts(
            selection, require_file_nodes=False
        )
        if not contexts:
            return

        all_assigned_objects = []
        for context in contexts:
            shader_name = context.get("shader_name")
            if not shader_name:
                continue
            try:
                assigned = MatUtils.find_by_mat_id(shader_name, shell=True)
                if assigned:
                    all_assigned_objects.extend(assigned)
            except Exception as e:
                print(f"Failed to query objects for '{shader_name}': {e}")

        if not all_assigned_objects:
            cmds.warning("No scene objects found for the selected materials.")
            return

        try:
            cmds.select(all_assigned_objects, r=True)
            om.MGlobal.displayInfo(
                f"Selected objects for {len(contexts)} material(s)."
            )
        except Exception as e:
            om.MGlobal.displayError(f"Failed to select objects: {str(e)}")

    def select_file_node(self, selection=None):
        """Select the file nodes from the selected rows."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_select = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_select.append(file_node)
        if not nodes_to_select:
            return

        try:
            cmds.select(nodes_to_select, r=True)
            om.MGlobal.displayInfo(f"Selected {len(nodes_to_select)} file node(s).")
        except Exception as e:
            om.MGlobal.displayError(f"Failed to select file nodes: {str(e)}")

    def row_show_in_hypershade(self, selection=None):
        """Graph the selected file node(s) in Hypershade."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_graph = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_graph.append(file_node)
        if not nodes_to_graph:
            return
        MatUtils.graph_materials(nodes_to_graph)

    def delete_file_node(self, selection=None):
        """Delete the selected file node(s)."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_delete = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_delete.append(file_node)
        if not nodes_to_delete:
            return

        nodes_to_delete = list(set(nodes_to_delete))
        node_names = [n.split("|")[-1].split(":")[-1] for n in nodes_to_delete]

        count = len(nodes_to_delete)
        msg = f"Are you sure you want to delete {count} file node(s)?"
        if count == 1:
            msg = f"Are you sure you want to delete the file node '{node_names[0]}'?"

        reply = self.sb.message_box(msg, "Yes", "No")
        if reply == "Yes":
            try:
                cmds.delete(nodes_to_delete)
                om.MGlobal.displayInfo(f"Deleted {count} file node(s).")
                self.ui.tbl000.init_slot()
            except Exception as e:
                om.MGlobal.displayError(f"Failed to delete file nodes: {str(e)}")

    # ------------------------------------------------------------------
    # Set-Directory workflow
    # ------------------------------------------------------------------

    def _set_texture_dir_flat(
        self,
        file_nodes,
        target_dir: str,
        relocate_mode: str = "rewrite",
    ) -> int:
        """Repath each file node so its texture lives directly under target_dir.

        ``relocate_mode`` controls disk behavior for nodes whose current path
        resolves to an existing file:
          - ``"rewrite"`` — path-only (no file movement).
          - ``"copy"`` — copy the file to ``target_dir`` then rebind.
          - ``"move"`` — move the file to ``target_dir`` then rebind.

        Collision policy mirrors ``_normalize_to_relative``: same-name + same-
        size at destination is a safe rebind (no overwrite); different size
        is skipped + warned. Records prior paths in ``self._previous_paths``
        so the table tooltip can show the original.
        Returns the number of file nodes actually updated.
        """
        # Derive valid modes from the combobox items — SSoT for the mode keys.
        valid_modes = {key for _label, key in self._RELOCATE_MODE_ITEMS}
        if relocate_mode not in valid_modes:
            raise ValueError(
                f"Unknown relocate_mode {relocate_mode!r}; expected one of {sorted(valid_modes)}."
            )
        if not file_nodes:
            return 0
        node_names = [str(n) for n in file_nodes]
        target_dir_norm = os.path.normpath(target_dir).replace("\\", "/")
        to_relative = self._project_relative_converter()
        workspace = EnvUtils.get_env_info("workspace") or ""

        # Phase 1 — collect (node, old_path, new_path, optional relocate pair).
        plan = []  # list of (node, old_path, new_path, src_abs_or_None, dst_abs_or_None)
        for node_name in node_names:
            try:
                old_path = cmds.getAttr(f"{node_name}.fileTextureName") or ""
            except Exception:
                continue
            if not old_path:
                continue
            new_abs = os.path.normpath(
                os.path.join(target_dir_norm, os.path.basename(old_path))
            ).replace("\\", "/")
            new_path = to_relative(new_abs)
            if new_path == old_path:
                continue

            src_abs = None
            if relocate_mode != "rewrite":
                old_abs = (
                    old_path if os.path.isabs(old_path)
                    else os.path.normpath(os.path.join(workspace, old_path)).replace("\\", "/")
                )
                if os.path.exists(old_abs) and old_abs.lower() != new_abs.lower():
                    src_abs = old_abs
            plan.append((node_name, old_path, new_path, src_abs, new_abs))

        # Phase 2 — perform relocations outside the undo chunk (disk ops aren't undoable).
        relocated = 0
        collision_skipped = 0
        skipped_nodes = set()
        if relocate_mode in ("copy", "move"):
            import shutil
            for node_name, _old_path, _new_path, src, dst in plan:
                if src is None:
                    continue
                try:
                    if os.path.exists(dst):
                        try:
                            same = os.path.getsize(src) == os.path.getsize(dst)
                        except OSError:
                            same = False
                        if not same:
                            cmds.warning(
                                f"{node_name}: '{os.path.basename(dst)}' already "
                                f"exists at destination with different size; "
                                f"skipping to avoid wrong-file rebind."
                            )
                            collision_skipped += 1
                            skipped_nodes.add(node_name)
                            continue
                        if relocate_mode == "move":
                            try:
                                os.remove(src)
                            except OSError as e:
                                cmds.warning(
                                    f"{node_name}: equivalent at dst, but could "
                                    f"not remove '{src}': {e}"
                                )
                    else:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        if relocate_mode == "move":
                            shutil.move(src, dst)
                        else:
                            shutil.copy2(src, dst)
                    relocated += 1
                except Exception as e:
                    cmds.warning(f"{node_name}: failed to {relocate_mode}: {e}")
                    skipped_nodes.add(node_name)

        # Phase 3 — apply path updates (undoable).
        count = 0
        cmds.undoInfo(openChunk=True, chunkName="Set Texture Directory")
        try:
            for node_name, old_path, new_path, _src, _dst in plan:
                if node_name in skipped_nodes:
                    continue
                try:
                    cmds.setAttr(
                        f"{node_name}.fileTextureName", new_path, type="string"
                    )
                    self._previous_paths[node_name] = old_path
                    count += 1
                except Exception as e:
                    cmds.warning(f"{node_name}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        if relocate_mode != "rewrite":
            om.MGlobal.displayInfo(
                f"Set Directory — relocated: {relocated}; "
                f"collision skipped: {collision_skipped}; "
                f"mode: {relocate_mode}."
            )
        return count

    # ------------------------------------------------------------------
    # Find-and-Copy workflow
    # ------------------------------------------------------------------

    def _find_and_copy_workflow(self, file_nodes, relocate_mode: str = "copy"):
        """Run find/copy-or-move/repath with a re-entry guard.

        Modal dir dialogs occasionally deliver trailing release events that
        retrigger the slot, popping a second source-dir prompt. The guard
        protects against this (same pattern used by row_browse_for_file).
        """
        if getattr(self, "_find_copy_in_progress", False):
            return
        self._find_copy_in_progress = True
        try:
            self._do_find_and_copy_workflow(file_nodes, relocate_mode=relocate_mode)
        finally:
            from qtpy.QtCore import QTimer
            QTimer.singleShot(
                250, lambda: setattr(self, "_find_copy_in_progress", False)
            )

    def _do_find_and_copy_workflow(self, file_nodes, relocate_mode: str = "copy"):
        node_names = [str(n) for n in file_nodes]
        start_dir = EnvUtils.get_env_info("sourceimages")

        source_dir = self.sb.dir_dialog(
            title="Select a root directory to recursively search for textures:",
            start_dir=start_dir,
        )
        if not source_dir:
            return

        # find_texture_files ticks per directory so the marquee advances during
        # the walk. The walk itself isn't interruptible mid-directory, but Esc
        # cancels via the ProgressBar event filter.
        with self.sb.progress(self.ui, text=f"Searching {source_dir}…") as update:
            found_textures = MatUtils.find_texture_files(
                file_nodes=node_names,
                source_dir=source_dir,
                recursive=True,
                progress_callback=self.sb.progress_adapter(update),
            )
        if not found_textures:
            cmds.warning("No textures found.")
            return

        dest_dir = self.sb.dir_dialog(
            title="Select destination directory for textures:",
            start_dir=start_dir,
        )
        if not dest_dir:
            return

        # Dedup by basename, newest mtime wins. Walks can return multiple
        # matches per target filename (versioned/archived copies, Dropbox
        # conflict copies); feeding all of them into the threaded copy pool
        # would have two workers racing on the same destination — a deadlock
        # pattern on Windows + Dropbox.
        by_basename = {}
        for fpath in found_textures:
            bn = os.path.basename(fpath).lower()
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                mtime = 0.0
            existing = by_basename.get(bn)
            if existing is None or mtime > existing[0]:
                by_basename[bn] = (mtime, fpath)
        deduped = [v[1] for v in by_basename.values()]
        if len(deduped) < len(found_textures):
            print(
                f"// {len(found_textures)} candidates found, "
                f"deduped to {len(deduped)} unique basenames (newest wins)."
            )

        relocate_verb = "Moving" if relocate_mode == "move" else "Copying"
        with self.sb.progress(self.ui, text=f"{relocate_verb} textures…") as update:
            copied = MatUtils.move_texture_files(
                found_files=deduped,
                new_dir=dest_dir,
                delete_old=(relocate_mode == "move"),
                progress_callback=self.sb.progress_adapter(update),
            )
        if not copied:
            cmds.warning(f"No textures {relocate_verb.lower()}.")
            return

        copied_basenames = {os.path.basename(dst).lower() for _src, dst in copied}
        nodes_to_remap = []
        for node_name in node_names:
            try:
                path = cmds.getAttr(f"{node_name}.fileTextureName")
                if path and os.path.basename(path).lower() in copied_basenames:
                    nodes_to_remap.append(node_name)
            except Exception:
                continue

        if nodes_to_remap:
            # Flatten path manually: files were copied to dest_dir's root, but
            # MatUtils.remap_texture_paths would try to preserve the original
            # relative depth which no longer corresponds to disk layout.
            project_sourceimages = EnvUtils.get_env_info("sourceimages")
            sourceimages_name = (
                os.path.basename(project_sourceimages) if project_sourceimages else ""
            )
            if project_sourceimages:
                project_sourceimages = os.path.abspath(project_sourceimages).replace(
                    "\\", "/"
                )

            cmds.undoInfo(openChunk=True, chunkName="Remap Found Textures")
            try:
                count = 0
                for node_name in nodes_to_remap:
                    path = cmds.getAttr(f"{node_name}.fileTextureName")
                    if not path:
                        continue
                    filename = os.path.basename(path)
                    new_abs_path = os.path.normpath(
                        os.path.join(dest_dir, filename)
                    ).replace("\\", "/")
                    final_path = new_abs_path

                    if project_sourceimages and new_abs_path.lower().startswith(
                        project_sourceimages.lower()
                    ):
                        rel = os.path.relpath(
                            new_abs_path, project_sourceimages
                        ).replace("\\", "/")
                        if sourceimages_name and not rel.startswith(
                            sourceimages_name + "/"
                        ):
                            final_path = f"{sourceimages_name}/{rel}"
                        else:
                            final_path = rel

                    cmds.setAttr(
                        f"{node_name}.fileTextureName", final_path, type="string"
                    )
                    count += 1
                om.MGlobal.displayInfo(f"Remapped {count} file nodes.")
            finally:
                cmds.undoInfo(closeChunk=True)
        else:
            cmds.warning("No file nodes matched the copied textures.")

        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------
    # Normalize workflow
    # ------------------------------------------------------------------

    def _normalize_to_relative(self, file_nodes, external_mode: str = "rewrite") -> None:
        """Rewrite (selected) paths under sourceimages to relative.

        Per node:
          - <udim> token → skip (preserve token).
          - already relative → no-op.
          - absolute under sourceimages → rewrite as relative.
          - absolute outside sourceimages, file exists:
              external_mode="rewrite" → leave untouched.
              external_mode="copy"    → copy into sourceimages, then rewrite relative.
              external_mode="move"    → move into sourceimages, then rewrite relative.
          - absolute outside sourceimages, file missing → leave untouched
            (Resolve Missing Textures is the command for that case).

        Collision policy for copy/move when the destination already exists in
        sourceimages: cheap size check as a proxy for "same file." Same size →
        treat as the same file (no disk write; for move, remove the redundant
        external). Different size → skip + warn, don't silently rebind to the
        wrong texture.
        """
        # Derive valid modes from the combobox items — SSoT for the mode keys.
        valid_modes = {key for _label, key in self._NORMALIZE_MODE_ITEMS}
        if external_mode not in valid_modes:
            raise ValueError(
                f"Unknown external_mode {external_mode!r}; "
                f"expected one of {sorted(valid_modes)}."
            )

        sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        if not sourceimages:
            cmds.warning("sourceimages directory is not set; cannot normalize.")
            return
        si_abs = os.path.abspath(sourceimages).replace("\\", "/")
        to_relative = self._project_relative_converter()

        rewritten = 0
        already_relative = 0
        external_left = 0
        external_relocated = 0
        external_collision_skipped = 0
        udim_skipped = 0
        missing_left = 0

        # Defer file IO out of the undo chunk (disk ops aren't undoable).
        relocate_actions = []  # list of (node_name, src_abs, dst_abs)

        cmds.undoInfo(openChunk=True, chunkName="Normalize Texture Paths")
        try:
            for node in [str(n) for n in file_nodes]:
                try:
                    path = cmds.getAttr(f"{node}.fileTextureName") or ""
                except Exception:
                    continue
                if not path:
                    continue
                if "<udim>" in path.lower():
                    udim_skipped += 1
                    continue
                if not os.path.isabs(path):
                    already_relative += 1
                    continue

                norm = os.path.normpath(path).replace("\\", "/")
                if norm.lower().startswith(si_abs.lower()):
                    new_path = to_relative(norm)
                    if new_path != path:
                        try:
                            cmds.setAttr(
                                f"{node}.fileTextureName", new_path, type="string"
                            )
                            self._previous_paths[node] = path
                            rewritten += 1
                        except Exception as e:
                            cmds.warning(f"{node}: failed to set path: {e}")
                    else:
                        already_relative += 1
                    continue

                # External absolute path: decide by file existence + mode.
                if os.path.exists(norm):
                    if external_mode == "rewrite":
                        external_left += 1
                    else:
                        dst = os.path.normpath(
                            os.path.join(si_abs, os.path.basename(norm))
                        ).replace("\\", "/")
                        relocate_actions.append((node, norm, dst))
                else:
                    missing_left += 1
        finally:
            cmds.undoInfo(closeChunk=True)

        # Relocate phase. setAttr is still undoable; disk ops are not.
        if relocate_actions:
            import shutil
            chunk_name = (
                "Normalize — move externals"
                if external_mode == "move"
                else "Normalize — copy externals"
            )
            cmds.undoInfo(openChunk=True, chunkName=chunk_name)
            try:
                for node, src, dst in relocate_actions:
                    try:
                        if os.path.exists(dst):
                            # Same-basename collision. Size as cheap proxy for
                            # "same file" — if bytes differ, refuse to silently
                            # rebind to the wrong texture.
                            try:
                                same = os.path.getsize(src) == os.path.getsize(dst)
                            except OSError:
                                same = False
                            if not same:
                                cmds.warning(
                                    f"{node}: '{os.path.basename(dst)}' already "
                                    f"exists in sourceimages with different "
                                    f"size; skipping to avoid wrong-file rebind."
                                )
                                external_collision_skipped += 1
                                continue
                            # Same size: rebind without overwriting dst. For
                            # move, the external is redundant — remove it.
                            if external_mode == "move":
                                try:
                                    os.remove(src)
                                except OSError as e:
                                    cmds.warning(
                                        f"{node}: copied-equivalent at dst but "
                                        f"could not remove '{src}': {e}"
                                    )
                        else:
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            if external_mode == "move":
                                shutil.move(src, dst)
                            else:
                                shutil.copy2(src, dst)
                        new_path = to_relative(dst)
                        try:
                            old_path = cmds.getAttr(f"{node}.fileTextureName") or ""
                        except Exception:
                            old_path = ""
                        cmds.setAttr(
                            f"{node}.fileTextureName", new_path, type="string"
                        )
                        if old_path and old_path != new_path:
                            self._previous_paths[node] = old_path
                        external_relocated += 1
                    except Exception as e:
                        cmds.warning(f"{node}: failed to relocate/repath: {e}")
            finally:
                cmds.undoInfo(closeChunk=True)

        relocate_label = {
            "rewrite": "—",
            "copy": "copied",
            "move": "moved",
        }[external_mode]
        om.MGlobal.displayInfo(
            f"Normalize Paths — rewritten: {rewritten}; "
            f"already relative: {already_relative}; "
            f"external {relocate_label}: {external_relocated}; "
            f"external collision skipped: {external_collision_skipped}; "
            f"external left as-is: {external_left}; "
            f"missing left as-is: {missing_left}; "
            f"UDIM skipped: {udim_skipped}."
        )

    # ------------------------------------------------------------------
    # Resolve Missing
    # ------------------------------------------------------------------

    def _strategies_for_modes(self, modes, index_stems):
        """Concatenate strategy lists for each enabled mode, dedup-preserving order.

        ``exact`` is the first tier of every mode and gets deduplicated so it
        only runs once at the head of the pipeline.
        """
        pipeline = []
        seen = set()
        for mode in modes:
            for s in self._strategies_for_mode(mode, index_stems):
                key = id(s) if callable(s) else s
                if key in seen:
                    continue
                seen.add(key)
                pipeline.append(s)
        return pipeline

    def _strategies_for_mode(self, mode: str, index_stems):
        if mode == "stem":
            return ["exact"]
        if mode == "fuzzy":
            # use_base_name is intentionally NOT in the pipeline: numbered
            # variants like texture_001 / texture_002 should not auto-fuse.
            return ["exact", "substring", "ratio"]
        if mode == "texture":
            return [
                "exact",
                self._texture_aware_strategy(index_stems),
                "substring",
                "ratio",
            ]
        raise ValueError(f"Unknown resolve mode: {mode!r}")

    def _texture_aware_strategy(self, index_stems):
        """Custom strategy: filter by map type, then fuzzy-match base name.

        For a missing ``c130j_..._ao``, this restricts candidates to other
        ``_AO`` files, so an ``_AO`` file node can never get repathed to a
        ``_DIFF`` / ``_NORM`` / ``_SPEC`` file.
        """
        candidate_meta = []
        for stem in index_stems:
            try:
                map_type = MapFactory.resolve_map_type(stem + ".png", key=True)
            except Exception:
                map_type = None
            try:
                base = ImgUtils.get_base_texture_name(stem + ".png").lower()
            except Exception:
                base = stem
            candidate_meta.append((stem, base, map_type))

        def texture(target, candidates):
            try:
                target_map = MapFactory.resolve_map_type(target + ".png", key=True)
            except Exception:
                target_map = None
            if not target_map:
                return None, 0.0, "no_match"
            try:
                target_base = ImgUtils.get_base_texture_name(
                    target + ".png"
                ).lower()
            except Exception:
                target_base = target

            same_map_stems = []
            same_map_bases = []
            for stem, base, map_type in candidate_meta:
                if map_type == target_map:
                    same_map_stems.append(stem)
                    same_map_bases.append(base)

            if not same_map_bases:
                return None, 0.0, "no_match"

            base_match, score, status = FuzzyMatcher.find_unique_match(
                target_base,
                same_map_bases,
                score_threshold=0.5,
                ambiguity_delta=0.05,
                use_base_name=False,
                use_substring=True,
                use_prefix=False,
                use_ratio=False,
            )
            if status == "no_match":
                return None, 0.0, "no_match"
            idx = same_map_bases.index(base_match)
            return same_map_stems[idx], score, status

        return texture

    def _resolve_missing_textures(self, modes, file_nodes=None):
        """Resolve missing textures using the given strategy modes (in cascade order).

        Parameters:
            modes: List of mode names drawn from ``_RESOLVE_STRATEGY_ORDER``.
            file_nodes: Optional list of file nodes to restrict to. If None,
                       processes all ``cmds.ls(type="file")``.
        """
        if not modes:
            raise ValueError("At least one mode is required.")
        unknown = set(modes) - set(self._RESOLVE_STRATEGY_ORDER)
        if unknown:
            raise ValueError(f"Unknown resolve mode(s): {sorted(unknown)}")

        sourceimages = EnvUtils.get_env_info("sourceimages")
        if not sourceimages or not os.path.isdir(sourceimages):
            cmds.warning(f"sourceimages directory not found: {sourceimages}")
            return

        workspace = EnvUtils.get_env_info("workspace") or ""
        if file_nodes is None:
            all_file_nodes = cmds.ls(type="file") or []
        else:
            # Preserve namespaces; stripping breaks cmds.getAttr/setAttr.
            all_file_nodes = [str(n) for n in file_nodes]
        if not all_file_nodes:
            cmds.warning("No file nodes to process.")
            return

        missing = []
        for node in all_file_nodes:
            try:
                path = cmds.getAttr(f"{node}.fileTextureName") or ""
            except Exception:
                continue
            if not path or "<udim>" in path.lower():
                continue
            abs_path = (
                path if os.path.isabs(path)
                else os.path.normpath(os.path.join(workspace, path))
            )
            if os.path.exists(abs_path):
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem:
                missing.append((node, path, stem))

        if not missing:
            om.MGlobal.displayInfo("No missing textures to resolve.")
            return

        index = []
        for root, _, files in os.walk(sourceimages):
            for f in files:
                stem = os.path.splitext(f)[0]
                if stem:
                    index.append((stem.lower(), os.path.join(root, f)))
        if not index:
            cmds.warning("No files in sourceimages to match against.")
            return

        to_project_relative = self._project_relative_converter()
        by_stem = {}
        for stem_key, abs_path in index:
            by_stem.setdefault(stem_key, []).append(abs_path)
        index_stems = list(by_stem.keys())

        strategies = self._strategies_for_modes(modes, index_stems)

        resolved = 0
        ambiguous = 0
        no_match = 0
        cmds.undoInfo(openChunk=True, chunkName="Resolve Missing Textures")
        try:
            for node, current_path, stem in missing:
                stem_lower = stem.lower()
                match_name, _score, status, strat_name = (
                    FuzzyMatcher.find_with_fallbacks(
                        stem_lower,
                        index_stems,
                        strategies=strategies,
                        score_threshold=0.6,
                        ambiguity_delta=0.05,
                    )
                )
                if status == "no_match":
                    no_match += 1
                    continue
                if status == "ambiguous":
                    ambiguous += 1
                    cmds.warning(
                        f"{node}: ambiguous {strat_name} match for '{stem}', skipped."
                    )
                    continue
                matches = by_stem.get(match_name) or []
                if not matches:
                    no_match += 1
                    continue
                if len(matches) > 1:
                    ambiguous += 1
                    cmds.warning(
                        f"{node}: '{match_name}' resolves to {len(matches)} files, skipped."
                    )
                    continue
                final_abs = matches[0]
                new_path = to_project_relative(final_abs)
                try:
                    cmds.setAttr(f"{node}.fileTextureName", new_path, type="string")
                    self._previous_paths[node] = current_path
                    resolved += 1
                    om.MGlobal.displayInfo(
                        f"{node}: '{current_path}' -> '{new_path}'"
                    )
                except Exception as e:
                    cmds.warning(f"{node}: failed to set path: {e}")
        finally:
            cmds.undoInfo(closeChunk=True)

        om.MGlobal.displayInfo(
            f"Resolved {resolved}/{len(missing)} missing "
            f"(no match: {no_match}, ambiguous: {ambiguous}); "
            f"strategies: {', '.join(modes)}."
        )
        self.ui.tbl000.init_slot()

    # ------------------------------------------------------------------
    # Table refresh / scene callbacks
    # ------------------------------------------------------------------

    def refresh_texture_table(self):
        """Manual refresh trigger from the header refresh button."""
        table = getattr(self.ui, "tbl000", None)
        if not table:
            return
        table.init_slot()

    def _setup_scene_change_callback(self, widget):
        """Subscribe to scene-change events via ScriptJobManager."""
        mgr = ScriptJobManager.instance()
        for event in (
            "SceneOpened",
            "NewSceneOpened",
            "SceneImported",
            "workspaceChanged",
        ):
            mgr.subscribe(
                event,
                lambda w=widget: self._on_scene_change(w),
                owner=self,
            )
        mgr.connect_cleanup(widget, owner=self)

    def _on_scene_change(self, widget):
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def do_refresh():
            self._refresh_pending = False
            self._previous_paths.clear()
            try:
                try:
                    if not widget.isVisible():
                        pass
                except RuntimeError:
                    # Widget has been deleted (C++ object gone).
                    self.cleanup_scene_callbacks()
                    return
                print(
                    "TexturePathEditor: Scene changed, refreshing texture path table..."
                )
                self._refresh_table_content(widget)
            except Exception as e:
                print(f"TexturePathEditor: Error refreshing table on scene change: {e}")

        cmds.evalDeferred(do_refresh)

    def _refresh_table_content(self, widget):
        """Refresh the table content with current scene data."""
        cmds.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.clear()
            rows = MatUtils.get_file_nodes(
                return_type="shaderName|path|fileNodeName", raw=True
            )
            if not rows:
                rows = [("", "", "No file nodes found")]

            formatted = []
            for shader_name, path, file_node_name in rows:
                # Stash node names in UserRole so handle_cell_edit can recover
                # the old name after editing.
                formatted.append([
                    (shader_name, shader_name),
                    path,
                    (file_node_name, file_node_name),
                ])

            widget.add(formatted, headers=["Shader", "Texture Path", "File Node"])

            header = widget.horizontalHeader()
            header.setSectionsMovable(False)
            header.setSectionResizeMode(
                0, self.sb.QtWidgets.QHeaderView.Interactive
            )
            header.setSectionResizeMode(
                1, self.sb.QtWidgets.QHeaderView.Stretch
            )
            header.setSectionResizeMode(
                2, self.sb.QtWidgets.QHeaderView.Interactive
            )
            widget.setColumnWidth(0, 200)
            widget.setColumnWidth(2, 200)

            self.setup_formatting(widget)
            widget.apply_formatting()
        finally:
            widget.setUpdatesEnabled(True)
            cmds.waitCursor(state=False)

        if self._footer_controller:
            self._footer_controller.update()

    def cleanup_scene_callbacks(self):
        """Clean up scene-change subscriptions via ScriptJobManager."""
        ScriptJobManager.instance().unsubscribe_all(self)

    def setup_formatting(self, widget):
        source_root = EnvUtils.get_env_info("workspace")
        path_cache = {}
        unique_paths = set()
        for row in range(widget.rowCount()):
            item = widget.item(row, 1)
            if item:
                path = str(item.text()).strip()
                if path:
                    unique_paths.add(path)

        def resolve_and_check(path):
            abs_path = (
                os.path.normpath(os.path.join(source_root, path))
                if not os.path.isabs(path)
                else os.path.normpath(path)
            )
            return path, os.path.exists(abs_path), abs_path

        if len(unique_paths) > 50:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(resolve_and_check, p): p for p in unique_paths
                }
                for future in as_completed(futures):
                    try:
                        path, exists, abs_path = future.result(timeout=5)
                        path_cache[path] = (exists, abs_path)
                    except Exception:
                        path = futures[future]
                        abs_path = (
                            os.path.normpath(os.path.join(source_root, path))
                            if not os.path.isabs(path)
                            else os.path.normpath(path)
                        )
                        path_cache[path] = (False, abs_path)
        else:
            for path in unique_paths:
                _, exists, abs_path = resolve_and_check(path)
                path_cache[path] = (exists, abs_path)

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()
            if path in path_cache:
                exists, abs_path = path_cache[path]
            else:
                abs_path = (
                    os.path.normpath(os.path.join(source_root, path))
                    if not os.path.isabs(path)
                    else os.path.normpath(path)
                )
                exists = os.path.exists(abs_path)
                path_cache[path] = (exists, abs_path)
            widget.format_item(item, key="reset" if exists else "invalid")
            tooltip_lines = [abs_path if exists else f"Missing file:\n{abs_path}"]
            fn_item = widget.item(row, 2)
            fn_name = str(fn_item.text()).strip() if fn_item else ""
            previous = self._previous_paths.get(fn_name) if fn_name else None
            if previous and previous != path:
                tooltip_lines.append(f"Previous: {previous}")
            item.setToolTip("\n\n".join(tooltip_lines))

        widget.set_column_formatter(1, format_if_invalid)

    # ------------------------------------------------------------------
    # Context resolution helpers
    # ------------------------------------------------------------------

    def _file_nodes_from_selection(self, selection):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return []
        nodes = []
        for ctx in contexts:
            nodes.extend(ctx.get("file_nodes") or [])
        return list(dict.fromkeys(nodes))

    def _project_relative_converter(self):
        """Closure converting an absolute path to project-relative under sourceimages."""
        si = EnvUtils.get_env_info("sourceimages") or ""
        si_abs = os.path.abspath(si).replace("\\", "/") if si else ""
        si_name = os.path.basename(si) if si else ""

        def to_relative(abs_path: str) -> str:
            norm = os.path.normpath(abs_path).replace("\\", "/")
            if si_abs and norm.lower().startswith(si_abs.lower()):
                rel = os.path.relpath(norm, si_abs).replace("\\", "/")
                if si_name and not rel.startswith(si_name + "/"):
                    return f"{si_name}/{rel}"
                return rel
            return norm

        return to_relative

    def _resolve_context(self, shader_name, file_node_data):
        shader_name = str(shader_name).strip() if shader_name else ""
        shader_node = shader_name if shader_name else None

        if isinstance(file_node_data, (list, tuple)):
            file_node_data = next(
                (v for v in file_node_data if v and cmds.objExists(str(v))),
                None,
            )

        material_file_nodes = []
        if file_node_data:
            material_file_nodes = [file_node_data]
        elif shader_node:
            # listHistory directly on the shader is much faster than
            # rebuilding the entire scene-wide mapping via get_file_nodes.
            try:
                history = cmds.ls(cmds.listHistory(shader_node) or [], type="file") or []
                material_file_nodes = list(dict.fromkeys(history))
            except Exception:
                material_file_nodes = []

        return {
            "shader_name": shader_name,
            "shader_node": shader_node,
            "file_node": file_node_data,
            "file_nodes": material_file_nodes,
        }

    def _get_selected_contexts(
        self,
        selection=None,
        require_file_nodes: bool = True,
        warn_on_empty: bool = False,
    ):
        table = getattr(self.ui, "tbl000", None)
        if table is None:
            return []

        if selection is None:
            selection = table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS,
                include_current=True,
            )

        if not selection:
            if warn_on_empty:
                cmds.warning("No row selected.")
            return []

        contexts = []
        for entry in selection:
            shader_value = self._selection_value(entry, "shader")
            file_node_value = self._selection_value(entry, "file_node")
            context = self._resolve_context(shader_value, file_node_value)
            if require_file_nodes and not context["file_nodes"]:
                continue
            contexts.append(context)

        if require_file_nodes and not contexts:
            if warn_on_empty:
                cmds.warning("No valid file nodes found in the selected row(s).")
            return []
        return contexts

    def _selection_value(self, entry, key: str):
        if hasattr(entry, "values"):
            return entry.values.get(key)
        if hasattr(entry, "get"):
            try:
                value = entry.get(key)
                if value is not None:
                    return value
            except TypeError:
                pass
        column = self._ROW_SELECTION_COLUMNS.get(key)
        if column is not None and isinstance(entry, dict):
            return entry.get(column)
        return None

    def _resolve_absolute_texture_path(self, file_node):
        try:
            path_value = cmds.getAttr(f"{file_node}.fileTextureName")
        except Exception:
            return ""
        if not path_value:
            return ""
        project_sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        if os.path.isabs(path_value):
            return os.path.abspath(path_value)
        return os.path.abspath(os.path.join(project_sourceimages, path_value))

    # ------------------------------------------------------------------
    # Cell editing
    # ------------------------------------------------------------------

    def handle_cell_edit(self, row: int, col: int):
        tbl = self.ui.tbl000
        item = tbl.item(row, col)
        if not item:
            return
        new_value = item.text()
        UserRole = self.sb.QtCore.Qt.UserRole

        def _restore_text(target_item, original):
            tbl.blockSignals(True)
            try:
                target_item.setText(original)
            finally:
                tbl.blockSignals(False)

        def _rename_node(label):
            old_name = item.data(UserRole)
            if not old_name:
                _restore_text(item, new_value)
                return
            if new_value == old_name:
                return
            if not cmds.objExists(old_name):
                cmds.warning(f"{label} '{old_name}' no longer exists; cannot rename.")
                _restore_text(item, old_name)
                return
            try:
                actual = cmds.rename(old_name, new_value)
            except Exception as e:
                cmds.warning(f"Failed to rename {label}: {e}")
                _restore_text(item, old_name)
                return
            item.setData(UserRole, actual)
            if actual != new_value:
                _restore_text(item, actual)
            om.MGlobal.displayInfo(f"Renamed {label} '{old_name}' -> '{actual}'")

        if col == 0:
            _rename_node("shader")
        elif col == 1:
            fn_item = tbl.item(row, 2)
            file_node = fn_item.data(UserRole) if fn_item else None
            if not file_node:
                cmds.warning("No file node associated with this row.")
                return
            if not cmds.objExists(file_node):
                cmds.warning(f"File node '{file_node}' no longer exists.")
                return
            try:
                cmds.setAttr(
                    f"{file_node}.fileTextureName", new_value, type="string"
                )
                om.MGlobal.displayInfo(
                    f"{file_node}: texture path -> '{new_value}'"
                )
                tbl.apply_formatting()
                if self._footer_controller:
                    self._footer_controller.update()
            except Exception as e:
                cmds.warning(f"Failed to update texture path: {e}")
        elif col == 2:
            _rename_node("file node")

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _create_footer_controller(self):
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        return FooterStatusController(
            footer=footer,
            resolver=self._resolve_source_images_path,
            default_text="",
            truncate_kwargs={"length": 96, "mode": "middle"},
        )

    def _resolve_source_images_path(self) -> str:
        return EnvUtils.get_env_info("sourceimages") or ""


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
