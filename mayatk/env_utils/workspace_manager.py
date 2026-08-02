# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
import os
from typing import Optional

import pythontk as ptk

# from this package:
from mayatk.env_utils._env_utils import EnvUtils


class WorkspaceManager(ptk.HelpMixin, ptk.LoggingMixin):
    """Shared workspace management utilities for UI components."""

    # Glob patterns the workspace-file cache scans for. A subclass widens this to list more
    # than the two native scene formats (the Reference Manager adds "*.fbx", which Maya
    # references natively) without reimplementing the scan.
    SCENE_FILE_TYPES: tuple[str, ...] = EnvUtils.SCENE_FILE_TYPES

    def __init__(self):
        self._workspace_files = None
        self._recursive_search = True
        self._ignore_empty_workspaces = True
        self._current_working_dir = None

    @property
    def current_workspace(self):
        """Get the current Maya workspace with fallback handling.

        Normalized (``workspace -q -rd`` keeps a trailing slash, which would
        collapse a later ``os.path.basename()`` to "").
        """
        try:
            workspace = cmds.workspace(q=True, rd=True)
            # If Maya returns "." or an invalid/relative path, use a reasonable default
            if (
                workspace == "."
                or not os.path.isabs(workspace)
                or not os.path.exists(workspace)
            ):
                self.logger.debug("Maya workspace invalid, using fallback")
                return self._get_fallback_workspace()
            return os.path.normpath(workspace)
        except Exception as e:
            # If Maya is not available or fails, use a reasonable default
            self.logger.debug(f"Maya workspace query failed: {e}, using fallback")
            return self._get_fallback_workspace()

    def _get_fallback_workspace(self):
        """Get a fallback workspace directory when Maya is not available."""
        # Try user's Documents folder first
        documents = os.path.expanduser("~/Documents")
        if os.path.isdir(documents):
            return documents

        # Try user's home directory
        home = os.path.expanduser("~")
        if os.path.isdir(home):
            return home

        # Last resort: use current working directory's parent
        return os.path.dirname(os.getcwd())

    @property
    def current_working_dir(self):
        """Get the current working directory."""
        if self._current_working_dir is None or not os.path.isdir(
            self._current_working_dir
        ):
            self._current_working_dir = self.current_workspace

        return self._current_working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):
        """Set the current working directory and invalidate cache."""
        if os.path.isdir(value):
            self._current_working_dir = value
            self.invalidate_workspace_files()

    @property
    def recursive_search(self):
        """Whether to search recursively for files."""
        return self._recursive_search

    @recursive_search.setter
    def recursive_search(self, value):
        """Set recursive search and invalidate cache."""
        self._recursive_search = value
        self.invalidate_workspace_files()

    @property
    def ignore_empty_workspaces(self):
        """Whether to ignore empty workspaces when searching."""
        return self._ignore_empty_workspaces

    @ignore_empty_workspaces.setter
    def ignore_empty_workspaces(self, value):
        """Set ignore empty workspaces and invalidate cache."""
        self._ignore_empty_workspaces = value
        self.invalidate_workspace_files()

    @property
    def workspace_files(self) -> dict[str, list[str]]:
        """Get cached workspace file dictionary, rebuilding if needed."""
        if self._workspace_files is None:
            self.invalidate_workspace_files()
        return self._workspace_files

    def find_available_workspaces(self, root_dir: str = None) -> list:
        """Find all available workspaces under the given root directory.

        Args:
            root_dir: Directory to search in. If None, uses current_working_dir.

        Returns:
            List of (dirname, path) tuples for found workspaces.
        """
        if root_dir is None:
            root_dir = self.current_working_dir

        if not root_dir or not os.path.isdir(root_dir):
            return []

        return EnvUtils.find_workspaces(
            root_dir,
            return_type="dirname|dir",
            ignore_empty=self.ignore_empty_workspaces,
            recursive=self.recursive_search,
            file_types=self.SCENE_FILE_TYPES,
        )

    def invalidate_workspace_files(self):
        """Scan for workspaces and rebuild the file cache."""
        self._workspace_files = {}

        workspaces = self.find_available_workspaces()

        if not workspaces:
            return

        for _, ws_path in workspaces:
            if os.path.isdir(ws_path):
                # Always search recursively within the workspace (including scenes/ subdirectory)
                # The recursive_search setting only controls workspace discovery
                scenes = EnvUtils.get_workspace_scenes(
                    root_dir=ws_path,
                    full_path=True,
                    recursive=True,
                    omit_autosave=True,
                    file_types=list(self.SCENE_FILE_TYPES),
                )
                self._workspace_files[ws_path] = scenes

    def resolve_file_path(self, selected_file: str) -> Optional[str]:
        """Resolve a file name to its full path by searching in workspace files."""
        return EnvUtils.resolve_file_path_in_workspaces(
            selected_file, self.workspace_files
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
