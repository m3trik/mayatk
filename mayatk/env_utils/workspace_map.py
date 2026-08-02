# !/usr/bin/python
# coding=utf-8
import os
from typing import Optional, Dict, List

import pythontk as ptk
# from this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager


class WorkspaceMap(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin):
    """Maps and displays Maya workspaces in a tree structure.

    Features:
    - Discovers workspaces recursively from a root directory
    - Displays workspace hierarchy in a tree widget
    - Shows workspace details (scene count, recent files, etc.)
    - Supports filtering and searching workspaces
    - Creates new projects from the shared workspace template, and promotes an
      existing folder to a shared Maya/Blender project
    """

    def __init__(self, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self._filter_text = ""
        self._workspace_data = {}

    @property
    def current_working_dir(self):
        """Get the current working directory for workspace discovery."""
        return super().current_working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):
        """Set the current working directory and invalidate cache."""
        if os.path.isdir(value):
            self._current_working_dir = value
            self.invalidate_workspace_data()

    @property
    def recursive_search(self):
        """Whether to search recursively for workspaces."""
        return super().recursive_search

    @recursive_search.setter
    def recursive_search(self, value):
        """Set recursive search and invalidate cache."""
        self._recursive_search = value
        self.invalidate_workspace_data()

    @property
    def workspace_data(self) -> Dict[str, Dict]:
        """Get cached workspace data, rebuilding if needed."""
        if not hasattr(self, "_workspace_data") or self._workspace_data is None:
            self.invalidate_workspace_data()
        return self._workspace_data

    def invalidate_workspace_data(self):
        """Scan for workspaces and build data cache."""
        self.logger.debug(f"Scanning for workspaces under: {self.current_working_dir}")
        self._workspace_data = {}

        workspaces = self.find_available_workspaces()

        if not workspaces:
            self.logger.warning("No valid workspaces found.")

        for workspace_name, workspace_path in workspaces:
            if os.path.isdir(workspace_path):
                workspace_info = self._analyze_workspace(workspace_path)
                workspace_info["name"] = workspace_name
                workspace_info["path"] = workspace_path
                self._workspace_data[workspace_path] = workspace_info

    def _analyze_workspace(self, workspace_path: str) -> Dict:
        """Analyze a workspace and return information about it.

        Deliberately cheap: one ``stat`` per scene file (reused for both the
        recent-files sort and the reported size) plus one ``listdir``. An
        earlier version walked the workspace's ENTIRE tree to total every file
        on disk — for every discovered workspace, synchronously, on each
        refresh — which stalls the UI for seconds on a real project root.
        ``size_mb`` is therefore the size of the *scene files*, which is what a
        scene browser is actually reporting on.

        Args:
            workspace_path: Path to the workspace directory

        Returns:
            Dictionary containing workspace analysis data
        """
        info = {
            "scene_count": 0,
            "scenes": [],
            "recent_files": [],
            "subdirectories": [],
            "size_mb": 0,
            "last_modified": None,
        }

        try:
            # Get scene files. Always recursive: ``recursive_search`` governs
            # workspace *discovery* only (same rule ``invalidate_workspace_files``
            # states) — a project's scenes/sub/ files are still its scenes.
            scenes = EnvUtils.get_workspace_scenes(
                root_dir=workspace_path,
                full_path=True,
                recursive=True,
                omit_autosave=True,
                file_types=self.SCENE_FILE_TYPES,
            )

            info["scenes"] = scenes
            info["scene_count"] = len(scenes)

            # One stat per scene: feeds the recent-files sort AND the size total.
            scene_data = []
            total_size = 0
            for scene in scenes:
                try:
                    stat = os.stat(scene)
                except OSError:
                    continue
                scene_data.append((scene, stat.st_mtime))
                total_size += stat.st_size

            if scene_data:
                # Sort by modification time, most recent first
                scene_data.sort(key=lambda x: x[1], reverse=True)
                info["recent_files"] = [s[0] for s in scene_data[:5]]  # Top 5
                info["last_modified"] = scene_data[0][1]
            info["size_mb"] = total_size / (1024 * 1024)  # Convert to MB

            # Get subdirectories
            try:
                subdirs = [
                    d
                    for d in os.listdir(workspace_path)
                    if os.path.isdir(os.path.join(workspace_path, d))
                ]
                info["subdirectories"] = subdirs
            except OSError:
                pass

        except Exception as e:
            self.logger.error(f"Error analyzing workspace {workspace_path}: {e}")

        return info

    def get_workspace_tree_data(self, filter_text: str = None) -> Dict:
        """Get workspace data organized for tree display.

        Args:
            filter_text: Optional filter text to limit results

        Returns:
            Dictionary organized for tree widget display
        """
        tree_data = {}

        for workspace_path, workspace_info in self.workspace_data.items():
            workspace_name = workspace_info["name"]

            # Apply filter if provided
            if filter_text and filter_text.strip():
                if filter_text.lower() not in workspace_name.lower():
                    continue

            # Organize by parent directory for tree structure
            parent_dir = os.path.dirname(workspace_path)
            parent_name = os.path.basename(parent_dir) if parent_dir else "Root"

            if parent_name not in tree_data:
                tree_data[parent_name] = {
                    "path": parent_dir,
                    "workspaces": {},
                    "type": "directory",
                }

            tree_data[parent_name]["workspaces"][workspace_name] = {
                **workspace_info,
                "type": "workspace",
            }

        return tree_data

    def get_filtered_workspaces(self, filter_text: str = None) -> List[Dict]:
        """Get a filtered list of workspaces.

        Args:
            filter_text: Filter text to apply

        Returns:
            List of workspace dictionaries matching the filter
        """
        workspaces = []

        for workspace_path, workspace_info in self.workspace_data.items():
            if filter_text and filter_text.strip():
                if filter_text.lower() not in workspace_info["name"].lower():
                    continue

            workspaces.append(workspace_info)

        return workspaces

    # Named create_/mark_PROJECT, not *_workspace: EnvUtils already owns
    # ``create_workspace(root, …)`` / ``promote_workspace(root)`` and these take
    # different arguments — same name, different contract is a trap for anyone
    # calling them through a subclass.
    def create_project(self, name: str) -> Optional[ptk.Workspace]:
        """Create a project named *name* under the current root, built from the
        ACTIVE workspace template (shared with blendertk) — the piece Maya's
        native Project Window has no scripted equivalent for."""
        root = self.current_working_dir
        if not (root and os.path.isdir(root)):
            raise OSError(f"Invalid root directory: {root}")
        ws = EnvUtils.create_workspace(os.path.join(root, name))
        self.invalidate_workspace_data()
        return ws

    def mark_root_as_project(self) -> Optional[ptk.Workspace]:
        """Mark the current ROOT directory as a shared Maya/Blender project —
        a workspace.mel describing the layout it already has; moves no files.

        The root, not a tree selection: discovery is marker-only, so every row
        in the tree is a project already and promoting one is a guaranteed
        no-op. The folder that needs promoting is the unmarked one the user
        just browsed to — which is exactly what the root field holds.
        """
        root = self.current_working_dir
        if ptk.Workspace(root).is_marked:
            return None
        ws = EnvUtils.promote_workspace(root)
        self.invalidate_workspace_data()
        return ws


