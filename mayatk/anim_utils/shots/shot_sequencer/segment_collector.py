# !/usr/bin/python
# coding=utf-8
"""Segment collection and attribute extraction for the shot sequencer.

Pure functions — no mixin needed.  The controller calls these directly,
passing in only the data they need.
"""

from __future__ import annotations

import logging

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

# Tolerance for matching shift-moved-out key times.
KEY_PROXIMITY_EPS = 0.5

__all__ = [
    "SegmentCollector",
]


class SegmentCollector:
    """SegmentCollector — module namespace."""

    @staticmethod
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
        pinned = (
            sequencer.store.pinned_objects if sequencer and sequencer.store else set()
        )
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
                    and any(
                        abs(t - ex) < KEY_PROXIMITY_EPS for ex in shifted_out_keys[obj]
                    )
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
                "[SYNC]   obj=%s start=%s end=%s dur=%s attr=%s",
                seg.get("obj"),
                seg.get("start"),
                seg.get("end"),
                seg.get("duration"),
                seg.get("attr"),
            )
        return segments_by_shot, all_objects

    @staticmethod
    def active_object_set(shot, segments_by_shot) -> set:
        """Return the set of objects that belong to the active shot.

        Only objects that have actual animation segments are included;
        objects stored in ``shot.objects`` but with flat/constant-only
        animation are excluded.
        """
        return {seg["obj"] for seg in segments_by_shot.get(shot.shot_id, [])}

    @staticmethod
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
                    # Attribute discovery is best-effort, but a failing curve
                    # (deleted mid-query, ambiguous name) is worth a trace.
                    logging.getLogger(__name__).debug(
                        "extract_attributes: skipped curve '%s'", curve, exc_info=True
                    )
        return sorted(attrs)

    @staticmethod
    def summary_times(segments, claimed_times) -> list:
        """Every key time an object row ticks in the flat key style.

        The segments' own ``keyframes`` (already collected upstream), minus
        the shot system's claimed bound samples: a time drops only when every
        curve of its segment that is keyed there holds it as a claimed sample.
        A claim on one channel beside an animator key on another keeps the
        tick -- the object has a real key there.

        Parameters:
            segments: Segment dicts of one object (``curves``, ``keyframes``).
            claimed_times: ``curve -> [time, ...]``, the samples the shot
                system created on that curve (the ledger).

        Returns:
            Ascending, de-duplicated times.
        """
        out: set = set()
        for seg in segments:
            curves = list(seg.get("curves") or [])
            claims = {id(c): list(claimed_times(c) or ()) for c in curves}
            for t in seg.get("keyframes") or ():
                claimers = [
                    c for c in curves if any(abs(t - h) <= 1e-3 for h in claims[id(c)])
                ]
                if not claimers or any(
                    SegmentCollector._has_key_at(c, t)
                    for c in curves
                    if all(c is not k for k in claimers)
                ):
                    out.add(float(t))
        return sorted(out)

    @staticmethod
    def _has_key_at(curve, t: float) -> bool:
        """Whether *curve* carries a key at *t* (within 1e-3 frames)."""
        try:
            return bool(
                cmds.keyframe(
                    str(curve),
                    query=True,
                    keyframeCount=True,
                    time=(t - 1e-3, t + 1e-3),
                )
            )
        except RuntimeError:  # the curve went away under the query
            return False

    @staticmethod
    def build_curve_preview(crv, t_start, t_end, hidden_times=()):
        """Bezier shape data for one anim curve, clipped to ``[t_start, t_end]``.

        The curve read as a host-free ``pythontk.KeyCurve``
        (:meth:`AnimUtils.get_key_curves`) and windowed by
        ``KeyCurve.to_preview``: the keys in range plus one bounding key each
        side, ``{keys, segments, broken, hidden, weighted, val_min, val_max}``
        -- the DCC-agnostic dict the widget painter draws with
        ``QPainterPath.cubicTo`` / ``lineTo``.  Every span but a ``step`` /
        ``stepnext`` one is the cubic Maya evaluates (a ``linear`` tangent is
        an angle like any other), its control points from the tangent angles
        and weights.

        Parameters
        ----------
        crv : str
            Maya animCurve node name.
        t_start, t_end : float
            Visible time range to clip to.
        hidden_times : iterable of float
            Key times the widget draws without a dot (``hidden``): keys that
            shape the curve but are not the animator's -- the shot system's
            claimed bound samples.

        Returns
        -------
        dict or None
            *None* when the curve has no usable data in the range.
        """
        if cmds is None:
            return None
        from mayatk.anim_utils._anim_utils import AnimUtils

        try:
            curves = AnimUtils.get_key_curves([str(crv)])
        except RuntimeError:  # the curve went away under the query
            return None
        if not curves:
            return None
        return curves[0].to_preview(t_start, t_end, hidden_times)
