# coding=utf-8
"""Shot-region detection — Maya scene acquisition over the pure engine math.

Maya-side acquisition (discovering animated transforms, resolving anim-curves
to transforms, gathering selected-key entries, filtering flat objects) feeding
the DCC-agnostic boundary math in ``pythontk.core_utils.engines.shots``
(:func:`~pythontk.cluster_segments_by_gap` /
:func:`~pythontk.boundaries_from_key_entries`).

Split out of :mod:`_shots` to keep that module focused on the domain
model (:class:`ShotStore`, :class:`ShotBlock`, events) while detection
logic lives here.

All names are re-exported by :mod:`_shots` so existing imports continue
to work.
"""
from typing import Any, Dict, List, Optional, Tuple

from pythontk import cluster_segments_by_gap, boundaries_from_key_entries

from mayatk.anim_utils._anim_utils import STANDARD_TRANSFORM_ATTRS


def resolve_to_transform(node, cache=None, _depth=0):
    """Resolve a curve-destination node to its owning transform.

    Returns the transform's long name, or ``None`` when the node is
    neither a transform nor parented under one (e.g. a material).
    Shapes resolve to their parent transform.  DG intermediaries
    (``unitConversion``, ``pairBlend``, …) are followed one connection
    hop downstream (bounded, cycle-safe) toward the driven node.

    ``cache`` (a dict) memoizes results across calls — pass one shared
    dict when resolving many nodes so repeated destinations (common when
    thousands of curves drive the same rig) cost one Maya query total.

    This is the single curve→transform resolution used by detection,
    the sequencer, and the manifest; keep per-site copies out.
    """
    import maya.cmds as cmds

    if cache is not None and node in cache:
        return cache[node]

    # ls(type="transform") matches transform SUBCLASSES too (joint,
    # ikHandle, constraint...) — a nodeType(node) == "transform" test
    # missed them, resolving a keyed joint to its PARENT so shot moves
    # silently left the joint's keys behind.
    hits = cmds.ls(node, long=True, type="transform")
    if hits:
        result = hits[0]
    else:
        try:
            parents = (
                cmds.listRelatives(
                    node, parent=True, type="transform", fullPath=True
                )
                or []
            )
        except (RuntimeError, ValueError):
            # Non-DAG destination (material, blendShape, …) — no parent.
            parents = []
        if parents:
            result = parents[0]
        elif _depth < 3:
            # DG intermediary: hop toward the driven node.  Depth-bounded
            # so DG feedback loops can't recurse forever.
            result = None
            try:
                downstream = cmds.listConnections(node, d=True, s=False) or []
            except (RuntimeError, ValueError):
                downstream = []
            for dst in dict.fromkeys(downstream):
                if dst == node:
                    continue
                hop = resolve_to_transform(dst, cache=cache, _depth=_depth + 1)
                if hop:
                    result = hop
                    break
        else:
            result = None

    if cache is not None:
        cache[node] = result
    return result


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
    node_cache: dict = {}
    for crv in curves:
        plugs = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
        for plug_str in plugs:
            attr = plug_str.rsplit(".", 1)[-1] if "." in plug_str else ""
            if attr not in STANDARD_TRANSFORM_ATTRS:
                continue
            node = plug_str.split(".")[0]
            transform = resolve_to_transform(node, cache=node_cache)
            if transform:
                result[transform].append(crv)
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

    # Discover objects if not provided.  One batched listConnections
    # over all curves (we only need the destination-node *set*, not a
    # per-curve mapping), then resolve each unique node once.
    if objects is None:
        curves = cmds.ls(type="animCurve") or []
        found: set = set()
        if curves:
            conns = cmds.listConnections(curves, d=True, s=False) or []
            node_cache: dict = {}
            for node in set(conns):
                transform = resolve_to_transform(node, cache=node_cache)
                if transform:
                    found.add(transform)
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

    # Pure clustering math lives in the engine (shared with blendertk).
    return cluster_segments_by_gap(
        segments, gap_threshold=gap_threshold, min_duration=min_duration
    )


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

    # Query each curve's full key data once, then evaluate every
    # candidate range in Python — candidates overlap the same curves,
    # so per-candidate ranged cmds.keyframe queries repeat work.
    curve_data: Dict[str, Tuple[list, list]] = {}

    def _varies_in_range(crv: str, start: float, end: float) -> bool:
        if crv not in curve_data:
            times = cmds.keyframe(crv, q=True, timeChange=True) or []
            values = cmds.keyframe(crv, q=True, valueChange=True) or []
            curve_data[crv] = (times, values)
        times, values = curve_data[crv]
        in_range = [v for t, v in zip(times, values) if start <= t <= end]
        return bool(in_range) and (max(in_range) - min(in_range)) > value_tolerance

    for cand in candidates:
        start, end = cand["start"], cand["end"]
        cand["objects"] = [
            obj
            for obj in cand["objects"]
            if any(
                _varies_in_range(crv, start, end)
                for crv in transform_curves.get(obj) or ()
            )
        ]
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
    node_cache: dict = {}
    for crv in set(sel_curves):
        times = cmds.keyframe(crv, query=True, selected=True, timeChange=True) or []
        values = cmds.keyframe(crv, query=True, selected=True, valueChange=True) or []
        conns = cmds.listConnections(crv, d=True, s=False) or []
        obj_name = crv  # fallback
        for node in conns:
            transform = resolve_to_transform(node, cache=node_cache)
            if transform:
                obj_name = transform
                break
        for t, v in zip(times, values):
            if v is None:
                continue
            entries.append((t, v, obj_name))

    if not entries:
        return []

    # Pure boundary math lives in the engine (shared with blendertk); the
    # flat-object post-filter needs scene queries, so it stays Maya-side.
    candidates = boundaries_from_key_entries(
        entries, gap_threshold=gap_threshold, key_filter=key_filter
    )
    return _filter_flat_objects(candidates)
