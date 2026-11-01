# !/usr/bin/python
# coding=utf-8
"""What the Shot Manifest panel needs from Maya: its store, engine, detection,
behaviors and range classes, the playhead, the Outliner, node names and icons.

The panel is tentacle's, one class for both hosts; every call it makes that
names the host is answered here (``mtk.ManifestHost``), and blendertk's
``ManifestHost`` answers the same calls for Blender.
"""

from typing import List, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

from mayatk.anim_utils.shots._shots import Detection, ShotStore
from mayatk.anim_utils.shots.shot_manifest._shot_manifest import ShotManifest


class ManifestHost:
    """Maya's answers to the Shot Manifest panel's host calls."""

    #: The host's name, as the panel's messages say it.
    APP = "Maya"
    #: Where the panel's colour presets are kept (``ColorMappingDialog``).
    COLOR_PRESET_DIR = "mayatk/shot_manifest_colors"

    @staticmethod
    def store_cls():
        """The host's ``ShotStore`` class."""
        return ShotStore

    @staticmethod
    def manifest_cls():
        """The host's manifest engine class."""
        return ShotManifest

    @staticmethod
    def detection():
        """The host's animation-region detection (``Detection``)."""
        return Detection

    @staticmethod
    def behaviors_cls():
        """The host's ``Behaviors`` (the recipes a manifest object's behaviors key)."""
        from mayatk.anim_utils.shots.shot_manifest.behaviors import Behaviors

        return Behaviors

    @staticmethod
    def range_resolver_cls():
        """The host's ``RangeResolver``: pythontk's, sizing audio steps to their clips."""
        from mayatk.anim_utils.shots.shot_manifest.range_resolver import RangeResolver

        return RangeResolver

    @staticmethod
    def available() -> bool:
        """Whether the scene can be reached (``maya.cmds`` imports)."""
        return cmds is not None

    @staticmethod
    def current_frame() -> Optional[float]:
        """The playhead's frame, or ``None`` without a scene."""
        return float(cmds.currentTime(q=True)) if cmds is not None else None

    @staticmethod
    def find_nodes(name: str, store) -> List[str]:
        """The scene nodes doc object *name* answers to: the store's members,
        else (an audio node is no transform) the node of that name."""
        hits = store.member_nodes(name) if store is not None else []
        if hits or cmds is None:
            return hits
        return cmds.ls(name, long=True) or []

    @staticmethod
    def reveal(nodes: List[str]) -> List[str]:
        """Select *nodes* and reveal them in the Outliner.  Returns notes on
        any it could not reveal (none in Maya)."""
        from mayatk.core_utils._core_utils import CoreUtils
        from mayatk.ui_utils._ui_utils import UiUtils

        # A view mirror, not an edit: recording it costs the user a Ctrl+Z per
        # click before they reach their own work (same guard as the
        # sequencer's ``_select_and_show``).
        with CoreUtils.undo_disabled():
            UiUtils.reveal_in_outliner(nodes)
        return []

    @staticmethod
    def leaf_name(name: str) -> str:
        """*name*'s leaf: its DAG path dropped."""
        from mayatk.core_utils._core_utils import CoreUtils

        return CoreUtils.leaf_name(name)

    @staticmethod
    def object_icon(obj, name: str):
        """The icon of a table row's object, or ``None``: *obj* is its
        ``BuilderObject`` (``None`` for a bare scene name).  An audio object
        takes its node type's icon directly -- its node may not exist yet --
        and anything else the scene node's (``NodeIcons``)."""
        try:
            from mayatk.ui_utils.node_icons import NodeIcons
        except ImportError:
            return None
        if cmds is None:
            return None
        if getattr(obj, "kind", None) == "audio":
            return NodeIcons.audio_icon()
        return NodeIcons.get_icon(name)
