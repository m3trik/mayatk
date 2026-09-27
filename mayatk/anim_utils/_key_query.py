# !/usr/bin/python
# coding=utf-8
"""Curve and key discovery behind :class:`mayatk.AnimUtils`.

Which animation curves drive a node (through unitConversion / pairBlend /
animBlend intermediaries), which keys exist and at what times, and the
channel-box / ignore-pattern filters every key operation scopes by. Reached
through :class:`mayatk.AnimUtils`; nothing here is called directly.
"""

from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

import pythontk as ptk


class _KeyQueryInternal:
    """Private helpers and ``AnimUtils`` method bodies; see the module docstring."""

    # Time-driven animCurve node types.  ``listConnections(type="animCurve")``
    # also matches the UNITLESS subtypes (animCurveUU/UA/UL/UT) that
    # set-driven keys use — for those, "keyframe times" are DRIVER VALUES,
    # so time operations (snap-to-frame, tie-bookends, untied/fractional
    # checks) silently corrupt the rig's driven-key mapping or false-positive.
    TIME_CURVE_TYPES = ("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT")

    #: DG node types that sit between an anim curve and the channel it
    #: drives; ``Detection._DG_INTERMEDIARIES`` is this same tuple.  The
    #: animBlendNode family is matched by prefix (see ``_is_curve_intermediary``).
    _CURVE_INTERMEDIARIES = ("unitConversion", "pairBlend")

    @classmethod
    def _filter_time_curves(
        cls, curves: List[str], include_driven: bool = False
    ) -> List[str]:
        """Restrict *curves* to time-driven animCurves (see TIME_CURVE_TYPES).

        ``include_driven=True`` returns the list unchanged — the escape hatch
        for a caller that genuinely wants unitless (set-driven-key) curves.
        """
        if include_driven or not curves:
            return curves
        return cmds.ls(curves, type=list(cls.TIME_CURVE_TYPES)) or []

    @staticmethod
    def _parse_ignore_patterns(
        ignore: Optional[Union[str, List[str]]],
    ) -> Tuple[Set[str], Set[str]]:
        """Parse ignore patterns into (full_names, simple_names) lowercase sets.

        The simple name is the last component after ``.`` or ``|`` so a
        pattern like ``pCube1.translateX`` also matches bare ``translateX``.
        """
        ignore_list = [ignore] if isinstance(ignore, str) else (ignore or [])
        ignored_full: Set[str] = set()
        ignored_simple: Set[str] = set()

        for pattern in ignore_list:
            if not pattern:
                continue
            pattern_lower = str(pattern).strip().lower()
            if not pattern_lower:
                continue
            ignored_full.add(pattern_lower)
            ignored_simple.add(pattern_lower.replace("|", ".").rsplit(".", 1)[-1])

        return ignored_full, ignored_simple

    @staticmethod
    def _get_channel_box_attrs() -> List[str]:
        """Selected Channel Box main-attribute names (SHORT names, e.g. 'tx').

        Returns an empty list when nothing is highlighted or no Channel Box
        exists (batch/standalone). Match short names against plugs with
        :meth:`_plug_attr_names`, which normalizes both spellings.
        """
        try:
            channel_box = mel.eval("$tmpvar=$gChannelBoxName")
        except RuntimeError:
            channel_box = "mainChannelBox"
        try:
            return (
                cmds.channelBox(channel_box, query=True, selectedMainAttributes=True)
                or []
            )
        except RuntimeError:
            return []

    @staticmethod
    def _is_curve_intermediary(node: str) -> bool:
        """Whether *node* is a unitConversion / pairBlend / animBlendNode."""
        try:
            ntype = cmds.nodeType(node)
        except RuntimeError:
            return False
        return ntype in _KeyQueryInternal._CURVE_INTERMEDIARIES or ntype.startswith(
            "animBlendNode"
        )

    @staticmethod
    def _curves_behind_blends(nodes: List[str], _depth: int = 0) -> List[str]:
        """Anim curves feeding *nodes* through their data inputs.

        Follows ``in*`` plugs only (``inputA`` / ``inputB`` on a blend node,
        ``input`` on a unitConversion, ``inTranslateX1`` on a pairBlend) --
        never a weight or ``message`` -- and recurses through nested
        intermediaries (stacked layers).  Depth-bounded like
        ``Detection.terminal_destinations``, its downstream mirror.
        """
        pairs = (
            cmds.listConnections(
                nodes, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        found: List[str] = []
        walk = _KeyQueryInternal
        for dst_on_node, src in zip(pairs[0::2], pairs[1::2]):
            if not dst_on_node.rsplit(".", 1)[-1].startswith("in"):
                continue
            src_node = src.split(".")[0]
            try:
                ntype = cmds.nodeType(src_node)
            except RuntimeError:
                continue
            if ntype.startswith("animCurve"):
                found.append(src_node)
            elif _depth < 3 and walk._is_curve_intermediary(src_node):
                found.extend(walk._curves_behind_blends([src_node], _depth + 1))
        return list(dict.fromkeys(found))

    @staticmethod
    def _plug_attr_names(plug: str) -> Set[str]:
        """Lowercased attribute-name spellings for a ``node.attr`` plug.

        Contains the leaf name as given plus its long and short forms
        (e.g. ``{'translatex', 'tx'}``) so Channel Box short names match
        plugs reported with long names and vice versa.
        """
        node, _, attr = str(plug).partition(".")
        leaf = attr.split(".")[-1].split("[")[0]
        names = {attr.lower(), leaf.lower()}
        try:
            long_n = cmds.attributeQuery(leaf, node=node, longName=True)
            short_n = cmds.attributeQuery(leaf, node=node, shortName=True)
            names.update((long_n.lower(), short_n.lower()))
        except RuntimeError:
            pass
        return names

    @classmethod
    def _filter_attributes_by_ignore(
        cls, attributes: Optional[List[Any]], ignore: Optional[Union[str, List[str]]]
    ) -> List[Any]:
        """Filter attribute names based on the ignore list."""

        if not attributes:
            return []

        if not ignore:
            return list(attributes)

        ignored_full, ignored_simple = cls._parse_ignore_patterns(ignore)
        if not ignored_full and not ignored_simple:
            return list(attributes)

        filtered: List[Any] = []
        for attr in attributes:
            attr_lower = str(attr).lower()
            simple = attr_lower.split(".")[-1]
            if attr_lower in ignored_full or simple in ignored_simple:
                continue
            filtered.append(attr)
        return filtered

    @classmethod
    def _filter_curves_by_ignore(
        cls,
        curves: Optional[List[str]],
        ignore: Optional[Union[str, List[str]]],
    ) -> List[str]:
        """Filter animation curves that should be ignored."""

        if not curves:
            return []

        if not ignore:
            return list(curves)

        ignored_full, ignored_attrs = cls._parse_ignore_patterns(ignore)

        ignored_suffixes: Tuple[str, ...] = tuple(
            list(f"_{attr}" for attr in ignored_attrs)
            + list(f".{attr}" for attr in ignored_attrs)
        )

        filtered: List[str] = []
        for curve in curves:
            curve_node = str(curve)
            curve_name = curve_node.lower()
            if curve_name in ignored_full:
                continue

            connections = (
                cmds.listConnections(
                    curve_node, plugs=True, destination=True, source=False
                )
                or []
            )

            include_curve = True
            for conn in connections:
                full_name = str(conn).lower()
                simple_name = full_name.split(".")[-1]

                if full_name in ignored_full or simple_name in ignored_attrs:
                    include_curve = False
                    break

            if include_curve and ignored_suffixes:
                if curve_name.endswith(ignored_suffixes):
                    include_curve = False

            if include_curve:
                filtered.append(curve_node)

        return filtered

    @staticmethod
    def _get_visibility_curves(
        curves: List[str],
    ) -> Tuple[List[str], List[str]]:
        """Split curves into visibility curves and others.

        Returns:
            Tuple of (visibility_curves, other_curves)
        """
        vis_curves = []
        other_curves = []

        for curve in curves:
            is_visibility = False
            curve_str = str(curve)
            curve_name = curve_str.split("|")[-1].split(":")[-1].lower()
            try:
                if "visibility" in curve_name:
                    is_visibility = True
                else:
                    plugs = (
                        cmds.listConnections(
                            curve_str, plugs=True, destination=True, source=False
                        )
                        or []
                    )
                    for plug in plugs:
                        plug_name = str(plug).split(".")[-1].lower()
                        if "visibility" in plug_name:
                            is_visibility = True
                            break

                        try:
                            attr_type = cmds.getAttr(str(plug), type=True)
                            if attr_type == "bool":
                                is_visibility = True
                                break
                        except Exception:
                            pass
            except Exception:
                pass  # Unclassifiable curve — treat as non-visibility.

            if is_visibility:
                vis_curves.append(curve)
            else:
                other_curves.append(curve)

        return vis_curves, other_curves

    @staticmethod
    def _curves_to_attributes(curves: List[str], obj: str) -> List[str]:
        """Helper method to extract attribute names from animation curves connected to an object."""

        attributes = []
        obj_str = str(obj)
        for curve in curves:
            connections = cmds.listConnections(
                str(curve), plugs=True, destination=True, source=False
            )
            if connections:
                for conn in connections:
                    attr_name = str(conn).split(".")[-1]
                    if attr_name and cmds.attributeQuery(
                        attr_name, node=obj_str, exists=True
                    ):
                        attributes.append(attr_name)
        return list(set(attributes))

    @staticmethod
    def _resolve_keyed_objects(objects) -> List[str]:
        """Coerce *objects* to a list of node-name strings.

        None means every keyed transform in the scene.
        """
        if objects is None:
            return [
                obj
                for obj in cmds.ls(type="transform", long=True)
                if cmds.keyframe(obj, query=True, timeChange=True)
            ]
        if isinstance(objects, str):
            return [objects]
        if isinstance(objects, (list, tuple, set)):
            return [str(o) for o in objects]
        return [str(objects)]

    @classmethod
    def _objects_to_curves(cls, objects, recursive, as_strings, through_blends):
        """Body of :meth:`AnimUtils.objects_to_curves`."""
        # Use cmds.ls to handle various forms of input (single object, string, list)
        # Ensure input is a list of strings for cmds
        if objects is None:
            objects = []
        elif isinstance(objects, str):
            objects = [objects]
        elif isinstance(objects, (list, tuple, set)):
            objects = [str(o) for o in objects]
        else:
            objects = [str(objects)]

        objects = cmds.ls(objects, flatten=True)
        if not objects:
            return []

        anim_curves = set()

        # Batch: separate objects that are already anim curves
        existing_curves = set(cmds.ls(objects, type="animCurve") or [])
        anim_curves.update(existing_curves)

        # Non-curve objects need connection queries
        non_curves = [o for o in objects if o not in existing_curves]
        if non_curves:
            probe = list(non_curves)
            if recursive:
                # Batch: get all descendants at once
                probe += (
                    cmds.listRelatives(
                        non_curves,
                        allDescendents=True,
                        type="transform",
                        fullPath=True,
                    )
                    or []
                )
            # Batch: single listConnections for every probed node
            anim_curves.update(
                cmds.listConnections(
                    probe, type="animCurve", source=True, destination=False
                )
                or []
            )
            if through_blends:
                # A layered / constrained / unit-converted channel is driven by
                # an intermediary, not a curve: walk behind it.
                blends = list(
                    dict.fromkeys(
                        n
                        for n in cmds.listConnections(
                            probe, source=True, destination=False
                        )
                        or []
                        if cls._is_curve_intermediary(n)
                    )
                )
                if blends:
                    anim_curves.update(cls._curves_behind_blends(blends))

        # Return the results as a list, preserving the unique set of animCurves
        return list(anim_curves)

    @classmethod
    def _get_anim_curves(cls, objects, selected_keys_only, recursive):
        """Body of :meth:`AnimUtils.get_anim_curves`."""
        if objects is None:
            if selected_keys_only:
                # Get animation curves from selected keys in graph editor
                anim_curves = cmds.keyframe(query=True, sl=True, name=True)
                return list(set(anim_curves)) if anim_curves else []
            else:
                # Get all animation curves in the scene
                return cmds.ls(type="animCurve")
        else:
            # Use existing objects_to_curves method for objects
            # This uses cmds.listConnections which properly gets ALL curve types including visibility
            return cls.objects_to_curves(objects, recursive=recursive)

    #: Node types through which ``cmds.keyframe`` reaches an object's keys
    #: INDIRECTLY: an animation-layer blend (``animBlendNodeBase`` is every
    #: layer blend's base class), a ``pairBlend``, the unit conversion Maya
    #: inserts between mismatched units, or a character set (its members'
    #: curves drive the set's value arrays, which drive the plugs). A curve
    #: wired straight to the plug is the direct case. A node with no incoming
    #: connection from a curve or one of these has nothing ``keyframe`` could
    #: report -- measured: driven keys behind a ``blendWeighted`` are invisible
    #: to it, so that type is deliberately absent.
    _KEY_BLEND_TYPES = ("animBlendNodeBase", "pairBlend", "unitConversion", "character")

    @classmethod
    def _key_sources(cls, objects: List[str]) -> Tuple[List[str], List[str], List[str]]:
        """``(keyed, direct_curves, blended)`` for *objects*, from ONE probe.

        *keyed* is the subset of *objects* that carries keys at all, spelled
        and ordered as given; *direct_curves* the animation curves wired
        straight onto their plugs (plus any of *objects* that is itself a
        curve); *blended* the subset whose keys sit behind a layer blend, a
        pairBlend or a unit conversion, which only a per-object
        ``cmds.keyframe`` query can read through.

        Two batched queries -- every incoming connection
        (:meth:`NodeUtils.incoming_connections`), then a type filter over its
        sources -- so a caller can answer "which keys" without
        paying ``cmds.keyframe``'s per-object price on every node (measured
        0.15 ms/node: 360 ms over a 2400-node export subtree, asked up to
        five times per export, against ~60 ms this way).
        """
        from mayatk.node_utils._node_utils import NodeUtils

        objects = [str(o) for o in objects]
        if not objects:
            return [], [], []
        pairs = NodeUtils.incoming_connections(objects)
        # Both sides come back as the shortest UNIQUE path, so they are
        # matched to the caller's spelling on the long path.
        src_nodes = [src.rsplit(".", 1)[0] for _dest, src in pairs]
        curves = set(cmds.ls(src_nodes, type="animCurve") or [])
        blends = set(cmds.ls(src_nodes, type=list(cls._KEY_BLEND_TYPES)) or [])
        direct: List[str] = []
        direct_dests: set = set()
        blended_dests: set = set()
        for dest, src in pairs:
            node = src.rsplit(".", 1)[0]
            if node in curves:
                # ``keyframe`` reads only KEYABLE, UNLOCKED plugs (measured: a
                # curve on a mesh shape's visibility, on a channel made
                # non-keyable, or on a locked one is invisible to it), so the
                # direct path applies the same rule -- two flag reads per wire.
                if not cmds.getAttr(dest, keyable=True) or cmds.getAttr(
                    dest, lock=True
                ):
                    continue
                direct.append(node)
                direct_dests.add(dest.rsplit(".", 1)[0])
            elif node in blends:
                blended_dests.add(dest.rsplit(".", 1)[0])
        # A list of full DAG paths (the export subtree case) needs no
        # resolving: it holds no DG node, so no curve, and it already IS the
        # long spelling ``ls`` would return.
        all_dag_paths = all(o.startswith("|") for o in objects)
        own_curves = (
            [] if all_dag_paths else cmds.ls(objects, type="animCurve", long=True) or []
        )
        direct.extend(own_curves)
        keyed_long = set(cmds.ls(list(direct_dests | blended_dests), long=True) or [])
        keyed_long.update(own_curves)
        blended_long = set(cmds.ls(list(blended_dests), long=True) or [])
        if not keyed_long:
            return [], [], []
        longs = objects if all_dag_paths else cmds.ls(objects, long=True) or []
        if len(longs) != len(objects):  # a missing or duplicated name misaligns
            longs = [(cmds.ls(o, long=True) or [None])[0] for o in objects]
        keyed = [o for o, long in zip(objects, longs) if long in keyed_long]
        blended = [o for o, long in zip(objects, longs) if long in blended_long]
        return keyed, list(dict.fromkeys(direct)), blended

    @classmethod
    def _keyed_nodes(cls, objects):
        """Body of :meth:`AnimUtils.keyed_nodes`."""
        if isinstance(objects, str):
            objects = [objects]
        return cls._key_sources(list(objects))[0]

    @classmethod
    def _curves_and_blended(
        cls,
        sources: Union[str, List[str]],
    ) -> Tuple[List[str], List[str]]:
        """``(curves, blended)`` behind *sources* (objects or curves), from the
        one probe :meth:`_key_sources` makes: the curves wired straight onto
        keyable plugs (and any source that is itself a curve), and the objects
        whose keys sit behind a layer blend, pairBlend or unit conversion --
        which only ``cmds.keyframe`` resolves."""
        if sources is None:
            sources = []
        elif isinstance(sources, str):
            sources = [sources]
        else:
            sources = [str(s) for s in sources]
        sources = cmds.ls(sources, flatten=True) or []
        if not sources:
            return [], []
        _keyed, curves, blended = cls._key_sources(sources)
        return curves, blended

    @staticmethod
    def _anim_curve_fns(curves: List[str]) -> list:
        """An ``MFnAnimCurve`` per existing curve in *curves* (deduplicated),
        resolved through one selection list."""
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        selection = om2.MSelectionList()
        for curve in dict.fromkeys(curves):
            try:
                selection.add(curve)
            except RuntimeError:  # gone since it was listed
                continue
        return [
            oma2.MFnAnimCurve(selection.getDependNode(i))
            for i in range(selection.length())
        ]

    @classmethod
    def _has_keyframes(cls, sources):
        """Body of :meth:`AnimUtils.has_keyframes`."""
        curves, blended = cls._curves_and_blended(sources)
        if any(fn.numKeys for fn in cls._anim_curve_fns(curves)):
            return True
        return any(
            cmds.keyframe(obj, query=True, keyframeCount=True) for obj in blended
        )

    @classmethod
    def _keyframe_range(cls, sources):
        """Body of :meth:`AnimUtils.keyframe_range`."""
        curves, blended = cls._curves_and_blended(sources)
        if blended:
            curves = curves + (cmds.keyframe(blended, query=True, name=True) or [])
        return cls.curve_key_spans(curves, [(None, None)])[0]

    @classmethod
    def _curve_key_spans(cls, curves, windows):
        """Body of :meth:`AnimUtils.curve_key_spans`."""
        import maya.api.OpenMaya as om2

        unit = om2.MTime.uiUnit()
        timelines = []
        for fn in cls._anim_curve_fns(curves):
            count = fn.numKeys
            if count and fn.isTimeInput:
                head = fn.input(0).asUnits(unit)
                timelines.append((fn, head, fn.input(count - 1).asUnits(unit)))
        spans: List[Optional[Tuple[float, float]]] = []
        for start, end in windows:
            start = None if start is None else float(start)
            end = None if end is None else float(end)
            first = last = None
            for fn, head, tail in timelines:
                if (start is not None and tail < start) or (
                    end is not None and head > end
                ):
                    continue
                # The key closest to a bound inside the curve is either the
                # first key past it or the last key before it; one step
                # settles which.
                lo, hi = head, tail
                if start is not None and head < start:
                    index = fn.findClosest(om2.MTime(start, unit))
                    if fn.input(index).asUnits(unit) < start:
                        index += 1
                    lo = fn.input(index).asUnits(unit)
                if end is not None and tail > end:
                    index = fn.findClosest(om2.MTime(end, unit))
                    if fn.input(index).asUnits(unit) > end:
                        index -= 1
                    hi = fn.input(index).asUnits(unit)
                if lo > hi:  # the window sits in a gap between two keys
                    continue
                first = lo if first is None else min(first, lo)
                last = hi if last is None else max(last, hi)
            spans.append(None if first is None else (first, last))
        return spans

    @classmethod
    def _get_keyframe_times(cls, sources, mode, from_curves, as_range, time_range):
        """Body of :meth:`AnimUtils.get_keyframe_times`."""
        # Ensure sources is a list of strings
        if sources is None:
            sources = []
        elif isinstance(sources, str):
            sources = [sources]
        elif isinstance(sources, (list, tuple, set)):
            sources = [str(s) for s in sources]
        else:
            sources = [str(sources)]

        sources = cmds.ls(sources, flatten=True)
        if not sources:
            return None

        # Auto-detect if working with curves -- one typed ``ls`` rather than
        # a ``nodeType`` per source, which a 2400-node subtree with no curve
        # in it walked to the end on every call.
        if from_curves is None:
            from_curves = bool(cmds.ls(sources, type="animCurve"))

        range_kw = {"time": time_range} if time_range else {}
        all_times = set()

        if from_curves:
            # Working with animation curves directly
            for curve in sources:
                times = None
                if mode in ("selected", "selected_or_all"):
                    times = cmds.keyframe(
                        curve, query=True, selected=True, timeChange=True, **range_kw
                    )

                # If mode is "all" or "selected_or_all" and no selected times found
                if mode == "all" or (mode == "selected_or_all" and not times):
                    times = cmds.keyframe(
                        curve, query=True, timeChange=True, **range_kw
                    )

                if times:
                    all_times.update(times)
        else:
            # Working with objects - need to check for selected keys first
            selected_times = set()

            if mode in ("selected", "selected_or_all"):
                for obj in sources:
                    # Get selected keyframe times from this object
                    curve_nodes = cmds.keyframe(
                        obj, query=True, name=True, selected=True
                    )
                    for curve in curve_nodes or []:
                        times = cmds.keyframe(
                            curve,
                            query=True,
                            selected=True,
                            timeChange=True,
                            **range_kw,
                        )
                        if times:
                            selected_times.update(times)

            # Use selected times if we found any, or get all if mode requires it
            if selected_times:
                all_times = selected_times
            elif mode == "all" or (mode == "selected_or_all" and not selected_times):
                # Read the curves wired straight onto the objects' plugs as
                # curves (one batched call, ~20 ms over 900 curves) and ask
                # ``keyframe`` per OBJECT only where it has to see through a
                # layer blend, a pairBlend or a unit conversion. Asking it per
                # object for everything costs the same for every node in the
                # list, animated or not (measured 0.15 ms/node: 360 ms over a
                # 2400-node export subtree, asked up to five times per export).
                _keyed, direct_curves, blended = cls._key_sources(sources)
                for batch in (direct_curves, blended):
                    if not batch:
                        continue
                    times = cmds.keyframe(
                        batch, query=True, timeChange=True, **range_kw
                    )
                    if times:
                        all_times.update(times)

        if not all_times:
            return None

        sorted_times = sorted(all_times)

        if as_range:
            return (sorted_times[0], sorted_times[-1])
        else:
            return sorted_times

    @staticmethod
    def _get_driver_animation_range(node, driver_type):
        """Body of :meth:`AnimUtils.get_driver_animation_range`."""
        from mayatk.node_utils._node_utils import NodeUtils

        times: List[float] = []

        def _collect_curve_times(source_node: str) -> None:
            """Extend *times* with key times of every animCurve driving *source_node*."""
            curves = (
                cmds.listConnections(
                    source_node, type="animCurve", source=True, destination=False
                )
                or []
            )
            for curve in curves:
                key_times = cmds.keyframe(curve, query=True, timeChange=True)
                if key_times:
                    times.extend(key_times)

        def _collect_ancestor_curve_times(node_name: str) -> None:
            """Extend *times* with key times of every ANCESTOR of *node_name*.

            A constraint (and an IK handle) samples its target's WORLD
            transform, so an animated ancestor keeps the target moving long
            after the target's own channels go quiet. Collecting only the
            target's own curves under-reports the range, and a bake sized from
            it stops early -- the constrained object freezes while its target
            travels on. Measured on a production scene: a wire-loom anchored to
            a plug locator baked to frame 1279 (the locator's own last key)
            while the locator's animated parent carried it to 1482, so the tube
            detached from the plug by ~16 units in the export.

            Walks full DAG paths, not the short names ``get_parent(all=True)``
            splits out -- a rig repeats short names across mirrored branches.
            """
            long_names = cmds.ls(node_name, long=True) or []
            if not long_names:
                return
            parts = long_names[0].split("|")[1:]  # drop the leading ""
            for i in range(1, len(parts)):
                _collect_curve_times("|" + "|".join(parts[:i]))

        # Auto-detect driver type
        if driver_type == "auto":
            if NodeUtils.is_constraint(node):
                driver_type = "constraint"
            elif NodeUtils.is_expression(node):
                driver_type = "expression"
            elif cmds.nodeType(node).startswith("animCurve"):
                if NodeUtils.is_driven_key_curve(node):
                    driver_type = "driven_key"
                else:
                    driver_type = "keyframe"
            elif cmds.nodeType(node) == "ikHandle":
                driver_type = "ik"
            elif NodeUtils.is_ik_effector(node):
                driver_type = "ik"
            elif cmds.nodeType(node) == "motionPath":
                driver_type = "motion_path"
            else:
                driver_type = "unknown"

        if driver_type == "constraint":
            for target in NodeUtils.get_constraint_targets(node):
                _collect_curve_times(target)
                _collect_ancestor_curve_times(target)
            # A constraint pins the constrained node in WORLD space, so its
            # own animated ancestors move its locals too: their keys are
            # driver times as much as the target's are. (The old
            # get_constraint_targets returned the constrained node among the
            # targets and covered this by accident.)
            constrained = (
                cmds.listConnections(
                    node, source=False, destination=True, type="transform"
                )
                or []
            )
            for driven in dict.fromkeys(constrained):
                if driven != node:
                    _collect_ancestor_curve_times(driven)

        elif driver_type == "driven_key":
            input_conn = (
                cmds.listConnections(
                    f"{node}.input", source=True, destination=False, plugs=True
                )
                or []
            )
            for inp in input_conn:
                _collect_curve_times(inp.split(".")[0])

        elif driver_type == "expression":
            inputs = cmds.listConnections(node, source=True, destination=False) or []
            for related in inputs:
                _collect_curve_times(related)

        elif driver_type == "ik":
            handles = cmds.listConnections(node, type="ikHandle", source=True) or []
            if cmds.nodeType(node) == "ikHandle":
                handles = [node]
            for handle in handles:
                _collect_curve_times(handle)
                _collect_ancestor_curve_times(handle)
                # Check pole vector constraint targets
                pv_constraint = cmds.listConnections(
                    f"{handle}.poleVectorX", source=True, destination=False
                )
                for pv in pv_constraint or []:
                    _collect_curve_times(pv)
                    _collect_ancestor_curve_times(pv)

        elif driver_type in ("matrix", "unknown"):
            # Two cases with no target list to query: a matrix drive
            # (offsetParentMatrix <- multMatrix), and any driver the taxonomy
            # does not name -- the curveInfo / distanceBetween networks a
            # spline-IK rig drives squash-stretch with. Both are resolved by
            # walking the network upstream for animCurves, ungated: multMatrix
            # and curveInfo appear in no passthrough set.
            #
            # Erring long is deliberate. A range that overshoots costs surplus
            # keys; one that falls short freezes the bake mid-shot and detaches
            # constrained geometry -- the failure mode this module shipped.
            from mayatk.node_utils.attributes._attributes import Attributes

            for curve in Attributes.upstream_anim_curves(
                node, plug_precise=False, depth=8
            ):
                key_times = cmds.keyframe(curve, query=True, timeChange=True)
                if key_times:
                    times.extend(key_times)

        elif driver_type == "motion_path":
            _collect_curve_times(f"{node}.uValue")

        elif driver_type == "keyframe":
            key_times = cmds.keyframe(node, query=True, timeChange=True)
            if key_times:
                times.extend(key_times)

        elif driver_type in ("inherited_visibility", "inherited_visibility_plugs"):
            # SmartBake's inherited-visibility analysis records ancestor
            # ``.visibility`` DRIVER NODES under "inherited_visibility" and the
            # ancestor ``.visibility`` PLUGS under "inherited_visibility_plugs".
            # Neither is covered by the driver taxonomy above, so without this
            # branch a vis-only bake resolved to no times at all, fell back to
            # the playback range, and silently clamped away every ancestor key
            # outside it -- the keys the bake exists to resolve.
            if "." in node:  # a plug: gather the curves feeding it
                _collect_curve_times(node)
            elif not cmds.objExists(node):
                pass
            elif cmds.nodeType(node).startswith("animCurve"):
                key_times = cmds.keyframe(node, query=True, timeChange=True)
                if key_times:
                    times.extend(key_times)
            else:  # expression / other driver: fall back to its own inputs
                _collect_curve_times(node)

        return times

    @classmethod
    def _resolve_keys(
        cls,
        objects=None,
        mode: str = "auto",
        resolution_order: Optional[Tuple[str, ...]] = None,
    ) -> Dict[str, Any]:
        """Resolve which animation keys to operate on.

        Provides a unified mechanism for determining target keys.  When
        *mode* is ``"auto"``, each strategy in *resolution_order* is
        tried in sequence; the first strategy that yields results wins.

        .. note::

           A classmethod: it calls ``cls.objects_to_curves``, so it is reached
           through ``AnimUtils`` (which composes this class), never directly.

        Strategies
        ----------
        ``"selected"``
            Keys currently selected in the Graph Editor **that belong to
            the resolved** *objects*.  If Channel Box attributes are also
            highlighted, only curves matching those attributes are
            included (with automatic fallback to all selected curves
            when filtering eliminates everything).

        ``"channel_box"``
            All keys on curves whose driven attribute is highlighted in
            the Channel Box.

        ``"current_frame"``
            Keys at the current time on the resolved objects.

        ``"all"``
            Every key on every curve of the resolved objects.

        Parameters:
            objects: Transforms / anim curves to operate on.  Falls back
                to ``cmds.ls(selection=True)`` when *None*.  Accepts pre-resolved
                name lists to avoid redundant ``cmds.ls`` calls by
                callers that already validated their objects.
            mode: Resolution mode.

                - ``"auto"`` — try strategies in *resolution_order*.
                - Any strategy name — use that strategy directly.
            resolution_order: Strategies to try for ``"auto"`` mode.
                Default: ``("selected", "channel_box", "current_frame",
                "all")``.

        Returns:
            dict with:

            ``mode`` (str)
                The concrete strategy that produced results.
            ``curves`` (Dict[str, Optional[List[float]]])
                Mapping of curve names to key times.  ``None`` means
                all keys on that curve.  An empty dict means nothing
                was resolved.
            ``objects`` (list)
                The resolved node objects.
            ``cb_attrs`` (Optional[Set[str]])
                Lowercased Channel Box attribute names when they
                influenced the resolution, else ``None``.
        """
        DEFAULT_ORDER = ("selected", "channel_box", "current_frame", "all")

        # --- Resolve objects (skip if pre-resolved) ---
        if objects is None:
            objects = cmds.ls(selection=True)
        objects = cmds.ls(objects, flatten=True)

        # --- Query context once ---
        sel_curves = cmds.keyframe(query=True, selected=True, name=True) or []
        cb_attrs_raw = cls._get_channel_box_attrs()
        cb_attrs: Optional[Set[str]] = (
            {a.lower() for a in cb_attrs_raw} if cb_attrs_raw else None
        )

        def _curve_matches_cb(crv: str, attrs: Set[str]) -> bool:
            """True if any driven plug of *crv* matches an attr in *attrs*.

            Channel Box reports SHORT names ('tx') while plugs usually carry
            long names — _plug_attr_names normalizes both spellings.
            """
            conns = (
                cmds.listConnections(crv, destination=True, source=False, plugs=True)
                or []
            )
            return any(not cls._plug_attr_names(p).isdisjoint(attrs) for p in conns)

        # Lazy cache for objects_to_curves — computed at most once.
        _obj_curves_cache: List[Optional[List[str]]] = [None]

        def _get_obj_curves() -> List[str]:
            if _obj_curves_cache[0] is None:
                _obj_curves_cache[0] = cls.objects_to_curves(objects) if objects else []
            return _obj_curves_cache[0]

        # Build a set of curve names that belong to *objects* for
        # scoping the "selected" strategy.
        _obj_curve_set_cache: List[Optional[Set[str]]] = [None]

        def _get_obj_curve_set() -> Set[str]:
            if _obj_curve_set_cache[0] is None:
                _obj_curve_set_cache[0] = set(_get_obj_curves())
            return _obj_curve_set_cache[0]

        # --- Strategy implementations ---
        def _try_selected():
            if not sel_curves:
                return None
            # Scope to curves belonging to the resolved objects.  When
            # objects were resolved but own no curves, none of the
            # selected curves can be theirs — returning them unscoped
            # would operate on unrelated objects' animation.
            obj_curve_set = _get_obj_curve_set()
            scoped = (
                [c for c in sel_curves if c in obj_curve_set]
                if objects
                else list(sel_curves)
            )
            if not scoped:
                return None

            # Filter to Channel-Box-highlighted attrs first; fall back to
            # all scoped curves when the filter eliminates everything.
            unique_scoped = set(scoped)
            effective_cb = cb_attrs
            if effective_cb is not None:
                cb_scoped = {
                    crv for crv in unique_scoped if _curve_matches_cb(crv, effective_cb)
                }
                if cb_scoped:
                    unique_scoped = cb_scoped
                else:
                    effective_cb = None

            curves: Dict[str, Optional[List[float]]] = {}
            for crv in unique_scoped:
                times = (
                    cmds.keyframe(crv, query=True, selected=True, timeChange=True) or []
                )
                curves[crv] = times if times else None
            return (
                {"mode": "selected", "curves": curves, "cb_attrs": effective_cb}
                if curves
                else None
            )

        def _try_channel_box():
            if not cb_attrs or not objects:
                return None
            all_curves = _get_obj_curves()
            if not all_curves:
                return None
            curves: Dict[str, Optional[List[float]]] = {
                crv: None for crv in all_curves if _curve_matches_cb(crv, cb_attrs)
            }
            return (
                {"mode": "channel_box", "curves": curves, "cb_attrs": cb_attrs}
                if curves
                else None
            )

        def _try_current_frame():
            if not objects:
                return None
            all_curves = _get_obj_curves()
            if not all_curves:
                return None
            current = cmds.currentTime(query=True)
            curves: Dict[str, Optional[List[float]]] = {}
            for crv in all_curves:
                count = cmds.keyframe(
                    crv, query=True, time=(current, current), keyframeCount=True
                )
                if count:
                    curves[crv] = [current]
            return (
                {"mode": "current_frame", "curves": curves, "cb_attrs": None}
                if curves
                else None
            )

        def _try_all():
            if not objects:
                return None
            all_curves = _get_obj_curves()
            if not all_curves:
                return None
            curves = {crv: None for crv in all_curves}
            return {"mode": "all", "curves": curves, "cb_attrs": None}

        strategies = {
            "selected": _try_selected,
            "channel_box": _try_channel_box,
            "current_frame": _try_current_frame,
            "all": _try_all,
        }

        empty: Dict[str, Any] = {
            "mode": mode,
            "curves": {},
            "objects": objects,
            "cb_attrs": None,
        }

        if mode == "auto":
            order = resolution_order or DEFAULT_ORDER
            for name in order:
                func = strategies.get(name)
                if func:
                    result = func()
                    if result and result["curves"]:
                        result["objects"] = objects
                        return result
            return empty
        else:
            func = strategies.get(mode)
            if func:
                result = func()
                if result:
                    result["objects"] = objects
                    return result
            return empty

    @staticmethod
    def _filter_objects_with_keys(objects, keys):
        """Body of :meth:`AnimUtils.filter_objects_with_keys`."""
        if objects is None:
            objects = cmds.ls(type="transform")
        else:
            objects = cmds.ls(objects, type="transform")

        if keys is None:
            keys = cmds.listAttr(objects, keyable=True) or []

        filtered_objects = []
        for obj in objects:
            for key in ptk.make_iterable(keys):
                if cmds.attributeQuery(key, node=str(obj), exists=True):
                    if cmds.keyframe(f"{obj}.{key}", query=True, name=True):
                        filtered_objects.append(obj)
                        break

        return filtered_objects

    @staticmethod
    def _scene_has_animation():
        """Body of :meth:`AnimUtils.scene_has_animation`."""
        if cmds is None:
            return False
        return bool(
            cmds.ls(type=["animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"])
        )

    @staticmethod
    def _group_overlapping_keyframes(obj_keyframe_data: List[dict]) -> List[dict]:
        """Helper method to group objects with overlapping keyframe ranges into single blocks.

        Objects are considered overlapping if their keyframe time ranges intersect.
        Grouped objects are treated as a single unit during staggering operations.

        Parameters:
            obj_keyframe_data (List[dict]): List of dictionaries containing object keyframe data.
                Each dict should have 'obj', 'keyframes', 'start', 'end', and 'duration' keys.

        Returns:
            List[dict]: List of grouped object data. Each group contains:
                - 'objects': List of objects in the group
                - 'keyframes': Combined keyframe times
                - 'start': Earliest keyframe in the group
                - 'end': Latest keyframe in the group
                - 'duration': Total duration of the group
                - 'obj': Representative object (for backward compatibility)
                - 'sub_groups': List of original data dicts in the group

        Example:
            # Objects with overlapping keyframes [1-10], [5-15], [20-30]
            # Would be grouped as: [[obj1, obj2]], [[obj3]]
        """
        from mayatk.anim_utils.segment_keys import SegmentKeys

        # Strict overlap (touching keys stay separate so sequential animations
        # can still be staggered independently); one entry per member object.
        return SegmentKeys._group_by_overlap(
            obj_keyframe_data, inclusive=False, dedupe_objects=False
        )

    @staticmethod
    def _parse_time_range(time):
        """Body of :meth:`AnimUtils.parse_time_range`."""
        # Handle pipe-separated time strings - return list for recursive processing
        if isinstance(time, str) and "|" in time:
            return [p.strip() for p in time.split("|")]

        # Handle tuples/lists with more than 2 values - return list for recursive processing
        if isinstance(time, (list, tuple)) and len(time) > 2:
            return list(time)

        # Determine time range for single time specification
        time_range = None

        if isinstance(time, str):
            time_lower = time.lower()
            current_time = cmds.currentTime(query=True)

            if time_lower == "current":
                time_range = (current_time, current_time)
            elif time_lower == "before":
                # From very early time to just before current.  A sub-frame
                # epsilon (not a whole frame) so fractional keys within one
                # frame of current are still included, per the contract
                # "everything before current, excluding current".
                time_range = (-1000000, current_time - 0.001)
            elif time_lower == "after":
                # From just after current to very late time
                time_range = (current_time + 0.001, 1000000)
            elif time_lower == "all":
                time_range = None  # Process all
        elif isinstance(time, (list, tuple)) and len(time) == 2:
            time_range = (time[0], time[1])
        elif isinstance(time, (int, float)) and not isinstance(time, bool):
            # Accept float frames too — falling through to None here would
            # make callers like delete_keys treat a single frame as
            # "entire timeline".
            time_range = (time, time)

        return time_range

    @classmethod
    def _get_frame_ranges(cls, objects, precision, gap):
        """Body of :meth:`AnimUtils.get_frame_ranges`."""

        def round_to_nearest(value: float, base: int) -> int:
            return int(base * round(value / base))

        # Normalize once: a non-positive precision would divide by zero.
        if precision is not None and precision <= 0:
            precision = None

        frame_ranges = {}
        for obj in objects:
            keyframes = cls.get_keyframe_times(obj)
            if keyframes:
                ranges = []
                start_frame = keyframes[0]
                last_frame = keyframes[0]

                for kf in keyframes[1:]:
                    if gap is not None and kf - last_frame > gap:
                        end_frame = last_frame
                        if precision is not None:
                            start_frame = round_to_nearest(start_frame, precision)
                            end_frame = round_to_nearest(end_frame, precision)
                        ranges.append((start_frame, end_frame))
                        start_frame = kf
                    last_frame = kf

                end_frame = last_frame
                if precision is not None:
                    start_frame = round_to_nearest(start_frame, precision)
                    end_frame = round_to_nearest(end_frame, precision)
                ranges.append((start_frame, end_frame))

                frame_ranges[obj] = ranges
            else:
                frame_ranges[obj] = [(None, None)]  # No keyframes, no range

        return frame_ranges

    @staticmethod
    def _get_selected_key_times(curves):
        """Body of :meth:`AnimUtils.get_selected_key_times`."""
        selected = cmds.keyframe(query=True, selected=True, name=True) or []
        if curves is not None:
            allowed = set(curves)
            selected = [c for c in selected if c in allowed]
        out: Dict[str, List[float]] = {}
        for crv in dict.fromkeys(selected):
            times = cmds.keyframe(crv, query=True, selected=True, timeChange=True)
            if times:
                out[crv] = sorted(set(times))
        return out
