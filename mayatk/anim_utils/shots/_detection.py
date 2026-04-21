# coding=utf-8
"""Shot-region detection — Maya animation-graph analysis.

Pure functions for discovering animation clusters, mapping anim-curves
to transforms, and building shot boundaries from selected keyframes.

Split out of :mod:`_shots` to keep that module focused on the domain
model (:class:`ShotStore`, :class:`ShotBlock`, events) while detection
logic lives here.

All names are re-exported by :mod:`_shots` so existing imports continue
to work.
"""
from typing import Any, Dict, List, Optional, Tuple

from mayatk.anim_utils._anim_utils import STANDARD_TRANSFORM_ATTRS


def _map_standard_curves_to_transforms(curves=None):
    """Map each transform to anim curves driving standard attrs.

    Returns ``dict[str, list[str]]`` — *transform_name* → [*curve_names*].
    Curves that only drive custom/user-defined attributes are skipped.
    Intermediate nodes (e.g. ``unitConversion``, ``pairBlend``) are
    resolved to their parent transform.
    """
    import maya.cmds as cmds
    from collections import defaultdict

    if curves is None:
        curves = cmds.ls(type="animCurve") or []

    result = defaultdict(list)
    for crv in curves:
        plugs = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
        for plug_str in plugs:
            attr = plug_str.rsplit(".", 1)[-1] if "." in plug_str else ""
            if attr not in STANDARD_TRANSFORM_ATTRS:
                continue
            node = plug_str.split(".")[0]
            if cmds.nodeType(node) == "transform":
                long = cmds.ls(node, long=True)
                result[long[0] if long else node].append(crv)
            else:
                parents = (
                    cmds.listRelatives(
                        node, parent=True, type="transform", fullPath=True
                    )
                    or []
                )
                if parents:
                    result[parents[0]].append(crv)
            break  # one standard destination per curve is sufficient
    return dict(result)


# ---------------------------------------------------------------------------
# Shot-region detection  (shared by sequencer + manifest)
# ---------------------------------------------------------------------------


