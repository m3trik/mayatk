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

from pythontk import ShotDetection

from mayatk.anim_utils._anim_utils import STANDARD_TRANSFORM_ATTRS, AnimUtils


class _DetectionInternal(object):
    """Internal helpers for Detection."""

    @staticmethod
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
            # Terminal destinations, not the raw plugs: constrained-channel
            # keys route through a pairBlend and layered keys through
            # animBlendNode*, whose input attrs never match STANDARD — the
            # curve's real target sits one hop further (see
            # first_standard_destination).
            hit = Detection.first_standard_destination(crv)
            if hit is None:
                continue
            transform = Detection.resolve_to_transform(hit[1], cache=node_cache)
            if transform:
                result[transform].append(crv)
        return dict(result)

    @staticmethod
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
            transform_curves = _DetectionInternal._map_standard_curves_to_transforms()
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


class Detection(_DetectionInternal):
    """Detection — module namespace."""

    @staticmethod
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
                    hop = Detection.resolve_to_transform(
                        dst, cache=cache, _depth=_depth + 1
                    )
                    if hop:
                        result = hop
                        break
            else:
                result = None

        if cache is not None:
            cache[node] = result
        return result

    #: DG node types that sit BETWEEN an anim curve and the plug it
    #: ultimately drives.  Keys on a constrained channel route through a
    #: pairBlend ('inTranslateX1'), animation-layer keys through the
    #: animBlendNode* family ('inputA'/'inputB'), unit-mismatched channels
    #: through a unitConversion ('input') — testing THOSE attrs against
    #: STANDARD_TRANSFORM_ATTRS classifies every such curve as
    #: non-standard.
    _DG_INTERMEDIARIES = AnimUtils._CURVE_INTERMEDIARIES

    @classmethod
    def terminal_destinations(cls, node, _depth=0):
        """Yield ``(attr, node)`` for the terminal plugs downstream of *node*.

        Follows DG intermediaries (see :data:`_DG_INTERMEDIARIES` plus the
        ``animBlendNode*`` family) to the plugs they ultimately drive.
        Depth-bounded so DG feedback loops can't recurse forever.
        """
        import maya.cmds as cmds

        # ``connections=True`` pairs each destination with the plug on *node*
        # it leaves from, so bookkeeping edges can be told from data flow: an
        # animBlendNode's ``message`` feeds the animLayer's ``blendNodes[]``
        # registry, and following it yields the layer as a "terminal".
        pairs = (
            cmds.listConnections(node, d=True, s=False, plugs=True, connections=True)
            or []
        )
        plugs = [
            dst
            for src, dst in zip(pairs[0::2], pairs[1::2])
            if src.rsplit(".", 1)[-1] != "message"
        ]
        yield from cls._terminals_from_plugs(plugs, _depth)

    @classmethod
    def _terminals_from_plugs(cls, plugs, _depth):
        import maya.cmds as cmds

        for plug in plugs:
            dst = plug.split(".")[0]
            attr = plug.rsplit(".", 1)[-1] if "." in plug else ""
            try:
                ntype = cmds.nodeType(dst)
            except RuntimeError:
                continue
            if _depth < 3 and (
                ntype in cls._DG_INTERMEDIARIES or ntype.startswith("animBlendNode")
            ):
                yield from cls.terminal_destinations(dst, _depth + 1)
            else:
                yield attr, dst

    @classmethod
    def transform_from_curve_names(cls, leaf_name, curves=None):
        """Resolve a VANISHED node name through the curves Maya named after it.

        Maya names an auto-created anim curve ``<node>_<attr>`` (plus a numeric
        suffix when the name repeats) and never renames it when the node is
        renamed — so a stored name whose node no longer exists can usually be
        recovered from the curve names it left behind.

        The match is self-validating: the text after ``<leaf_name>_`` must be
        the very attribute the curve drives (trailing digits ignored), so a
        node named ``FOO`` cannot claim ``FOO_BAR``'s curves.  Returns the one
        transform those curves drive, or ``None`` when there is no candidate
        or more than one — a guess here would silently re-point a shot at the
        wrong object, which is worse than leaving the name unresolved.

        Pass *curves* to reuse one ``cmds.ls(type="animCurve")`` across a batch
        of lookups (see ``ShotSequencer._renamed_target``).
        """
        import maya.cmds as cmds

        if not leaf_name:
            return None
        prefix = f"{leaf_name}_"
        targets = set()
        for crv in (cmds.ls(type="animCurve") or []) if curves is None else curves:
            name = str(crv).rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            if not name.startswith(prefix):
                continue
            # Name test first — it decides most candidates and costs no DG
            # query: the text after the prefix must be a standard attribute
            # (Maya appends digits when a curve name repeats).
            suffix = name[len(prefix) :].rstrip("0123456789")
            if suffix not in STANDARD_TRANSFORM_ATTRS:
                continue
            hit = cls.first_standard_destination(crv)
            if not hit or hit[0] != suffix:
                continue  # a longer node name's curve, not ours
            targets.add(hit[1])
        if len(targets) != 1:
            return None
        matches = cmds.ls(targets.pop(), long=True, type="transform") or []
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def first_standard_destination(cls, crv):
        """First ``(attr, node)`` of *crv* landing on a standard transform attr.

        The single test every "is this curve scene content?" site shares —
        detection, sequencer membership, and the keyed-object auto-add —
        so constrained/layered channels classify identically everywhere.
        Returns ``None`` when no terminal destination is standard.
        """
        import maya.cmds as cmds

        plugs = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
        # Fast path: a DIRECTLY-driven standard plug — the overwhelmingly
        # common case — needs none of the per-plug nodeType probes the
        # intermediary walk requires.
        for plug in plugs:
            attr = plug.rsplit(".", 1)[-1] if "." in plug else ""
            if attr in STANDARD_TRANSFORM_ATTRS:
                return attr, plug.split(".")[0]
        for attr, node in cls._terminals_from_plugs(plugs, 0):
            if attr in STANDARD_TRANSFORM_ATTRS:
                return attr, node
        return None

    @staticmethod
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
                    transform = Detection.resolve_to_transform(node, cache=node_cache)
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
        return ShotDetection.cluster_segments_by_gap(
            segments, gap_threshold=gap_threshold, min_duration=min_duration
        )

    @staticmethod
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
            values = (
                cmds.keyframe(crv, query=True, selected=True, valueChange=True) or []
            )
            conns = cmds.listConnections(crv, d=True, s=False) or []
            obj_name = crv  # fallback
            for node in conns:
                transform = Detection.resolve_to_transform(node, cache=node_cache)
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
        candidates = ShotDetection.boundaries_from_key_entries(
            entries, gap_threshold=gap_threshold, key_filter=key_filter
        )
        return _DetectionInternal._filter_flat_objects(candidates)


# ---------------------------------------------------------------------------
# Shot-region detection  (shared by sequencer + manifest)
# ---------------------------------------------------------------------------
