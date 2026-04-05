# !/usr/bin/python
# coding=utf-8
"""Segment collection and attribute extraction for the shot sequencer.

Pure functions — no mixin needed.  The controller calls these directly,
passing in only the data they need.
"""
from __future__ import annotations

import math

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

# Tolerance for matching shift-moved-out key times.
KEY_PROXIMITY_EPS = 0.5

__all__ = [
    "collect_segments",
    "active_object_set",
    "extract_attributes",
    "build_curve_preview",
]


def collect_segments(
    sequencer,
    shot,
    visible_shots,
    segment_cache,
    shifted_out_keys,
    logger,
):
    """Collect animation segments for visible shots.

    Returns ``(segments_by_shot, all_objects)``.

    Object-level tracks always ignore holds — hold visibility is an
    attribute-level detail handled by ``_provide_sub_rows``.

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
    pinned = sequencer.store.pinned_objects if sequencer and sequencer.store else set()
    for vs in visible_shots:
        is_active_shot = vs.shot_id == shot.shot_id
        if is_active_shot or vs.shot_id not in segment_cache:
            segs = sequencer.collect_object_segments(vs.shot_id, ignore_holds=True)
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


def build_curve_preview(crv, t_start, t_end):
    """Extract Bézier curve shape data for a single anim curve.

    Returns a DCC-agnostic dict that the widget painter can render
    directly using ``QPainterPath.cubicTo`` / ``lineTo``.

    Parameters
    ----------
    crv : str
        Maya animCurve node name.
    t_start, t_end : float
        Visible time range to clip to.

    Returns
    -------
    dict
        ``{keys, segments, val_min, val_max}`` or *None* if the
        curve has no usable data in the range.

        *keys*: ``[(t, v), ...]`` — keyframe dot positions.

        *segments*: list of dicts, one per consecutive key pair::

            {t0, v0, t1, v1, out_type,
             cp1: (x, y) | None, cp2: (x, y) | None}

        *val_min*, *val_max*: value range for Y normalisation.
    """
    if cmds is None:
        return None

    try:
        crv = str(crv)
        times = cmds.keyframe(crv, q=True, timeChange=True) or []
        values = cmds.keyframe(crv, q=True, valueChange=True) or []
        if not times or len(times) != len(values):
            return None

        out_angles = cmds.keyTangent(crv, q=True, outAngle=True) or []
        in_angles = cmds.keyTangent(crv, q=True, inAngle=True) or []
        out_types = cmds.keyTangent(crv, q=True, outTangentType=True) or []

        # Weighted tangents: outWeight is the real Bézier handle distance.
        # Non-weighted (default): outWeight ≈ 1.0 — use 1/3-span rule instead.
        is_weighted = bool(cmds.getAttr(crv + ".weightedTangents"))
        if is_weighted:
            out_weights = cmds.keyTangent(crv, q=True, outWeight=True) or []
            in_weights = cmds.keyTangent(crv, q=True, inWeight=True) or []
        else:
            out_weights = in_weights = None
    except Exception:
        return None

    n = len(times)
    check_lists = [values, out_angles, in_angles, out_types]
    if is_weighted:
        check_lists.extend([out_weights, in_weights])
    if any(len(lst) != n for lst in check_lists):
        return None

    # --- Determine visible key indices (plus one bounding key each side) ---
    first_vis = None
    last_vis = None
    for i, t in enumerate(times):
        if t_start - 0.001 <= t <= t_end + 0.001:
            if first_vis is None:
                first_vis = i
            last_vis = i

    if first_vis is None:
        # No keys in range — check if curve interpolates through the range
        before = [i for i, t in enumerate(times) if t < t_start]
        after = [i for i, t in enumerate(times) if t > t_end]
        if not before or not after:
            return None
        # Include bounding keys so we get one segment spanning the view
        first_vis = before[-1]
        last_vis = after[0]
    else:
        # Extend to include one bounding key on each side for edge segments
        if first_vis > 0:
            first_vis -= 1
        if last_vis < n - 1:
            last_vis += 1

    # --- Build keys and segments for the visible range ---
    vis_keys = []  # (t, v) for dot drawing
    vis_segs = []  # per-span segment data
    all_vals = []  # for min/max

    for i in range(first_vis, last_vis + 1):
        t, v = times[i], values[i]
        vis_keys.append((t, v))
        all_vals.append(v)

    for i in range(first_vis, last_vis):
        t0, v0 = times[i], values[i]
        t1, v1 = times[i + 1], values[i + 1]
        ot = out_types[i]

        cp1 = None
        cp2 = None
        if ot not in ("step", "stepnext", "linear"):
            oa_rad = math.radians(out_angles[i])
            ia_rad = math.radians(in_angles[i + 1])

            if is_weighted:
                # Weighted tangents: weight IS the Bézier handle distance
                ow = out_weights[i]
                cp1 = (t0 + ow * math.cos(oa_rad), v0 + ow * math.sin(oa_rad))
                iw = in_weights[i + 1]
                cp2 = (t1 - iw * math.cos(ia_rad), v1 - iw * math.sin(ia_rad))
            else:
                # Non-weighted: CP x-offset is always 1/3 of the span,
                # y-offset follows from the tangent angle.
                third = (t1 - t0) / 3.0
                cp1 = (t0 + third, v0 + third * math.tan(oa_rad))
                cp2 = (t1 - third, v1 - third * math.tan(ia_rad))

            # Track CP values for accurate normalization
            all_vals.extend([cp1[1], cp2[1]])

        vis_segs.append(
            {
                "t0": t0,
                "v0": v0,
                "t1": t1,
                "v1": v1,
                "out_type": ot,
                "cp1": cp1,
                "cp2": cp2,
            }
        )

    if not vis_keys:
        return None

    val_min = min(all_vals)
    val_max = max(all_vals)

    return {
        "keys": vis_keys,
        "segments": vis_segs,
        "val_min": val_min,
        "val_max": val_max,
    }
