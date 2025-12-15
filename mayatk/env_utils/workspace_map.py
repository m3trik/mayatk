# !/usr/bin/python
# coding=utf-8
import os
import re
from typing import Optional, Dict, List, Tuple

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
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
    - Provides workspace navigation and selection
    """

    def __init__(self):
        super().__init__()
        self._filter_text = ""
        self._workspace_data = {}
        self.prefilter_regex = re.compile(r".+\.\d{4}\.(ma|mb)$")

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
            # Get scene files
            scenes = EnvUtils.get_workspace_scenes(
                root_dir=workspace_path,
                full_path=True,
                recursive=self.recursive_search,
                omit_autosave=True,
            )

            info["scenes"] = scenes
            info["scene_count"] = len(scenes)

            # Get recent files (sorted by modification time)
            if scenes:
                scene_data = []
                for scene in scenes:
                    try:
                        mod_time = os.path.getmtime(scene)
                        scene_data.append((scene, mod_time))
                    except OSError:
                        continue

                # Sort by modification time, most recent first
                scene_data.sort(key=lambda x: x[1], reverse=True)
                info["recent_files"] = [
                    s[0] for s in scene_data[:5]
                ]  # Top 5 recent files

                if scene_data:
                    info["last_modified"] = scene_data[0][1]

            # Get workspace size (approximate)
            try:
                total_size = 0
                for dirpath, dirnames, filenames in os.walk(workspace_path):
                    for filename in filenames:
                        filepath = os.path.join(dirpath, filename)
                        try:
                            total_size += os.path.getsize(filepath)
                        except OSError:
                            continue
                info["size_mb"] = total_size / (1024 * 1024)  # Convert to MB
            except OSError:
                pass

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

    def refresh_workspace_data(self, invalidate: bool = False):
        """Refresh the workspace data cache.

        Args:
            invalidate: Whether to force a complete rebuild
        """
        if invalidate:
            self.invalidate_workspace_data()


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

    def _update_workspace_tree(self):
        """Update the workspace tree widget."""
        self.logger.debug("_update_workspace_tree: Updating workspace tree")

        # Get tree data
        tree_data = self.get_workspace_tree_data()

        # Clear existing tree
        self.ui.tree000.clear()

        # Populate tree
        self._populate_tree_widget(tree_data)

    def _populate_tree_widget(self, tree_data: Dict):
        """Populate the tree widget with workspace data.

        Args:
            tree_data: Dictionary containing organized tree data
        """
        tree = self.ui.tree000

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
            invalidate: Whether to force a complete rebuild
        """
        self.refresh_workspace_data(invalidate=invalidate)
        self._update_workspace_tree()

    def handle_tree_selection(self):
        """Handle tree item selection."""
        tree = self.ui.tree000
        current_item = tree.currentItem()

        if current_item:
            workspace_data = current_item.data(0, self.sb.QtCore.Qt.UserRole)
            if workspace_data and workspace_data.get("type") == "workspace":
                workspace_path = workspace_data.get("path")
                self.logger.info(f"Selected workspace: {workspace_path}")
                # You can add more selection handling here


class WorkspaceMapSlots(ptk.HelpMixin, ptk.LoggingMixin):
    """UI slots for the WorkspaceMap interface."""

    def __init__(self, switchboard, log_level="DEBUG"):
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

    def txt000_init(self, widget):
        """Initialize the directory input widget."""
        self.logger.debug(
            f"txt000_init called, is_initialized: {getattr(widget, 'is_initialized', False)}"
        )

        if not widget.is_initialized:
            widget.menu.add(
                "QPushButton",
                setText="Browse",
                setObjectName="b000",
                setToolTip="Open a file browser to select a root directory.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Set To Workspace",
                setObjectName="b001",
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
            widget.itemSelectionChanged.connect(self.controller.handle_tree_selection)

            # Add context menu
            widget.menu.setTitle("Workspace Options:")
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
        # Apply filter and refresh tree
        tree_data = self.controller.get_workspace_tree_data(filter_text=text)
        self.controller._populate_tree_widget(tree_data)

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
        tree = self.ui.tree000
        current_item = tree.currentItem()

        if current_item:
            workspace_data = current_item.data(0, self.sb.QtCore.Qt.UserRole)
            if workspace_data and workspace_data.get("type") == "workspace":
                workspace_path = workspace_data.get("path")
                if workspace_path and os.path.isdir(workspace_path):
                    pm.workspace(workspace_path, o=True)
                    self.logger.info(f"Opened workspace: {workspace_path}")
                    self.sb.message_box(f"Workspace set to:<br>{workspace_path}")

    def btn_explore_folder(self):
        """Open selected workspace folder in file explorer."""
        tree = self.ui.tree000
        current_item = tree.currentItem()

        if current_item:
            workspace_data = current_item.data(0, self.sb.QtCore.Qt.UserRole)
            if workspace_data and workspace_data.get("type") == "workspace":
                workspace_path = workspace_data.get("path")
                if workspace_path and os.path.isdir(workspace_path):
                    ptk.open_explorer(workspace_path)
                    self.logger.info(f"Opened folder: {workspace_path}")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("workspace_map", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