def detect_shot_regions(
    objects: Optional[List[str]] = None,
    gap_threshold: float = 5.0,
    ignore: Optional[str] = None,
    motion_rate: float = 1e-3,
    min_duration: float = 2.0,
) -> List[Dict[str, Any]]:
    """Detect animation regions by clustering per-object segments.

    Scans the full timeline using ``SegmentKeys`` and groups contiguous
    segments into regions separated by gaps of at least *gap_threshold*
    frames.  This is the single source of truth for shot-boundary
    detection — used by both the shot sequencer and the shot manifest.

    Flat/constant-value intervals are always excluded so that
    boundaries hidden by baked animation are correctly detected.

    Parameters:
        objects: Transform names to scan.  ``None`` discovers all
            transforms driven by animation curves.
        gap_threshold: Minimum gap (frames) between clusters.
        ignore: Attribute pattern(s) to exclude from segment collection.
        motion_rate: Per-frame rate-of-change threshold.  Intervals
            whose per-frame rate falls below this are treated as static.
        min_duration: Minimum shot duration in frames.  Clusters
            shorter than this are discarded.  Default ``2.0``.

    Returns:
        List of dicts with ``"name"``, ``"start"``, ``"end"``, and
        ``"objects"`` keys, sorted by start time.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return []

    from mayatk.anim_utils.segment_keys import SegmentKeys

    # Discover objects if not provided
    if objects is None:
        curves = cmds.ls(type="animCurve") or []
        found: set = set()
        for crv in curves:
            conns = cmds.listConnections(crv, d=True, s=False) or []
            for node in conns:
                node_type = cmds.nodeType(node)
                if node_type == "transform":
                    long = cmds.ls(node, long=True)
                    found.add(long[0] if long else node)
                else:
                    parents = (
                        cmds.listRelatives(
                            node, parent=True, type="transform", fullPath=True
                        )
                        or []
                    )
                    if parents:
                        found.add(parents[0])
        objects = sorted(found)

    if not objects:
        return []

    # Validate existence — use long names to avoid ambiguity
    valid = cmds.ls(objects, long=True) or []
    if not valid:
        return []

    segments = SegmentKeys.collect_segments(
        valid,
        split_static=True,
        ignore=ignore,
        ignore_holds=True,
        ignore_visibility_holds=True,
        motion_only=True,
        motion_rate=motion_rate,
    )
    if not segments:
        return []

    segments.sort(key=lambda s: s["start"])

    # Cluster segments by gap_threshold
    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [segments[0]]
    current_end = segments[0]["end"]

    for seg in segments[1:]:
        if seg["start"] - current_end > gap_threshold:
            clusters.append(current)
            current = [seg]
            current_end = seg["end"]
        else:
            current.append(seg)
            current_end = max(current_end, seg["end"])
    clusters.append(current)

    candidates: List[Dict[str, Any]] = []
    for cluster in clusters:
        start = min(s["start"] for s in cluster)
        end = max(s["end"] for s in cluster)
        if (end - start) < min_duration:
            continue
        objs = sorted({str(s["obj"]) for s in cluster})
        candidates.append(
            {
                "name": f"Shot {len(candidates) + 1}",
                "start": start,
                "end": end,
                "objects": objs,
            }
        )
    return candidates


def _filter_flat_objects(
    candidates: List[Dict[str, Any]], value_tolerance: float = 1e-4
) -> List[Dict[str, Any]]:
    """Remove objects whose animation is flat or only on custom trigger attributes.

    An object is considered genuine animated content if it has at least
    one animation curve that drives a standard transform or visibility
    attribute **and** that curve has changing values within the shot's
    range.  Objects animated only on custom attributes (e.g.
    ``audio_trigger``) are treated as boundary markers and excluded.

    Candidates with no remaining objects are kept (the shot boundary
    is still valid); only the ``"objects"`` list is pruned.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return candidates

    if not candidates:
        return candidates

    try:
        transform_curves = _map_standard_curves_to_transforms()
    except (AttributeError, RuntimeError):
        return candidates
    if not transform_curves:
        return candidates

    for cand in candidates:
        start, end = cand["start"], cand["end"]
        filtered = []
        for obj in cand["objects"]:
            crvs = transform_curves.get(obj)
            if not crvs:
                continue
            for crv in crvs:
                vals = cmds.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if vals and (max(vals) - min(vals)) > value_tolerance:
                    filtered.append(obj)
                    break
        cand["objects"] = filtered
    return candidates


