# !/usr/bin/python
# coding=utf-8
"""Low-level DG audio node primitives.

Thin wrappers over ``maya.cmds`` for creating / configuring / querying
Maya ``audio`` DG nodes.  These are the primitives the compositor uses
to materialize the derived view from keyed track events.

Not intended for direct use by consumers (sequencer, manifest, UI).
Those go through :func:`mayatk.audio_utils.sync`.
"""
import logging
import os
from typing import Optional

import pythontk as ptk

from mayatk.audio_utils._audio_utils import MARKER_ATTR

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)

_CACHE_DIR_NAME = "_maya_audio_cache"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_playable_path(
    audio_path: str,
    cache_dir: Optional[str] = None,
) -> Optional[str]:
    """Return a Maya-playable path, converting to WAV via ``ptk.AudioUtils``."""
    if cache_dir is None:
        cache_dir = os.path.join(os.path.dirname(audio_path), _CACHE_DIR_NAME).replace(
            "\\", "/"
        )
    return ptk.AudioUtils.resolve_playable_path(
        audio_path, cache_dir=cache_dir, logger=logger
    )


def workspace_sound_dir() -> Optional[str]:
    """Return the Maya workspace ``sound/`` directory, or ``None``."""
    if cmds is None:
        return None
    try:
        root = cmds.workspace(q=True, rootDirectory=True)
        rule = cmds.workspace(fileRuleEntry="sound") or "sound"
        snd_dir = os.path.join(root, rule).replace("\\", "/")
        if os.path.isdir(snd_dir):
            return snd_dir
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Node lifecycle
# ---------------------------------------------------------------------------


def create_dg(
    file_path: str,
    name: Optional[str] = None,
    offset: float = 0,
    track_id: Optional[str] = None,
) -> Optional[str]:
    """Create a new audio DG node configured for playback.

    When *track_id* is supplied, stamp the marker attr
    ``audio_node_source`` so the compositor recognizes it as managed.
    """
    if cmds is None:
        return None
    playable = resolve_playable_path(file_path)
    if not playable:
        return None
    if name is None:
        name = os.path.splitext(os.path.basename(file_path))[0]

    node = cmds.createNode("audio", name=name, skipSelect=True)
    configure_dg(node, playable, offset)

    if track_id is not None:
        _stamp_marker(node, track_id)
    return node


def configure_dg(node_name: str, file_path: str, offset: float) -> None:
    """Configure an existing DG audio node for reliable playback."""
    if cmds is None:
        return
    path = file_path.replace("\\", "/")
    cmds.setAttr(f"{node_name}.filename", path, type="string")
    cmds.setAttr(f"{node_name}.offset", offset)

    # Force Maya audio init via sound command (works around silent-waveform
    # bug in some Maya builds).
    try:
        cmds.sound(node_name, e=True, file=path, offset=offset)
    except Exception as exc:
        logger.warning(
            "sound edit failed for %r: %s; relying on setAttr fallback",
            node_name,
            exc,
        )
    try:
        if cmds.attributeQuery("mute", node=node_name, exists=True):
            cmds.setAttr(f"{node_name}.mute", 0)
    except Exception:
        pass


def query_duration(node_name: str) -> float:
    """Return the duration of an audio DG node in frames (0 on failure)."""
    if cmds is None:
        return 0.0
    try:
        return cmds.sound(node_name, q=True, length=True) or 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Marker attr (managed-node signalling)
# ---------------------------------------------------------------------------


def _stamp_marker(node: str, track_id: str) -> None:
    """Stamp the compositor marker attr on *node* with *track_id*."""
    if not cmds.attributeQuery(MARKER_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=MARKER_ATTR, dataType="string")
    cmds.setAttr(f"{node}.{MARKER_ATTR}", track_id, type="string")
