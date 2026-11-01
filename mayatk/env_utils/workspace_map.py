# !/usr/bin/python
# coding=utf-8
"""Workspace Map -- Maya's host adapter for ``ptk.WorkspaceMapBase``.

The scan, its cache, each workspace's measure and the folder -> workspace tree
are pythontk's (``file_utils/workspace_map.py``), shared with blendertk. What
stays here is what reaches Maya, as :class:`MayaWorkspaceMapHost` (a
:class:`pythontk.WorkspaceMapHost`):

- ``current_workspace`` -- Maya's project folder (``WorkspaceManager``'s rule,
  the Reference Manager's too: a Documents fallback when Maya has none);
- ``find_workspaces`` -- marked folders holding a Maya scene
  (``EnvUtils.find_workspaces``);
- ``scene_files`` -- a workspace's ``.ma`` / ``.mb`` files, autosaves left out
  (``EnvUtils.get_workspace_scenes``);
- ``create_workspace`` / ``promote_workspace`` -- ``EnvUtils``'.

:class:`WorkspaceMap` is the shared class bound to it: configuration only. Its
panel is the map of tentacle's Workspaces tool (``slots/tools/workspaces.py``).
"""

from typing import List, Optional

import pythontk as ptk

# from this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager


class MayaWorkspaceMapHost:
    """What the Workspace Map asks of Maya (:class:`pythontk.WorkspaceMapHost`)."""

    @staticmethod
    def current_workspace() -> str:
        """Maya's project folder, else a fallback folder (``WorkspaceManager``)."""
        return WorkspaceManager().current_workspace

    @staticmethod
    def find_workspaces(root_dir: str, recursive: bool) -> List[str]:
        """The marked workspaces under *root_dir* holding a Maya scene."""
        return EnvUtils.find_workspaces(
            root_dir, return_type="dir", ignore_empty=True, recursive=recursive
        )

    @staticmethod
    def scene_files(workspace: str) -> List[str]:
        """Every Maya scene under *workspace*, autosaves left out. Always
        recursive: the map's recursion governs discovery only, and a project's
        ``scenes/sub/`` files are still its scenes."""
        return EnvUtils.get_workspace_scenes(
            root_dir=workspace, full_path=True, recursive=True, omit_autosave=True
        )

    @staticmethod
    def create_workspace(root: str) -> Optional[ptk.Workspace]:
        """A project at *root*, built from the active template."""
        return EnvUtils.create_workspace(root)

    @staticmethod
    def promote_workspace(root: str) -> Optional[ptk.Workspace]:
        """*root* marked by the layout it has (Maya's scene extensions)."""
        return EnvUtils.promote_workspace(root)


class WorkspaceMap(ptk.WorkspaceMapBase, ptk.HelpMixin):
    """Maya's workspaces under a root folder, as a tree.

    :class:`pythontk.WorkspaceMapBase` bound to :class:`MayaWorkspaceMapHost`.
    Mirror of blendertk's ``WorkspaceMap``.
    """

    HOST = MayaWorkspaceMapHost()


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