class WorkspaceMapController(WorkspaceMap, ptk.LoggingMixin):
    """Controller for the WorkspaceMap UI components."""

    def __init__(self, slot, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.slot = slot
        self.sb = slot.sb
        self.ui = slot.ui

        self._last_dir_valid = None
        self._updating_directory = False
        self.logger.debug("WorkspaceMapController initialized.")

    def update_current_dir(self, text: Optional[str] = None):
        """Update the current working directory from UI input."""
        if self._updating_directory:
            self.logger.debug(
                "update_current_dir: Already updating directory, skipping"
            )
            return

        self._updating_directory = True
        try:
            text = text or self.ui.txt000.text()
            new_dir = os.path.normpath(text.strip())

            is_valid = os.path.isdir(new_dir)
            changed = new_dir != self.current_working_dir

            self.logger.debug(
                f"update_current_dir: new_dir='{new_dir}', current='{self.current_working_dir}', is_valid={is_valid}, changed={changed}"
            )

            self.ui.txt000.setToolTip(new_dir if is_valid else "Invalid directory")
            self.ui.txt000.set_action_color("reset" if is_valid else "invalid")

            revalidate = is_valid and (changed or self._last_dir_valid is False)
            self._last_dir_valid = is_valid

            if revalidate:
                self.logger.debug(
                    "update_current_dir: Revalidating and updating current working dir."
                )
                self.current_working_dir = new_dir
                self._update_workspace_tree()
            elif not is_valid:
                self.logger.debug(
                    "update_current_dir: Directory is not valid, clearing tree."
                )
                self.ui.tree000.clear()
                self.current_working_dir = new_dir
            else:
                self.logger.debug("update_current_dir: No revalidation needed")
        finally:
            self._updating_directory = False

    def _update_workspace_tree(self, filter_text: Optional[str] = None):
        """Rebuild the workspace tree widget (optionally filtered)."""
        self.logger.debug(f"_update_workspace_tree: filter={filter_text!r}")
        self._populate_tree_widget(self.get_workspace_tree_data(filter_text))

    def _populate_tree_widget(self, tree_data: Dict):
        """Clear and repopulate the tree widget with workspace data.

        The clear is part of populating — a filter keystroke that repopulated
        without it appended a duplicate set of rows on every character typed.

        Args:
            tree_data: Dictionary containing organized tree data
        """
        tree = self.ui.tree000
        tree.clear()

        for parent_name, parent_data in tree_data.items():
            # Create parent item (directory)
            parent_item = self.sb.QtWidgets.QTreeWidgetItem(tree)
            parent_item.setText(0, parent_name)
            parent_item.setData(0, self.sb.QtCore.Qt.UserRole, parent_data)

            # Add workspace children
            for workspace_name, workspace_data in parent_data.get(
                "workspaces", {}
            ).items():
                workspace_item = self.sb.QtWidgets.QTreeWidgetItem(parent_item)
                workspace_item.setText(0, workspace_name)
                workspace_item.setText(1, str(workspace_data.get("scene_count", 0)))
                workspace_item.setText(2, f"{workspace_data.get('size_mb', 0):.1f} MB")
                workspace_item.setData(0, self.sb.QtCore.Qt.UserRole, workspace_data)

        # Expand all items by default
        tree.expandAll()

    def refresh_tree(self, invalidate: bool = False):
        """Refresh the workspace tree.

        Args:
            invalidate: Whether to force a complete rebuild of the scan cache
                (otherwise only the tree widget is repopulated).
        """
        if invalidate:
            self.invalidate_workspace_data()
        self._update_workspace_tree()

    def selected_workspace(self) -> Optional[Dict]:
        """The workspace record under the tree cursor, or None (a directory
        grouping row selects to None)."""
        current_item = self.ui.tree000.currentItem()
        if current_item is None:
            return None
        data = current_item.data(0, self.sb.QtCore.Qt.UserRole)
        if isinstance(data, dict) and data.get("type") == "workspace":
            return data
        return None

    def open_selected_workspace(self) -> Optional[str]:
        """Set Maya's project to the selected workspace. Returns the opened root,
        or None when nothing valid is selected."""
        data = self.selected_workspace()
        path = (data or {}).get("path")
        if not (path and os.path.isdir(path)):
            return None
        opened = EnvUtils.set_current_workspace(path)
        self.logger.info(f"Opened workspace: {opened}")
        return opened or None


class WorkspaceMapSlots(ptk.HelpMixin, ptk.LoggingMixin):
    """UI slots for the WorkspaceMap interface."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.workspace_map

        self._initializing = True

        self.controller = WorkspaceMapController(self)
        self.ui.txt000.setText(self.controller.current_working_dir)

        # Connect buttons
        self.ui.b000.clicked.connect(self.browse_directory)
        self.ui.b001.clicked.connect(self.set_to_workspace)
        self.ui.b002.clicked.connect(
            lambda: self.controller.refresh_tree(invalidate=True)
        )

        self._initializing = False

        # Initial tree population
        self.sb.defer_with_timer(
            lambda: self.controller.refresh_tree(invalidate=True), ms=100
        )

        self.logger.debug("WorkspaceMapSlots initialized.")

    def header_init(self, widget):
        """Project creation actions + header help text.

        No ``config_buttons`` call: this panel takes the handler-installed
        default chrome (menu / collapse / pin-or-hide), and configuring it here
        would silently drop the collapse + dismissal buttons.
        """
        widget.menu.add("Separator", setTitle="Project:")
        widget.menu.add(
            "QPushButton",
            setText="New Project",
            setObjectName="new_project",
            setToolTip="Create a project under the root directory, built from the\n"
            "ACTIVE workspace template (shared with blendertk). Maya's own\n"
            "Project Window can customize a project but cannot build one from\n"
            "a saved template.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Mark Root As Shared Project",
            setObjectName="mark_root",
            setToolTip="Write a workspace.mel into the ROOT folder describing the layout\n"
            "it already has, so Maya and Blender both resolve it as a project.\n"
            "No files move. For the unmarked folder you just browsed to — the\n"
            "tree itself only ever lists folders that are projects already.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Save Rules As Template",
            setObjectName="save_template",
            setToolTip="Publish the ACTIVE project's file rules as a named workspace\n"
            "template — what every subsequent New Project (in Maya or Blender)\n"
            "is built from.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Workspace Map",
                body="Browse a directory tree of Maya workspaces and switch "
                "between them. A workspace is a folder holding a "
                "<i>workspace.mel</i> — the shared Maya/Blender project "
                "format, so projects authored in either DCC show up here.",
                steps=[
                    "Enter or browse to a root directory in the "
                    "<b>Directory</b> field (option box ▸ for browse / "
                    "Set To Workspace / Recursive Search).",
                    "Expand the tree and <b>double-click</b> a workspace — "
                    "Maya's <i>workspace</i> command switches to it (the "
                    "right-click menu does the same, plus Explore Folder).",
                    "The header menu (▾) creates a project from the active "
                    "workspace template, or publishes the current project's "
                    "rules as a template.",
                ],
                sections=[
                    (
                        "Directory field option box (▸)",
                        [
                            "<b>Browse…</b> — pick a root directory.",
                            "<b>Set To Workspace</b> — set the root to the "
                            "current workspace's directory.",
                            "<b>Recursive Search</b> — also discover nested "
                            "workspace folders under each child folder.",
                        ],
                    ),
                    (
                        "Header menu (▾)",
                        [
                            "<b>New Project</b> — build one under the root from "
                            "the active workspace template.",
                            "<b>Mark Root As Shared Project</b> — write a "
                            "workspace.mel describing the root folder's existing "
                            "layout, so Blender sees it as a project too.",
                            "<b>Save Rules As Template</b> — publish the active "
                            "project's rules for future New Projects.",
                        ],
                    ),
                    (
                        "Tree right-click",
                        [
                            "<b>Open Workspace</b> — make it Maya's project.",
                            "<b>Explore Folder</b> — open it in the file browser.",
                        ],
                    ),
                ],
            )
        )

    def txt000_init(self, widget):
        """Initialize the directory input widget."""
        self.logger.debug(
            f"txt000_init called, is_initialized: {getattr(widget, 'is_initialized', False)}"
        )

        if not widget.is_initialized:
            from uitk.widgets.optionBox.options.browse import BrowseOption

            self._browse_option = BrowseOption(
                wrapped_widget=widget,
                mode="directory",
                title="Select a root directory",
                start_dir=lambda: self.controller.current_workspace,
            )
            widget.option_box.add_option(self._browse_option)

            # objectName must NOT shadow the .ui's b001 (same dispatch /
            # state key); name it after the slot so menu auto-wiring
            # dispatches to set_to_workspace like the main button does.
            widget.menu.add(
                "QPushButton",
                setText="Set To Workspace",
                setObjectName="set_to_workspace",
                setToolTip="Set the root folder to that of the current workspace.",
            )
            widget.menu.add(
                "QCheckBox",
                setText="Recursive Search",
                setObjectName="chk000",
                setChecked=True,
                setToolTip="Also search sub-folders.",
            )

            widget.textChanged.connect(
                lambda text: self.sb.defer_with_timer(
                    lambda: self.controller.update_current_dir(text), ms=500
                )
            )
            self.logger.debug("txt000 text input initialized.")

        self.controller.update_current_dir()

    def txt001_init(self, widget):
        """Initialize the filter input widget."""
        self.logger.debug(
            f"txt001_init called, is_initialized: {getattr(widget, 'is_initialized', False)}"
        )

        if not widget.is_initialized:
            widget.setPlaceholderText("Filter workspaces...")
            widget.textChanged.connect(self.filter_workspaces)
            self.logger.debug("txt001 filter input initialized.")

    def tree000_init(self, widget):
        """Initialize the workspace tree widget."""
        if not widget.is_initialized:
            widget.setColumnCount(3)
            widget.setHeaderLabels(["Workspace", "Scenes", "Size"])
            widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.SingleSelection)
            widget.setAlternatingRowColors(True)
            # Double-click (NOT selection) switches Maya's project: changing the
            # active project is a scene-wide side effect, so it needs a deliberate
            # gesture — merely arrowing through the tree must not retarget it.
            widget.itemDoubleClicked.connect(lambda *_: self.btn_open_workspace())

            # Add context menu
            widget.menu.add(
                "QPushButton",
                setText="Open Workspace",
                setObjectName="btn_open_workspace",
                setToolTip="Set Maya workspace to selected workspace",
            )
            widget.menu.add(
                "QPushButton",
                setText="Explore Folder",
                setObjectName="btn_explore_folder",
                setToolTip="Open workspace folder in file explorer",
            )

            self.logger.debug("tree000 workspace tree initialized.")

    def filter_workspaces(self, text):
        """Handle filter text changes."""
        self.logger.debug(f"Filter text changed: {text}")
        self.controller._update_workspace_tree(filter_text=text)

    def chk000(self, checked):
        """Handle recursive search toggle."""
        if getattr(self, "_initializing", False):
            self.logger.debug("chk000 called during initialization - ignoring")
            return

        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("chk000 called during directory update - ignoring")
            return

        self.logger.debug(f"chk000 recursive search toggled: {checked}")

        if isinstance(checked, int):
            checked_bool = checked == 2  # Qt.Checked
        else:
            checked_bool = bool(checked)

        old_recursive = self.controller.recursive_search

        if old_recursive == checked_bool:
            self.logger.debug("chk000 recursive search unchanged, no refresh needed")
            return

        self.controller.recursive_search = checked_bool
        self.logger.debug("chk000 recursive search changed, refreshing tree")
        self.controller.refresh_tree(invalidate=True)

    def browse_directory(self):
        """Browse for a root directory."""
        if hasattr(self, "_browse_option"):
            self._browse_option.browse()
            return

        start_dir = self.ui.txt000.text()
        if not os.path.isdir(start_dir):
            start_dir = self.controller.current_workspace

        selected_directory = self.sb.dir_dialog(
            "Select a root directory", start_dir=start_dir
        )
        self.logger.debug(f"browse_directory selected: {selected_directory}")
        if selected_directory:
            self.ui.txt000.setText(selected_directory)

    def set_to_workspace(self):
        """Set directory to current Maya workspace."""
        self.logger.debug("set_to_workspace clicked.")
        self.ui.txt000.setText(self.controller.current_workspace)

    def btn_open_workspace(self):
        """Open selected workspace in Maya."""
        opened = self.controller.open_selected_workspace()
        if opened:
            self.sb.message_box(f"Workspace set to:<br>{opened}")

    def btn_explore_folder(self):
        """Open selected workspace folder in file explorer."""
        data = self.controller.selected_workspace()
        path = (data or {}).get("path")
        if path and os.path.isdir(path):
            ptk.open_explorer(path)
            self.logger.info(f"Opened folder: {path}")

    # ------------------------------------------------------------------ project creation
    def new_project(self):
        """Create a project under the root directory from the ACTIVE template."""
        name = (
            self.sb.input_dialog("New Project", "Project folder name:", "") or ""
        ).strip()
        if not name:
            return
        try:
            ws = self.controller.create_project(name)
        except OSError as e:
            self.sb.message_box(str(e))
            return
        self.controller.refresh_tree()
        if ws is not None:
            self.sb.message_box(f"Created project:<br>{ws.root}")

    def mark_root(self):
        """Promote the ROOT directory to a shared Maya/Blender project."""
        root = self.controller.current_working_dir
        if ptk.Workspace(root).is_marked:
            self.sb.message_box(
                f"<hl>{os.path.basename(os.path.normpath(root))}</hl> is already "
                "a project."
            )
            return
        try:
            ws = self.controller.mark_root_as_project()
        except OSError as e:
            self.sb.message_box(str(e))
            return
        if ws is None:
            self.sb.message_box("Set a valid root directory first.")
            return
        self.controller.refresh_tree()
        self.sb.message_box(f"Marked as a shared project:<br>{ws.root}")

    def save_template(self):
        """Publish the ACTIVE project's file rules as a named workspace template."""
        name = (
            self.sb.input_dialog("Save Template", "Template name:", "") or ""
        ).strip()
        if not name:
            return
        try:
            saved = EnvUtils.save_workspace_template(name)
        except ValueError as e:
            self.sb.message_box(str(e))
            return
        self.sb.message_box(
            f"Saved template <hl>{saved}</hl> — new projects build from it."
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("workspace_map", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
