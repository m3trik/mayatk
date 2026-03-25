# !/usr/bin/python
# coding=utf-8
"""Segment collection and attribute extraction for the shot sequencer.

Pure functions — no mixin needed.  The controller calls these directly,
passing in only the data they need.
"""
from __future__ import annotations

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

# Tolerance for matching shift-moved-out key times.
KEY_PROXIMITY_EPS = 0.5

__all__ = ["collect_segments", "active_object_set", "extract_attributes"]


def collect_segments(
    sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger
):
    """Collect animation segments for visible shots.

    Returns ``(segments_by_shot, all_objects)``.

    Parameters
    ----------
    sequencer : ShotSequencer
    shot : ShotBlock
        The active shot.
    visible_shots : list[ShotBlock]
    segment_cache : dict
        ``{shot_id: segments_list}`` — mutated in-place.
    shifted_out_keys : dict
        ``{obj_name: {time, …}}`` — keys shift-moved out of their shot.
    logger
        A logging-compatible logger.
    """
    segments_by_shot: dict = {}
    all_objects: set = set()
    pinned = (
        sequencer.store.pinned_objects
        if sequencer and sequencer.store
        else set()
    )
    for vs in visible_shots:
        is_active_shot = vs.shot_id == shot.shot_id
        if is_active_shot or vs.shot_id not in segment_cache:
            segs = sequencer.collect_object_segments(vs.shot_id)
            segment_cache[vs.shot_id] = segs
        else:
            segs = segment_cache[vs.shot_id]
        segments_by_shot[vs.shot_id] = segs
        all_objects.update(seg["obj"] for seg in segs)
        all_objects.update(o for o in vs.objects if o in pinned)

    active_segs = segments_by_shot.get(shot.shot_id, [])

    # Filter out segments for keys that were shift-moved out of this shot.
    if shifted_out_keys:
        filtered = []
        for seg in active_segs:
            obj = seg.get("obj")
            t = seg.get("start")
            if (
                obj in shifted_out_keys
                and t is not None
                and any(abs(t - ex) < KEY_PROXIMITY_EPS for ex in shifted_out_keys[obj])
            ):
                logger.debug(
                    "[SYNC] excluding shift-moved-out segment: obj=%s time=%s",
                    obj,
                    t,
                )
                continue
            filtered.append(seg)
        active_segs = filtered
        segments_by_shot[shot.shot_id] = active_segs

    logger.debug(
        "[SYNC] shot=%s range=(%s,%s) total_segments=%s objects=%s",
        shot.shot_id,
        shot.start,
        shot.end,
        len(active_segs),
        sorted(all_objects),
    )
    for seg in active_segs:
        logger.debug(
            "[SYNC]   obj=%s start=%s end=%s dur=%s stepped=%s attr=%s",
            seg.get("obj"),
            seg.get("start"),
            seg.get("end"),
            seg.get("duration"),
            seg.get("is_stepped"),
            seg.get("attr"),
        )
    return segments_by_shot, all_objects


def active_object_set(shot, segments_by_shot) -> set:
    """Return the set of objects that belong to the active shot.

    Only objects that have actual animation segments are included;
    objects stored in ``shot.objects`` but with flat/constant-only
    animation are excluded.
    """
    return {seg["obj"] for seg in segments_by_shot.get(shot.shot_id, [])}


def extract_attributes(segments) -> list:
    """Extract attribute names from animation curves in the given segments.

    Only includes attributes whose curves actually have keyframes
    within the segment's time range.  Uses a curve→attr cache to
    avoid repeated ``listConnections`` calls, and checks the
    segment's own ``keyframes`` list (already collected upstream)
    instead of issuing per-curve ``cmds.keyframe`` queries.
    """
    attrs: set = set()
    _curve_attr_cache: dict = {}

    for seg in segments:
        seg_start = seg.get("start")
        seg_end = seg.get("end")
        seg_keys = seg.get("keyframes")
        for curve in seg.get("curves", []):
            try:
                crv_str = str(curve)
                if crv_str not in _curve_attr_cache:
                    conns = (
                        cmds.listConnections(
                            crv_str,
                            plugs=True,
                            destination=True,
                            source=False,
                        )
                        or []
                    )
                    attr = None
                    for conn in conns:
                        if "." in conn:
                            attr = conn.rsplit(".", 1)[-1]
                            break
                    _curve_attr_cache[crv_str] = attr

                attr = _curve_attr_cache[crv_str]
                if attr is None:
                    continue

                if seg_start is not None and seg_end is not None:
                    if seg_keys:
                        if attr not in attrs:
                            crv_keys = cmds.keyframe(
                                crv_str,
                                query=True,
                                timeChange=True,
                                time=(seg_start, seg_end),
                            )
                            if not crv_keys:
                                continue
                    else:
                        crv_keys = cmds.keyframe(
                            crv_str,
                            query=True,
                            timeChange=True,
                            time=(seg_start, seg_end),
                        )
                        if not crv_keys:
                            continue

                attrs.add(attr)
            except Exception:
                pass
    return sorted(attrs)
