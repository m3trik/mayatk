# !/usr/bin/python
# coding=utf-8
"""Compositor — derives DG audio nodes from keyed track events.

The compositor is the only module that creates, updates, or deletes
managed DG audio nodes.  Un-marked DG nodes (lacking
``audio_node_source``) are user-authored and left alone.

Semantics:
  - For every ``(track_id, first_start_key_frame)`` pair, ensure a DG
    audio node exists with correct ``.filename`` and ``.offset``.
  - Match existing DG nodes to tracks by the marker attr
    ``audio_node_source`` (not by name — rename-safe).
  - Delete managed DG nodes whose track no longer has any start key,
    or whose track attr was removed.
  - Idempotent: repeated ``sync()`` calls produce identical scene state.
"""
import logging
from typing import List, Optional

from mayatk.audio_utils import nodes as _nodes
from mayatk.audio_utils._audio_utils import AudioUtils, CARRIER_NODE, MARKER_ATTR

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Managed-node discovery
# ---------------------------------------------------------------------------


def is_managed_dg(node: str) -> bool:
    """True if *node* has the ``audio_node_source`` marker attr."""
    if cmds is None:
        return False
    return bool(cmds.attributeQuery(MARKER_ATTR, node=node, exists=True))


def _marker_value(node: str) -> str:
    return cmds.getAttr(f"{node}.{MARKER_ATTR}") or ""


def _all_managed_nodes() -> List[str]:
    if cmds is None:
        return []
    return [n for n in (cmds.ls(type="audio") or []) if is_managed_dg(n)]


def find_dg_node_for_track(track_id: str) -> Optional[str]:
    """Return the managed DG audio node for *track_id*, or ``None``."""
    for node in _all_managed_nodes():
        if _marker_value(node) == track_id:
            return node
    return None


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def sync(
    tracks: Optional[List[str]] = None,
    carrier: Optional[str] = None,
) -> dict:
    """Reconcile managed DG audio nodes with keyed track state.

    Parameters:
        tracks: Restrict sync to these track_ids.  When ``None``,
            performs a full scan-and-diff across all tracks on the
            carrier.
        carrier: Carrier node.  Default: canonical.

    Returns:
        Dict with keys ``created``, ``updated``, ``deleted`` — each a
        list of DG node names.
    """
    if cmds is None:
        return {"created": [], "updated": [], "deleted": []}

    carrier = carrier or CARRIER_NODE
    file_map = AudioUtils.load_file_map(carrier)

    # Which tracks are in scope?
    all_track_ids = AudioUtils.list_tracks(carrier)
    if tracks is None:
        scope = set(all_track_ids)
        full_scan = True
    else:
        scope = set(tracks)
        full_scan = False

    created: List[str] = []
    updated: List[str] = []
    deleted: List[str] = []

    # Index existing managed nodes by track_id.
    managed_by_tid: dict = {}
    for node in _all_managed_nodes():
        tid = _marker_value(node)
        if tid:
            managed_by_tid.setdefault(tid, []).append(node)

    # 1. Create / update for each in-scope track that has a start key.
    for tid in sorted(scope):
        if tid not in all_track_ids:
            # Track attr deleted — clean up any managed nodes for it.
            for node in managed_by_tid.get(tid, []):
                _delete_safely(node)
                deleted.append(node)
            continue

        keys = AudioUtils.read_keys(tid, carrier)
        start_frames = [f for f, v in keys if int(round(v)) >= 1]
        if not start_frames:
            # No start keys → no audible events → delete managed nodes.
            for node in managed_by_tid.get(tid, []):
                _delete_safely(node)
                deleted.append(node)
            continue

        path = file_map.get(tid)
        if not path:
            logger.debug("sync: no file_map entry for track %r; skipping", tid)
            continue

        offset = start_frames[0]  # First start defines the DG offset.
        existing = managed_by_tid.get(tid, [])

        if not existing:
            node = _nodes.create_dg(path, name=tid, offset=offset, track_id=tid)
            if node:
                created.append(node)
            continue

        # Update the first existing node; delete any extras (shouldn't
        # happen in normal flow but self-heals if it does).
        primary = existing[0]
        playable = _nodes.resolve_playable_path(path)
        if not playable:
            logger.debug("sync: cannot resolve playable path %r", path)
            continue
        current_path = (cmds.getAttr(f"{primary}.filename") or "").replace("\\", "/")
        current_offset = cmds.getAttr(f"{primary}.offset") or 0.0
        if current_path != playable or abs(current_offset - offset) > 1e-6:
            _nodes.configure_dg(primary, playable, offset)
            updated.append(primary)
        for extra in existing[1:]:
            _delete_safely(extra)
            deleted.append(extra)

    # 2. Orphan cleanup (full-scan only).
    if full_scan:
        for tid, managed in managed_by_tid.items():
            if tid in all_track_ids or tid in scope:
                continue
            for node in managed:
                _delete_safely(node)
                deleted.append(node)

    return {"created": created, "updated": updated, "deleted": deleted}


def _delete_safely(node: str) -> None:
    if not cmds.objExists(node):
        return
    try:
        cmds.delete(node)
    except Exception as exc:
        logger.warning("compositor: failed to delete %r: %s", node, exc)
