# !/usr/bin/python
# coding=utf-8
"""One-shot migration from legacy single-enum carriers to per-track schema.

Legacy shape (pre-2026-04):
    <carrier>.audio_trigger     # single enum with N event labels,
                                # value = enum index, keys are set to index
    <carrier>.audio_manifest    # derived string (rebuilt automatically)
    <carrier>.audio_file_map    # JSON (same location; reused)

New shape:
    <carrier>.audio_clip_<tid>  # one keyed enum per track, values 0/1
    <carrier>.audio_file_map    # JSON keyed by track_id (same attr)

Migration is explicit (called from the UI), never automatic on scene
load.  Converts legacy data in place and removes the old attrs.
"""
import json
import logging
from typing import Dict, List, Optional

from mayatk.audio_utils._audio_utils import AudioUtils, CARRIER_NODE, FILE_MAP_ATTR

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

logger = logging.getLogger(__name__)


def detect_legacy(obj: str, category: str = "audio") -> bool:
    """Return True if *obj* has the legacy ``<category>_trigger`` attr."""
    if cmds is None:
        return False
    trigger_attr = f"{category}_trigger"
    if not cmds.objExists(obj):
        return False
    return bool(cmds.attributeQuery(trigger_attr, node=obj, exists=True))


def _read_legacy_keys(obj: str, trigger_attr: str) -> List[tuple]:
    """Return time-sorted ``[(frame, enum_idx, label), ...]`` for legacy attr."""
    full = f"{obj}.{trigger_attr}"
    times = cmds.keyframe(full, q=True) or []
    vals = cmds.keyframe(full, q=True, valueChange=True) or []
    enum_str = cmds.attributeQuery(trigger_attr, node=obj, listEnum=True) or [""]
    labels = enum_str[0].split(":") if enum_str and enum_str[0] else []
    out: List[tuple] = []
    for t, v in zip(times, vals):
        idx = int(round(v))
        label = labels[idx] if 0 <= idx < len(labels) else ""
        out.append((t, idx, label))
    out.sort(key=lambda p: p[0])
    return out