def regions_from_selected_keys(
    gap_threshold: float = 5.0,
    key_filter: str = "all",
) -> List[Dict[str, Any]]:
    """Build shot regions from currently selected keyframes.

    Each unique selected key time is treated as an explicit shot
    boundary.  Keys closer than *gap_threshold* are merged into a
    single boundary.  This is designed for stepped / marker keys
    (e.g. audio triggers) where each key marks the start of a shot
    rather than representing continuous animation.

    Objects with flat/constant animation within a shot's range are
    automatically excluded from that shot's ``"objects"`` list.

    Parameters:
        gap_threshold: Keys within this many frames are merged
            into one boundary.
        key_filter: How to interpret key values:

            ``"all"``
                Every key is a boundary (contiguous shots).
            ``"skip_zero"``
                Keys with value 0 are ignored; only non-zero keys
                become boundaries.
            ``"zero_as_end"``
                Non-zero keys start shots; zero-value keys end the
                preceding shot (allows gaps between shots).

    Returns:
        List of dicts with ``"name"``, ``"start"``, ``"end"``, and
        ``"objects"`` keys, sorted by start time.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return []

    sel_curves = cmds.keyframe(query=True, selected=True, name=True) or []
    if not sel_curves:
        return []

    # Collect (time, value, object) triples from selected keys
    entries: List[Tuple[float, float, str]] = []
    for crv in set(sel_curves):
        times = cmds.keyframe(crv, query=True, selected=True, timeChange=True) or []
        values = cmds.keyframe(crv, query=True, selected=True, valueChange=True) or []
        conns = cmds.listConnections(crv, d=True, s=False) or []
        obj_name = crv  # fallback
        for node in conns:
            node_type = cmds.nodeType(node)
            if node_type == "transform":
                long = cmds.ls(node, long=True)
                obj_name = long[0] if long else node
                break
            parents = (
                cmds.listRelatives(node, parent=True, type="transform", fullPath=True)
                or []
            )
            if parents:
                obj_name = parents[0]
                break
        for t, v in zip(times, values):
            if v is None:
                continue
            entries.append((t, v, obj_name))

    if not entries:
        return []

    def _is_zero(v) -> bool:
        """Treat None and near-zero floats as 'zero'."""
        return v is None or abs(v) < 1e-9

    # Stable sort: same-time entries have zeros first so that in
    # ``zero_as_end`` mode a closing zero is processed before the
    # opening non-zero trigger at the same frame.
    entries.sort(key=lambda e: (e[0], 0 if _is_zero(e[1]) else 1))

    # ---- "zero_as_end" mode: pair non-zero starts with zero ends ---------
    if key_filter == "zero_as_end":
        candidates: List[Dict[str, Any]] = []
        current_start: Optional[float] = None
        current_objs: set = set()
        for t, v, obj in entries:
            if not _is_zero(v):
                if current_start is None:
                    current_start = t
                    current_objs = {obj}
                else:
                    current_objs.add(obj)
            else:
                # Zero-value key ends the current shot
                if current_start is not None:
                    candidates.append(
                        {
                            "name": f"Shot {len(candidates) + 1}",
                            "start": current_start,
                            "end": t,
                            "objects": sorted(str(o) for o in current_objs),
                        }
                    )
                    current_start = None
                    current_objs = set()
        # Trailing shot with no closing zero key
        if current_start is not None:
            candidates.append(
                {
                    "name": f"Shot {len(candidates) + 1}",
                    "start": current_start,
                    "end": current_start + 1.0,
                    "objects": sorted(str(o) for o in current_objs),
                }
            )
        return _filter_flat_objects(candidates)

    # ---- "skip_zero" mode: filter zeros, then use boundary logic below -----
    if key_filter == "skip_zero":
        entries = [(t, v, obj) for t, v, obj in entries if not _is_zero(v)]
        if not entries:
            return []
        # Fall through to "all" mode boundary logic.

    # ---- "all" mode: merge keys within gap_threshold into boundary points
    boundaries: List[Tuple[float, set]] = []  # (time, {objects})
    first_time = entries[0][0]
    cur_time = entries[0][0]
    cur_objs: set = {entries[0][2]}

    for t, _v, obj in entries[1:]:
        if t - cur_time <= gap_threshold:
            cur_objs.add(obj)
            cur_time = t
        else:
            boundaries.append((first_time, cur_objs))
            first_time = t
            cur_time = t
            cur_objs = {obj}
    boundaries.append((first_time, cur_objs))

    if not boundaries:
        return []

    # Build contiguous regions: each boundary starts a shot that ends
    # at the next boundary.  The last shot gets a nominal 1-frame end
    # (the manifest's range resolver will compute the real end).
    candidates = []
    for i, (start, objs) in enumerate(boundaries):
        if i + 1 < len(boundaries):
            end = boundaries[i + 1][0]
        else:
            end = start + 1.0
        candidates.append(
            {
                "name": f"Shot {len(candidates) + 1}",
                "start": start,
                "end": end,
                "objects": sorted(str(o) for o in objs),
            }
        )
    return _filter_flat_objects(candidates)
