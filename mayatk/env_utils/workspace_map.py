# !/usr/bin/python
# coding=utf-8
import os
from typing import Optional, Dict, List

import pythontk as ptk

# from this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager


class WorkspaceMap(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin):
    """Maps Maya workspaces for a tree view (the Workspace Map panel: tentacle's
    ``slots/tools/workspace_map.py``).

    Features:
    - Discovers workspaces recursively from a root directory
    - Shapes them as a folder -> workspace tree (:meth:`get_workspace_tree_data`)
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


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