def migrate_legacy_triggers(
    obj: str,
    category: str = "audio",
    keep_old_attrs: bool = False,
) -> List[str]:
    """Migrate legacy ``<category>_trigger`` keys to per-track attrs.

    Converts each (label, frame) with non-"None" label into a
    ``value=1`` start key on ``audio_clip_<tid>``.  When a "None" key
    follows a labelled key, it becomes a ``value=0`` stop key on the
    most-recent track.

    File paths: reads ``<obj>.<category>_file_map`` (or
    ``audio_file_map`` for the audio category) and copies entries to
    ``<CARRIER>.audio_file_map`` under the normalized track_id.

    Parameters:
        obj: Legacy carrier name (often the ``data_internal`` node
            itself in recent scenes, but may be a user transform).
        category: Legacy category prefix (default ``"audio"``).
        keep_old_attrs: If False (default), delete the legacy
            ``<category>_trigger`` / ``<category>_manifest`` attrs after
            migration.  Left alone when True (useful for dry-run).

    Returns:
        List of track_ids written.  Empty if nothing to migrate.
    """
    if cmds is None:
        return []
    trigger_attr = f"{category}_trigger"
    manifest_attr = f"{category}_manifest"
    legacy_fm_attr = "audio_file_map" if category == "audio" else f"{category}_file_map"

    if not cmds.objExists(obj):
        return []
    if not cmds.attributeQuery(trigger_attr, node=obj, exists=True):
        return []

    # 1. Load legacy file map (on same obj, may or may not exist).
    legacy_files: Dict[str, str] = {}
    if cmds.attributeQuery(legacy_fm_attr, node=obj, exists=True):
        raw = cmds.getAttr(f"{obj}.{legacy_fm_attr}") or ""
        if raw:
            try:
                legacy_files = json.loads(raw) or {}
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "migrate_legacy_triggers: invalid JSON in %s.%s",
                    obj,
                    legacy_fm_attr,
                )

    # 2. Read legacy keys time-ordered.
    entries = _read_legacy_keys(obj, trigger_attr)
    if not entries:
        if not keep_old_attrs:
            _delete_legacy_attrs(obj, trigger_attr, manifest_attr, legacy_fm_attr)
        return []

    # 3. Carrier for new per-track attrs (canonical).
    carrier = CARRIER_NODE
    from mayatk.node_utils.data_nodes import DataNodes

    if not cmds.objExists(carrier):
        DataNodes.ensure_internal()

    # 4. Derive track_ids per label and collect file paths to migrate.
    label_to_tid: Dict[str, str] = {}
    seen_tids: List[str] = []
    tid_paths: Dict[str, str] = {}
    for _, _, label in entries:
        if not label or label == "None":
            continue
        if label in label_to_tid:
            continue
        try:
            tid = AudioUtils.normalize_track_id(label)
        except ValueError as exc:
            logger.warning("migrate: cannot normalize %r: %s", label, exc)
            continue
        label_to_tid[label] = tid
        if tid not in seen_tids:
            seen_tids.append(tid)
        AudioUtils.ensure_track_attr(tid, carrier)
        # Legacy file_map keys are lowercase label stems.
        path = legacy_files.get(label.lower()) or legacy_files.get(label)
        if path:
            tid_paths[tid] = path

    # 5. Walk entries in time order and author per-track keys.
    #    A "None" key becomes a stop on the last active labelled track.
    active_tid: Optional[str] = None
    for frame, _idx, label in entries:
        if label and label != "None" and label in label_to_tid:
            tid = label_to_tid[label]
            AudioUtils.write_key(tid, frame, value=1, carrier=carrier)
            active_tid = tid
        elif label == "None" and active_tid is not None:
            AudioUtils.write_key(active_tid, frame, value=0, carrier=carrier)
            active_tid = None

    # 6. Rewrite file_map with ONLY migrated tids (prune orphan legacy keys).
    #    Preserve paths that are already on the canonical carrier under a
    #    valid tid (not touched by this migration).
    current = AudioUtils.load_file_map(carrier)
    merged: Dict[str, str] = {}
    for tid in AudioUtils.list_tracks(carrier):
        if tid in tid_paths:
            merged[tid] = tid_paths[tid]
        elif tid in current:
            merged[tid] = current[tid]
    AudioUtils._save_file_map(carrier, merged)

    # 7. Optionally remove legacy attrs.
    if not keep_old_attrs:
        _delete_legacy_attrs(obj, trigger_attr, manifest_attr, legacy_fm_attr)

    logger.info(
        "migrate_legacy_triggers: %s → %d track(s): %s",
        obj,
        len(seen_tids),
        seen_tids,
    )
    return seen_tids


def _delete_legacy_attrs(
    obj: str,
    trigger_attr: str,
    manifest_attr: str,
    file_map_attr: str,
) -> None:
    """Unlock and delete legacy trigger/manifest/file_map attrs from *obj*.

    Skips the shared canonical ``audio_file_map`` when *obj* is the
    canonical carrier — that attr is the new home and must not be
    deleted.  Preserves the original lock state of *obj*.
    """
    # Capture original lock state so migration is a no-op on lock status.
    was_locked = False
    try:
        was_locked = bool(cmds.lockNode(obj, q=True, lock=True)[0])
    except Exception:
        pass
    if was_locked:
        try:
            cmds.lockNode(obj, lock=False, lockName=False)
        except Exception:
            pass
    try:
        for attr in (trigger_attr, manifest_attr):
            if cmds.attributeQuery(attr, node=obj, exists=True):
                # Delete any anim curves first.
                curves = cmds.listConnections(f"{obj}.{attr}", type="animCurve") or []
                if curves:
                    cmds.delete(curves)
                cmds.deleteAttr(f"{obj}.{attr}")
        # Only delete the legacy file_map if it's NOT the canonical shared one.
        if cmds.attributeQuery(file_map_attr, node=obj, exists=True):
            if not (obj == CARRIER_NODE and file_map_attr == FILE_MAP_ATTR):
                cmds.deleteAttr(f"{obj}.{file_map_attr}")
    finally:
        if was_locked:
            try:
                cmds.lockNode(obj, lock=True, lockName=True)
            except Exception:
                pass
