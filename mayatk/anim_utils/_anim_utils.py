# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Dict, Iterable, Optional, Union, Any, Set, Callable
import collections
import json
import math

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError as error:
    cmds = None
    mel = None
    print(__file__, error)
try:
    from maya.api import OpenMaya as om
except ImportError:
    om = None

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils

STANDARD_TRANSFORM_ATTRS: frozenset = frozenset(
    {
        "translateX",
        "translateY",
        "translateZ",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scaleX",
        "scaleY",
        "scaleZ",
        "visibility",
    }
)
"""Per-axis transform + visibility attributes.

Used across the shots system and SmartBake to distinguish genuine
scene-content animation from custom trigger/marker attributes.
"""


class _AnimUtilsInternal:
    """Helper mixin that contains internal shared logic for AnimUtils"""

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
        return ntype in _AnimUtilsInternal._CURVE_INTERMEDIARIES or ntype.startswith(
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
        walk = _AnimUtilsInternal
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
    def _set_smart_tangents(
        curves: List[str],
        tangent_type: str = "auto",
        time_range: Optional[Tuple[float, float]] = None,
        preserve_stepped: bool = True,
    ) -> None:
        """Apply tangent type to curves, enforcing 'step' for visibility attributes.

        Parameters:
            curves: List of animation curves to modify.
            tangent_type: The tangent type to apply to standard curves (default: 'auto').
            time_range: Optional (start, end) tuple to limit the effect.
            preserve_stepped: If True, existing 'step' tangents on non-visibility curves
                            will be preserved. Default is True.
        """
        if not curves:
            return

        curves_to_step, curves_to_smooth = _AnimUtilsInternal._get_visibility_curves(
            curves
        )

        range_args = {"time": time_range} if time_range else {}

        if curves_to_smooth:
            for curve in curves_to_smooth:
                try:
                    # If preserving stepped tangents, we need to check existing types
                    restore_steps = []
                    if preserve_stepped:
                        # Query existing tangent types
                        # Note: keyTangent query returns list of strings
                        # We must query times first to match them up, as keyTangent might return types for all keys in range
                        times = cmds.keyframe(curve, query=True, tc=True, **range_args)
                        if times:
                            types = cmds.keyTangent(
                                curve, query=True, outTangentType=True, **range_args
                            )
                            if types and len(types) == len(times):
                                # Identify stepped keys
                                for t, type_name in zip(times, types):
                                    if type_name in ("step", "stepnext"):
                                        restore_steps.append(t)

                    # Apply smoothing to all (bulk operation is faster)
                    cmds.keyTangent(
                        curve,
                        edit=True,
                        outTangentType=tangent_type,
                        inTangentType=tangent_type,
                        **range_args,
                    )

                    # Restore stepped tangents if any were found
                    if restore_steps:
                        for t in restore_steps:
                            cmds.keyTangent(
                                curve,
                                edit=True,
                                time=(t,),
                                outTangentType="step",
                                inTangentType="clamped",
                            )

                except RuntimeError as e:
                    cmds.warning(f"Failed to adjust smooth tangents for {curve}: {e}")

        if curves_to_step:
            try:
                # 'step' is only valid for out-tangents.
                # For in-tangents, we use 'clamped' to avoid errors, though it doesn't affect the step behavior.
                cmds.keyTangent(
                    curves_to_step,
                    edit=True,
                    outTangentType="step",
                    inTangentType="clamped",
                    **range_args,
                )
            except RuntimeError as e:
                cmds.warning(f"Failed to adjust step tangents: {e}")

    @classmethod
    def _compute_motion_progress(
        cls,
        obj: str,
        time_range: Tuple[float, float],
        samples: Optional[int] = None,
        include_rotation: Union[bool, str] = False,
    ) -> Tuple[List[float], List[float], float]:
        """Sample an object's motion and return normalized progress values."""

        if obj is None or not cmds.objExists(obj):
            return [], [], 0.0

        if not time_range or len(time_range) != 2:
            return [], [], 0.0

        start, end = time_range
        if start is None or end is None or end <= start:
            return [], [], 0.0

        try:
            sample_count = int(samples) if samples is not None else 64
        except (TypeError, ValueError):
            sample_count = 64

        sample_count = max(3, sample_count)

        span = end - start
        if math.isclose(span, 0.0):
            return [], [], 0.0

        sample_times = [
            float(start + (span * index) / (sample_count - 1))
            for index in range(sample_count)
        ]

        current_time = cmds.currentTime(query=True)
        positions: List[Tuple[float, float, float]] = []
        rotations: List[Tuple[float, float, float]] = []

        try:
            for time_value in sample_times:
                cmds.currentTime(time_value, edit=True)

                # Sample position
                position = cmds.xform(
                    obj, query=True, worldSpace=True, translation=True
                )
                if not position or len(position) < 3:
                    return [], [], 0.0
                positions.append(
                    (float(position[0]), float(position[1]), float(position[2]))
                )

                # Sample rotation if needed
                if include_rotation:
                    rotation = cmds.xform(
                        obj, query=True, worldSpace=True, rotation=True
                    )
                    if rotation and len(rotation) >= 3:
                        rotations.append(
                            (float(rotation[0]), float(rotation[1]), float(rotation[2]))
                        )
                    else:
                        rotations.append((0.0, 0.0, 0.0))

        except Exception:
            return [], [], 0.0
        finally:
            cmds.currentTime(current_time, edit=True)

        if len(positions) < 2:
            return [], [], 0.0

        cumulative: List[float] = [0.0]
        total_distance = 0.0

        for index in range(1, len(positions)):
            # Translation distance
            dist_trans = 0.0
            if include_rotation != "only":
                dist_trans = ptk.MathUtils.distance_between_points(
                    positions[index - 1], positions[index]
                )

            dist_rot = 0.0

            # Rotation distance (arc length approximation)
            if include_rotation and index < len(rotations):
                r1_vals = rotations[index - 1]
                r2_vals = rotations[index]

                # Simplified rotation distance: Euclidean distance of Euler angles
                # Treats 1 degree of rotation as equivalent to 1 unit of translation
                d_rx = abs(r2_vals[0] - r1_vals[0])
                d_ry = abs(r2_vals[1] - r1_vals[1])
                d_rz = abs(r2_vals[2] - r1_vals[2])
                dist_rot = math.sqrt(d_rx * d_rx + d_ry * d_ry + d_rz * d_rz)

            # Use the maximum of translation or rotation distance
            step_distance = max(dist_trans, dist_rot)

            total_distance += step_distance
            cumulative.append(total_distance)

        if total_distance <= 1e-8:
            progress = [0.0 for _ in cumulative]
        else:
            progress = [value / total_distance for value in cumulative]

        return sample_times, progress, total_distance

    @staticmethod
    def _get_curve_tangent_data(curve: str, time: float) -> Optional[Dict[str, Any]]:
        """Capture tangent information for a keyframe on the given curve."""

        try:
            return {
                "inTangentType": cmds.keyTangent(
                    curve, query=True, time=(time,), inTangentType=True
                )[0],
                "outTangentType": cmds.keyTangent(
                    curve, query=True, time=(time,), outTangentType=True
                )[0],
                "inAngle": cmds.keyTangent(
                    curve, query=True, time=(time,), inAngle=True
                )[0],
                "outAngle": cmds.keyTangent(
                    curve, query=True, time=(time,), outAngle=True
                )[0],
                "inWeight": cmds.keyTangent(
                    curve, query=True, time=(time,), inWeight=True
                )[0],
                "outWeight": cmds.keyTangent(
                    curve, query=True, time=(time,), outWeight=True
                )[0],
            }
        except Exception:
            return None

    # Stepped tangents are an OUT-side-only property: Maya rejects
    # ``inTangentType="step"`` outright and accepts ``inTangentType="stepnext"``
    # while ignoring it — a segment's shape is governed entirely by the OUT
    # tangent of the key that PRECEDES it.  Mirroring in time therefore has to
    # migrate a step flag to the neighbouring key (and swap its sense), not
    # swap it onto the in side like an ordinary tangent handle.
    _STEP_TANGENT_TYPES: Tuple[str, str] = ("step", "stepnext")
    _MIRRORED_STEP_TYPES: Dict[str, str] = {"step": "stepnext", "stepnext": "step"}

    @classmethod
    def _mirror_tangent_data(
        cls,
        data: List[Dict[str, Any]],
        flip_time: bool,
        flip_value: bool,
    ) -> List[Dict[str, Any]]:
        """Mirror per-key tangent snapshots for a time and/or value inversion.

        A time flip swaps each key's handles (in <-> out, angles negated) and
        relocates stepped segments: the hold described by key *i*'s out tangent
        spans the segment to key *i+1*, and that segment's later-in-time end is
        key *i* once reversed — so the flag moves to key *i+1*'s out tangent as
        its opposite (``step`` <-> ``stepnext``).  A value flip only negates the
        angles; a hold is a hold regardless of which way the values run.

        Angles ride along for every type, including the self-computing ones
        (``auto``, ``linear``, ...) whose angle Maya recomputes: ``set_tangent_info``
        writes the types LAST, so a type that owns its own angle simply
        discards what was written and the caller needs no per-type gating.

        Parameters:
            data: One snapshot per key of a SINGLE curve, in ascending time
                order (entries as returned by ``get_tangent_info``; an empty
                dict for a time carrying no key).
            flip_time: The keys are being reversed in time.
            flip_value: The key values are being flipped about a pivot.

        Returns:
            List[Dict[str, Any]]: Parallel to *data* — entry ``i`` is the
            snapshot to apply to the key that entry ``i`` described, at its
            new (inverted) time.
        """
        if not flip_time:  # value-only flip: handles keep their sides
            mirrored = []
            for snapshot in data:
                if not snapshot:
                    mirrored.append(snapshot)
                    continue
                entry = dict(snapshot)
                if flip_value:
                    for key in ("inAngle", "outAngle"):
                        entry[key] = -entry[key]
                mirrored.append(entry)
            return mirrored

        # Time flip: swap each key's handles.  A second negation cancels out
        # when the values are flipped too, so "both" is a plain swap.
        sign = 1.0 if flip_value else -1.0
        mirrored = []
        for snapshot in data:
            if not snapshot:
                mirrored.append(snapshot)
                continue
            in_type = snapshot["inTangentType"]
            out_type = snapshot["outTangentType"]
            mirrored.append(
                {
                    # A stepped tangent's own handle is flat (angle 0) and it
                    # cannot live on the in side, so it crosses over as "flat";
                    # the migration pass below re-homes the hold itself.
                    "inTangentType": (
                        "flat" if out_type in cls._STEP_TANGENT_TYPES else out_type
                    ),
                    "outTangentType": (
                        "flat" if in_type in cls._STEP_TANGENT_TYPES else in_type
                    ),
                    "inAngle": sign * snapshot["outAngle"],
                    "outAngle": sign * snapshot["inAngle"],
                    "inWeight": snapshot["outWeight"],
                    "outWeight": snapshot["inWeight"],
                }
            )

        # Migrate stepped segments onto the key that now precedes them.  The
        # last key's out tangent only drives extrapolation, so it is dropped.
        for index, snapshot in enumerate(data):
            if not snapshot or index + 1 >= len(data):
                continue
            out_type = snapshot["outTangentType"]
            if out_type in cls._STEP_TANGENT_TYPES and mirrored[index + 1]:
                mirrored[index + 1]["outTangentType"] = cls._MIRRORED_STEP_TYPES[
                    out_type
                ]

        return mirrored

    @classmethod
    def _apply_curve_tangent_data(
        cls, curve: str, time: float, data: Optional[Dict[str, Any]]
    ) -> None:
        """Restore tangent information for a keyframe on the given curve."""

        if not data:
            return

        in_type = data.get("inTangentType")
        out_type = data.get("outTangentType")

        try:
            cmds.keyTangent(
                curve,
                edit=True,
                time=(time,),
                inTangentType=in_type,
                outTangentType=out_type,
            )
            # Only apply angle/weight per non-step side — editing an angle on
            # a stepped side silently converts it to "fixed", destroying the
            # hold, so each side must be gated independently.  Sides whose
            # data is absent (partial snapshots) are skipped.
            angle_kwargs = {}
            if (
                in_type not in cls._STEP_TANGENT_TYPES
                and data.get("inAngle") is not None
            ):
                angle_kwargs["inAngle"] = data["inAngle"]
                angle_kwargs["inWeight"] = data["inWeight"]
            if (
                out_type not in cls._STEP_TANGENT_TYPES
                and data.get("outAngle") is not None
            ):
                angle_kwargs["outAngle"] = data["outAngle"]
                angle_kwargs["outWeight"] = data["outWeight"]
            if angle_kwargs:
                cmds.keyTangent(curve, edit=True, time=(time,), **angle_kwargs)
        except Exception:
            pass

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
    def _freeze_adjacent_tangent(
        fn, idx, is_in, bookend_facing, auto_types, step_types
    ):
        """Freeze an auto tangent to kFixed (preserving its current XY), or set
        the bookend-facing side to kFlat for a constant-value hold.

        Parameters:
            fn (MFnAnimCurve): The animation curve function set.
            idx (int): Key index whose tangent to freeze.
            is_in (bool): True = in-tangent, False = out-tangent.
            bookend_facing (bool): True if this tangent handle faces the bookend
                key (should become flat).  False if it faces the curve interior
                (should lock its current angle via kFixed).
            auto_types (set): Set of MFnAnimCurve tangent type constants that are
                auto-computed (kTangentAuto, kTangentSmooth, kTangentClamped).
            step_types (set): Set of step tangent type constants to skip.
        """
        import maya.api.OpenMayaAnim as oma2

        tt = fn.inTangentType(idx) if is_in else fn.outTangentType(idx)
        if tt in step_types:
            return  # Stepped tangents are never recalculated — nothing to freeze.

        if tt in auto_types:
            if bookend_facing:
                # Set to flat for a clean constant-value hold into the bookend.
                if is_in:
                    fn.setInTangentType(idx, oma2.MFnAnimCurve.kTangentFlat)
                else:
                    fn.setOutTangentType(idx, oma2.MFnAnimCurve.kTangentFlat)
            else:
                # Interior-facing: snapshot current XY, then convert to kFixed
                # so Maya won't recalculate it when a neighbor key is added.
                xy = fn.getTangentXY(idx, is_in)
                if is_in:
                    fn.setInTangentType(idx, oma2.MFnAnimCurve.kTangentFixed)
                    fn.setTangent(idx, xy[0], xy[1], True)
                else:
                    fn.setOutTangentType(idx, oma2.MFnAnimCurve.kTangentFixed)
                    fn.setTangent(idx, xy[0], xy[1], False)

    @staticmethod
    def _curve_value_to_ui(fn) -> Callable[[float], float]:
        """Return a converter from *fn*'s internal value units to UI units
        (radians -> UI angle for angular curves, cm -> UI distance for linear
        ones, identity for unitless)."""
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        kind = fn.animCurveType
        if kind in (oma2.MFnAnimCurve.kAnimCurveTA, oma2.MFnAnimCurve.kAnimCurveUA):
            ui = om2.MAngle.uiUnit()
            return lambda v: om2.MAngle(v, om2.MAngle.kRadians).asUnits(ui)
        if kind in (oma2.MFnAnimCurve.kAnimCurveTL, oma2.MFnAnimCurve.kAnimCurveUL):
            ui = om2.MDistance.uiUnit()
            return lambda v: om2.MDistance(v, om2.MDistance.kCentimeters).asUnits(ui)
        if kind in (oma2.MFnAnimCurve.kAnimCurveTT, oma2.MFnAnimCurve.kAnimCurveUT):
            # Time-valued curves evaluate to an MTime.
            ui = om2.MTime.uiUnit()
            return lambda v: v.asUnits(ui)
        return lambda v: v

    @staticmethod
    def _find_adjacent_key(fn, frame, n):
        """Find the index of the first key at or after *frame*.

        Returns None if no key is found (all keys are before *frame*).
        """
        for ki in range(n):
            if fn.input(ki).value >= frame - 1e-4:
                return ki
        return None

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

    @staticmethod
    def _read_tied_key_metadata(curve: str) -> Optional[List[float]]:
        """Return the bookend times recorded on *curve* by tie_keyframes, or
        None if the curve carries no (or unreadable) metadata.
        """
        try:
            if not cmds.attributeQuery(TIED_KEYS_ATTR, node=curve, exists=True):
                return None
            raw = cmds.getAttr(f"{curve}.{TIED_KEYS_ATTR}")
            if not raw:
                return []
            return [float(t) for t in json.loads(raw)]
        except (RuntimeError, ValueError, TypeError):
            return None

    @staticmethod
    def _write_tied_key_metadata(curve: str, times: List[float]) -> None:
        """Merge *times* into the bookend-time record stored on *curve*.

        Silently skips curves that can't take the attribute (e.g. referenced or
        locked nodes) — untie then falls back to heuristic detection.
        """
        if not times:
            return
        try:
            existing = _AnimUtilsInternal._read_tied_key_metadata(curve) or []
            if not cmds.attributeQuery(TIED_KEYS_ATTR, node=curve, exists=True):
                cmds.addAttr(curve, longName=TIED_KEYS_ATTR, dataType="string")
            merged = sorted({round(t, 4) for t in [*existing, *times]})
            cmds.setAttr(f"{curve}.{TIED_KEYS_ATTR}", json.dumps(merged), type="string")
        except RuntimeError:
            pass

    @staticmethod
    def _clear_tied_key_metadata(curve: str) -> None:
        """Remove the bookend-time record from *curve*, if present."""
        try:
            if cmds.attributeQuery(TIED_KEYS_ATTR, node=curve, exists=True):
                cmds.deleteAttr(f"{curve}.{TIED_KEYS_ATTR}")
        except RuntimeError:
            pass

    @staticmethod
    def _key_is_flat_or_stepped(
        curve: str, time: float, angle_tol: float = 0.01
    ) -> bool:
        """True if the key at *time* has flat or stepped tangents on both sides —
        the shape tie_keyframes always gives its bookend keys.

        A shaped (sloped) key is genuine animation and must survive untie.
        """
        t = (time, time)
        types = cmds.keyTangent(
            curve, query=True, time=t, inTangentType=True, outTangentType=True
        )
        angles = cmds.keyTangent(curve, query=True, time=t, inAngle=True, outAngle=True)
        if not types or not angles:
            return False
        for side_type, side_angle in zip(types, angles):
            if side_type in ("step", "stepnext"):
                continue
            if abs(side_angle) > angle_tol:
                return False
        return True


_SETKEY_IN_TANGENT_REMAP = {"step": "stepnext", "fixed": "auto"}
"""In-tangent types ``cmds.setKeyframe`` rejects, mapped to accepted
equivalents ("step" is out-tangent-only; its in-tangent form is "stepnext").
``cmds.keyTangent`` accepts "fixed" directly — only remap for setKeyframe.
"""

_SETKEY_OUT_TANGENT_REMAP = {"fixed": "auto"}
"""Out-tangent types ``cmds.setKeyframe`` rejects ("Cannot set out-tangents to
fixed"), mapped to accepted equivalents.  "step"/"stepnext" are both valid
out-tangents, so only "fixed" needs remapping.  As with the in-tangent table,
``cmds.keyTangent`` accepts "fixed" directly — callers re-assert the original
type there after the key exists.
"""

_KEYTANGENT_IN_TANGENT_REMAP = {"step": "stepnext"}
"""In-tangent remap for ``cmds.keyTangent``, which — unlike setKeyframe —
accepts "fixed"; only the out-tangent-only "step" needs its in-side form.
"""

TIED_KEYS_ATTR = "mayatkTiedKeys"
"""String attribute added to anim curve nodes by tie_keyframes, holding a
JSON list of the bookend key times it inserted.  untie_keyframes uses this
record to remove exactly those keys instead of guessing from value equality.
"""


class AnimUtils(_AnimUtilsInternal, ptk.HelpMixin):
    """Animation utilities for Maya.

    For help on this class use: AnimUtils.help()

    BEST PRACTICES FOR GETTING ANIMATION CURVES:
    ============================================

    When working with animation curves, use these methods to ensure you capture ALL curve types
    (including visibility, custom attributes, etc.):

    1. For simple object-to-curves conversion:
       curves = AnimUtils.objects_to_curves(objects, recursive=False)

    2. For common patterns (scene curves, selected keys, object curves):
       curves = AnimUtils.get_anim_curves(objects=None, selected_keys_only=False, recursive=False)

    3. Both methods use cmds.listConnections(type="animCurve") which properly captures all curve types.

    AVOID querying keyframes at the object level for curve operations:
       - cmds.keyframe(obj, query=True, timeChange=True) # May miss some attributes

    PREFERRED approach - work with curves directly:
       - Get curves first using objects_to_curves() or get_anim_curves()
       - Then query/modify the curves: cmds.keyframe(curve, query=True, timeChange=True)
    """

    #: Optimization levels for :meth:`optimize_keys`, least to most aggressive.
    #: The single source of truth every consumer reads -- the Scene Exporter's
    #: Optimize Keys combo, SmartBake's pass-through, and any headless caller --
    #: so a level added here reaches all of them without a second edit.  Each
    #: value is literally the ``optimize_keys`` kwargs that level means: the
    #: level is sugar over the primitive, never a replacement for it, and a
    #: caller that wants a combination no level names still passes kwargs.
    OPTIMIZE_LEVELS: Dict[str, Dict[str, Any]] = {
        # Delete curves whose value never changes; leave every surviving curve's
        # keys alone.  The conservative rung: nothing that carries motion is
        # touched, so it is safe on hand-animated curves whose flat sections are
        # deliberate holds.
        "static": {"remove_static_curves": True, "remove_flat_keys": False},
        # ... plus the redundant middle keys of a flat run.  What every caller
        # got before levels existed (see DEFAULT_OPTIMIZE_LEVEL).
        "flat": {"remove_static_curves": True, "remove_flat_keys": True},
        # ... plus filterCurve(keyReducer) within value_tolerance.  Lossy by
        # construction: it removes keys whose absence changes the curve by less
        # than the tolerance, which is a judgement about the tolerance.
        "simplify": {
            "remove_static_curves": True,
            "remove_flat_keys": True,
            "simplify_keys": True,
        },
        # Reduce smooth curves to their extrema with tangents refit against the
        # samples (:meth:`reduce_to_extremes`, selected by the negative tolerance).
        # The answer for per-frame BAKED output, where the other rungs have
        # almost nothing to delete -- a bake has no redundant flat keys to find.
        "extremes": {
            "remove_static_curves": True,
            "remove_flat_keys": True,
            "value_tolerance": -1.0,
        },
    }

    #: The level a bare ``True`` resolves to -- what every caller got before
    #: levels existed, so a bool keeps behaving exactly as it did.
    DEFAULT_OPTIMIZE_LEVEL: str = "flat"

    #: Level names accepted for one release after a rename, mapped to the
    #: canonical key. ``"unbake"`` (until 2026-09-02) read as reversing a
    #: bake -- which is ``SmartBake.restore`` -- when the level only thins a
    #: bake to its extremes; saved templates and headless callers still say it.
    _OPTIMIZE_LEVEL_ALIASES = {"unbake": "extremes"}

    @staticmethod
    def scene_animation_range() -> Tuple[float, float]:
        """The scene's AUTHORED animation range, as ``(start, end)``.

        ``animationStartTime``/``animationEndTime`` -- never
        ``minTime``/``maxTime``, which is the playback slider the artist
        happens to have scrubbed in.  A narrowed slider is not a statement
        about the deliverable, and every consumer here wants the authored
        extent: the FBX bake range, the exporter's "outermost statement of
        intent" fallback when a set-scoped key query comes back empty, and a
        USD export's sampling window.

        The read half of :meth:`fit_playback_range`, which writes the same pair.
        """
        return (
            float(cmds.playbackOptions(query=True, animationStartTime=True)),
            float(cmds.playbackOptions(query=True, animationEndTime=True)),
        )

    @classmethod
    def normalize_optimize_level(cls, level):
        """The canonical :attr:`OPTIMIZE_LEVELS` key *level* names, or None for OFF.

        Split out of :meth:`resolve_optimize_level` so a caller that wants to
        REPORT the level (a log line, a summary) names the same thing the pass
        actually ran -- ``"  Extremes "`` resolves correctly but should not be
        echoed back with the caller's spacing and case.

        Parameters:
            level: A key of :attr:`OPTIMIZE_LEVELS`, or a bool -- ``True`` for
                :attr:`DEFAULT_OPTIMIZE_LEVEL`, anything falsy for OFF.

        Raises:
            ValueError: *level* is a non-empty string naming no known level.
        """
        if not level:  # None/False/0/"" -- OFF.  Tested BEFORE the string
            return None  # branch: "" is a falsy config value, not a bad level
        if not isinstance(level, str):  # True, or a legacy truthy bool flag
            return cls.DEFAULT_OPTIMIZE_LEVEL
        key = level.strip().lower()
        key = cls._OPTIMIZE_LEVEL_ALIASES.get(key, key)
        if key not in cls.OPTIMIZE_LEVELS:
            raise ValueError(
                f"Unknown optimize level {level!r}; expected one of "
                f"{', '.join(cls.OPTIMIZE_LEVELS)}."
            )
        return key

    @classmethod
    def resolve_optimize_level(
        cls, level: Union[bool, str, None]
    ) -> Optional[Dict[str, Any]]:
        """Resolve an optimization level into :meth:`optimize_keys` kwargs.

        The seam between a UI/config choice and the primitive, so no consumer
        hard-codes a level's kwargs:

            kwargs = AnimUtils.resolve_optimize_level(level)
            if kwargs:
                AnimUtils.optimize_keys(objects, **kwargs)

        Parameters:
            level: A key of :attr:`OPTIMIZE_LEVELS`, or a bool -- ``True`` for
                :attr:`DEFAULT_OPTIMIZE_LEVEL`, anything falsy for OFF.

        Returns:
            The kwargs for that level, or None when it is OFF (so the caller
            skips the pass rather than running it with everything disabled).

        Raises:
            ValueError: *level* is a string naming no known level.  Loud rather
                than silently falling back: an unknown level is a config error,
                and a quiet default would optimize the user's curves at a
                setting they did not choose.
        """
        key = cls.normalize_optimize_level(level)
        return dict(cls.OPTIMIZE_LEVELS[key]) if key else None

    @staticmethod
    def _set_key_preserving_tangents(
        plug: str, time: float, value: float, **kwargs
    ) -> None:
        """Set a keyframe while preserving existing tangent types.

        If a key already exists at *time* its in/out tangent types are
        snapshotted before the value is written and restored afterwards.
        If no key exists, the tangent type of the nearest preceding key on
        the same curve is inherited so that stepped (or other non-default)
        tangent styles propagate correctly.

        Any extra *kwargs* are forwarded to ``cmds.setKeyframe``.

        Note:
            If *kwargs* includes ``inTangentType`` or ``outTangentType``
            they will override the automatically preserved tangent types.
        """
        existing_key = cmds.keyframe(
            plug, query=True, time=(time, time), timeChange=True
        )
        tangent_types = None

        if existing_key:
            try:
                itt = cmds.keyTangent(
                    plug, query=True, time=(time, time), inTangentType=True
                )
                ott = cmds.keyTangent(
                    plug, query=True, time=(time, time), outTangentType=True
                )
                if itt and ott:
                    tangent_types = (itt[0], ott[0])
            except Exception:
                pass
        else:
            # No key at this time — inherit from nearest preceding key.
            try:
                all_times = cmds.keyframe(plug, query=True, timeChange=True)
                if all_times:
                    preceding = [kt for kt in all_times if kt < time]
                    if preceding:
                        pt = max(preceding)
                        itt = cmds.keyTangent(
                            plug, query=True, time=(pt, pt), inTangentType=True
                        )
                        ott = cmds.keyTangent(
                            plug, query=True, time=(pt, pt), outTangentType=True
                        )
                        if itt and ott:
                            tangent_types = (itt[0], ott[0])
            except Exception:
                pass

        # Set the keyframe — pass tangent types at creation time when
        # available so Maya doesn't need a second edit pass (which can
        # silently drop "step" in-tangent types).
        kw = dict(time=time, value=value)
        if tangent_types:
            # setKeyframe rejects types keyTangent accepts ("fixed" on either
            # side, "step" as an in-tangent) — remap for creation only; the
            # keyTangent pass below re-asserts the ORIGINAL types.
            kw["inTangentType"] = _SETKEY_IN_TANGENT_REMAP.get(
                tangent_types[0], tangent_types[0]
            )
            kw["outTangentType"] = _SETKEY_OUT_TANGENT_REMAP.get(
                tangent_types[1], tangent_types[1]
            )
        kw.update(kwargs)
        cmds.setKeyframe(plug, **kw)

        # Belt-and-suspenders: re-apply tangent types after creation
        # in case setKeyframe's auto-tangent logic overrode them.
        # Skip sides the caller explicitly set via kwargs — the preserved
        # types must not override an intentional caller choice.
        if tangent_types:
            if "outTangentType" not in kwargs:
                try:
                    cmds.keyTangent(
                        plug,
                        time=(time, time),
                        edit=True,
                        outTangentType=tangent_types[1],
                    )
                except Exception:
                    pass
            if "inTangentType" not in kwargs:
                try:
                    cmds.keyTangent(
                        plug,
                        time=(time, time),
                        edit=True,
                        inTangentType=_KEYTANGENT_IN_TANGENT_REMAP.get(
                            tangent_types[0], tangent_types[0]
                        ),
                    )
                except Exception:
                    pass

    @classmethod
    def bake(
        cls,
        objects: Union[str, List[str]],
        attributes: Optional[Union[str, List[str]]] = None,
        time_range: Optional[Tuple[float, float]] = None,
        sample_by: float = 1.0,
        preserve_outside_keys: bool = True,
        simulation: bool = False,
        destination_layer: Optional[str] = None,
        remove_baked_attr_from_layer: bool = False,
        bake_on_override_layer: bool = False,
        minimize_rotation: bool = True,
        sparse_anim_curve_bake: bool = False,
        disable_implicit_control: bool = True,
        control_points: bool = False,
        shape: bool = False,
        only_keyed: bool = False,
    ) -> List[str]:
        """Bake animation on specified objects and attributes with smart grouping.

        Handles filtering valid attributes per object and grouping them for
        efficient batch execution.

        Parameters:
            objects: Object(s) to bake.
            attributes: Attribute name(s) to bake. If None, bakes all keyable.
            time_range: (start, end) tuple. If None, Maya's bakeResults
                default range is used (the existing keyed range — NOT the
                playback range).
            sample_by: Step size for baking keys.
            preserve_outside_keys: Keep keys outside the bake range.
            simulation: Perform simulation bake.
            destination_layer: Target animation layer name.
            remove_baked_attr_from_layer: Remove attributes from source layer.
            bake_on_override_layer: Bake onto the override layer.
            minimize_rotation: Ensure rotation continuity (Euler filter).
            sparse_anim_curve_bake: Use sparse baking.
            disable_implicit_control: Disable implicit control during bake.
            control_points: Bake control points.
            shape: Bake shapes.
            only_keyed: Only bake attributes that already have animation curves.

        Returns:
            List of objects that were baked successfully.
        """
        # 1. Normalize objects
        if not objects:
            return []

        if not isinstance(objects, (list, tuple, set)):
            objects = [objects]

        valid_objects = []
        for o in objects:
            o = str(o)
            if cmds.objExists(o):
                valid_objects.append(o)

        if not valid_objects:
            return []

        # 2. Normalize attributes
        req_attrs = None
        if attributes:
            if isinstance(attributes, str):
                req_attrs = [attributes]
            else:
                req_attrs = attributes

        # 3. Group objects by bakeable attributes to batch efficiently
        # Map: tuple(sorted_attr_names) -> list[objects]
        groups = collections.defaultdict(list)

        for obj in valid_objects:
            attrs_to_bake = []

            # If specific attributes requested
            if req_attrs:
                for attr_name in req_attrs:
                    if not cmds.attributeQuery(attr_name, node=str(obj), exists=True):
                        continue

                    if only_keyed:
                        if not cmds.listConnections(
                            f"{obj}.{attr_name}", type="animCurve"
                        ):
                            continue

                    attrs_to_bake.append(attr_name)

                # If no requested attributes found valid, skip object
                if not attrs_to_bake:
                    continue

                key = tuple(sorted(attrs_to_bake))
                groups[key].append(obj)

            # If no attributes requested (bake all)
            else:
                if only_keyed:
                    # bakeResults without -at bakes all keyable, so keyed-only
                    # must be enforced manually: bake just the attributes that
                    # already have animation curves.
                    curves = cmds.keyframe(str(obj), query=True, name=True) or []
                    attrs_to_bake = cls._curves_to_attributes(curves, obj)
                    if not attrs_to_bake:
                        continue
                    groups[tuple(sorted(attrs_to_bake))].append(obj)
                else:
                    groups[None].append(obj)

        # 4. Execute Batches
        results = []

        # Build common kwargs
        kwargs = {
            "sampleBy": sample_by,
            "preserveOutsideKeys": preserve_outside_keys,
            "simulation": simulation,
            "minimizeRotation": minimize_rotation,
            "sparseAnimCurveBake": sparse_anim_curve_bake,
            "disableImplicitControl": disable_implicit_control,
            "controlPoints": control_points,
            "shape": shape,
        }
        if time_range:
            kwargs["time"] = time_range
        if destination_layer:
            kwargs["destinationLayer"] = destination_layer
            kwargs["removeBakedAttributeFromLayer"] = remove_baked_attr_from_layer
            kwargs["bakeOnOverrideLayer"] = bake_on_override_layer

        for attr_tuple, objs_in_group in groups.items():
            run_kwargs = kwargs.copy()
            if attr_tuple is not None:
                run_kwargs["attribute"] = list(attr_tuple)

            try:
                cmds.bakeResults(objs_in_group, **run_kwargs)
                # bakeResults returns None but does not raise on
                # success.  Record the objects as successfully baked.
                results.extend([str(o) for o in objs_in_group])
            except Exception as e:
                cmds.warning(f"AnimUtils.bake batch failed: {e}")

        return results

    @staticmethod
    def objects_to_curves(
        objects: Union[str, List[str]],
        recursive: bool = False,
        as_strings: bool = False,
        through_blends: bool = False,
    ) -> List[str]:
        """Converts objects into a list of animation curves.
        Optionally recurses through the objects to find animation curves on children.
        Ensures no duplicates are returned.

        Parameters:
            objects: Single object name or list of names (keyed objects or curves).
            recursive: Whether to recursively search through children of objects for curves.
            as_strings: Deprecated, no effect — results are always name strings.
            through_blends: Also return the curves a layered, constrained or
                unit-converted channel hides behind an animBlendNode / pairBlend /
                unitConversion.  A direct connection query sees only the
                intermediary, so by default a layered rig yields no curves.

        Returns:
            A list of unique animation curve names.
        """
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
                        if AnimUtils._is_curve_intermediary(n)
                    )
                )
                if blends:
                    anim_curves.update(AnimUtils._curves_behind_blends(blends))

        # Return the results as a list, preserving the unique set of animCurves
        return list(anim_curves)

    @classmethod
    def get_anim_curves(
        cls,
        objects: Optional[List[str]] = None,
        selected_keys_only: bool = False,
        recursive: bool = False,
    ) -> List[str]:
        """Get animation curves from objects, selected keys, or all scene curves.

        This is a higher-level convenience method that handles common patterns for getting
        animation curves. It properly handles visibility and all other attribute types by
        working directly with animation curve nodes rather than querying at the object level.

        This method should be used when you need to:
        - Get all curves in a scene
        - Get curves from selected graph editor keys
        - Get curves from specific objects (with optional recursion)

        Parameters:
            objects: Objects to get curves from. If None, uses all scene curves or selected keys.
            selected_keys_only: If True, gets curves from selected keys in graph editor.
                               Only applies when objects is None.
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of unique animation curves.

        Example:
            # Get all animation curves in the scene
            all_curves = AnimUtils.get_anim_curves()

            # Get curves from selected keys
            selected_curves = AnimUtils.get_anim_curves(selected_keys_only=True)

            # Get curves from specific objects
            curves = AnimUtils.get_anim_curves(objects=cmds.ls(selection=True))

            # Get curves from objects and their children
            curves = AnimUtils.get_anim_curves(objects=cmds.ls(selection=True), recursive=True)
        """
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

    @classmethod
    def snapshot_curves(
        cls, objects: Union[str, List[str]], recursive: bool = True
    ) -> Dict[str, Any]:
        """Stash every animation curve driving *objects*, so it can be put back.

        The counterpart of :meth:`restore_curves`, and the animation half of
        what the texture pass gets from staging copies: a caller may edit keys
        destructively -- optimize, snap, tie, bake -- and hand the scene back
        exactly as it was.

        The stash is a DUPLICATE of each curve node, which is why this is
        exact rather than approximately exact: key times, values, tangent
        types, weights, the curve's ``weightedTangents`` flag, its pre/post
        infinity and its node type all come along without being enumerated and
        re-applied one property at a time. The duplicate is disconnected
        (``inputConnections=False``), so it drives nothing while it waits.

        Nothing is locked or hidden: the stash nodes are ordinary DG nodes
        named ``<curve>__snapshot#`` and :meth:`restore_curves` deletes them.
        A caller that abandons a snapshot leaks those nodes, so pair the calls
        (the scene exporter stages the restore with
        ``stage_deferred_restore`` and therefore covers every exit path).

        Parameters:
            objects: Objects (or curves) whose animation should be captured.
            recursive: Include curves on the objects' descendants. Default
                True -- the opposite of :meth:`objects_to_curves`, because a
                caller asking to protect an object's animation means the
                animation that will actually be edited, and an export set
                names roots.

        Returns:
            An opaque snapshot dict for :meth:`restore_curves`. Empty
            ``records`` when nothing is animated, which restores as a no-op.
        """
        curves = cls.objects_to_curves(objects, recursive=recursive)
        records: List[Dict[str, Any]] = []
        for curve in curves:
            if not cmds.objExists(curve):
                continue
            # Where this curve plugs in, so a curve DELETED by the caller (the
            # optimize pass drops static ones outright) can be put back rather
            # than merely restored in place.
            targets = (
                cmds.listConnections(
                    f"{curve}.output", plugs=True, source=False, destination=True
                )
                or []
            )
            # And what drives it: a set-driven-key curve is fed by another
            # attribute, and a stash that lost its input would restore as a
            # curve driven by time.
            drivers = (
                cmds.listConnections(
                    f"{curve}.input", plugs=True, source=True, destination=False
                )
                or []
            )
            try:
                stash = cmds.duplicate(
                    curve,
                    name=f"{CoreUtils.short_name(curve)}__snapshot",
                    inputConnections=False,
                    upstreamNodes=False,
                )[0]
            except RuntimeError as error:  # pragma: no cover - defensive
                cmds.warning(f"Could not snapshot the curve {curve!r}: {error}")
                continue
            records.append(
                {
                    "curve": curve,
                    "stash": stash,
                    "targets": targets,
                    "drivers": drivers,
                }
            )
        return {"records": records}

    @classmethod
    def restore_curves(cls, snapshot: Optional[Dict[str, Any]]) -> int:
        """Put the animation captured by :meth:`snapshot_curves` back, exactly.

        Restores in place wherever the curve survived -- the live node keeps
        its identity and every connection it has, and only its CONTENT is
        replaced -- so a restore cannot disturb an animation layer, a pairBlend
        or a driven-key setup that the caller never touched. A curve the caller
        deleted is rebuilt by reconnecting its stash in its place.

        Always deletes the stash nodes, including on the paths where the
        restore itself fails, because a leaked stash is a curve-shaped node
        sitting in the artist's scene.

        Side effect worth knowing: an in-place restore goes through
        ``copyKey``/``pasteKey``, which use Maya's single global key clipboard
        -- so whatever the user had copied there is replaced. That is the price
        of replacing a curve's content exactly rather than re-applying it key
        by key, and it is why this is an export-time operation rather than
        something to call from an interactive tool.

        Parameters:
            snapshot: The dict from :meth:`snapshot_curves`. ``None`` or an
                empty snapshot restores nothing and reports 0.

        Returns:
            The number of curves put back.
        """
        records = (snapshot or {}).get("records") or []
        restored = 0
        for record in records:
            curve, stash = record.get("curve"), record.get("stash")
            try:
                if not stash or not cmds.objExists(stash):
                    continue
                if cmds.objExists(curve):
                    # Content-only replacement: `replaceCompletely` swaps the
                    # whole curve (keys AND tangents) while the node, its name
                    # and its connections stay put.
                    cmds.copyKey(stash)
                    cmds.pasteKey(curve, option="replaceCompletely")
                    weighted = cmds.keyTangent(stash, query=True, weightedTangents=True)
                    if weighted:
                        cmds.keyTangent(
                            curve, edit=True, weightedTangents=bool(weighted[0])
                        )
                    pre = cmds.setInfinity(stash, query=True, preInfinite=True)
                    post = cmds.setInfinity(stash, query=True, postInfinite=True)
                    if pre and post:
                        cmds.setInfinity(
                            curve, preInfinite=pre[0], postInfinite=post[0]
                        )
                else:
                    # The caller deleted it (optimize drops static curves), so
                    # the stash BECOMES the curve: wire it where the original
                    # sat and give it the original's name back.
                    for driver in record.get("drivers") or []:
                        cmds.connectAttr(driver, f"{stash}.input", force=True)
                    for target in record.get("targets") or []:
                        cmds.connectAttr(f"{stash}.output", target, force=True)
                    # Consumed the moment it is WIRED IN, before the rename:
                    # the rename is cosmetic and the connections are the
                    # restore, so a rename that raises must not send this
                    # through the cleanup below -- that would delete the curve
                    # just put back, and the original is already gone.
                    wired, stash = stash, None
                    try:
                        cmds.rename(wired, CoreUtils.short_name(curve))
                    except RuntimeError as error:
                        cmds.warning(
                            f"Restored {curve!r} as {wired!r}; it could not be "
                            f"renamed: {error}"
                        )
                restored += 1
            except RuntimeError as error:
                cmds.warning(f"Could not restore the curve {curve!r}: {error}")
            finally:
                if stash and cmds.objExists(stash):
                    try:
                        cmds.delete(stash)
                    except RuntimeError:  # pragma: no cover - defensive
                        pass
        return restored

    @classmethod
    def get_static_curves(
        cls,
        objects: List[str],
        value_tolerance: float = 1e-5,
        recursive: bool = False,
        as_strings: bool = False,
    ) -> List[str]:
        """Detects static curves (curves with constant values) that are safe
        to delete.

        A static curve is one where all keyframe values are identical
        (within *value_tolerance*).  However, if the constant value
        differs from the driven attribute's default value, removing the
        curve would change the object's resting state (e.g. a
        constraint-baked constant position would revert to zero).  Such
        curves are **excluded** from the result.

        Parameters:
            objects: List of nodes (curves or objects).
            value_tolerance: The value tolerance to consider for static curves (difference between keyframe values).
            recursive: Whether to recursively search through children of objects for curves.
            as_strings: Deprecated, no effect — results are always name strings.

        Returns:
            A list of static curves that are safe to delete.
        """
        from math import isclose
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        curves = cls.objects_to_curves(objects, recursive=recursive)
        static_curves = []

        # Use OpenMaya for fast first/last/mid value checks to skip
        # non-static curves before the expensive full-value query.
        sel = om2.MSelectionList()
        for curve in curves:
            sel.clear()
            try:
                sel.add(curve)
            except RuntimeError:
                continue
            fn = oma2.MFnAnimCurve(sel.getDependNode(0))
            n = fn.numKeys
            # A 0-key curve holds no value at all -- nothing to compare and
            # nothing to preserve, so it is not this function's business.
            # A 1-key curve is the MOST static curve possible (one value held
            # forever); it simply cannot be sampled by the first/last/mid
            # comparisons below, which is why it used to be dropped here
            # outright. Both fall through to the shared default-value guard,
            # so a single key off its default is still preserved.
            if n == 0:
                continue

            if n > 1:
                # Quick check: first, last, mid values.  fn.value() returns
                # internal units (radians for rotations) but comparisons are
                # within the same curve / same unit, so the tolerance is
                # self-consistent (stricter for radians than degrees).
                first_val = fn.value(0)
                if abs(fn.value(n - 1) - first_val) > value_tolerance:
                    continue
                if abs(fn.value(n // 2) - first_val) > value_tolerance:
                    continue

                # Full check via om2 (same-unit, fast C++ loop).
                is_static = True
                for i in range(1, n):
                    if abs(fn.value(i) - first_val) > value_tolerance:
                        is_static = False
                        break
                if not is_static:
                    continue

            # Check whether the constant value matches the driven
            # attribute's default.  Use cmds for both sides so the
            # units agree (e.g. degrees for rotations).
            driven = cmds.listConnections(
                curve, source=False, destination=True, plugs=True
            )
            if not driven:
                continue

            try:
                node, attr = driven[0].split(".", 1)
                defaults = cmds.attributeQuery(attr, node=node, listDefault=True)
                if defaults is not None:
                    default_val = defaults[0]
                    first_ui = cmds.keyframe(
                        curve, index=(0, 0), q=True, valueChange=True
                    )
                    if not first_ui or not isclose(
                        first_ui[0], default_val, abs_tol=value_tolerance
                    ):
                        continue
            except (ValueError, RuntimeError):
                continue

            static_curves.append(curve)

        return static_curves

    @classmethod
    @CoreUtils.undoable
    def get_redundant_flat_keys(
        cls,
        objects: List[str],
        value_tolerance: float = 1e-5,
        remove: bool = False,
        recursive: bool = False,
        as_strings: bool = False,
    ) -> List[Tuple[Any, List[float]]]:
        """Detects redundant flat keys in curves and optionally deletes them.

        A "flat segment" is a run of 3+ consecutive keys whose values all
        fall within ``value_tolerance`` of the first key in the run.  The
        interior keys are redundant because the boundary pair alone
        reproduces the constant value.

        Parameters:
            objects: List of nodes (curves or objects).
            value_tolerance: The value tolerance to consider for redundant flat keys.
            remove: If True, the redundant keys are deleted.
            recursive: Whether to recursively search through children of objects for curves.
            as_strings: Deprecated, no effect — curve names are always strings.

        Returns:
            A list of ``(curve, [redundant_times])`` tuples.
        """
        import maya.api.OpenMaya as om2

        curves = cls.objects_to_curves(objects, recursive=recursive)
        redundant = []
        # cmds.keyframe answers in UI DISPLAY units, but the tolerance is
        # tuned in centimeters (Maya's internal linear unit). In a scene
        # set to meters -- the web-export pipeline does exactly that before
        # optimizing -- every linear value shrinks 100x, so real slow motion
        # reads as flat and a loop-closing drift (equal endpoints defeat any
        # span guard) collapses to its boundary pair: a wire loom froze at
        # its rest pose, 2.7 cm from truth, in a shipped deliverable.
        # Normalize linear-output curves (nodeType animCurve?L) back to cm;
        # angular (degrees) and unitless curves are display-unit-stable.
        lin_to_cm = om2.MDistance(1.0, om2.MDistance.uiUnit()).asCentimeters()

        for curve in curves:
            if not cmds.objExists(curve):
                continue
            times = cmds.keyframe(curve, query=True, timeChange=True) or []
            if len(times) < 3:
                continue

            values = cmds.keyframe(curve, query=True, valueChange=True) or []
            if len(values) != len(times):
                continue

            if lin_to_cm != 1.0 and cmds.nodeType(curve).endswith("L"):
                values = [v * lin_to_cm for v in values]

            remove_indices, seg_starts, seg_lasts = ptk.find_flat_interior_indices(
                values, value_tolerance
            )
            if len(remove_indices) == 0:
                continue

            if remove:
                # --- Undoable removal: interior keys of a flat run are
                # contiguous, so each run is removed with a single
                # time-range cutKey — O(segments) commands.  Boundary
                # tangents are frozen first (flat on the hold-facing
                # side, fixed on the interior-facing side) so the curve
                # keeps its shape when its neighbors vanish; keys away
                # from a run keep both neighbors and recompute their
                # auto tangents to identical values.  Everything goes
                # through cmds so the whole edit lands in the undo
                # chunk opened by @CoreUtils.undoable — unlike an om2
                # rebuild, which bypasses the undo queue entirely. ---
                _AUTO_TANGENTS = {"auto", "spline", "clamped", "autoease", "automix"}

                # No lock handling needed: cutKey/keyTangent addressed at
                # the CURVE NODE edit keys even when the driven attribute
                # (or its parent compound) is locked — locks only guard the
                # plug connection the old delete/reconnect rebuild touched.
                in_types = cmds.keyTangent(curve, query=True, inTangentType=True) or []
                out_types = (
                    cmds.keyTangent(curve, query=True, outTangentType=True) or []
                )

                seg_pairs = [(int(s), int(e)) for s, e in zip(seg_starts, seg_lasts)]
                try:
                    # 1) Freeze ALL auto tangents to 'fixed' (locks each
                    # key's current angle) — not just the boundary keys.
                    # Downstream FBX export reinterprets 'auto' tangents
                    # with its own algorithm, corrupting the curve shape,
                    # so no survivor may remain auto.  Contiguous runs
                    # are edited with one index-range call (a baked curve
                    # is typically a single run).
                    for flag, tlist in (
                        ("inTangentType", in_types),
                        ("outTangentType", out_types),
                    ):
                        run = None
                        for i, tt in enumerate(tlist):
                            if tt in _AUTO_TANGENTS:
                                if run is None:
                                    run = i
                            elif run is not None:
                                cmds.keyTangent(
                                    curve,
                                    edit=True,
                                    index=(run, i - 1),
                                    **{flag: "fixed"},
                                )
                                run = None
                        if run is not None:
                            cmds.keyTangent(
                                curve,
                                edit=True,
                                index=(run, len(tlist) - 1),
                                **{flag: "fixed"},
                            )

                    # 2) Boundary tangents facing a flat run go flat for
                    # proper hold handles — only where the original type
                    # was auto-computed (shaped/stepped sides stay put).
                    for s, e in seg_pairs:
                        if s < len(out_types) and out_types[s] in _AUTO_TANGENTS:
                            cmds.keyTangent(
                                curve,
                                edit=True,
                                time=(times[s], times[s]),
                                outTangentType="flat",
                            )
                        if e < len(in_types) and in_types[e] in _AUTO_TANGENTS:
                            cmds.keyTangent(
                                curve,
                                edit=True,
                                time=(times[e], times[e]),
                                inTangentType="flat",
                            )

                    # 3) cutKey accepts only ONE range per call — cut each
                    # flat run's interior separately (time ranges are
                    # stable across removals, so order is irrelevant).
                    for s, e in seg_pairs:
                        cmds.cutKey(
                            curve, time=(times[s + 1], times[e - 1]), clear=True
                        )
                except RuntimeError as exc:
                    # Skip this curve rather than aborting the whole
                    # batch (and every later optimize phase) — the
                    # remaining curves are independent.
                    cmds.warning(
                        f"AnimUtils.get_redundant_flat_keys: key removal on "
                        f"'{curve}' failed ({exc}); curve skipped."
                    )
                    continue

            redundant.append((curve, [times[int(i)] for i in remove_indices]))

        return redundant

    @classmethod
    def simplify_curve(
        cls,
        objects: List[str],
        value_tolerance: float = 0.001,
        time_tolerance: float = 0.001,
        recursive: bool = False,
        as_strings: bool = False,
    ) -> List[str]:
        """Simplify curves by removing keys that don't contribute to shape.

        Uses Maya's ``filterCurve`` with the ``keyReducer`` filter, which
        evaluates each key's contribution to the overall curve shape and
        removes keys whose absence would change the curve by less than
        *value_tolerance*.  This is far more effective than ``cmds.simplify``
        for post-bake cleanup because it handles smooth transitions (not
        just per-key value differences).

        Parameters:
            objects: List of nodes (curves or objects).
            value_tolerance: Maximum allowed value deviation when removing
                a key.  Maps to ``filterCurve -precision``.
            time_tolerance: Unused (kept for API compatibility).
            recursive: Whether to recursively search children for curves.
            as_strings: Deprecated, no effect — curve names are always strings.

        Returns:
            A list of curves that were simplified.
        """
        curves = cls.objects_to_curves(objects, recursive=recursive)
        simplified_curves = []

        for curve in curves:
            try:
                before = cmds.keyframe(curve, q=True, keyframeCount=True) or 0
                cmds.filterCurve(
                    curve,
                    filter="keyReducer",
                    precisionMode=0,  # value precision
                    precision=value_tolerance,
                )
                after = cmds.keyframe(curve, q=True, keyframeCount=True) or 0
                if after < before:
                    simplified_curves.append(curve)
            except RuntimeError:
                pass

        return simplified_curves

    @classmethod
    @CoreUtils.undoable
    def repair_corrupted_curves(
        cls,
        objects: Optional[Union[str, List[str]]] = None,
        recursive: bool = True,
        delete_corrupted: bool = False,
        fix_infinite: bool = True,
        fix_invalid_times: bool = True,
        time_range_threshold: float = 1e6,
        value_threshold: float = 1e6,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Legacy wrapper maintained for backwards compatibility.

        The implementation now lives in :class:`AnimCurveDiagnostics`.
        """

        from mayatk.core_utils.diagnostics.animation_diag import AnimCurveDiagnostics

        return AnimCurveDiagnostics.repair_corrupted_curves(
            objects=objects,
            recursive=recursive,
            delete_corrupted=delete_corrupted,
            fix_infinite=fix_infinite,
            fix_invalid_times=fix_invalid_times,
            time_range_threshold=time_range_threshold,
            value_threshold=value_threshold,
            quiet=quiet,
        )

    @classmethod
    def reduce_to_extremes(
        cls,
        objects: Optional[Union[str, List[str]]] = None,
        value_tolerance: float = 0.001,
        recursive: bool = True,
        quiet: bool = False,
        stats: Optional[dict] = None,
    ) -> List[str]:
        """Reduce baked curves to their shape-defining keys and refit the tangents.

        A per-frame bake thinned to its shape, not undone: the keys stay on
        the objects and only the tweens go (reversing a bake is
        ``SmartBake.restore``).  Each curve keeps only its endpoints,
        peaks, valleys and hold boundaries (``ptk.IterUtils.find_extrema_indices``);
        the tweens are deleted and the survivors get ``fixed`` tangents fitted by
        least squares against the deleted samples
        (``ptk.MathUtils.fit_hermite_slopes``), so the sparse curve traces the
        baked motion.  A hold stays exactly flat -- its facing tangents are
        ``flat`` and that key's tangents are broken; everywhere else the tangents
        are unified.  Curves are made non-weighted.  Curves carrying stepped
        tangents are left untouched (a step has no tween to refit) and are not
        returned.

        Driven (unitless-input) curves are reduced per driver unit.  Tangents
        are written through ``MFnAnimCurve`` (exact in UI units per frame for
        every curve type), so this edit is not undoable -- same class as
        :meth:`optimize_keys`, which runs it for ``value_tolerance < 0``.

        Parameters:
            objects: Objects or curves to reduce; None means every keyed
                transform in the scene.
            value_tolerance: Consecutive samples closer than this are one flat
                step, and a segment within it of its start value is a hold.
            recursive: Whether to search through children of objects.
            quiet: If True, suppress output messages.
            stats: If provided, receives ``reduced`` (curve count),
                ``reduce_keys_removed`` and ``reduce_max_error`` (largest
                deviation of the refit curve from the baked samples, UI units).

        Returns:
            The curves that were reduced.
        """
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        curves = cls.objects_to_curves(
            cls._resolve_keyed_objects(objects), recursive=recursive
        )

        step_types = {"step", "stepnext"}
        reduced: List[str] = []
        keys_removed = 0
        max_error = 0.0
        sel = om2.MSelectionList()
        for curve in curves:
            if not cmds.objExists(curve):
                continue
            sel.clear()
            sel.add(curve)
            fn = oma2.MFnAnimCurve(sel.getDependNode(0))
            # A driven curve (unitless input) answers to the float flags,
            # not the time ones.
            time_input = fn.isTimeInput
            range_kw = "time" if time_input else "float"
            times = cmds.keyframe(curve, q=True, **{f"{range_kw}Change": True}) or []
            if len(times) < 3:
                continue
            tangent_types = (
                cmds.keyTangent(curve, q=True, inTangentType=True) or []
            ) + (cmds.keyTangent(curve, q=True, outTangentType=True) or [])
            if step_types.intersection(tangent_types):
                continue
            values = cmds.keyframe(curve, q=True, valueChange=True) or []
            if len(values) != len(times):
                continue

            keep = ptk.IterUtils.find_extrema_indices(values, value_tolerance)
            if len(keep) == len(times):
                continue
            in_slopes, out_slopes = ptk.MathUtils.fit_hermite_slopes(
                times, values, keep, flat_tolerance=value_tolerance
            )

            if fn.isWeighted:
                cmds.keyTangent(curve, edit=True, weightedTangents=False)
            # Tweens between consecutive kept keys are contiguous: one
            # range cut per gap.
            for a, b in zip(keep[:-1], keep[1:]):
                if b - a > 1:
                    cmds.cutKey(
                        curve, clear=True, **{range_kw: (times[a + 1], times[b - 1])}
                    )
            keys_removed += len(times) - len(keep)

            # setTangent reads x as UI-time frames and y as UI value units
            # (probed exact for TL/TA/TU at film and ntsc, cm and m).  It
            # applies the frames->seconds conversion to a unitless-input
            # (driven) curve as well, whose x is plain driver units, so one
            # driver unit has to be handed over as one second's worth of
            # frames.
            x_unit = (
                1.0
                if time_input
                else om2.MTime(1.0, om2.MTime.kSeconds).asUnits(om2.MTime.uiUnit())
            )
            fixed = oma2.MFnAnimCurve.kTangentFixed
            flat = oma2.MFnAnimCurve.kTangentFlat
            for k in range(len(keep)):
                m_in, m_out = in_slopes[k], out_slopes[k]
                fn.setTangentsLocked(k, False)
                fn.setInTangentType(k, flat if m_in == 0.0 else fixed)
                fn.setOutTangentType(k, flat if m_out == 0.0 else fixed)
                if m_in != 0.0:
                    fn.setTangent(k, x_unit, m_in, True)
                if m_out != 0.0:
                    fn.setTangent(k, x_unit, m_out, False)
                fn.setTangentsLocked(k, m_in == m_out)

            # Largest deviation of the refit curve from the bake, in UI units.
            to_ui = cls._curve_value_to_ui(fn)
            for t, v in zip(times, values):
                at = om2.MTime(t, om2.MTime.uiUnit()) if time_input else t
                max_error = max(max_error, abs(to_ui(fn.evaluate(at)) - v))
            reduced.append(curve)

        if not quiet:
            print(
                f"[extremes] {len(reduced)} curves reduced, {keys_removed} keys removed, "
                f"max deviation {max_error:.6f}"
            )
        if stats is not None:
            stats.update(
                {
                    "reduced": len(reduced),
                    "reduce_keys_removed": keys_removed,
                    "reduce_max_error": max_error,
                }
            )
        return reduced

    #: Deprecated alias (2026-09-02): the method was renamed because "unbake"
    #: read as reversing a bake (that is ``SmartBake.restore``) when it only
    #: thins one. Remove in the release after.
    unbake_keys = reduce_to_extremes

    @classmethod
    @CoreUtils.undoable
    def optimize_keys(
        cls,
        objects: Union[str, List[str]],
        value_tolerance: float = 0.001,
        time_tolerance: float = 0.001,
        remove_flat_keys: bool = True,
        remove_static_curves: bool = True,
        simplify_keys: bool = False,
        recursive: bool = True,
        quiet: bool = False,
        stats: Optional[dict] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[str]:
        """Optimize animation keys for the given objects by removing static curves,
        redundant flat keys, and simplifying curves.

        A negative ``value_tolerance`` (``-1``) selects **extremes** mode: after
        the static-curve pass, every smooth curve is reduced to its endpoints,
        peaks, valleys and hold boundaries with tangents refit to the baked
        motion (:meth:`reduce_to_extremes`); stepped curves still get the flat-key
        pass.  ``simplify_keys`` is ignored in that mode and the static/flat
        tolerance falls back to the default.

        Parameters:
            objects (str, node, or list): The objects to optimize.
            value_tolerance (float): Tolerance for value comparison; negative
                selects extremes mode.
            time_tolerance (float): Tolerance for time comparison.
            remove_flat_keys (bool): Whether to remove redundant flat keys.
            remove_static_curves (bool): Whether to remove static curves.
            simplify_keys (bool): Whether to simplify curves.
            recursive (bool): Whether to search through children of objects.
            quiet (bool): If True, suppress output messages.
            stats (dict, optional): If provided, populated with
                ``keys_before``, ``keys_after``, ``curves_before``,
                ``curves_after``, ``static_deleted``, ``flat_removed``,
                ``simplified``, and ``auto_frozen`` counts (plus the
                :meth:`reduce_to_extremes` stats in extremes mode).

        Returns:
            list: A list of modified curve names (strings).
        """
        # Guard: Maya's lazy initialization (triggered on first use)
        # runs initialStartup.mel which can reset the scene time-unit
        # to Maya's default (film/24fps), rescaling all keyframe times.
        # Capture the time-unit now (before any cmds call) and restore
        # it at the end if it changed.
        _saved_time_unit = cmds.currentUnit(query=True, time=True)

        # The extremes sentinel carries no magnitude: the static/flat passes
        # keep the default tolerance.
        extremes = value_tolerance < 0
        if extremes:
            value_tolerance = 0.001

        # Convert the input objects into curves once (avoid 3 redundant calls)
        if isinstance(objects, str):
            objects = [objects]
        elif isinstance(objects, (list, tuple, set)):
            objects = [str(o) for o in objects]
        else:
            objects = [str(objects)]

        targets = cmds.ls(objects, flatten=True)
        anim_curves = cls.objects_to_curves(targets, recursive=recursive)

        curves_before_count = len(anim_curves)
        keys_before_count = sum(
            cmds.keyframe(c, q=True, keyframeCount=True) or 0
            for c in anim_curves
            if cmds.objExists(c)
        )

        if not quiet:
            print(f"[optimize] Processing {len(anim_curves)} curves...")

        # Suspend viewport refresh for the duration of the optimization.
        # In interactive Maya this prevents per-operation viewport updates
        # that dominate wall-clock time.
        cmds.refresh(suspend=True)
        try:
            return cls._optimize_keys_inner(
                anim_curves,
                value_tolerance=value_tolerance,
                time_tolerance=time_tolerance,
                remove_flat_keys=remove_flat_keys,
                remove_static_curves=remove_static_curves,
                simplify_keys=simplify_keys,
                quiet=quiet,
                keys_before_count=keys_before_count,
                curves_before_count=curves_before_count,
                stats=stats,
                _saved_time_unit=_saved_time_unit,
                progress_callback=progress_callback,
                extremes=extremes,
            )
        finally:
            cmds.refresh(suspend=False)

    @classmethod
    def _optimize_keys_inner(
        cls,
        anim_curves,
        *,
        value_tolerance,
        time_tolerance,
        remove_flat_keys,
        remove_static_curves,
        simplify_keys,
        quiet,
        keys_before_count,
        curves_before_count,
        stats,
        _saved_time_unit,
        progress_callback=None,
        extremes=False,
    ):
        # Optimization is destructive-by-design and not usefully undoable;
        # disable undo recording to eliminate per-call overhead in
        # interactive Maya (each recorded entry updates the undo list,
        # attribute editor, channel box, etc.).
        _autokey_was_on = cmds.autoKeyframe(q=True, state=True)
        try:
            if _autokey_was_on:
                cmds.autoKeyframe(state=False)
            with CoreUtils.undo_disabled():
                return cls.__optimize_keys_body(
                    anim_curves,
                    value_tolerance=value_tolerance,
                    time_tolerance=time_tolerance,
                    remove_flat_keys=remove_flat_keys,
                    remove_static_curves=remove_static_curves,
                    simplify_keys=simplify_keys,
                    quiet=quiet,
                    keys_before_count=keys_before_count,
                    curves_before_count=curves_before_count,
                    stats=stats,
                    _saved_time_unit=_saved_time_unit,
                    progress_callback=progress_callback,
                    extremes=extremes,
                )
        finally:
            if _autokey_was_on:
                cmds.autoKeyframe(state=True)

    @classmethod
    def __optimize_keys_body(
        cls,
        anim_curves,
        *,
        value_tolerance,
        time_tolerance,
        remove_flat_keys,
        remove_static_curves,
        simplify_keys,
        quiet,
        keys_before_count,
        curves_before_count,
        stats,
        _saved_time_unit,
        progress_callback=None,
        extremes=False,
    ):
        static_curves_deleted = 0
        flat_keys_deleted = 0
        simplified_curves_count = 0

        # Phase 1: Remove static curves (if remove_static_curves is True)
        if progress_callback:
            progress_callback(0, 4, "Removing static curves")
        if remove_static_curves:
            static_curves = cls.get_static_curves(
                anim_curves, value_tolerance=value_tolerance
            )
            static_curves_deleted += len(static_curves)
            if static_curves:
                cmds.delete(static_curves)
                # Remove deleted curves from anim_curves list
                static_set = set(static_curves)
                anim_curves = [c for c in anim_curves if c not in static_set]

        # Phase 2: Remove redundant flat keys (if remove_flat_keys is True).
        # In extremes mode the smooth curves are reduced to their extrema with
        # refit tangents instead; only the stepped curves it leaves alone
        # still go through the flat-key pass.
        if progress_callback:
            progress_callback(
                1,
                4,
                "Reducing curves to extremes" if extremes else "Removing flat keys",
            )
        rebuilt_curves = set()
        extremes_stats: dict = {}
        flat_candidates = anim_curves
        if extremes:
            reduced = cls.reduce_to_extremes(
                anim_curves,
                value_tolerance=value_tolerance,
                recursive=False,
                quiet=True,
                stats=extremes_stats,
            )
            rebuilt_curves.update(reduced)  # tangents already explicit
            flat_candidates = [c for c in anim_curves if c not in rebuilt_curves]
        if remove_flat_keys and flat_candidates:
            redundant_keys_to_delete = cls.get_redundant_flat_keys(
                flat_candidates,
                value_tolerance=value_tolerance,
                remove=True,
            )
            flat_keys_deleted += sum(len(keys) for _, keys in redundant_keys_to_delete)
            # The rebuild approach already freezes all auto tangents on
            # rebuilt curves — track them so Phase 3 can skip them.
            rebuilt_curves.update(c for c, keys in redundant_keys_to_delete if keys)

        # Phase 3: Freeze auto tangent types to fixed.
        # Maya's auto tangent recomputes based on neighbors, which
        # produces correct results in Maya.  However FBX maps 'auto'
        # to eTangentAuto — a different algorithm that corrupts
        # sparse curves (after flat-key removal).  Freezing to 'fixed'
        # captures the exact current angle as eTangentUser.
        # This also gives the key reducer explicit angles to work with,
        # allowing it to remove more keys while preserving shape.
        if progress_callback:
            progress_callback(2, 4, "Freezing tangents")
        auto_tangents_frozen = 0
        for curve in anim_curves:
            if curve in rebuilt_curves:
                continue  # Already frozen during rebuild
            if not cmds.objExists(curve):
                continue
            times = cmds.keyframe(curve, q=True, timeChange=True) or []
            if not times:
                continue
            out_types = cmds.keyTangent(curve, q=True, outTangentType=True) or []
            in_types = cmds.keyTangent(curve, q=True, inTangentType=True) or []

            for types_list, kw in [
                (out_types, {"outTangentType": "fixed"}),
                (in_types, {"inTangentType": "fixed"}),
            ]:
                # Group consecutive auto keys into time ranges for
                # batch keyTangent calls instead of per-key overhead.
                seg_start = None
                for i, tt in enumerate(types_list):
                    if tt == "auto":
                        if seg_start is None:
                            seg_start = i
                        auto_tangents_frozen += 1
                    elif seg_start is not None:
                        cmds.keyTangent(
                            curve,
                            time=(times[seg_start], times[i - 1]),
                            lock=False,
                            **kw,
                        )
                        seg_start = None
                if seg_start is not None:
                    cmds.keyTangent(
                        curve,
                        time=(times[seg_start], times[len(types_list) - 1]),
                        lock=False,
                        **kw,
                    )

        # Phase 4: Simplify curves (if simplify_keys is True).
        # Uses filterCurve(keyReducer) to remove keys whose absence
        # changes the curve by less than value_tolerance.  Runs after
        # auto-freeze so the reducer has explicit tangent angles.
        if progress_callback:
            progress_callback(3, 4, "Simplifying curves")
        if simplify_keys and not extremes:
            simplified = cls.simplify_curve(
                anim_curves,
                value_tolerance=value_tolerance,
                time_tolerance=time_tolerance,
            )
            simplified_curves_count += len(simplified)

        if progress_callback:
            progress_callback(4, 4, "Done")
        if not quiet:
            print(f"[optimize] {static_curves_deleted} static curves deleted")
            print(f"[optimize] {flat_keys_deleted} flat keys removed")
            print(f"[optimize] {simplified_curves_count} curves simplified")
            print(f"[optimize] {auto_tangents_frozen} auto tangents frozen")
            if extremes:
                print(
                    f"[optimize] {extremes_stats.get('reduced', 0)} curves reduced "
                    f"({extremes_stats.get('reduce_keys_removed', 0)} tweens removed, "
                    f"max deviation {extremes_stats.get('reduce_max_error', 0.0):.6f})"
                )

        # Restore time-unit if Maya init changed it during this call.
        if cmds.currentUnit(query=True, time=True) != _saved_time_unit:
            cmds.currentUnit(time=_saved_time_unit)

        surviving = [c for c in anim_curves if cmds.objExists(c)]

        if stats is not None:
            keys_after_count = sum(
                cmds.keyframe(c, q=True, keyframeCount=True) or 0 for c in surviving
            )
            stats.update(
                {
                    "keys_before": keys_before_count,
                    "keys_after": keys_after_count,
                    "curves_before": curves_before_count,
                    "curves_after": len(surviving),
                    "static_deleted": static_curves_deleted,
                    "flat_removed": flat_keys_deleted,
                    "simplified": simplified_curves_count,
                    "auto_frozen": auto_tangents_frozen,
                    **extremes_stats,
                }
            )

        return surviving

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

    @staticmethod
    def _key_sources(objects: List[str]) -> Tuple[List[str], List[str], List[str]]:
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
        blends = set(cmds.ls(src_nodes, type=list(AnimUtils._KEY_BLEND_TYPES)) or [])
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

    @staticmethod
    def keyed_nodes(objects: Union[str, List[str]]) -> List[str]:
        """The subset of *objects* ``cmds.keyframe`` can find keys on.

        The cheap prefilter in front of a per-object key query, which pays
        the same per node whether or not it is animated. Names come back
        spelled and ordered as *objects* gave them, and an object that is
        itself an animation curve is kept: ``keyframe`` reads a curve
        directly.

        Parameters:
            objects: Object names (DAG paths or DG nodes); a single string is
                accepted.

        Returns:
            The animated subset of *objects*, in input order.
        """
        if isinstance(objects, str):
            objects = [objects]
        return AnimUtils._key_sources(list(objects))[0]

    @staticmethod
    def get_keyframe_times(
        sources: Union[str, List[str]],
        mode: str = "all",
        from_curves: Optional[bool] = None,
        as_range: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
    ) -> Union[List[float], Tuple[float, float], None]:
        """Get keyframe times from objects or curves with flexible filtering options.

        This is a low-level utility for extracting keyframe time values. For getting
        animation curves themselves, use objects_to_curves() or get_anim_curves().

        Parameters:
            sources: Objects or animation curves to get keyframe times from.
            mode: How to select keyframes. Options:
                - "all": Get all keyframes (default)
                - "selected": Get only selected keyframes in graph editor
                - "selected_or_all": Try selected first, fallback to all if none selected
            from_curves: If True, treats sources as curves. If False, treats as objects.
                        If None (default), auto-detects based on node type.
            as_range: If True, returns (min_time, max_time) tuple. If False, returns sorted list.
            time_range: Optional (start, end) tuple to filter keyframes within a range.

        Returns:
            - List[float]: Sorted unique keyframe times (if as_range=False)
            - Tuple[float, float]: (start_time, end_time) range (if as_range=True)
            - None: If no keyframes found
        """
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
                _keyed, direct_curves, blended = AnimUtils._key_sources(sources)
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
    def get_driver_animation_range(
        node: str,
        driver_type: str = "auto",
    ) -> List[float]:
        """Get keyframe times from a driver node's animation or its targets.

        Traces through different driver types (constraints, driven keys,
        expressions, IK, motion paths) to find the animation range of
        the ultimate source.

        Parameters:
            node: The driver node to query.
            driver_type: The type of driver. Options:
                - "auto": Auto-detect the node type
                - "constraint": Query constraint target animation
                - "driven_key": Query the driver of the SDK
                - "expression": Query expression input animation
                - "ik": Query IK handle/pole vector animation
                - "matrix": Walk an offsetParentMatrix network upstream
                  for animCurves (SmartBake matrix-drive analysis)
                - "motion_path": Query uValue animation
                - "inherited_visibility": Query an ancestor ``.visibility``
                  driver node (SmartBake's inherited-visibility analysis)
                - "inherited_visibility_plugs": Query an ancestor
                  ``<transform>.visibility`` PLUG (same analysis, plug form)

        Returns:
            List of keyframe times from the driver's animation.
            Empty list if no animation found.

        Example:
            >>> times = AnimUtils.get_driver_animation_range("pCube1_parentConstraint1")
            >>> print(min(times), max(times))  # 1.0 100.0
        """
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

    @staticmethod
    def get_tangent_info(attr_name: str, time: float) -> Dict[str, Any]:
        """Get tangent information (types, angles, and weights) for a given attribute at a specific time.

        Parameters:
            attr_name (str): The name of the attribute.
            time (float): The time at which to query the tangent information.

        Returns:
            Dict[str, Any]: A dictionary containing tangent information.
                Empty dict when no key exists at *time* (``set_tangent_info``
                treats an empty dict as a no-op).
        """
        itt = cmds.keyTangent(attr_name, query=True, time=(time,), inTangentType=True)
        if not itt:
            return {}
        return {
            "inTangentType": itt[0],
            "outTangentType": cmds.keyTangent(
                attr_name, query=True, time=(time,), outTangentType=True
            )[0],
            "inAngle": cmds.keyTangent(
                attr_name, query=True, time=(time,), inAngle=True
            )[0],
            "outAngle": cmds.keyTangent(
                attr_name, query=True, time=(time,), outAngle=True
            )[0],
            "inWeight": cmds.keyTangent(
                attr_name, query=True, time=(time,), inWeight=True
            )[0],
            "outWeight": cmds.keyTangent(
                attr_name, query=True, time=(time,), outWeight=True
            )[0],
        }

    @staticmethod
    def set_tangent_info(
        attr_name: str, time: float, tangent_info: Dict[str, Any]
    ) -> None:
        """Restore tangent information on a keyframe.

        Applies tangent types in a separate call after angles/weights so that
        type-specific tangents (e.g. stepped) are not overridden by the
        angle/weight values which would implicitly force the type to 'fixed'.

        Parameters:
            attr_name (str): The attribute name.
            time (float): The time of the keyframe.
            tangent_info (Dict[str, Any]): Tangent dict from ``get_tangent_info``.
        """
        types = {}
        weights_angles = {}
        for k, v in tangent_info.items():
            if "TangentType" in k:
                types[k] = v
            else:
                weights_angles[k] = v

        # Angles/weights first (these may implicitly set type to 'fixed')
        if weights_angles:
            cmds.keyTangent(attr_name, time=(time,), edit=True, **weights_angles)
        # Types last so they take precedence
        if types:
            cmds.keyTangent(attr_name, time=(time,), edit=True, **types)

    @staticmethod
    def _resolve_keys(
        objects=None,
        mode: str = "auto",
        resolution_order: Optional[Tuple[str, ...]] = None,
    ) -> Dict[str, Any]:
        """Resolve which animation keys to operate on.

        Provides a unified mechanism for determining target keys.  When
        *mode* is ``"auto"``, each strategy in *resolution_order* is
        tried in sequence; the first strategy that yields results wins.

        .. note::

           This helper lives on ``AnimUtils`` (not on the ``_AnimUtilsInternal``)
           because it calls ``AnimUtils.objects_to_curves`` — placing it on
           the mixin would create a circular reference.

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
        cb_attrs_raw = AnimUtils._get_channel_box_attrs()
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
            return any(
                not AnimUtils._plug_attr_names(p).isdisjoint(attrs) for p in conns
            )

        # Lazy cache for objects_to_curves — computed at most once.
        _obj_curves_cache: List[Optional[List[str]]] = [None]

        def _get_obj_curves() -> List[str]:
            if _obj_curves_cache[0] is None:
                _obj_curves_cache[0] = (
                    AnimUtils.objects_to_curves(objects) if objects else []
                )
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
    @CoreUtils.undoable
    def step_keys(
        objects=None,
        keys=None,
        tangent: str = "out",
        resolution_order: Optional[Tuple[str, ...]] = None,
    ) -> dict:
        """Set stepped tangents on animation keys.

        Parameters:
            objects: Transforms / anim curves to operate on.  Falls back
                     to ``cmds.ls(selection=True)`` when *None*.
            keys: Which keys to step.  Accepted values:

                  - ``None`` (default) — step **all** keys on *objects*.
                  - ``"auto"`` — smart cascade via :meth:`_resolve_keys`:
                    uses selected keys if any (narrowed by Channel Box
                    highlights), falls back to Channel Box attributes,
                    then current-frame keys, then all keys.
                  - A ``float`` or ``int`` — treated as a time; only keys
                    at that frame are stepped.
                  - A ``list[str]`` — treated as animation-curve node
                    names (e.g. from ``cmds.keyframe(selected=True,
                    name=True)``); those curves are stepped directly
                    and *objects* is ignored.
                  - A ``dict[str, list[float] | None]`` — mapping of
                    curve names to specific times.  A *None* value means
                    step every key on that curve.
            tangent: Which tangent(s) to set stepped.
                  - ``"out"`` (default) — out-tangent ``step``.
                  - ``"in"`` — in-tangent ``stepnext``.
                  - ``"both"`` — both out ``step`` and in ``stepnext``.
            resolution_order: Strategies to try for ``"auto"`` mode.
                Default: ``("selected", "channel_box", "current_frame",
                "all")``.  See :meth:`_resolve_keys`.

        Returns:
            dict: ``{"curves": int, "objects": int}`` counts.
        """
        # --- Auto resolution: resolve keys via _resolve_keys helper ---
        if keys == "auto":
            resolved = AnimUtils._resolve_keys(
                objects, mode="auto", resolution_order=resolution_order
            )
            # Empty dict → None so the "step all" path handles no-result.
            keys = resolved["curves"] if resolved["curves"] else None
            objects = resolved["objects"]

        result = {"curves": 0, "objects": 0}

        # Build tangent kwargs from the tangent parameter.
        tan_kw: dict = {}
        if tangent in ("out", "both"):
            tan_kw["outTangentType"] = "step"
        if tangent in ("in", "both"):
            tan_kw["inTangentType"] = "stepnext"
        if not tan_kw:
            tan_kw["outTangentType"] = "step"  # fallback

        # When setting only one side we must preserve the opposite side's
        # type, angle, and weight.  Maya's tangent lock couples the
        # handles geometrically, so a naive ``lock=False`` causes the
        # untouched side's handle to jump.  Instead we snapshot → apply
        # both sides → restore the untouched side.
        _one_side = tangent in ("in", "out")

        def _apply(curve: str, time_arg=None):
            """Apply tangent kwargs to *curve*, optionally at *time_arg*."""
            if _one_side and time_arg is not None:
                # Per-key: snapshot the opposite side, apply, restore.
                _apply_one_side(curve, time_arg, tangent)
                return
            if _one_side and time_arg is None:
                # Whole-curve: iterate every key individually.
                all_times = cmds.keyframe(curve, q=True, timeChange=True) or []
                for t in all_times:
                    _apply_one_side(curve, (t, t), tangent)
                return
            # "both" — set out=step + in=stepnext on current key.
            kw = dict(edit=True, **tan_kw)
            if time_arg is not None:
                kw["time"] = time_arg
            try:
                cmds.keyTangent(curve, **kw)
            except RuntimeError as e:
                cmds.warning(f"step_keys: failed to step {curve}: {e}")
            # For "both" with a specific time, also set out=step on
            # the predecessor key so the incoming segment is stepped.
            # inTangentType="stepnext" alone does NOT produce step
            # interpolation — the predecessor's out-tangent must be
            # "step" for the segment to hold flat.
            if tangent == "both" and time_arg is not None:
                all_times = cmds.keyframe(curve, q=True, timeChange=True) or []
                t = time_arg[0]
                prev_times = [pt for pt in all_times if pt < t]
                if prev_times:
                    prev_t = max(prev_times)
                    ott = cmds.keyTangent(
                        curve,
                        q=True,
                        time=(prev_t, prev_t),
                        outTangentType=True,
                    )
                    if not (ott and ott[0] == "step"):
                        _apply_one_side(curve, (prev_t, prev_t), "out")

        def _apply_one_side(curve: str, time_arg, side: str):
            """Set one tangent side while preserving the other side completely.

            Parameters:
                side: ``"in"`` to set inTangentType=stepnext (preserving out),
                      ``"out"`` to set outTangentType=step (preserving in).

            Angle and weight are only restored when the original tangent type
            is ``"fixed"`` — the only type where those values are user-defined.
            For auto-computed types (auto, spline, linear, flat, plateau,
            clamped) restoring the type alone lets Maya recompute the handle
            geometry.  Restoring angle on an auto tangent would silently
            convert it to ``"fixed"``, breaking auto-computation.
            """
            try:
                if side == "in":
                    # Preserve out-tangent
                    ott = cmds.keyTangent(
                        curve, q=True, time=time_arg, outTangentType=True
                    )
                    restore_geometry = ott and ott[0] == "fixed"
                    if restore_geometry:
                        oa = cmds.keyTangent(
                            curve, q=True, time=time_arg, outAngle=True
                        )
                        ow = cmds.keyTangent(
                            curve, q=True, time=time_arg, outWeight=True
                        )
                    cmds.keyTangent(
                        curve,
                        edit=True,
                        time=time_arg,
                        lock=False,
                        inTangentType="stepnext",
                    )
                    # Restore out-tangent type (always)
                    if ott:
                        cmds.keyTangent(
                            curve, edit=True, time=time_arg, outTangentType=ott[0]
                        )
                    # Restore angle/weight only for "fixed" tangents
                    if restore_geometry:
                        if oa is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, outAngle=oa[0]
                            )
                        if ow is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, outWeight=ow[0]
                            )
                else:  # side == "out"
                    # Preserve in-tangent
                    itt = cmds.keyTangent(
                        curve, q=True, time=time_arg, inTangentType=True
                    )
                    restore_geometry = itt and itt[0] == "fixed"
                    if restore_geometry:
                        ia = cmds.keyTangent(curve, q=True, time=time_arg, inAngle=True)
                        iw = cmds.keyTangent(
                            curve, q=True, time=time_arg, inWeight=True
                        )
                    cmds.keyTangent(
                        curve,
                        edit=True,
                        time=time_arg,
                        lock=False,
                        outTangentType="step",
                    )
                    # Restore in-tangent type (always)
                    if itt:
                        cmds.keyTangent(
                            curve, edit=True, time=time_arg, inTangentType=itt[0]
                        )
                    # Restore angle/weight only for "fixed" tangents
                    if restore_geometry:
                        if ia is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, inAngle=ia[0]
                            )
                        if iw is not None:
                            cmds.keyTangent(
                                curve, edit=True, time=time_arg, inWeight=iw[0]
                            )
            except RuntimeError as e:
                cmds.warning(
                    f"step_keys: tangent edit on {curve} at {time_arg} failed "
                    f"({e}); the opposite side may not be fully restored."
                )

        # --- Dict: per-curve per-time stepping ---
        if isinstance(keys, dict) and keys:
            for curve, key_times in keys.items():
                if key_times is None:
                    _apply(curve)
                else:
                    for t in key_times:
                        _apply(curve, time_arg=(t, t))
            result["curves"] = len(keys)
            return result

        # --- Curve names passed directly (e.g. graph-editor selection) ---
        # Query the *selected* key times per curve so that only those
        # keys are stepped, not the entire curve.
        if isinstance(keys, (list, tuple)) and keys:
            unique = set(keys)
            for curve in unique:
                sel_times = cmds.keyframe(
                    curve, query=True, selected=True, timeChange=True
                )
                if sel_times:
                    for t in sel_times:
                        _apply(curve, time_arg=(t, t))
                else:
                    # Fallback: no selection info — step the whole curve
                    _apply(curve)
            result["curves"] = len(unique)
            return result

        # --- Resolve objects ---
        if objects is None:
            objects = cmds.ls(selection=True)
        if not objects:
            return result

        curves = AnimUtils._filter_time_curves(AnimUtils.objects_to_curves(objects))
        if not curves:
            return result

        # --- Time value: step only keys at that frame ---
        if isinstance(keys, (int, float)):
            time_arg = (keys, keys)
            touched = [
                c
                for c in curves
                if cmds.keyframe(c, query=True, time=time_arg, keyframeCount=True)
            ]
            for curve in touched:
                _apply(curve, time_arg=time_arg)
            result["curves"] = len(touched)
            result["objects"] = len(objects)
            return result

        # --- None: step all keys ---
        for curve in curves:
            _apply(curve)
        result["curves"] = len(curves)
        result["objects"] = len(objects)
        return result

    @staticmethod
    def set_current_frame(
        time: Optional[float] = None,
        update: bool = True,
        relative: bool = False,
        snap_mode: Optional[str] = None,
        invert_snap: bool = False,
    ) -> float:
        """Set the current frame on the timeslider with optional snapping.

        Parameters:
            time: The desired frame number or offset. If None, uses current time.
            update: If True (default), the scene evaluates at the new time;
                if False, only the time slider moves (the world is not updated).
            relative: If True, the frame will be moved relative to its current position.
            snap_mode: Snapping mode ('nearest', 'preferred', 'aggressive', etc.).
            invert_snap: If True, swaps directional snap modes ('floor' <-> 'ceil').
                Has no effect on other snap modes.

        Returns:
            float: The final time that was set.
        """
        current_time = cmds.currentTime(query=True)

        # Determine base target time
        if time is None:
            target_time = current_time
        elif relative:
            target_time = current_time + time
        else:
            target_time = time

        # Apply snapping
        if snap_mode and snap_mode.lower() != "none":
            # Handle alias for aggressive
            mode = snap_mode.lower()
            if mode == "aggressive":
                mode = "aggressive_preferred"

            # Invert swaps directional modes (floor ↔ ceil)
            if invert_snap:
                if mode == "floor":
                    mode = "ceil"
                elif mode == "ceil":
                    mode = "floor"

            target_time = ptk.MathUtils.round_value(
                target_time,
                mode=mode,
            )

        cmds.currentTime(target_time, edit=True, update=update)
        return target_time

    @staticmethod
    @CoreUtils.undoable
    def move_keys_to_frame(
        objects=None,
        frame=None,
        time_range=None,
        selected_keys_only=False,
        retain_spacing=False,
        channel_box_attrs_only=False,
        align: str = "auto",
    ):
        """Move keyframes to the given frame with comprehensive control options.

        Parameters:
            objects (list, optional): Objects to move keys for. If None, uses selection.
            frame (int or float, optional): The frame to move keys to.
                                                   If None, uses the current time.
            time_range (tuple, optional): (start_frame, end_frame) to limit which keys to move.
                                         If None, moves all keys.
            selected_keys_only (bool): If True, only moves selected keys from the graph editor.
                                 If False, moves all keys in the specified time range.
            retain_spacing (bool): If True, maintains relative spacing between objects.
                                   If False, moves each object's first key to the target frame.
            channel_box_attrs_only (bool): If True, only affects attributes selected in the channel box.
                                    Works in combination with selected_keys_only.
            align (str): Which end of the key range lands on *frame*.
                ``"start"`` — the earliest key aligns to *frame*.
                ``"end"`` — the latest key aligns to *frame*.
                ``"auto"`` (default) — if the midpoint of the key range is
                before *frame*, behaves like ``"end"``; otherwise ``"start"``.
        Returns:
            bool: True if keys were moved successfully, False otherwise.
        """
        # Get target frame (use current time if not specified)
        if frame is None:
            frame = cmds.currentTime(query=True)

        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return False

        objects = cmds.ls(objects)  # Ensure we have Maya nodes

        # Get channel box attributes if filtering is requested
        channel_box_attrs = None
        if channel_box_attrs_only:
            channel_box_attrs = AnimUtils._get_channel_box_attrs()
            if not channel_box_attrs:
                cmds.warning("No attributes selected in channel box.")
                return False

        # If working with selected keys, validate there are selected keys
        if selected_keys_only:
            all_active_key_times = cmds.keyframe(query=True, sl=True, tc=True)
            if not all_active_key_times:
                cmds.warning("No keyframes selected.")
                return False

        range_kw = {"time": time_range} if time_range else {}

        def _query_times(target: Union[str, List[str]], selected: bool) -> List[float]:
            """Key times on *target*, optionally selected-only / range-limited."""
            if not target:
                return []
            sel_kw = {"sl": True} if selected else {}
            return (
                cmds.keyframe(target, query=True, tc=True, **sel_kw, **range_kw) or []
            )

        def _time_curves(target: str, selected: bool = False) -> List[str]:
            """Time-driven animCurves on *target* — unitless (set-driven-key)
            curves are excluded so their driver values are never time-shifted."""
            sel_kw = {"sl": True} if selected else {}
            names = cmds.keyframe(target, query=True, name=True, **sel_kw) or []
            return AnimUtils._filter_time_curves(names)

        # Resolve "auto" align by scanning all relevant key times
        if align == "auto":
            all_times = []
            for obj in objects:
                if selected_keys_only:
                    for _node in _time_curves(obj, selected=True):
                        all_times.extend(_query_times(_node, selected=True))
                else:
                    all_times.extend(_query_times(_time_curves(obj), selected=False))
            if all_times:
                midpoint = (min(all_times) + max(all_times)) / 2.0
                align = "end" if midpoint < frame else "start"
            else:
                align = "start"

        # Pick the anchor function based on align mode
        align_end = align == "end"
        _anchor = max if align_end else min
        # Comparison used for the retain_spacing scan across objects
        _is_better = (
            (lambda new, best: new > best)
            if align_end
            else (lambda new, best: new < best)
        )

        keys_moved = 0

        # If maintaining spacing, calculate the global offset from the anchor key across all objects
        global_offset = None
        if retain_spacing:
            anchor_key_time = None

            # Find the anchor key across all objects
            for obj in objects:
                if selected_keys_only:
                    keys = _time_curves(obj, selected=True)
                    for node in keys:
                        active_key_times = _query_times(node, selected=True)
                        if active_key_times:
                            obj_anchor = _anchor(active_key_times)
                            if anchor_key_time is None or _is_better(
                                obj_anchor, anchor_key_time
                            ):
                                anchor_key_time = obj_anchor
                else:
                    keys = _query_times(_time_curves(obj), selected=False)
                    if keys:
                        obj_anchor = _anchor(keys)
                        if anchor_key_time is None or _is_better(
                            obj_anchor, anchor_key_time
                        ):
                            anchor_key_time = obj_anchor

            if anchor_key_time is not None:
                global_offset = frame - anchor_key_time

        for obj in objects:
            # Get keyframes based on selection preference and time range
            if selected_keys_only:
                # Use AnimUtils pattern for selected keys
                keys = _time_curves(obj, selected=True)

                # Filter by channel box attributes if requested.  The
                # Channel Box reports SHORT names ('tx') while plugs carry
                # long names — match via the normalized name set.
                if channel_box_attrs_only:
                    cb_set = {a.lower() for a in channel_box_attrs}
                    filtered_keys = []
                    for node in keys:
                        connections = cmds.listConnections(
                            node, plugs=True, destination=True, source=False
                        )
                        if connections and any(
                            not AnimUtils._plug_attr_names(p).isdisjoint(cb_set)
                            for p in connections
                        ):
                            filtered_keys.append(node)
                    keys = filtered_keys

                for node in keys:
                    active_key_times = _query_times(node, selected=True)
                    if active_key_times:
                        # Calculate offset - use global offset if maintaining spacing
                        if retain_spacing and global_offset is not None:
                            offset = global_offset
                        else:
                            anchor_time = _anchor(active_key_times)
                            offset = frame - anchor_time

                        # Move exactly the selected keys — a (min, max) range
                        # edit would also drag unselected keys inside the span.
                        keys_moved += AnimUtils._shift_key_times(
                            node, active_key_times, offset
                        )
            else:
                # Handle all keys in time range
                if channel_box_attrs_only:
                    # Move keys only for channel box attributes
                    for attr in channel_box_attrs:
                        if not cmds.attributeQuery(attr, node=str(obj), exists=True):
                            continue

                        attr_name = f"{obj}.{attr}"
                        curves = _time_curves(attr_name)
                        keys = _query_times(curves, selected=False)
                        if keys:
                            # Calculate offset - use global offset if maintaining spacing
                            if retain_spacing and global_offset is not None:
                                offset = global_offset
                            else:
                                anchor_time = _anchor(keys)
                                offset = frame - anchor_time

                            # Move the keys.  option="over" lets a range move
                            # travel past unmoved out-of-range neighbors — the
                            # default move CLAMPS at the adjacent key (see
                            # _shift_key_times for the same trap).
                            if time_range:
                                cmds.keyframe(
                                    curves,
                                    edit=True,
                                    time=time_range,
                                    relative=True,
                                    timeChange=offset,
                                    option="over",
                                )
                            else:
                                cmds.keyframe(
                                    curves,
                                    edit=True,
                                    relative=True,
                                    timeChange=offset,
                                )
                            keys_moved += len(keys)
                else:
                    # Move all keys on object
                    curves = _time_curves(obj)
                    keys = _query_times(curves, selected=False)
                    if keys:
                        # Calculate offset - use global offset if maintaining spacing
                        if retain_spacing and global_offset is not None:
                            offset = global_offset
                        else:
                            anchor_time = _anchor(keys)
                            offset = frame - anchor_time

                        # Move the keys.  option="over" lets a range move
                        # travel past unmoved out-of-range neighbors — the
                        # default move CLAMPS at the adjacent key (see
                        # _shift_key_times for the same trap).
                        if time_range:
                            cmds.keyframe(
                                curves,
                                edit=True,
                                time=time_range,
                                relative=True,
                                timeChange=offset,
                                option="over",
                            )
                        else:
                            cmds.keyframe(
                                curves, edit=True, relative=True, timeChange=offset
                            )

                        keys_moved += len(keys)

        if keys_moved > 0:
            selection_type = "selected" if selected_keys_only else "all"
            range_info = f" in range {time_range}" if time_range else ""
            spacing_info = " (maintaining relative spacing)" if retain_spacing else ""
            print(
                f"Moved {keys_moved} {selection_type} keys to frame {frame}{range_info}{spacing_info}"
            )
            return True
        else:
            cmds.warning("No keyframes found to move.")
            return False

    @staticmethod
    @CoreUtils.undoable
    def set_keys_for_attributes(
        objects, target_times=None, refresh_channel_box=False, **kwargs
    ):
        """Sets keyframes for the specified attributes on given objects at given times.

        Automatically detects whether to apply the same values to all objects (shared mode)
        or different values per object (per-object mode) based on the data structure.

        Parameters:
            objects (list): The objects to set the keyframes on.
            target_times (int/list, optional): Frame(s) to set keys at. Default: current time.
            refresh_channel_box (bool, optional): Update channel box after setting keys. Default: False.
            **kwargs: Can be used in two modes:

                SHARED MODE - Same values to all objects:
                    Attribute names as keys with their values.

                PER-OBJECT MODE - Different values per object:
                    Pass the per-object dictionary unpacked. The function auto-detects this mode when
                    the first kwarg value is a dict containing attribute/value pairs.
                    Format when unpacking: {obj_name: {attr: value, ...}, ...}

        Example:
            # Shared mode - same values to all objects
            set_keys_for_attributes([obj1, obj2], translateX=5, translateY=10)
            set_keys_for_attributes(objects, target_times=[10, 15, 20], translateX=5)

            # Per-object mode - different values per object (auto-detected)
            data = {'pCube1': {'translateX': 5.0}, 'pCube2': {'translateX': 10.0}}
            set_keys_for_attributes([obj1, obj2], **data)

            # With times and refresh
            set_keys_for_attributes(objects, target_times=10, refresh_channel_box=True, translateX=5)
        """
        if target_times is None:
            target_times = [cmds.currentTime(query=True)]
        elif isinstance(target_times, (int, float)) and not isinstance(
            target_times, bool
        ):
            target_times = [target_times]

        # Auto-detect per-object mode: if first remaining kwarg value is a dict of attributes
        per_object_mode = False
        if kwargs:
            first_value = next(iter(kwargs.values()))
            # Per-object mode if the value is a dict (and likely contains attribute mappings)
            if isinstance(first_value, dict):
                per_object_mode = True

        if per_object_mode:
            # Per-object mode: Each object gets its specific attribute values
            # kwargs structure: {obj_name: {attr: value, ...}, ...}
            per_object_data = kwargs

            for obj in cmds.ls(objects, long=True):
                obj_name = str(obj)

                # Try to find matching stored data
                obj_attrs = per_object_data.get(obj_name)

                if not obj_attrs:
                    # Try short name if full path didn't match
                    short_name = str(obj).split("|")[-1]
                    obj_attrs = per_object_data.get(short_name)

                    # Try matching stored short names against current long name
                    if not obj_attrs:
                        for stored_name in per_object_data.keys():
                            if stored_name.split("|")[-1] == short_name:
                                obj_attrs = per_object_data.get(stored_name)
                                break

                if obj_attrs:
                    for attr, value in obj_attrs.items():
                        attr_full_name = f"{obj}.{attr}"
                        for time in target_times:
                            AnimUtils._set_key_preserving_tangents(
                                attr_full_name, time, value
                            )
        else:
            # Shared mode: All objects get the same attribute values
            # kwargs structure: {attr: value, attr2: value2, ...}
            for obj in cmds.ls(objects):
                for attr, value in kwargs.items():
                    attr_full_name = f"{obj}.{attr}"
                    for time in target_times:
                        AnimUtils._set_key_preserving_tangents(
                            attr_full_name, time, value
                        )

        if refresh_channel_box:
            mel.eval("channelBoxCommand -update;")

    @staticmethod
    def filter_objects_with_keys(
        objects: Optional[Union[str, List[str]]] = None,
        keys: Optional[List[str]] = None,
    ) -> List[str]:
        """Filter the given objects for those with specific keys set. If no objects are given, use all scene objects. If no specific keys are given, check all keys.

        Parameters:
            objects: The objects (or their names) to filter. Can be a single object or a list of objects. If None, all scene objects are used.
            keys: Specific keys to check for. If none are provided, all keys are checked.

        Returns:
            List of transforms with the specified keys set.
        """
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
    def scene_has_animation() -> bool:
        """True if the scene contains any time-based animation a playblast would capture.

        Looks for time-input animation curves (``animCurveTL/TA/TU/TT``) — keyed
        attributes driven by the timeline. This deliberately covers *all* keyed
        content (transforms, cameras, blendshape morphs, visibility, lights,
        materials, …), not just transforms, and excludes driven keys
        (``animCurveU*``), whose motion depends on a driver rather than time.

        Unlike :meth:`ShotStore.has_animation` (transform-scoped, for shot
        detection), this is the canonical "does anything move over time?" check —
        used to early-out of playblast/preview captures on a static scene.

        It is intentionally lightweight: it checks for the *existence* of time
        curves, not whether they carry meaningful (non-flat) motion. Returns
        ``False`` when Maya is unavailable.
        """
        if cmds is None:
            return False
        return bool(
            cmds.ls(type=["animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"])
        )

    @classmethod
    @CoreUtils.undoable
    def adjust_key_spacing(
        cls,
        objects: Optional[List[str]] = None,
        spacing: int = 1,
        time: Optional[int] = 0,
        relative: bool = True,
        preserve_keys: bool = False,
        selected_keys_only: bool = False,
        exact_gap: bool = False,
        prevent_collisions: bool = True,
    ):
        """Adjusts the spacing between keyframes for specified objects at a given time,
        with an option to preserve and adjust a keyframe at the specified time.

        Operates on animation curves directly, supporting both object-level and
        graph-editor-selection workflows.

        Parameters:
            objects (Optional[List[str]]): Objects to adjust keyframes for.
                If None, adjusts all scene objects (or selected keys when selected_keys_only is True).
                When selected_keys_only is True, objects is ignored and curves are
                determined entirely from the graph editor selection.
            spacing (int): Spacing to add or remove. Negative values remove spacing.
                When exact_gap is True, this is the desired gap size in frames
                (must be positive) and the actual shift is calculated so the first
                key after the start time lands exactly at (start + spacing).
            time (Optional[int]): Time at which to start adjusting spacing.
                                 If None, uses the current playhead time.
            relative (bool): If True, time is relative to the current frame.
            preserve_keys (bool): Preserves and adjusts a keyframe at the specified time
                if it exists. Only considers keys visible to the current query
                (i.e. when selected_keys_only is True, only selected keys are preserved).
            selected_keys_only (bool): If True, only affects selected keyframes in the graph editor.
                Overrides objects — the curve set comes from the graph editor selection.
            exact_gap (bool): If True, calculates the actual shift amount so that the
                first key after the start time is moved exactly to (start + spacing),
                clearing a precise range. Spacing must be positive in this mode.
            prevent_collisions (bool): If True (default), performs a dry-run
                collision check before moving any keys. If any destination time
                would land on an existing unmoved key, the entire operation is
                aborted and a warning is issued.
        """
        if spacing == 0:
            return

        if exact_gap and spacing < 0:
            cmds.warning("exact_gap mode requires a positive spacing value.")
            return

        current_time = cmds.currentTime(query=True)

        # Auto-detect: use current playhead time
        if time is None:
            adjusted_time = current_time
        else:
            adjusted_time = time + current_time if relative else time

        # Get animation curves — selected_keys_only overrides objects
        if selected_keys_only:
            anim_curves = cls.get_anim_curves(
                objects=None, selected_keys_only=True, recursive=False
            )
        else:
            anim_curves = cls.get_anim_curves(
                objects=objects, selected_keys_only=False, recursive=False
            )
        # Unitless (set-driven-key) curves are excluded — their "times" are
        # driver values, and spacing shifts would corrupt the driven mapping.
        anim_curves = cls._filter_time_curves(anim_curves)
        if not anim_curves:
            if selected_keys_only:
                cmds.warning("No keyframes selected.")
            else:
                cmds.warning("No animation found.")
            return

        # In exact_gap mode, find the earliest key after adjusted_time to compute offset
        actual_spacing = spacing
        if exact_gap:
            gap_end = adjusted_time + spacing
            earliest_key = None
            for curve in anim_curves:
                if selected_keys_only:
                    kf = cmds.keyframe(curve, query=True, sl=True, timeChange=True)
                else:
                    kf = cmds.keyframe(curve, query=True, timeChange=True)
                if kf:
                    after = [k for k in kf if k > adjusted_time + 0.001]
                    if after:
                        curve_min = min(after)
                        if earliest_key is None or curve_min < earliest_key:
                            earliest_key = curve_min
            if earliest_key is None:
                cmds.warning(f"No keyframes found after frame {adjusted_time}.")
                return
            # Keep the offset fractional — truncating to int breaks the
            # "first key lands exactly at start + spacing" guarantee for
            # sub-frame key times.
            actual_spacing = gap_end - earliest_key
            if actual_spacing <= 1e-9:
                # Gap already clear
                return

        # If preserve_keys, save keyframes at adjusted_time before moving them
        preserved_keys = []  # [(attr_name, value, tangent_info)]
        tolerance = 0.0001

        # ---------- Collision pre-flight check ----------
        # Build per-curve move plans so we can detect collisions before
        # touching any keys.
        curve_plans = []  # [(curve, keys_to_move, keyframes)]
        for curve in anim_curves:
            if selected_keys_only:
                keyframes = cmds.keyframe(curve, query=True, sl=True, timeChange=True)
            else:
                keyframes = cmds.keyframe(curve, query=True, timeChange=True)
            if not keyframes:
                continue

            keys_to_move = sorted(
                [k for k in keyframes if k >= adjusted_time - tolerance],
                reverse=(actual_spacing > 0),
            )
            curve_plans.append((curve, keys_to_move, keyframes))

        if prevent_collisions:
            for curve, keys_to_move, keyframes in curve_plans:
                if not keys_to_move:
                    continue
                moving_set = set(keys_to_move)
                # Keys on this curve that are NOT being moved. Query the FULL
                # curve, not the (possibly sl=True-filtered) plan query: with
                # selected_keys_only, unselected keys are invisible to
                # `keyframes`, and option="over" below would silently
                # overwrite one if a moved key landed on it (the same blind
                # spot fixed in snap_keys_to_frames' occupied set).
                all_times = cmds.keyframe(curve, query=True, timeChange=True) or []
                stationary = {k for k in all_times if k not in moving_set}
                dests: List[float] = []
                for key in keys_to_move:
                    dest = max(key + actual_spacing, 0)
                    if dest == key:
                        # Unmoved (clamped in place) — still occupies its
                        # time; a later key clamping onto it must collide.
                        dests.append(dest)
                        continue
                    # Collision with a stationary key?
                    if any(abs(dest - s) < tolerance for s in stationary):
                        cmds.warning(
                            f"Cannot move keys: frame {int(key)} would collide "
                            f"with an existing key at frame {int(dest)}. "
                            f"Reduce the spacing amount or move the blocking key first."
                        )
                        return
                    # Collision between two MOVED keys — only possible when
                    # the clamp-at-0 flattens distinct sources onto the same
                    # destination (large negative spacing).
                    if any(abs(dest - d) < tolerance for d in dests):
                        cmds.warning(
                            f"Cannot move keys: multiple keys on {curve} would "
                            f"merge at frame {int(dest)} (clamped at 0). "
                            f"Reduce the negative spacing amount."
                        )
                        return
                    dests.append(dest)

        # ---------- Execute moves ----------
        for curve, keys_to_move, keyframes in curve_plans:
            # Get the attribute name connected to this curve for preserve_keys
            if preserve_keys and any(
                abs(k - adjusted_time) < tolerance for k in keyframes
            ):
                connections = cmds.listConnections(
                    curve, plugs=True, source=False, destination=True
                )
                if connections:
                    attr_name = str(connections[0])
                    value = cmds.getAttr(attr_name, time=adjusted_time)
                    tangent_info = cls.get_tangent_info(attr_name, adjusted_time)
                    preserved_keys.append((attr_name, value, tangent_info))

            if not keys_to_move:
                continue

            # Batch move: group into contiguous ranges where possible
            for key in keys_to_move:
                new_time = max(key + actual_spacing, 0)
                if new_time != key:
                    try:
                        # option="over" lets a key travel past unmoved
                        # neighbors — the default move CLAMPS at the adjacent
                        # key, leaving it off-frame (see _shift_key_times and
                        # snap_keys_to_frames for the same trap). The
                        # prevent_collisions pre-flight already aborts on a
                        # destination that lands ON a stationary key.
                        cmds.keyframe(
                            curve,
                            edit=True,
                            time=(key,),
                            timeChange=new_time,
                            option="over",
                        )
                    except RuntimeError as e:
                        cmds.warning(
                            f"adjust_key_spacing: failed to move key at {key} "
                            f"on {curve}: {e}"
                        )

        # Restore preserved keyframes at the original time
        for attr_name, value, tangent_info in preserved_keys:
            cmds.setKeyframe(attr_name, time=(adjusted_time,), value=value)
            cls.set_tangent_info(attr_name, adjusted_time, tangent_info)

    @staticmethod
    @CoreUtils.undoable
    def add_intermediate_keys(
        objects: Union[str, List[str]],
        time_range: Optional[Union[int, Tuple[int, int]]] = None,
        percent: Optional[float] = None,
        include_flat: bool = False,
        ignore: Union[str, List[str], None] = None,
    ) -> None:
        """Keys selected or animated attributes on given object(s) within a time range.
        If attributes are selected in the channel box, only those will be keyed.
        If time_range is not specified, automatically detects the first and last keyframe per attribute.

        Parameters:
            objects (str/list): One or more objects to key.
            time_range (int, tuple, or None):
                - None: Auto-detects range from first to last keyframe per attribute
                - int: End frame (starts from first keyframe)
                - tuple (start, end): Explicit start and end frames
            percent (float): Optional percent (0-100) of frames to key, evenly distributed.
            include_flat (bool): If False, skips keys where value doesn't vary across time.
            ignore (str/list, optional): Attribute name(s) to ignore when adding keys.
                E.g., 'visibility' or ['visibility', 'translateX']. Curves connected to these
                attributes will not have intermediate keys added.
        """
        from math import isclose

        targets = cmds.ls(objects, flatten=True)
        cb_attrs = AnimUtils._get_channel_box_attrs()
        if cb_attrs:
            # The Channel Box reports SHORT names ('tx'); normalize to long
            # names so the ignore filter matches user input ('translateX').
            attrs = set()
            for obj in targets:
                for attr in cb_attrs:
                    try:
                        attrs.add(
                            cmds.attributeQuery(attr, node=str(obj), longName=True)
                        )
                    except RuntimeError:
                        continue
            attrs = list(attrs)
        else:
            attrs = set()
            for obj in targets:
                keyable = cmds.listAttr(obj, keyable=True, scalar=True) or []
                for attr in keyable:
                    plug = f"{obj}.{attr}"
                    if cmds.listConnections(plug, source=True, destination=False):
                        attrs.add(attr)
            attrs = list(attrs)

        if not attrs:
            cmds.warning("No keyable or connected attributes found.")
            return

        # Filter out ignored attributes
        attrs = AnimUtils._filter_attributes_by_ignore(attrs, ignore)
        if not attrs:
            cmds.warning("All attributes were ignored.")
            return

        # Build per-attribute key data with auto-detected or explicit ranges
        attr_key_data = {}
        for obj in targets:
            for attr in attrs:
                plug = f"{obj}.{attr}"
                if not cmds.objExists(plug):
                    continue
                if not cmds.listConnections(
                    plug, source=True, destination=False
                ) or not cmds.getAttr(plug, keyable=True):
                    continue

                # Determine range based on time_range parameter
                if time_range is None:
                    # Auto-detect full range
                    key_times = cmds.keyframe(plug, query=True, timeChange=True)
                    if not key_times or len(key_times) < 2:
                        continue
                    attr_start = int(key_times[0])
                    attr_end = int(key_times[-1])
                elif isinstance(time_range, tuple):
                    # Tuple (start, end) - either can be None for auto-detect
                    start_val, end_val = time_range
                    key_times = None

                    if start_val is None or end_val is None:
                        key_times = cmds.keyframe(plug, query=True, timeChange=True)
                        if not key_times:
                            continue

                    attr_start = int(key_times[0]) if start_val is None else start_val
                    attr_end = int(key_times[-1]) if end_val is None else end_val
                else:
                    # Single int - start from first key, end at specified frame
                    key_times = cmds.keyframe(plug, query=True, timeChange=True)
                    if not key_times:
                        continue
                    attr_start = int(key_times[0])
                    attr_end = time_range

                # Calculate frames to key (excluding bookends).
                # Coerce caller-supplied bounds to int the same way the
                # auto-detect path does — range() rejects float arguments.
                attr_start, attr_end = int(attr_start), int(attr_end)
                frames = list(range(attr_start + 1, attr_end))
                if percent is not None:
                    count = max(1, int(len(frames) * (percent / 100.0)))
                    step = max(1, len(frames) // count)
                    frames = frames[::step]

                if frames:
                    attr_key_data.setdefault((obj, attr), []).extend(frames)

        # Collect values, then set keys — both phases scrub the playhead,
        # so restore it afterward regardless of failures.
        original_time = cmds.currentTime(query=True)
        try:
            frame_values = {}
            for (obj, attr), frames in attr_key_data.items():
                plug = f"{obj}.{attr}"
                for frame in frames:
                    cmds.currentTime(frame, edit=True)
                    frame_values.setdefault(frame, {}).setdefault(obj, {})[attr] = (
                        cmds.getAttr(plug)
                    )

            for frame, obj_data in frame_values.items():
                cmds.currentTime(frame, edit=True)
                for obj, attr_values in obj_data.items():
                    for attr, value in attr_values.items():
                        plug = f"{obj}.{attr}"
                        if not include_flat:
                            try:
                                val_prev = cmds.getAttr(plug, time=frame - 1)
                                val_next = cmds.getAttr(plug, time=frame + 1)
                                if isclose(value, val_prev, abs_tol=1e-6) and isclose(
                                    value, val_next, abs_tol=1e-6
                                ):
                                    continue
                            except RuntimeError:
                                # Flatness probe failed (unreadable plug) —
                                # skip this frame rather than key blindly.
                                continue
                        cmds.setAttr(plug, value)
                        cmds.setKeyframe(plug)
        finally:
            cmds.currentTime(original_time, edit=True)

    @staticmethod
    @CoreUtils.undoable
    def remove_intermediate_keys(
        objects: Union[str, List[str]],
        time_range: Optional[Union[int, Tuple[int, int]]] = None,
        ignore: Union[str, List[str], None] = None,
    ) -> int:
        """Removes all intermediate keyframes, keeping only the first and last key on each attribute.
        If attributes are selected in the channel box, only those will be affected.
        Automatically detects the keyframe range for each attribute if time_range is not specified.

        Parameters:
            objects (str/list): One or more objects to remove intermediate keys from.
            time_range (int, tuple, or None):
                - None: Auto-detects range from first to last keyframe per attribute
                - int: End frame (starts from first keyframe)
                - tuple (start, end): Explicit start and end frames
            ignore (str/list, optional): Attribute name(s) to ignore when removing keys.
                E.g., 'visibility' or ['visibility', 'translateX']. Curves connected to these
                attributes will not have intermediate keys removed.

        Returns:
            int: Number of keyframes removed.

        Example:
            # Remove all intermediate keys, keeping only first and last
            remove_intermediate_keys(cmds.ls(selection=True))

            # Remove intermediate keys for channel box selected attributes only
            remove_intermediate_keys([obj1, obj2])

            # Remove intermediate keys except for visibility
            remove_intermediate_keys(cmds.ls(selection=True), ignore='visibility')

            # Remove intermediate keys within specific range
            remove_intermediate_keys(cmds.ls(selection=True), time_range=(10, 50))
        """
        targets = cmds.ls(objects, flatten=True)
        if not targets:
            cmds.warning("No valid objects provided.")
            return 0

        # Channel Box selected attributes (SHORT names, e.g. 'tx')
        cb_attrs = AnimUtils._get_channel_box_attrs()

        def _resolve_range(target: str) -> Optional[Tuple[float, float]]:
            """Resolve the (start, end) strip range for a plug or curve."""
            key_times = cmds.keyframe(target, query=True, timeChange=True)
            if time_range is None:
                if not key_times or len(key_times) < 2:
                    return None
                return (min(key_times), max(key_times))
            if isinstance(time_range, tuple):
                start_val, end_val = time_range
                if (start_val is None or end_val is None) and not key_times:
                    return None
                start = min(key_times) if start_val is None else start_val
                end = max(key_times) if end_val is None else end_val
                return (start, end)
            # Single int — start from first key, end at specified frame
            if not key_times:
                return None
            return (min(key_times), time_range)

        def _strip_intermediate(target: str) -> int:
            """Cut keys strictly between the resolved range ends of *target*."""
            rng = _resolve_range(target)
            if rng is None:
                return 0
            start, end = rng
            window = (start + 0.001, end - 0.001)
            intermediate_keys = cmds.keyframe(
                target, query=True, timeChange=True, time=window
            )
            if not intermediate_keys:
                return 0
            cmds.cutKey(target, time=window, clear=True)
            return len(intermediate_keys)

        keys_removed = 0

        for obj in targets:
            if cb_attrs:
                # Narrow to the Channel Box selection.  Normalize the short
                # names to long names so the ignore filter matches user
                # input like 'visibility'.  If ignore removes the entire
                # selection, honor the user's narrowed intent and remove
                # NOTHING rather than falling through to every attribute.
                obj_attrs = []
                for attr in cb_attrs:
                    try:
                        obj_attrs.append(
                            cmds.attributeQuery(attr, node=str(obj), longName=True)
                        )
                    except RuntimeError:
                        continue
                for attr in AnimUtils._filter_attributes_by_ignore(obj_attrs, ignore):
                    keys_removed += _strip_intermediate(f"{obj}.{attr}")
            else:
                # All keyed curves on the object, minus ignored ones
                keyed_curves = cmds.keyframe(obj, query=True, name=True)
                for curve in AnimUtils._filter_curves_by_ignore(keyed_curves, ignore):
                    keys_removed += _strip_intermediate(curve)

        if keys_removed > 0:
            print(f"Removed {keys_removed} intermediate keyframe(s).")
        else:
            print("No intermediate keyframes found to remove.")

        return keys_removed

    @staticmethod
    @CoreUtils.undoable
    def invert_keys(
        objects=None,
        time=None,
        relative=True,
        delete_original=False,
        mode="horizontal",
        value_pivot=0.0,
    ):
        """Invert keyframes, preferring selected keys over all keys.

        When any keys are selected in the graph editor, only selected keys
        are inverted (a RuntimeError is raised if none of them belong to
        *objects*).  With no graph-editor selection, all keys on *objects*
        are inverted.

        When `time` is None (default) the keys are mirrored **in place**: the
        animation reverses within its own key range. That is a move, not a
        copy — `relative` and `delete_original` are ignored. When `time` is
        given, a reversed copy is placed at that time instead, and the source
        keys are kept unless `delete_original` is True.

        Tangents travel with the keys.  On a time flip the handles swap sides
        and a stepped hold is re-homed to the key that now precedes its
        segment, as its opposite (``step`` <-> ``stepnext``); types Maya
        recomputes itself (``auto``, ``linear``, ``flat``, ...) stay their own
        type rather than being frozen into ``fixed`` handles.

        Parameters:
            objects (str/list, optional): Objects whose keys to invert.
                Defaults to the current selection.
            time (int, optional): Start time for the reversed copy.
                If None, mirrors the keys in place (no copy is made).
            relative (bool): When True, time is treated as an offset from the last key.
                Ignored when time is None. Defaults to True.
            delete_original (bool): Delete the source keyframes after copying.
                Implied when time is None. Defaults to False.
            mode (str): Inversion mode. "horizontal" (time), "vertical" (value), or "both". Defaults to "horizontal".
            value_pivot (float): Pivot value for vertical inversion. Defaults to 0.0.
        """
        if objects is None:
            objects = cmds.ls(selection=True)
        else:
            objects = [str(o) for o in ptk.make_iterable(objects)]
        if not objects:
            raise RuntimeError("No objects selected.")

        selected_key_times = cmds.keyframe(query=True, sl=True, tc=True) or []
        use_selected = bool(selected_key_times)

        # Grouped per curve because tangent mirroring is order-dependent:
        # stepped segments have to migrate to the neighbouring key.
        times_by_curve: Dict[str, Set[float]] = {}

        for obj in objects:
            key_nodes = (
                cmds.keyframe(obj, query=True, name=True, selected=True) or []
                if use_selected
                else cmds.keyframe(obj, query=True, name=True) or []
            )

            for node in key_nodes:
                times = (
                    cmds.keyframe(node, query=True, selected=True, timeChange=True)
                    if use_selected
                    else cmds.keyframe(node, query=True, timeChange=True)
                )
                if not times:
                    continue

                times_by_curve.setdefault(str(node), set()).update(
                    float(t) for t in times
                )

        all_key_times: List[float] = [t for ts in times_by_curve.values() for t in ts]

        if not all_key_times:
            raise RuntimeError("No keyframes selected or found to invert.")

        max_time = max(all_key_times)
        min_time = min(all_key_times)

        if time is None:
            # In-place mirror: t' = min + (max - t) reverses the keys within
            # their own range; forcing delete_original turns the insert-then-
            # cut below into a move.
            inversion_point = min_time
            delete_original = True
        else:
            inversion_point = max_time + time if relative else time

        flip_time = mode in ("horizontal", "both")
        flip_value = mode in ("vertical", "both")

        # Snapshot every tangent BEFORE touching the curves: an in-place mirror
        # writes new keys over the originals it is still reading from.
        keyframe_data: List[Tuple[str, float, float, float, Dict[str, Any]]] = []
        for node, curve_times in times_by_curve.items():
            ordered = sorted(curve_times)
            # Neighbours are taken within the inverted set: with a partial
            # graph-editor selection the unselected keys stay put, so the
            # selection is the only run the mirror can be defined over.
            tangents = AnimUtils._mirror_tangent_data(
                [AnimUtils.get_tangent_info(node, t) for t in ordered],
                flip_time,
                flip_value,
            )

            for key_time, tangent_data in zip(ordered, tangents):
                key_value = cmds.keyframe(
                    node, query=True, time=(key_time,), eval=True
                )[0]
                inverted_time = (
                    inversion_point - (key_time - max_time) if flip_time else key_time
                )
                inverted_value = (
                    value_pivot - (key_value - value_pivot) if flip_value else key_value
                )
                keyframe_data.append(
                    (node, key_time, inverted_time, inverted_value, tangent_data)
                )

        for node, _, inverted_time, inverted_value, tangent_data in keyframe_data:
            cmds.setKeyframe(node, time=inverted_time, value=inverted_value)
            AnimUtils.set_tangent_info(node, inverted_time, tangent_data)

        if delete_original:
            inverted_positions = {
                (node, round(inverted_time, 3))
                for node, _, inverted_time, _, _ in keyframe_data
            }

            for node, key_time, _, _, _ in keyframe_data:
                rounded_time = round(key_time, 3)
                if (node, rounded_time) not in inverted_positions:
                    cmds.cutKey(node, time=(key_time, key_time))

    @staticmethod
    def _move_curve_keys(
        curve: str,
        time_pairs: List[Tuple[float, float]],
        tolerance: float = 1e-4,
        allow_merge: bool = False,
    ) -> int:
        """Move keys on a curve to new times, preserving value and tangents.

        Uses a read-cut-set approach to avoid keyframe collisions during retiming.

        Parameters:
            curve: The animation curve to modify.
            time_pairs: List of (old_time, new_time) tuples.
            tolerance: Tolerance for floating point comparisons.
            allow_merge: If True, keys moved to the same time will overwrite each other.
                         If False (default), keys are nudged to avoid collision.
        """

        if not time_pairs:
            return 0

        # 1. Collect data for all keys that need moving
        keys_to_move = []
        for old_time, new_time in time_pairs:
            if abs(new_time - old_time) <= tolerance:
                continue

            # Maya's keyframe query is most reliable when the time argument is a
            # (start, end) pair, even when targeting a single key time.  The
            # epsilon must stay well below one frame — a wide window (e.g. 0.5)
            # can capture a NEIGHBORING key on sub-frame animation and move
            # the wrong key's value.
            eps = max(tolerance, 1e-3)
            values = cmds.keyframe(
                curve,
                query=True,
                time=(old_time - eps, old_time + eps),
                valueChange=True,
            )
            if not values:
                continue

            tangent_data = AnimUtils._get_curve_tangent_data(curve, old_time)
            keys_to_move.append((old_time, new_time, values[0], tangent_data))

        if not keys_to_move:
            return 0

        # Add a temporary key to prevent the curve from being deleted if we cut all keys
        # Use a very large time value that is unlikely to conflict with actual animation
        temp_time = 1000000.0
        cmds.setKeyframe(curve, time=temp_time, value=0)

        # 2. Cut all old keys to clear the way (prevents overwriting/collisions)
        # Process in reverse order to maintain index stability if needed, though time-based cut is robust
        for old_time, _, _, _ in sorted(keys_to_move, key=lambda x: x[0], reverse=True):
            try:
                cmds.cutKey(curve, time=(old_time, old_time), option="keys")
            except RuntimeError as error:
                cmds.warning(f"Failed to cut key on {curve} at {old_time}: {error}")

        # 3. Set keys at new positions
        moved_count = 0
        # Collect remaining key times (keys not being moved) to avoid overwriting them.
        try:
            remaining_times = cmds.keyframe(curve, query=True, timeChange=True) or []
        except RuntimeError as error:
            # Without this list collision avoidance is blind — warn so a
            # silent overwrite of unmoved keys is at least diagnosable.
            cmds.warning(
                f"_move_curve_keys: could not query remaining keys on "
                f"{curve} ({error}); collision avoidance is degraded."
            )
            remaining_times = []

        # Use an epsilon that's small but larger than tolerance so we can escape collisions.
        epsilon = max(1e-3, tolerance * 10.0)

        def _is_time_taken(candidate: float, taken: List[float]) -> bool:
            for t in taken:
                if abs(candidate - t) <= tolerance:
                    return True
            return False

        taken_times: List[float] = list(remaining_times)

        for _, new_time, value, tangent_data in keys_to_move:
            try:
                candidate_time = float(new_time)

                # Avoid collisions with existing keys and other moved keys.
                # If the time is taken, nudge forward by a tiny epsilon until free.
                # This preserves key count and prevents segments collapsing to 0 duration.
                if not allow_merge:
                    safety = 0
                    while _is_time_taken(candidate_time, taken_times) and safety < 1000:
                        candidate_time += epsilon
                        safety += 1

                cmds.setKeyframe(curve, time=candidate_time, value=value)
                AnimUtils._apply_curve_tangent_data(curve, candidate_time, tangent_data)
                taken_times.append(candidate_time)
                moved_count += 1
            except RuntimeError as error:
                cmds.warning(f"Failed to set key on {curve} at {new_time}: {error}")

        # Remove the temporary key
        try:
            cmds.cutKey(curve, time=(temp_time, temp_time), option="keys")
        except RuntimeError as error:
            cmds.warning(
                f"_move_curve_keys: failed to remove the temporary key at "
                f"{temp_time} on {curve}: {error}"
            )

        return moved_count

    @staticmethod
    def _shift_key_times(curve: str, times: List[float], offset: float) -> int:
        """Shift exactly the given key times on a curve by a relative offset.

        Unlike a (min, max) range edit, only the listed keys move — keys
        lying between them are untouched.  Keys are processed in an order
        that prevents a moved key from colliding with a later source key.

        Parameters:
            curve (str): The animation curve (or plug) to edit.
            times (List[float]): Key times to move.
            offset (float): Relative frame offset to apply.

        Returns:
            int: Number of keys moved.
        """
        if not times or abs(offset) < 1e-9:
            return 0

        moved = 0
        for t in sorted(set(times), reverse=offset > 0):
            try:
                # option="over" lets a key travel past unmoved neighbors —
                # Maya's default move clamps at the adjacent key.
                cmds.keyframe(
                    curve,
                    edit=True,
                    time=(t, t),
                    relative=True,
                    timeChange=offset,
                    option="over",
                )
                moved += 1
            except RuntimeError as e:
                cmds.warning(f"Failed to move key at {t} on {curve}: {e}")
        return moved

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
        if not obj_keyframe_data:
            return []

        # Sort by start frame
        sorted_data = sorted(obj_keyframe_data, key=lambda x: x["start"])

        groups = []
        current_group = {
            "objects": [sorted_data[0]["obj"]],
            "keyframes": sorted_data[0]["keyframes"],
            "start": sorted_data[0]["start"],
            "end": sorted_data[0]["end"],
            "duration": sorted_data[0]["duration"],
            "curves": list(sorted_data[0].get("curves", [])),  # Preserve curves data
            "obj": sorted_data[0][
                "obj"
            ],  # Representative object for backward compatibility
            "sub_groups": [sorted_data[0]],
        }

        for i in range(1, len(sorted_data)):
            data = sorted_data[i]

            # Check if this object overlaps with the current group
            # Use strict inequality (<) to treat touching keys (end == start) as separate groups
            # This ensures sequential animations are not grouped and can be staggered independently
            if data["start"] < current_group["end"]:
                # Overlapping - add to current group
                current_group["objects"].append(data["obj"])
                # Merge keyframes
                current_group["keyframes"] = sorted(
                    set(current_group["keyframes"] + data["keyframes"])
                )
                # Merge curves from all objects in the group
                current_group["curves"].extend(data.get("curves", []))
                # Add to sub_groups
                current_group["sub_groups"].append(data)
                # Update group boundaries
                current_group["end"] = max(current_group["end"], data["end"])
                current_group["duration"] = (
                    current_group["end"] - current_group["start"]
                )
            else:
                # Not overlapping - start new group
                groups.append(current_group)
                current_group = {
                    "objects": [data["obj"]],
                    "keyframes": data["keyframes"],
                    "start": data["start"],
                    "end": data["end"],
                    "duration": data["duration"],
                    "curves": list(data.get("curves", [])),  # Preserve curves data
                    "obj": data["obj"],
                    "sub_groups": [data],
                }

        # Add the last group
        groups.append(current_group)

        return groups

    @staticmethod
    @CoreUtils.undoable
    def align_selected_keyframes(
        objects: Optional[List[str]] = None,
        target_frame: Optional[float] = None,
        use_earliest: bool = True,
    ) -> bool:
        """Aligns the starting keyframes of selected keyframes in the graph editor across multiple objects.

        This method finds the earliest (or latest) selected keyframe across all objects and shifts
        each object's selected keyframes so they start at the same frame. Only processes selected
        keyframes from the graph editor.

        Parameters:
            objects (Optional[List[str]]): Objects to align. If None, uses current selection.
            target_frame (Optional[float]): Specific frame to align to. If None, aligns to the
                                           earliest (or latest, if use_earliest=False) selection
                                           START frame among the objects.
            use_earliest (bool): If True, aligns to the earliest per-object selection start.
                                If False, aligns to the latest per-object selection start.
                                Only used when target_frame is None. Default is True.

        Returns:
            bool: True if keyframes were successfully aligned, False otherwise.

        Example:
            # Align selected keyframes to their earliest frame
            align_selected_keyframes()

            # Align selected keyframes to frame 10
            align_selected_keyframes(target_frame=10)

            # Align selected keyframes to their latest frame
            align_selected_keyframes(use_earliest=False)
        """
        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return False

        # Ensure we have transform nodes, not shape nodes
        objects = cmds.ls(objects, type="transform", flatten=True)

        if not objects:
            cmds.warning("No valid transform nodes found.")
            return False

        # Collect selected keyframe data for each object
        obj_keyframe_data = []

        for obj in objects:
            # Get animation curve nodes with selected keyframes
            curve_nodes = cmds.keyframe(obj, query=True, name=True, selected=True)

            if not curve_nodes:
                continue

            # Get the selected keyframe times
            obj_selected_times = AnimUtils.get_keyframe_times(
                curve_nodes, mode="selected", from_curves=True
            )

            if obj_selected_times:
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "curve_nodes": curve_nodes,
                        "times": obj_selected_times,
                        "start": obj_selected_times[0],
                        "end": obj_selected_times[-1],
                    }
                )

        if not obj_keyframe_data:
            cmds.warning("No selected keyframes found on any objects.")
            return False

        # Determine the alignment target frame
        if target_frame is None:
            if use_earliest:
                target_frame = min(data["start"] for data in obj_keyframe_data)
            else:
                target_frame = max(data["start"] for data in obj_keyframe_data)

        # Align each object's selected keyframes
        for data in obj_keyframe_data:
            curve_nodes = data["curve_nodes"]
            current_start = data["start"]

            # Calculate the shift amount
            shift_amount = target_frame - current_start

            if abs(shift_amount) < 1e-6:  # Skip if already aligned (within tolerance)
                continue

            # Move exactly the selected keys per curve — an object-level
            # (min, max) range edit would also drag unselected keys lying
            # inside the span and touch curves without any selection.
            for node in curve_nodes:
                sel_times = cmds.keyframe(
                    node, query=True, selected=True, timeChange=True
                )
                if sel_times:
                    AnimUtils._shift_key_times(node, sel_times, shift_amount)

        print(
            f"Aligned selected keyframes for {len(obj_keyframe_data)} object(s) to frame {target_frame:.2f}"
        )
        return True

    @staticmethod
    @CoreUtils.undoable
    def set_visibility_keys(
        objects: Optional[List[str]] = None,
        visible: bool = True,
        when: str = "start",
        offset: int = 0,
        group_overlapping: bool = False,
    ) -> int:
        """Sets visibility keyframes for objects with options for timing and grouping.

        This method creates visibility keyframes at specific points in the animation timeline,
        with support for grouping objects that have overlapping keyframe ranges.

        Parameters:
            objects (Optional[List[str]]): Objects to set visibility keys on.
                If None, uses current selection.
            visible (bool): Visibility state to set (True = visible, False = hidden). Default is True.
            when (str): When to set the visibility key. Options:
                - "start": At the start of each object's keyframe range
                - "end": At the end of each object's keyframe range
                - "both": At both start and end
                - "before_start": One frame before the start
                - "after_end": One frame after the end
                Default is "start".
            offset (int): Frame offset to apply to the keyframe timing. Positive values move
                keys later, negative values move keys earlier. Default is 0.
            group_overlapping (bool): If True, treats objects with overlapping keyframe ranges
                as a single group, setting visibility keys based on the group's combined range.
                Default is False.

        Returns:
            int: Number of visibility keyframes created.

        Example:
            # Hide objects at the start of their animation
            set_visibility_keys(visible=False, when="start")

            # Make objects visible at the end of their animation with 5 frame offset
            set_visibility_keys(visible=True, when="end", offset=5)

            # Set visibility for grouped overlapping animations
            set_visibility_keys(visible=True, when="both", group_overlapping=True)
        """
        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        # Ensure we have transform nodes
        objects = cmds.ls(objects, type="transform", flatten=True)

        if not objects:
            cmds.warning("No valid transform nodes found.")
            return 0

        # Collect keyframe data for each object
        obj_keyframe_data = []

        for obj in objects:
            keyframes = AnimUtils.get_keyframe_times(obj)

            if keyframes:
                obj_keyframe_data.append(
                    {
                        "obj": obj,
                        "keyframes": keyframes,
                        "start": keyframes[0],
                        "end": keyframes[-1],
                        "duration": keyframes[-1] - keyframes[0],
                    }
                )

        if not obj_keyframe_data:
            cmds.warning("No keyframes found on the provided objects.")
            return 0

        # Group overlapping objects if requested
        if group_overlapping:
            obj_keyframe_data = AnimUtils._group_overlapping_keyframes(
                obj_keyframe_data
            )

        # Determine visibility value (0 or 1)
        visibility_value = 1 if visible else 0

        # Set visibility keyframes based on 'when' parameter
        keys_created = 0

        for data in obj_keyframe_data:
            objects_in_group = data.get("objects", [data["obj"]])
            start_frame = data["start"]
            end_frame = data["end"]

            # Determine target frames based on 'when' parameter
            target_frames = []

            if when == "start":
                target_frames = [start_frame + offset]
            elif when == "end":
                target_frames = [end_frame + offset]
            elif when == "both":
                target_frames = [start_frame + offset, end_frame + offset]
            elif when == "before_start":
                target_frames = [start_frame - 1 + offset]
            elif when == "after_end":
                target_frames = [end_frame + 1 + offset]
            else:
                cmds.warning(f"Invalid 'when' parameter: {when}. Using 'start'.")
                target_frames = [start_frame + offset]

            # Set visibility keys for each object in the group
            for obj in objects_in_group:
                for frame in target_frames:
                    cmds.setKeyframe(
                        obj, attribute="visibility", time=frame, value=visibility_value
                    )
                    keys_created += 1

        print(
            f"Created {keys_created} visibility keyframe(s) for {len(obj_keyframe_data)} object(s)/group(s)"
        )
        return keys_created

    @staticmethod
    @CoreUtils.undoable
    def snap_keys_to_frames(
        objects: Optional[List[str]] = None,
        method: str = "nearest",
        selected_only: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
        include_driven: bool = False,
    ) -> int:
        """Snaps keyframes with decimal time values to whole frame numbers.

        This method rounds keyframe times to the nearest whole number, useful for cleaning up
        keyframes that have been scaled, retimed, or imported with fractional frame values.

        Parameters:
            objects (Optional[List[str]]): Objects to process keyframes for.
                If None, uses current selection.
            method (str): Rounding method to use. Options:
                - "nearest": Round to nearest whole number (default)
                - "floor": Always round down
                - "ceil": Always round up
                - "half_up": Round .5 and above up, below .5 down (standard rounding)
                - "preferred": Round to aesthetically pleasing numbers when very close (within ~1 frame).
                  Examples: 24→25, 19→20, 18→20, 99→100. Conservative approach.
                - "aggressive_preferred": Round to preferred numbers even when farther away.
                  Examples: 48.x→50, 73.x→75, 88.x→90, 23.x→25, 7.x→10. More aggressive rounding.
            selected_only (bool): If True, only snap selected keyframes. If False, snap all
                keyframes on the objects. Default is False.
            time_range (Optional[Tuple[float, float]]): (start_time, end_time) to limit which
                keyframes to snap. If None, processes all keyframes. Default is None.

        Returns:
            int: Number of keyframes that were snapped to whole frames.

        Example:
            # Snap all keyframes to nearest whole frame
            snap_keys_to_frames()

            # Snap only selected keyframes, always rounding down
            snap_keys_to_frames(method="floor", selected_only=True)

            # Snap keyframes in a specific time range
            snap_keys_to_frames(time_range=(10, 100))

            # Snap to preferred round numbers (conservative)
            snap_keys_to_frames(method="preferred")

            # Snap to preferred round numbers (aggressive)
            snap_keys_to_frames(method="aggressive_preferred")
        """
        # Get objects to work with
        if objects is None:
            objects = cmds.ls(selection=True)
        else:
            if isinstance(objects, str):
                objects = [objects]
            elif isinstance(objects, (list, tuple, set)):
                objects = [str(o) for o in objects]
            else:
                objects = [str(objects)]

        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        objects = cmds.ls(objects, flatten=True)

        # Optimization: Batch query all curves
        if selected_only:
            all_curves = (
                cmds.keyframe(objects, query=True, name=True, selected=True) or []
            )
        else:
            all_curves = (
                cmds.listConnections(
                    objects, type="animCurve", source=True, destination=False
                )
                or []
            )

        # Unitless (set-driven-key) curves are excluded by default — their
        # "times" are driver values, and snapping those rewrites the rig's
        # driven-key mapping.  include_driven=True opts back in deliberately.
        all_curves = _AnimUtilsInternal._filter_time_curves(
            list(set(all_curves)), include_driven
        )

        keys_snapped = 0

        for curve in all_curves:
            # Query keyframe times
            if selected_only:
                kw = {"selected": True}
            else:
                kw = {}
            if time_range:
                kw["time"] = time_range

            keyframe_times = cmds.keyframe(curve, query=True, timeChange=True, **kw)
            if not keyframe_times:
                continue

            # Collect fractional keys for this curve and snap them in one pass
            # Process in reverse time order to avoid time-shift collisions
            moves = []
            for t in keyframe_times:
                if t != int(t):
                    new_time = ptk.MathUtils.round_value(t, mode=method)
                    moves.append((t, new_time))

            if not moves:
                continue

            # Move keys in reverse order (highest time first) to avoid collisions
            moves.sort(key=lambda x: x[0], reverse=True)

            # Build a set of occupied times for collision detection.
            # Must include ALL whole-frame keys on the curve (selected AND
            # unselected, and outside any time_range) so a snapped key never
            # overwrites an unselected whole-frame key via option="over".
            # keyframe_times is the selected/time-range-filtered query and is
            # blind to those keys.
            all_curve_times = cmds.keyframe(curve, query=True, timeChange=True) or []
            occupied = set(t for t in all_curve_times if t == int(t))

            for old_time, new_time in moves:
                # Skip if another key already occupies the target time
                # (either an original whole-frame key or a previously
                # snapped key).  Moving would overwrite/merge and lose
                # the existing key's tangent data.
                if new_time in occupied:
                    continue

                try:
                    # Use keyframe -edit -timeChange to move in-place,
                    # preserving values and tangent data without
                    # delete+recreate overhead (~1 cmd vs ~8 cmds per key).
                    # option="over" lets the key travel past unmoved
                    # fractional neighbors — the default move CLAMPS at the
                    # adjacent key, leaving the key off-frame (see
                    # _shift_key_times for the same trap).
                    cmds.keyframe(
                        curve,
                        edit=True,
                        time=(old_time, old_time),
                        timeChange=new_time,
                        option="over",
                    )
                    occupied.add(new_time)
                    keys_snapped += 1
                except RuntimeError as e:
                    cmds.warning(
                        f"Failed to snap keyframe on {curve} at time {old_time}: {e}"
                    )

        if keys_snapped > 0:
            print(
                f"Snapped {keys_snapped} keyframe(s) to whole frames using '{method}' method"
            )
        else:
            print("No keyframes with decimal values found to snap")

        return keys_snapped

    @classmethod
    @CoreUtils.undoable
    def transfer_keyframes(
        cls,
        objects: List[str],
        relative: bool = False,
        transfer_tangents: bool = False,
        optimize: bool = False,
    ):
        """Transfer keyframes from the first selected object to the subsequent objects.

        If keyframes are selected in the graph editor, only those keyframes and their
        associated attributes will be transferred. Otherwise, all keyframes are transferred.

        Parameters:
            objects (List[str]): List of objects. The first object is the source, and the rest are targets.
            relative (bool): If True, apply keyframes relative to the current values of the target objects.
            transfer_tangents (bool): If True, transfer the tangent handles along with the keyframes.
            optimize (bool): If True, run optimize_keys on the source before transferring.
        """
        resolved_objects = cmds.ls(objects, long=True)
        if len(resolved_objects) < 2:
            cmds.warning("Please provide at least one source and one target object.")
            return

        source_obj = resolved_objects[0]
        target_objs = resolved_objects[1:]

        if optimize:
            cls.optimize_keys([source_obj], quiet=True)

        # Check if keyframes are selected, if not use all keyframes
        selected_curves = cmds.keyframe(
            source_obj, query=True, name=True, selected=True
        )

        if selected_curves:
            # Use only selected keyframes and their attributes
            keyframe_times = cls.get_keyframe_times(
                selected_curves, mode="selected", from_curves=True
            )
            keyframe_attributes = cls._curves_to_attributes(selected_curves, source_obj)
        else:
            # Use all animation curves and keyframes from the source object
            all_curves = cls.objects_to_curves([source_obj])
            if not all_curves:
                cmds.warning(f"No keyframes found on source object '{source_obj}'.")
                return

            keyframe_times = cls.get_keyframe_times(all_curves, from_curves=True)
            keyframe_attributes = cls._curves_to_attributes(all_curves, source_obj)

        if not keyframe_times or not keyframe_attributes:
            cmds.warning(f"No keyframes found on source object '{source_obj}'.")
            return

        # Store initial values for target objects (for relative mode)
        initial_values = {
            target: {
                attr: cmds.getAttr(f"{target}.{attr}")
                for attr in keyframe_attributes
                if cmds.attributeQuery(attr, node=str(target), exists=True)
            }
            for target in target_objs
        }

        src_str = str(source_obj)

        # Copy keyframes from source to each target
        for target_obj in target_objs:
            for attr in keyframe_attributes:
                try:
                    if not cmds.attributeQuery(attr, node=str(target_obj), exists=True):
                        cmds.warning(
                            f"Skipping attribute '{attr}': not found on '{target_obj}'."
                        )
                        continue

                    initial_value = initial_values[target_obj].get(attr)
                    if initial_value is None:
                        continue

                    src_plug = f"{src_str}.{attr}"
                    tgt_plug = f"{target_obj}.{attr}"

                    # Pre-compute the relative offset once per attribute using
                    # this attribute's own first key (not the global earliest).
                    relative_offset = 0.0
                    if relative:
                        attr_first_val = cmds.keyframe(
                            src_plug,
                            query=True,
                            time=(keyframe_times[0], keyframe_times[-1]),
                            valueChange=True,
                        )
                        if attr_first_val:
                            relative_offset = initial_value - attr_first_val[0]

                    for time in keyframe_times:
                        values = cmds.keyframe(
                            src_plug,
                            query=True,
                            time=(time,),
                            valueChange=True,
                        )
                        if values:
                            value = values[0]
                            if relative:
                                value += relative_offset

                            cmds.setKeyframe(tgt_plug, time=time, value=value)

                            if transfer_tangents:
                                tangent_info = cls.get_tangent_info(src_plug, time)
                                cls.set_tangent_info(tgt_plug, time, tangent_info)
                except Exception as e:
                    cmds.warning(
                        f"Could not transfer attribute '{attr}' to '{target_obj}': {e}"
                    )

    @staticmethod
    def parse_time_range(
        time: Union[None, int, str, Tuple, List],
    ) -> Union[Tuple[float, float], None, List]:
        """Parse time specification into a time range tuple for keyframe operations.

        This helper method handles various time specifications and converts them into
        time ranges suitable for Maya keyframe operations. Complex specifications
        (pipe-separated strings, 3+ element sequences) return a list — callers
        recurse over its elements themselves.

        Parameters:
            time (None, int, str, tuple, list): Time specification to parse.
                Accepts:
                - None or 'all': Returns None (entire timeline)
                - int: Returns (time, time) for specific frame
                - 'current': Returns (current_time, current_time)
                - 'before': Returns a range ending just before the current frame
                - 'after': Returns a range starting just after the current frame
                - tuple/list of 2 elements: Returns (start, end) range
                - tuple/list of 3+ elements: Returns list for recursive processing
                - Pipe-separated strings: Returns list for recursive processing

        Returns:
            Union[Tuple[float, float], None, List]:
                - None: Process entire timeline
                - Tuple[float, float]: (start_time, end_time) range
                - List: Multiple time values/ranges requiring recursive processing

        Example:
            # Single frame
            time_range = parse_time_range(10)  # Returns (10, 10)

            # Current frame
            time_range = parse_time_range('current')  # Returns (current_time, current_time)

            # Before current frame
            time_range = parse_time_range('before')  # Returns (-1000000, just before current)

            # Range
            time_range = parse_time_range((5, 15))  # Returns (5, 15)

            # Multiple frames (returns list for recursive processing)
            time_values = parse_time_range((1, 5, 10, 20))  # Returns [1, 5, 10, 20]

            # Pipe-separated (returns list for recursive processing)
            time_values = parse_time_range('before|current')  # Returns ['before', 'current']
        """
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

    @staticmethod
    @CoreUtils.undoable
    def delete_keys(objects=None, *attributes, time=None, channel_box_only=False):
        """Deletes keyframes for specified attributes on given objects, optionally within a time range.

        This function can delete keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. Supports flexible time specification including single frames, ranges, and
        combinations using pipe separators or sequences.

        Parameters:
            objects (list): The list of objects from which to delete keyframes.
            *attributes (str): Variable length argument list of attribute names.
                            If empty, keyframes for all attributes will be deleted (unless channel_box_only=True).
                            Can accept a list by unpacking when calling the function using *
            time (None, int, str, tuple, list): Specifies the time range for keyframe deletion.
                    Accepts:
                    - None or 'all': Delete all keyframes (entire timeline)
                    - int: Delete keyframes at specific frame
                    - 'current': Delete keyframes at current frame
                    - 'before': Delete all keyframes before current frame (excluding current)
                    - 'after': Delete all keyframes after current frame (excluding current)
                    - Pipe-separated combinations: 'before|current', 'after|current', etc.
                    - tuple/list of 2 elements: (start, end) - Delete keyframes in range
                    - tuple/list of 3+ elements: (t1, t2, t3, ...) - Delete at each frame recursively
            channel_box_only (bool): If True, only deletes keys for attributes selected in the channel box.
                                    Ignores the *attributes parameter. Default is False.

        Notes:
            - Pipe-separated strings are processed recursively (e.g., 'before|current' deletes both ranges)
            - Tuples with more than 2 elements are processed as individual frames recursively
            - All string values are case-insensitive
            - When channel_box_only=True, no attributes are selected in channel box will result in no deletion

        Example Usage:
            delete_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Delete keyframes at frame 10
            delete_keys([obj1, obj2], time='current') # Delete keyframes at current frame
            delete_keys([obj1, obj2], time='before') # Delete all keyframes before current (excluding current)
            delete_keys([obj1, obj2], time='after') # Delete all keyframes after current (excluding current)
            delete_keys([obj1, obj2], time='before|current') # Delete up to and including current
            delete_keys([obj1, obj2], time='after|current') # Delete from and after current
            delete_keys([obj1, obj2], time='before|current|after') # Delete all keyframes (equivalent to 'all')
            delete_keys([obj1, obj2], time=(5, 15)) # Delete all keyframes between frames 5 and 15
            delete_keys([obj1, obj2], time=(1, 5, 10, 20)) # Delete keyframes at frames 1, 5, 10, and 20
            delete_keys([obj1, obj2], 'rotateX', 'rotateY') # Delete all keyframes for specified attributes
            delete_keys([obj1, obj2], channel_box_only=True) # Delete only for channel box selected attributes
        """
        if objects is None:
            objects = cmds.ls(selection=True)

        objects = cmds.ls(objects, flatten=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return

        # Handle channel box filtering
        if channel_box_only:
            cb_attrs = AnimUtils._get_channel_box_attrs()
            if not cb_attrs:
                cmds.warning("No attributes selected in channel box.")
                return
            # Override attributes with channel box selection
            attributes = cb_attrs

        # Parse time range using helper method
        time_range = AnimUtils.parse_time_range(time)

        # Handle recursive cases (pipe-separated or multi-element sequences)
        if isinstance(time_range, list):
            for t in time_range:
                AnimUtils.delete_keys(
                    objects, *attributes, time=t, channel_box_only=False
                )
            return

        # Optimized: batch cutKey operations
        if attributes:
            # Build list of attribute plugs for all objects
            attr_plugs = [f"{obj}.{attr}" for obj in objects for attr in attributes]
            if attr_plugs:
                if time_range:
                    cmds.cutKey(attr_plugs, time=time_range, clear=True)
                else:
                    cmds.cutKey(attr_plugs, clear=True)
        else:
            # Delete keyframes for all attributes - pass all objects at once
            if time_range:
                cmds.cutKey(objects, time=time_range, clear=True)
            else:
                cmds.cutKey(objects, clear=True)

    @staticmethod
    def select_keys(
        objects: Optional[List[str]] = None,
        *attributes: str,
        time: Union[None, int, str, Tuple, List] = None,
        channel_box_only: bool = False,
        add_to_selection: bool = False,
    ) -> int:
        """Selects keyframes for specified attributes on given objects, optionally within a time range.

        This function selects keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. Supports flexible time specification including single frames, ranges, and
        combinations using pipe separators or sequences.

        Parameters:
            objects (list, optional): The list of objects from which to select keyframes. If None, uses selection.
            *attributes (str): Variable length argument list of attribute names.
                            If empty, keyframes for all attributes will be selected (unless channel_box_only=True).
                            Can accept a list by unpacking when calling the function using *
            time (None, int, str, tuple, list): Specifies the time range for keyframe selection.
                    Accepts:
                    - None or 'all': Select all keyframes (entire timeline)
                    - int: Select keyframes at specific frame
                    - 'current': Select keyframes at current frame
                    - 'before': Select all keyframes before current frame (excluding current)
                    - 'after': Select all keyframes after current frame (excluding current)
                    - Pipe-separated combinations: 'before|current', 'after|current', etc.
                    - tuple/list of 2 elements: (start, end) - Select keyframes in range
                    - tuple/list of 3+ elements: (t1, t2, t3, ...) - Select at each frame recursively
            channel_box_only (bool): If True, only selects keys for attributes selected in the channel box.
                                    Ignores the *attributes parameter. Default is False.
            add_to_selection (bool): If True, adds to existing keyframe selection. If False, replaces selection.
                                    Default is False.

        Returns:
            int: Number of keyframes selected.

        Notes:
            - Pipe-separated strings are processed recursively (e.g., 'before|current' selects both ranges)
            - Tuples with more than 2 elements are processed as individual frames recursively
            - All string values are case-insensitive
            - When channel_box_only=True, no attributes selected in channel box will result in no selection

        Example Usage:
            select_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Select keyframes at frame 10
            select_keys([obj1, obj2], time='current') # Select keyframes at current frame
            select_keys([obj1, obj2], time='before') # Select all keyframes before current (excluding current)
            select_keys([obj1, obj2], time='after') # Select all keyframes after current (excluding current)
            select_keys([obj1, obj2], time='before|current') # Select up to and including current
            select_keys([obj1, obj2], time='after|current') # Select from and after current
            select_keys([obj1, obj2], time='before|current|after') # Select all keyframes (equivalent to 'all')
            select_keys([obj1, obj2], time=(5, 15)) # Select all keyframes between frames 5 and 15
            select_keys([obj1, obj2], time=(1, 5, 10, 20)) # Select keyframes at frames 1, 5, 10, and 20
            select_keys([obj1, obj2], 'rotateX', 'rotateY') # Select all keyframes for specified attributes
            select_keys([obj1, obj2], channel_box_only=True) # Select only for channel box selected attributes
            select_keys([obj1, obj2], time='current', add_to_selection=True) # Add current frame keys to selection
        """
        if objects is None:
            objects = cmds.ls(selection=True)

        objects = cmds.ls(objects, flatten=True)

        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        # Handle channel box filtering
        if channel_box_only:
            cb_attrs = AnimUtils._get_channel_box_attrs()
            if not cb_attrs:
                cmds.warning("No attributes selected in channel box.")
                return 0
            # Override attributes with channel box selection
            attributes = cb_attrs

        # Parse time range using helper method
        time_range = AnimUtils.parse_time_range(time)

        # Handle recursive cases (pipe-separated or multi-element sequences)
        if isinstance(time_range, list):
            total_selected = 0
            for i, t in enumerate(time_range):
                # Only replace selection on first iteration, add to selection afterward
                add = add_to_selection or (i > 0)
                count = AnimUtils.select_keys(
                    objects,
                    *attributes,
                    time=t,
                    channel_box_only=False,
                    add_to_selection=add,
                )
                total_selected += count
            return total_selected

        # Clear selection if not adding to it
        if not add_to_selection:
            cmds.selectKey(clear=True)

        range_kw = {"time": time_range} if time_range else {}

        def _select_and_count(target: str) -> int:
            """Select keys on *target*, returning only the NEWLY selected count.

            selectKey(add=True) accumulates, so counting all selected keys
            after the call would re-count keys selected by earlier targets
            or a pre-existing selection.
            """
            before = (
                cmds.keyframe(target, query=True, selected=True, keyframeCount=True)
                or 0
            )
            cmds.selectKey(target, add=True, **range_kw)
            after = (
                cmds.keyframe(target, query=True, selected=True, keyframeCount=True)
                or 0
            )
            return max(0, after - before)

        keys_selected = 0

        for obj in objects:
            if attributes:  # Select keyframes for specified attributes
                for attr in attributes:
                    keys_selected += _select_and_count(f"{obj}.{attr}")
            else:  # Select keyframes for all attributes
                keys_selected += _select_and_count(obj)

        return keys_selected

    @staticmethod
    def get_frame_ranges(
        objects: List[str],
        precision: Optional[int] = None,
        gap: Optional[int] = None,
    ) -> Dict[str, List[Tuple[int, int]]]:
        """Calculate frame ranges for a list of objects based on their keyframes.

        This method analyzes the keyframes of given objects and determines continuous
        frame ranges. It supports optional rounding of frame numbers to a specified precision
        and allows for specifying a gap threshold to split ranges.

        Parameters:
            objects (List[str]): List of object names to analyze.
            precision (Optional[int]): Precision for rounding frame numbers. If provided,
                                    frame numbers will be rounded to the nearest multiple
                                    of this value.
            gap (Optional[int]): Maximum allowed gap between consecutive keyframes in a
                                range. If the gap between two consecutive keyframes exceeds
                                this value, a new range will be started.
        Returns:
            Dict[str, List[Tuple[int, int]]]: Dictionary mapping object names to lists of
                                            frame ranges. Each frame range is represented
                                            as a tuple (start_frame, end_frame) — rounded
                                            ints when *precision* is given, otherwise raw
                                            (possibly fractional) keyframe times. If an
                                            object has no keyframes, the range will be
                                            [(None, None)].
        """

        def round_to_nearest(value: float, base: int) -> int:
            return int(base * round(value / base))

        # Normalize once: a non-positive precision would divide by zero.
        if precision is not None and precision <= 0:
            precision = None

        frame_ranges = {}
        for obj in objects:
            keyframes = AnimUtils.get_keyframe_times(obj)
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
    def get_tied_keyframes(
        objects: Optional[List[str]] = None,
        tolerance: float = 1e-5,
    ) -> Dict[str, Dict[str, List[float]]]:
        """Detects tied (bookend) keyframes for given objects.

        Curves tied by tie_keyframes carry an exact record of the inserted
        bookend times (see TIED_KEYS_ATTR); those are returned authoritatively.
        Curves without a record (tied before the metadata existed, or created
        by hand) fall back to a conservative heuristic: an end key is tied if
        it duplicates its neighbor's value AND is itself unshaped (flat or
        stepped tangents on both sides), on a curve with at least 3 keys.
        The tangent requirement protects genuine shaped keys (e.g. an authored
        overshoot returning to the same value); the 3-key minimum protects a
        deliberate 2-key hold from being flagged in its entirety.

        This is useful for:
        - Identifying keys added by tie_keyframes()
        - Filtering out bookend keys from operations
        - Validating animation data

        Parameters:
            objects (Optional[List[str]]): Objects to check for tied keyframes.
                If None, checks all keyed objects in the scene.
            tolerance (float): Tolerance for comparing keyframe values. Two values are
                considered the same if their difference is less than this value.
                Default is 1e-5.

        Returns:
            Dict[str, Dict[str, List[float]]]: Dictionary mapping objects to their
                tied keyframes. For each object, maps attribute names (curve names) to
                lists of tied keyframe times.

        Example:
            # Get all tied keyframes in the scene
            tied_keys = AnimUtils.get_tied_keyframes()
            # Returns: {obj1: {'pCube1_translateX': [1.0, 100.0]}, obj2: {...}}

            # Get tied keyframes for selected objects
            tied_keys = AnimUtils.get_tied_keyframes(cmds.ls(selection=True))

            # Check if a specific object has tied keyframes
            tied_keys = AnimUtils.get_tied_keyframes([my_obj])
            if my_obj in tied_keys:
                print(f"Object has tied keys: {tied_keys[my_obj]}")
        """
        objects = _AnimUtilsInternal._resolve_keyed_objects(objects)
        if not objects:
            return {}

        tied_keyframes = {}

        for obj in objects:
            # Get all animation curves for this object
            keyed_curves = cmds.keyframe(obj, query=True, name=True)

            if not keyed_curves:
                continue

            obj_tied_keys = {}

            for curve in keyed_curves:
                # cmds.keyframe returns keys in time order; times and values
                # are index-aligned.
                keyframe_times = cmds.keyframe(curve, query=True, timeChange=True)
                if not keyframe_times:
                    continue

                recorded = _AnimUtilsInternal._read_tied_key_metadata(curve)
                if recorded is not None:
                    # Exact record of what tie_keyframes inserted. Keep only
                    # times that still have a key (the user may have removed
                    # or moved some since).
                    tied_times = [
                        t
                        for t in recorded
                        if any(abs(t - k) < 1e-4 for k in keyframe_times)
                    ]
                else:
                    # Heuristic fallback for curves tied before metadata
                    # existed.  Need at least 3 keys: a 2-key curve is a
                    # deliberate hold, not animation plus a bookend.
                    tied_times = []
                    if len(keyframe_times) >= 3:
                        values = cmds.keyframe(curve, query=True, valueChange=True)
                        if abs(values[0] - values[1]) < tolerance and (
                            _AnimUtilsInternal._key_is_flat_or_stepped(
                                curve, keyframe_times[0]
                            )
                        ):
                            tied_times.append(keyframe_times[0])
                        if abs(values[-1] - values[-2]) < tolerance and (
                            _AnimUtilsInternal._key_is_flat_or_stepped(
                                curve, keyframe_times[-1]
                            )
                        ):
                            tied_times.append(keyframe_times[-1])

                # Store tied keyframes for this attribute if any were found
                if tied_times:
                    obj_tied_keys[curve] = tied_times

            # Store object's tied keyframes if any were found
            if obj_tied_keys:
                tied_keyframes[obj] = obj_tied_keys

        return tied_keyframes

    @staticmethod
    @CoreUtils.undoable
    def insert_keys(
        objects: Union[str, List[str]],
        times: Iterable[float],
        tolerance: float = 1e-4,
        report: bool = False,
    ):
        """Insert keys at *times* WITHOUT changing what any curve evaluates to.

        The shape-preserving twin of :meth:`tie_keyframes`, and the difference
        is the whole point of having both. ``tie_keyframes`` gives its bookends
        FLAT tangents, which is what you want to HOLD an animation at the ends
        of a range -- and is a change to the curve everywhere near them
        (measured on a production assembly: tying at 12 shot boundaries moved
        ``USER_POS_LOC`` by up to 237 cm). ``insert_keys`` uses Maya's own
        ``setKeyframe -insert``, which computes the value and both tangents so
        the curve is bit-identical before and after; all it does is give the
        curve a key it can be CUT at.

        That is what makes a shot self-contained: with a key on each of its
        bounds, nothing outside the shot can change what plays inside it, so a
        move that repositions the shot cannot alter its content. Without it,
        moving a neighbour retimes the segment that spans the boundary -- and
        with auto tangents the change reaches back past the boundary into
        frames that never moved.

        Only times INSIDE a curve's own key range are inserted at, and that is
        a correctness rule rather than an optimisation. Outside its keys a
        curve HOLDS (constant extrapolation), so there is no shape there to
        preserve and a rigid move carries the hold with the key that produces
        it -- while asking Maya to insert there is not a shape-preserving
        insert at all: measured on Maya 2025, inserting at frame 7 on a
        ``visibility`` curve whose first key is at 8 dropped that key's STEP
        out-tangent, and the boolean it drove ramped instead of holding, so an
        object hidden until frame 23 reappeared at 16.

        Idempotent: a time a curve already has a key at is skipped, so
        re-running inserts nothing and cannot stack duplicates.

        Fully undoable, which is the other thing that separates it from
        :meth:`tie_keyframes`: this goes through ``cmds.setKeyframe`` and lands
        in Maya's undo queue, while the om2 ``addKey`` path records no
        ``MAnimCurveChange`` and leaves its bookends behind on an undo. A
        caller that inserts as the precondition for a larger edit (the shot
        respace does) therefore gets the whole thing back with one Ctrl+Z.

        Parameters:
            objects: Node(s) whose animated curves should be split.
            times: Frames to insert at.
            tolerance: How close an existing key has to be to count as
                already-there. Keys land on whole frames by default, so this
                only has to clear float noise from a previous move.
            report: Return ``[(curve, time), ...]`` for the keys inserted
                instead of a count, for a caller that has to be able to name
                them again later -- the shot system claims its own inserts so
                it can move or retire them when the bound they pin moves.

        Returns:
            The number of keys actually inserted, or the per-key list when
            *report* is set.
        """
        wanted = sorted({float(t) for t in times})
        if not wanted:
            return [] if report else 0
        curves = _AnimUtilsInternal._filter_time_curves(
            AnimUtils.objects_to_curves(objects, as_strings=True) or []
        )
        inserted = []
        for curve in curves:
            existing = cmds.keyframe(curve, query=True, timeChange=True) or []
            if len(existing) < 2:
                continue  # nothing between two keys to split
            first, last = existing[0], existing[-1]
            for t in wanted:
                if not first < t < last:
                    continue  # outside the keys: a hold, not a shape
                if any(abs(t - k) <= tolerance for k in existing):
                    continue
                try:
                    cmds.setKeyframe(curve, time=(t, t), insert=True)
                except RuntimeError:
                    continue  # locked or referenced curve — leave it as it was
                inserted.append((curve, t))
        return inserted if report else len(inserted)

    @staticmethod
    @CoreUtils.undoable
    def tie_keyframes(
        objects: List[str] = None,
        absolute: bool = False,
        padding: int = 0,
        custom_range: Optional[Tuple[float, float]] = None,
    ):
        """Ties the keyframes of all given objects (or all keyed objects in the scene if none are provided)
        by setting keyframes only on the attributes that already have keyframes,
        at the start and end of the specified animation range.

        Uses OpenMaya 2.0 (MFnAnimCurve) to freeze auto tangents on adjacent
        keys BEFORE inserting bookend keys, preventing Maya from recalculating
        them.  This eliminates the need for post-insertion tangent restoration
        and is O(curves) with only fast C++ calls per curve.

        Each curve records the inserted bookend times in a string attribute
        (TIED_KEYS_ATTR), so untie_keyframes can later remove exactly those
        keys — even bookends inside the keyed range or stacked by repeated
        tie passes.  untie_keyframes clears the record.

        Note:
            The OpenMaya key/tangent edits bypass Maya's undo queue — undo
            after a tie does NOT remove the inserted bookends.  Use
            untie_keyframes to revert.

        Parameters:
            objects (List[str], optional): List of transform node names to process.
                If None, all keyed objects in the scene will be used.
            absolute (bool, optional): If True, uses the absolute start and end keyframes
                across all objects as the range. If False, uses the scene's playback range. Default is False.
            padding (int, optional): Number of frames to extend the tie keyframes beyond the range.
                Positive values add padding (e.g., 5 = tie 5 frames before start and 5 frames after end).
                Negative values shrink the range inward. Default is 0.
            custom_range (Tuple[float, float], optional): Explicit (start, end) range to use.
                If provided, overrides absolute and scene range settings.

        Example:
            # Tie keyframes at the exact playback range (e.g., 10-100)
            tie_keyframes()  # Ties at 10 and 100

            # Add 5 frames of padding on both ends
            tie_keyframes(padding=5)  # Ties at 5 and 105 (if playback is 10-100)

            # Use with absolute=True to add padding around actual keyframes
            tie_keyframes(absolute=True, padding=10)  # Adds 10 frame hold before/after animation
        """
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        objects = _AnimUtilsInternal._resolve_keyed_objects(objects)
        if not objects:
            cmds.warning("No keyed objects found.")
            return

        # Determine the keyframe range
        if custom_range:
            start_frame, end_frame = custom_range
        elif absolute:
            range_result = AnimUtils.get_keyframe_times(objects, as_range=True)
            if range_result is None:
                cmds.warning("No keyframes found on any objects.")
                return
            start_frame, end_frame = range_result
        else:
            start_frame = cmds.playbackOptions(query=True, minTime=True)
            end_frame = cmds.playbackOptions(query=True, maxTime=True)

        tie_start_frame = start_frame - padding
        tie_end_frame = end_frame + padding

        # Collect unique animation curves.  Unitless (set-driven-key) curves
        # are excluded: bookends belong at time-range extremes, and an SDK
        # curve's x-axis is a driver value, not a frame (om2's
        # MFnAnimCurve.input() even returns a bare float there, which would
        # crash the MTime path below).
        all_keyed_curves = _AnimUtilsInternal._filter_time_curves(
            list(
                set(
                    cmds.listConnections(
                        objects, type="animCurve", source=True, destination=False
                    )
                    or []
                )
            )
        )

        if not all_keyed_curves:
            print(
                f"Keyframes tied to frames {tie_start_frame} and {tie_end_frame} for keyed attributes."
            )
            return

        # Tangent type constants
        kStep = oma2.MFnAnimCurve.kTangentStep
        kStepNext = oma2.MFnAnimCurve.kTangentStepNext
        kFlat = oma2.MFnAnimCurve.kTangentFlat
        _step_types = {kStep, kStepNext}
        _auto_types = {
            oma2.MFnAnimCurve.kTangentAuto,
            oma2.MFnAnimCurve.kTangentSmooth,
            oma2.MFnAnimCurve.kTangentClamped,
        }

        start_mtime = om2.MTime(tie_start_frame, om2.MTime.uiUnit())
        end_mtime = om2.MTime(tie_end_frame, om2.MTime.uiUnit())

        # Build MSelectionList for all curves at once
        sel = om2.MSelectionList()
        for curve_name in all_keyed_curves:
            try:
                sel.add(curve_name)
            except RuntimeError:
                continue  # Curve may have been deleted

        for i in range(sel.length()):
            dep = sel.getDependNode(i)
            fn = oma2.MFnAnimCurve(dep)
            n = fn.numKeys
            if n == 0:
                continue

            inserted_bookends = []

            first_t = fn.input(0).value
            last_t = fn.input(n - 1).value

            # Determine if curve is fully stepped (early-exit check)
            is_fully_stepped = True
            for ki in range(n):
                if fn.outTangentType(ki) not in _step_types:
                    is_fully_stepped = False
                    break

            bookend_tt = kStep if is_fully_stepped else kFlat

            # --- Start bookend ---
            if (
                abs(tie_start_frame - first_t) < 1e-4
                or fn.find(start_mtime) is not None
            ):
                # A key already exists at the bookend time (range boundary or
                # an interior key) — skip so it's never corrupted or recorded
                # (and later removed) as a tie.
                pass
            else:
                if tie_start_frame < first_t:
                    # Bookend is BEFORE the curve range.
                    # Freeze first key's auto tangents before inserting.
                    if not is_fully_stepped:
                        _AnimUtilsInternal._freeze_adjacent_tangent(
                            fn,
                            0,
                            is_in=True,
                            bookend_facing=True,
                            auto_types=_auto_types,
                            step_types=_step_types,
                        )
                        _AnimUtilsInternal._freeze_adjacent_tangent(
                            fn,
                            0,
                            is_in=False,
                            bookend_facing=False,
                            auto_types=_auto_types,
                            step_types=_step_types,
                        )
                else:
                    # Bookend is INSIDE the curve range — freeze neighbors.
                    if not is_fully_stepped:
                        adj_idx = _AnimUtilsInternal._find_adjacent_key(
                            fn, tie_start_frame, n
                        )
                        if adj_idx is not None:
                            # Key after insertion: freeze its in-tangent
                            _AnimUtilsInternal._freeze_adjacent_tangent(
                                fn,
                                adj_idx,
                                is_in=True,
                                bookend_facing=False,
                                auto_types=_auto_types,
                                step_types=_step_types,
                            )
                            # Key before insertion: freeze its out-tangent
                            if adj_idx > 0:
                                _AnimUtilsInternal._freeze_adjacent_tangent(
                                    fn,
                                    adj_idx - 1,
                                    is_in=False,
                                    bookend_facing=False,
                                    auto_types=_auto_types,
                                    step_types=_step_types,
                                )

                # Evaluate curve value at the bookend time and insert
                start_val = fn.evaluate(start_mtime)
                fn.addKey(start_mtime, start_val, bookend_tt, bookend_tt)
                inserted_bookends.append(tie_start_frame)

            # --- End bookend ---
            # Re-read numKeys since we may have added a key
            n = fn.numKeys
            # Re-read last_t from the actual last key
            last_t = fn.input(n - 1).value

            if abs(tie_end_frame - last_t) < 1e-4 or fn.find(end_mtime) is not None:
                # A key already exists at the bookend time — skip (see start)
                pass
            else:
                if tie_end_frame > last_t:
                    # Bookend is AFTER the curve range.
                    last_idx = n - 1
                    if not is_fully_stepped:
                        _AnimUtilsInternal._freeze_adjacent_tangent(
                            fn,
                            last_idx,
                            is_in=False,
                            bookend_facing=True,
                            auto_types=_auto_types,
                            step_types=_step_types,
                        )
                        _AnimUtilsInternal._freeze_adjacent_tangent(
                            fn,
                            last_idx,
                            is_in=True,
                            bookend_facing=False,
                            auto_types=_auto_types,
                            step_types=_step_types,
                        )
                else:
                    # Bookend is INSIDE the curve range — freeze neighbors.
                    if not is_fully_stepped:
                        adj_idx = _AnimUtilsInternal._find_adjacent_key(
                            fn, tie_end_frame, n
                        )
                        if adj_idx is not None:
                            _AnimUtilsInternal._freeze_adjacent_tangent(
                                fn,
                                adj_idx,
                                is_in=True,
                                bookend_facing=False,
                                auto_types=_auto_types,
                                step_types=_step_types,
                            )
                            if adj_idx > 0:
                                _AnimUtilsInternal._freeze_adjacent_tangent(
                                    fn,
                                    adj_idx - 1,
                                    is_in=False,
                                    bookend_facing=False,
                                    auto_types=_auto_types,
                                    step_types=_step_types,
                                )

                end_val = fn.evaluate(end_mtime)
                fn.addKey(end_mtime, end_val, bookend_tt, bookend_tt)
                inserted_bookends.append(tie_end_frame)

            # Record exactly which keys were inserted so untie_keyframes can
            # remove them without relying on value-equality guesswork.
            _AnimUtilsInternal._write_tied_key_metadata(fn.name(), inserted_bookends)

        print(
            f"Keyframes tied to frames {tie_start_frame} and {tie_end_frame} for keyed attributes."
        )

    @staticmethod
    @CoreUtils.undoable
    def untie_keyframes(
        objects: List[str] = None,
    ) -> Dict[str, Dict[str, List[float]]]:
        """Removes bookend keyframes added by tie_keyframes, but preserves genuine animation keys.

        Curves tied by tie_keyframes carry an exact record of the inserted
        bookend times, so those keys are removed precisely — including
        bookends that landed inside the keyed range and bookends stacked by
        multiple tie passes.  Curves without a record fall back to the
        conservative heuristic in get_tied_keyframes (value-duplicate end key
        with flat/stepped tangents, on a curve with at least 3 keys), so
        genuine shaped keys and deliberate 2-key holds are never deleted.

        Parameters:
            objects (List[str], optional): List of transform node names to process.
                If None, all keyed objects in the scene will be used.

        Returns:
            Dict[str, Dict[str, List[float]]]: The removed keys, mapping each
                object to {curve_name: [removed_times]}.  Empty if nothing
                was removed.

        Example:
            # Remove bookend keys added by tie_keyframes
            untie_keyframes()

            # Remove bookend keys for specific objects
            untie_keyframes([obj1, obj2])
        """
        # Use the helper method to detect tied keyframes
        tied_keyframes = AnimUtils.get_tied_keyframes(objects)

        keys_removed = 0

        # Remove all detected tied keyframes
        for obj, attr_dict in tied_keyframes.items():
            for attr, tied_times in attr_dict.items():
                for time in tied_times:
                    cmds.cutKey(attr, time=(time, time), clear=True)
                    keys_removed += 1

        # The scene is untied now — drop the bookend records so stale entries
        # can't linger on these objects' curves.
        for obj in _AnimUtilsInternal._resolve_keyed_objects(objects):
            for curve in cmds.keyframe(obj, query=True, name=True) or []:
                _AnimUtilsInternal._clear_tied_key_metadata(curve)

        if keys_removed > 0:
            print(f"Removed {keys_removed} bookend keyframe(s).")
        else:
            print("No bookend keyframes found to remove.")

        return tied_keyframes

    @staticmethod
    def create_animation_layer(
        name: str = "AnimLayer",
        override: bool = True,
        additive: bool = False,
        attributes: Optional[List[str]] = None,
        objects: Optional[List[str]] = None,
        weight: float = 1.0,
        mute: bool = False,
        solo: bool = False,
        lock: bool = False,
        preferred: bool = True,
        parent: Optional[str] = None,
        unique_name: bool = True,
        timestamp_suffix: bool = False,
        color: Optional[Tuple[float, float, float]] = None,
    ) -> str:
        """Create an animation layer with flexible configuration options.

        Creates a new animation layer and optionally adds attributes/objects to it.
        Handles unique naming, hierarchy, and layer properties.

        Parameters:
            name: Base name for the layer. Will be made unique if unique_name=True.
            override: If True, creates an override layer (replaces base animation).
                If False, creates an additive layer (adds to base animation).
            additive: Explicit additive mode. If True, sets override=False.
            attributes: List of attribute paths (e.g., ["pCube1.tx", "pCube1.ry"])
                to add to the layer. These attributes will be animatable on this layer.
                CAUTION: do NOT pre-register attributes you are about to
                ``bakeResults(destinationLayer=...)`` onto this same layer —
                the bake then writes a flat constant (the value live at
                registration time) at every sampled frame instead of the true
                curve. Hand bakeResults the empty layer and let it wire the
                attributes itself (see SmartBake._create_override_layer).
            objects: List of objects to add all keyable attributes from.
                Shorthand for adding all keyable attrs of each object.
            weight: Layer weight (0.0 to 1.0). Default is 1.0 (full influence).
            mute: If True, mute the layer (disable its effect).
            solo: If True, solo the layer (only this layer affects playback).
            lock: If True, lock the layer (prevent editing).
            preferred: If True, set as the preferred/selected layer for editing.
            parent: Name of parent layer. If None, uses the root (BaseAnimation).
            unique_name: If True, ensures layer name is unique by appending
                a counter if necessary (e.g., "MyLayer", "MyLayer_1", "MyLayer_2").
            timestamp_suffix: If True, appends timestamp to name for uniqueness
                (e.g., "MyLayer_20260203_143052"). Overrides unique_name counter.
            color: Optional RGB tuple (0-1 range) for layer display color in editor.

        Returns:
            The actual name of the created layer (may differ from input if
            unique_name=True and name collision occurred).

        Raises:
            RuntimeError: If layer creation fails.

        Example:
            >>> # Simple override layer
            >>> layer = AnimUtils.create_animation_layer("BakeLayer", override=True)

            >>> # Additive layer with specific attributes
            >>> layer = AnimUtils.create_animation_layer(
            ...     "Offset",
            ...     additive=True,
            ...     attributes=["pCube1.translateY", "pCube1.rotateZ"],
            ...     weight=0.5,
            ... )

            >>> # Layer for multiple objects
            >>> layer = AnimUtils.create_animation_layer(
            ...     "CharacterLayer",
            ...     objects=["joint1", "joint2", "joint3"],
            ...     timestamp_suffix=True,
            ... )

            >>> # Muted layer for comparison
            >>> layer = AnimUtils.create_animation_layer(
            ...     "Alternate", mute=True, preferred=False
            ... )
        """
        import time as time_module

        # Handle additive shorthand
        if additive:
            override = False

        # Build unique layer name
        layer_name = name
        if timestamp_suffix:
            timestamp = time_module.strftime("%Y%m%d_%H%M%S")
            layer_name = f"{name}_{timestamp}"

        if unique_name:
            base_name = layer_name
            counter = 1
            while cmds.objExists(layer_name):
                layer_name = f"{base_name}_{counter}"
                counter += 1

        # Create the layer
        layer = cmds.animLayer(layer_name, override=override)

        # Set layer properties
        if weight != 1.0:
            cmds.animLayer(layer, edit=True, weight=weight)

        if mute:
            cmds.animLayer(layer, edit=True, mute=True)

        if solo:
            cmds.animLayer(layer, edit=True, solo=True)

        if lock:
            cmds.animLayer(layer, edit=True, lock=True)

        if preferred:
            cmds.animLayer(layer, edit=True, preferred=True)

        if parent:
            cmds.animLayer(layer, edit=True, parent=parent)

        if color:
            # animLayer has no color flag; best-effort via the node's
            # attribute when present.  Warn instead of silently no-oping so
            # a caller relying on the color knows it didn't apply.
            try:
                if cmds.attributeQuery("ghostColor", node=layer, exists=True):
                    cmds.setAttr(f"{layer}.ghostColor", *color, type="float3")
                else:
                    cmds.warning(
                        f"create_animation_layer: '{layer}' has no color "
                        f"attribute; 'color' was ignored."
                    )
            except RuntimeError as e:
                cmds.warning(
                    f"create_animation_layer: could not set color on '{layer}': {e}"
                )

        # Add attributes from objects (all keyable attributes)
        if objects:
            for obj in objects:
                if not cmds.objExists(obj):
                    continue
                keyable_attrs = cmds.listAttr(obj, keyable=True) or []
                for attr in keyable_attrs:
                    attr_path = f"{obj}.{attr}"
                    try:
                        cmds.animLayer(layer, edit=True, attribute=attr_path)
                    except RuntimeError:
                        pass  # Attribute may not be animatable

        # Add explicit attributes
        if attributes:
            for attr_path in attributes:
                try:
                    cmds.animLayer(layer, edit=True, attribute=attr_path)
                except RuntimeError:
                    pass  # Attribute may not exist or not be animatable

        return layer

    @staticmethod
    def get_animation_layers(
        include_base: bool = False,
        muted_only: bool = False,
        active_only: bool = False,
    ) -> List[str]:
        """Get all animation layers in the scene.

        Parameters:
            include_base: If True, includes the BaseAnimation layer.
            muted_only: If True, returns only muted layers.
            active_only: If True, returns only non-muted layers.

        Returns:
            List of animation layer names.
        """
        layers = cmds.ls(type="animLayer") or []

        if not include_base:
            layers = [lyr for lyr in layers if lyr != "BaseAnimation"]

        if muted_only:
            layers = [
                lyr for lyr in layers if cmds.animLayer(lyr, query=True, mute=True)
            ]
        elif active_only:
            layers = [
                lyr for lyr in layers if not cmds.animLayer(lyr, query=True, mute=True)
            ]

        return layers

    @staticmethod
    def copy_keys(
        objects=None,
        mode: str = "auto",
        resolution_order: Optional[Tuple[str, ...]] = None,
        tangent_detail: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Copy attribute values from objects for later pasting as keys.

        Parameters:
            objects: Objects to copy from. Defaults to selection.
            mode: Copy mode — one of:
                - ``"auto"`` (default): Smart cascade via
                  :meth:`_resolve_keys` — uses selected keys if any
                  (narrowed to Channel Box highlights when they overlap,
                  otherwise all selected keys are used), falls back to
                  Channel Box values, then to all keyed attributes at
                  the current frame.
                - ``"current_frame"``: Copy all keyed attribute values at the
                  current time for each object.
                - ``"selected"``: Copy values of keys currently selected in the
                  Graph Editor.
                - ``"channel_box"``: Copy values of attributes highlighted in
                  the Channel Box.
            resolution_order: Strategies to try for ``"auto"`` mode.
                Default: ``("selected", "channel_box", "current_frame")``.
                See :meth:`_resolve_keys` for available strategies.
            tangent_detail: When True, multi-key data additionally
                captures tangent angles and weights per key, plus
                pre/post infinity types per curve.  This produces
                lossless snapshots suitable for undo/take management.
                Only affects ``"selected"`` mode.

        Returns:
            Nested dict ``{object_name: {attr: data, ...}, ...}``.

            For ``"current_frame"`` and ``"channel_box"`` modes, *data* is a
            single ``float``.

            For ``"selected"`` mode, *data* is a list of key dicts::

                [{"time": float, "value": float,
                  "inTangentType": str, "outTangentType": str}, ...]

            When *tangent_detail* is True each key dict also contains
            ``"inAngle"``, ``"outAngle"``, ``"inWeight"``,
            ``"outWeight"`` (floats), and the attribute entry gains
            ``"preInfinity"`` and ``"postInfinity"`` (strings).

            Empty dict when nothing could be copied.
        """
        if objects is None:
            objects = cmds.ls(selection=True)
        objects = cmds.ls(objects, flatten=True, long=True)
        if not objects:
            cmds.warning("No objects specified or selected.")
            return {}

        # Resolve "auto" into the best concrete mode, with optional CB filter.
        # Pass pre-resolved *objects* so _resolve_keys skips redundant cmds.ls.
        cb_filter: Optional[Set[str]] = None
        if mode == "auto":
            resolved = AnimUtils._resolve_keys(
                objects,
                mode="auto",
                resolution_order=resolution_order
                or ("selected", "channel_box", "current_frame"),
            )
            mode = resolved["mode"]
            cb_filter = resolved["cb_attrs"]

        result: Dict[str, Dict[str, float]] = {}

        if mode == "current_frame":
            current = cmds.currentTime(query=True)
            for obj in objects:
                obj_str = str(obj)
                curves = (
                    cmds.listConnections(
                        obj_str, type="animCurve", source=True, destination=False
                    )
                    or []
                )
                if not curves:
                    continue
                obj_data: Dict[str, float] = {}
                for crv in curves:
                    # Get the attribute this curve drives
                    conns = (
                        cmds.listConnections(
                            crv, destination=True, source=False, plugs=True
                        )
                        or []
                    )
                    for plug in conns:
                        attr = plug.split(".")[-1]
                        # A shared curve can also drive plugs on OTHER
                        # nodes — only read attrs that exist on this one.
                        if not cmds.attributeQuery(attr, node=obj_str, exists=True):
                            continue
                        try:
                            obj_data[attr] = cmds.getAttr(
                                f"{obj_str}.{attr}", time=current
                            )
                        except RuntimeError:
                            continue
                if obj_data:
                    result[obj_str] = obj_data

        elif mode == "selected":
            # Get curves with selected keys, scoped to the given objects —
            # the scene-wide graph-editor selection may include curves that
            # belong to unrelated objects.
            sel_curves = cmds.keyframe(query=True, selected=True, name=True) or []
            if sel_curves and objects:
                obj_curve_set = set(AnimUtils.objects_to_curves(objects))
                sel_curves = [c for c in sel_curves if c in obj_curve_set]
            if not sel_curves:
                cmds.warning("No keys selected in the Graph Editor.")
                return {}

            def _collect_selected_keys(curves, attr_filter):
                """Collect key data from *curves*, optionally filtering by attrs."""
                collected: Dict[str, Dict[str, Any]] = {}
                for crv in curves:
                    conns = (
                        cmds.listConnections(
                            crv, destination=True, source=False, plugs=True
                        )
                        or []
                    )
                    if not conns:
                        continue
                    plug = conns[0]
                    parts = plug.split(".", 1)
                    obj_name = parts[0]
                    attr = parts[1] if len(parts) > 1 else ""
                    if not attr:
                        continue
                    # attr_filter carries Channel Box SHORT names — match
                    # against all spellings of this plug's attribute.
                    if attr_filter is not None and AnimUtils._plug_attr_names(
                        plug
                    ).isdisjoint(attr_filter):
                        continue
                    times = (
                        cmds.keyframe(crv, query=True, selected=True, timeChange=True)
                        or []
                    )
                    values = (
                        cmds.keyframe(crv, query=True, selected=True, valueChange=True)
                        or []
                    )
                    if times and values:
                        key_list = []
                        for t, v in zip(times, values):
                            itt = cmds.keyTangent(
                                crv, q=True, time=(t, t), inTangentType=True
                            )
                            ott = cmds.keyTangent(
                                crv, q=True, time=(t, t), outTangentType=True
                            )
                            kd = {
                                "time": t,
                                "value": v,
                                "inTangentType": itt[0] if itt else "auto",
                                "outTangentType": ott[0] if ott else "auto",
                            }
                            if tangent_detail:
                                ia = cmds.keyTangent(
                                    crv, q=True, time=(t, t), inAngle=True
                                )
                                oa = cmds.keyTangent(
                                    crv, q=True, time=(t, t), outAngle=True
                                )
                                iw = cmds.keyTangent(
                                    crv, q=True, time=(t, t), inWeight=True
                                )
                                ow = cmds.keyTangent(
                                    crv, q=True, time=(t, t), outWeight=True
                                )
                                kd["inAngle"] = ia[0] if ia else 0.0
                                kd["outAngle"] = oa[0] if oa else 0.0
                                kd["inWeight"] = iw[0] if iw else 1.0
                                kd["outWeight"] = ow[0] if ow else 1.0
                            key_list.append(kd)
                        attr_entry = key_list
                        if tangent_detail:
                            pre = cmds.setInfinity(plug, q=True, preInfinite=True)
                            post = cmds.setInfinity(plug, q=True, postInfinite=True)
                            attr_entry = {
                                "keys": key_list,
                                "preInfinity": pre[0] if pre else "constant",
                                "postInfinity": post[0] if post else "constant",
                            }
                        collected.setdefault(obj_name, {})[attr] = attr_entry
                return collected

            result = _collect_selected_keys(sel_curves, cb_filter)
            # If the CB filter eliminated everything, fall back to all
            # selected keys so the user isn't silently blocked.
            if not result and cb_filter is not None:
                result = _collect_selected_keys(sel_curves, None)

        else:  # channel_box (default)
            attrs = AnimUtils._get_channel_box_attrs()
            if attrs:
                for obj in objects:
                    obj_data = {}
                    for attr in attrs:
                        try:
                            obj_data[attr] = cmds.getAttr(f"{obj}.{attr}")
                        except (RuntimeError, ValueError):
                            continue  # Attr absent/unreadable on this object.
                    if obj_data:
                        result[str(obj)] = obj_data

        return result

    @staticmethod
    @CoreUtils.undoable
    def paste_keys(
        objects=None,
        copied_data: Optional[Dict[str, Dict[str, Any]]] = None,
        target_time=None,
        match_source: bool = True,
        refresh_channel_box: bool = True,
        **kwargs,
    ) -> int:
        """Paste previously copied attribute values as keyframes.

        Supports two data formats produced by :meth:`copy_keys`:

        * **Scalar** (``current_frame`` / ``channel_box``): a single float
          per attribute is keyed at *target_time*.
        * **Multi-key** (``selected``): a list of key dicts with time,
          value and tangent types.  Keys are offset so the earliest
          copied time aligns with *target_time* and tangent types are
          applied exactly as stored.

        Parameters:
            objects: Objects to paste onto. Defaults to selection.
            copied_data: Nested dict from :meth:`copy_keys`.
            target_time: Frame at which to paste.  Defaults to current time.
                For multi-key data the earliest copied key aligns here;
                later keys are offset accordingly.
            match_source: When True (default), each target object is
                matched to its corresponding source entry in *copied_data*
                by name.  When False, all attribute data from every source
                in *copied_data* is merged and applied to each target
                object — useful for pasting one object's animation onto
                a different object.
            refresh_channel_box: Update the Channel Box after keying.
            **kwargs: Extra flags forwarded to ``cmds.setKeyframe``
                (e.g. ``breakdown``, ``hierarchy``, ``shape``,
                ``controlPoints``, ``animLayer``).

        Returns:
            Number of objects that received keys.
        """
        if not copied_data:
            cmds.warning("No copied data to paste.")
            return 0

        if objects is None:
            objects = cmds.ls(selection=True)
        objects = cmds.ls(objects, flatten=True, long=True)
        if not objects:
            cmds.warning("No objects specified or selected.")
            return 0

        if target_time is None:
            target_time = cmds.currentTime(query=True)

        # When not matching by name, merge all source attrs into one dict
        merged_attrs: Optional[Dict[str, Any]] = None
        if not match_source:
            merged_attrs = {}
            for src_attrs in copied_data.values():
                merged_attrs.update(src_attrs)

        keys_set = 0

        for obj in objects:
            if not match_source:
                obj_attrs = merged_attrs
            else:
                obj_name = str(obj)
                short_name = obj_name.split("|")[-1]

                # Try to find matching stored data
                obj_attrs = copied_data.get(obj_name)
                if not obj_attrs:
                    obj_attrs = copied_data.get(short_name)
                if not obj_attrs:
                    for stored_name in copied_data:
                        if stored_name.split("|")[-1] == short_name:
                            obj_attrs = copied_data[stored_name]
                            break

            if obj_attrs:
                for attr, data in obj_attrs.items():
                    plug = f"{obj}.{attr}"
                    # Unwrap tangent_detail envelope if present.
                    infinity = None
                    if isinstance(data, dict) and "keys" in data:
                        infinity = (
                            data.get("preInfinity", "constant"),
                            data.get("postInfinity", "constant"),
                        )
                        data = data["keys"]
                    if isinstance(data, list):
                        # --- Multi-key paste (selected mode) ---
                        if not data:
                            continue
                        # Multi-key blocks paste at a single anchor time —
                        # take the first entry of a list target.
                        anchor = target_time
                        if isinstance(anchor, (list, tuple)):
                            if len(anchor) > 1:
                                cmds.warning(
                                    "paste_keys: multi-key data pastes at a "
                                    "single time; using the first target time."
                                )
                            anchor = anchor[0]
                        base_time = data[0]["time"]
                        offset = float(anchor) - base_time

                        for kd in data:
                            t = kd["time"] + offset
                            v = kd["value"]
                            itt = kd.get("inTangentType", "auto")
                            ott = kd.get("outTangentType", "auto")
                            kw = dict(
                                time=t,
                                value=v,
                                # setKeyframe rejects some tangent types
                                # keyTangent accepts ("fixed" on either side,
                                # "step" in) — remap those only here; the
                                # set_tangent_info pass below restores the
                                # stored types verbatim.
                                inTangentType=_SETKEY_IN_TANGENT_REMAP.get(itt, itt),
                                outTangentType=_SETKEY_OUT_TANGENT_REMAP.get(ott, ott),
                            )
                            kw.update(kwargs)
                            cmds.setKeyframe(plug, **kw)
                            # Re-assert the ORIGINAL stored tangent data via
                            # set_tangent_info: angles/weights first, types
                            # last.  The final type pass keeps auto types
                            # (spline/auto/clamped) as themselves instead of
                            # the implicit "fixed" an angle edit causes, and
                            # re-asserting a stored "fixed" keeps its exact
                            # angle (remapping it to "auto" — the old
                            # behavior — let Maya recalculate the handle).
                            tangent_info = {
                                # "step" is out-tangent-only; its in-side
                                # form is "stepnext".
                                "inTangentType": _KEYTANGENT_IN_TANGENT_REMAP.get(
                                    itt, itt
                                ),
                                "outTangentType": ott,
                            }
                            if "inAngle" in kd:
                                tangent_info.update(
                                    inAngle=kd["inAngle"],
                                    outAngle=kd["outAngle"],
                                    inWeight=kd["inWeight"],
                                    outWeight=kd["outWeight"],
                                )
                            try:
                                AnimUtils.set_tangent_info(plug, t, tangent_info)
                            except RuntimeError as e:
                                cmds.warning(
                                    f"paste_keys: tangent restore on {plug} "
                                    f"at {t} failed: {e}"
                                )
                        # Restore infinity types when present (undoable,
                        # unlike an MFnAnimCurve edit).
                        if infinity:
                            try:
                                cmds.setInfinity(
                                    plug,
                                    preInfinite=infinity[0],
                                    postInfinite=infinity[1],
                                )
                            except RuntimeError as e:
                                cmds.warning(
                                    f"paste_keys: could not restore infinity "
                                    f"on {plug}: {e}"
                                )
                    else:
                        # --- Scalar paste (current_frame / channel_box) ---
                        times = (
                            [target_time]
                            if not isinstance(target_time, (list, tuple))
                            else list(target_time)
                        )
                        for t in times:
                            AnimUtils._set_key_preserving_tangents(
                                plug, t, data, **kwargs
                            )
                keys_set += 1

        if refresh_channel_box:
            mel.eval("channelBoxCommand -update;")

        return keys_set

    @staticmethod
    def delete_animation_layer(
        layer: str,
        merge_to_base: bool = False,
    ) -> bool:
        """Delete an animation layer.

        Parameters:
            layer: Name of the layer to delete.
            merge_to_base: If True, merges the layer's animation to the base
                layer before deleting. If False, animation is discarded.

        Returns:
            True if layer was deleted successfully, False otherwise.
        """
        if not cmds.objExists(layer):
            return False

        try:
            if merge_to_base:
                # animLayer -attribute lists the plugs that live on the
                # layer — those are the bake targets.  (-affectedLayers is a
                # selection-based query and returns LAYER names, not plugs.)
                layer_plugs = cmds.animLayer(layer, query=True, attribute=True) or []
                if layer_plugs:
                    cmds.bakeResults(
                        layer_plugs,
                        destinationLayer="BaseAnimation",
                        removeBakedAttributeFromLayer=True,
                    )
            cmds.delete(layer)
            return True
        except RuntimeError as e:
            cmds.warning(f"delete_animation_layer: failed on '{layer}': {e}")
            return False

    @staticmethod
    def fit_playback_range(
        objects=None,
        padding: float = 0,
    ) -> bool:
        """Set the playback range to encompass keyframes on all (or given) scene objects.

        Queries every keyed object in the scene (or a supplied list) and adjusts
        Maya's playback-range and animation-range to span from the earliest to
        the latest keyframe, optionally padded.

        Parameters:
            objects: Objects to consider. If None, every time-based animation
                curve in the scene is considered (including layered animation).
            padding: Extra frames to add before the first and after the last key.

        Returns:
            True if the range was updated, False if no keyframes were found.
        """
        if objects is None:
            # Query the range from TIME-based curves directly — resolving
            # curves to transforms misses animation routed through anim
            # layers (blend nodes), and driven-key curves (animCurveU*) have
            # driver-value inputs, not times, so they must be excluded.
            curves = cmds.ls(
                type=["animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"]
            )
            if not curves:
                cmds.warning("No animation curves in the scene.")
                return False
            result = AnimUtils.get_keyframe_times(
                curves, from_curves=True, as_range=True
            )
        else:
            result = AnimUtils.get_keyframe_times(objects, as_range=True)
        if result is None:
            cmds.warning("No keyframes found on the given objects.")
            return False

        start, end = result
        start -= padding
        end += padding

        cmds.playbackOptions(
            minTime=start,
            maxTime=end,
            animationStartTime=start,
            animationEndTime=end,
        )
        return True

    # ---- key selection readers ---------------------------------------------

    @staticmethod
    def get_selected_key_times(
        curves: Optional[List[str]] = None,
    ) -> Dict[str, List[float]]:
        """Graph Editor key selection as ``{curve: [times]}``.

        Per curve, because a selection is per key: the user may have picked
        frames 10-30 on ``translateX`` and 15-40 on ``rotateY``.

        Parameters:
            curves: Restrict to these curve nodes (the scene-wide selection can
                include curves of unrelated objects).  ``None`` = every curve
                holding a selected key.

        Returns:
            Sorted, de-duplicated key times per curve; curves with no selected
            key are absent.
        """
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

    @staticmethod
    def get_timeline_selection() -> Optional[Tuple[float, float]]:
        """The time slider's drag-selected range, or ``None`` when nothing is selected.

        Maya reports a one-frame "range" at the current time when there is no
        drag selection; that is treated as no selection.
        """
        try:
            slider = mel.eval("$_tmp = $gPlayBackSlider")
            lo, hi = cmds.timeControl(slider, query=True, rangeArray=True)
        except RuntimeError:
            # No time slider: batch / mayapy never defines the global.
            return None
        if hi - lo <= 1.0:
            return None
        # rangeArray's end is exclusive (one past the last selected frame).
        return float(lo), float(hi - 1)

    # ---- transient preview layer -------------------------------------------

    @staticmethod
    def create_preview_layer(
        sources: Dict[str, str],
        gate: Optional[Tuple[float, float]] = None,
        name: str = "previewLayer",
    ) -> str:
        """Play foreign curves on an object's plugs through a throwaway override layer.

        The plugs' own animation is untouched: an override layer at the top of
        the stack wins at weight 1 (no solo needed — soloing would also silence
        the user's own layers), and deleting the layer restores the direct
        curve→plug connections.  Used by the key stash to preview a stored
        clip without retrieving it; general enough to preview any curve set.

        Parameters:
            sources: ``{plug: anim_curve}`` — each curve's keys are pasted onto
                the layer's curve for that plug (the source is only read).
            gate: ``(start, end)``.  When given, the layer's weight is keyed to
                1 inside the range and 0 outside (stepped), so the base
                animation plays up to the range, the preview takes over, and the
                base resumes — the in-context view.  Without it the layer holds
                its end poses outside its keys (override extrapolation).
            name: Layer base name; made unique.

        Returns:
            The layer node name — hand it to :meth:`remove_preview_layer`.

        Raises:
            ValueError: When no source curve holds a key.
        """
        # ``preferred=False``: a preferred layer becomes the target of the
        # user's own setKeyframe — a preview must never capture their keys.
        layer = AnimUtils.create_animation_layer(
            name, override=True, unique_name=True, preferred=False
        )
        pasted = 0
        for plug, src in sources.items():
            times = cmds.keyframe(src, query=True, timeChange=True) or []
            if not times:
                continue
            node, _, attr = str(plug).partition(".")
            cmds.animLayer(layer, edit=True, attribute=plug)
            # Membership alone spawns no layer curve; one key on the layer does.
            # Diffing the layer's curve list around it is the only unambiguous
            # way to learn WHICH curve is this plug's (verified Maya 2025).
            before = set(cmds.animLayer(layer, query=True, animCurves=True) or [])
            first_value = cmds.keyframe(
                src, query=True, valueChange=True, time=(times[0], times[0])
            )
            cmds.setKeyframe(
                node,
                attribute=attr,
                time=times[0],
                value=first_value[0] if first_value else 0.0,
                animLayer=layer,
            )
            after = set(cmds.animLayer(layer, query=True, animCurves=True) or [])
            new = after - before
            if len(new) != 1:
                cmds.warning(f"create_preview_layer: no layer curve spawned for {plug}")
                continue
            layer_curve = new.pop()
            cmds.copyKey(src, time=(times[0], times[-1]))
            cmds.pasteKey(layer_curve, option="replaceCompletely")
            pasted += len(times)
        if not pasted:
            cmds.delete(layer)
            raise ValueError("create_preview_layer: no source curve holds a key")
        if gate is not None:
            start, end = float(gate[0]), float(gate[1])
            for t, w in ((start - 1, 0.0), (start, 1.0), (end, 1.0), (end + 1, 0.0)):
                cmds.setKeyframe(
                    layer, attribute="weight", time=t, value=w, outTangentType="step"
                )
        return layer

    @staticmethod
    def remove_preview_layer(layer: Optional[str]) -> bool:
        """Delete a layer made by :meth:`create_preview_layer`; ``True`` if it existed.

        Deleting the layer removes its blend nodes and reconnects each plug's
        base curve directly (verified Maya 2025) — nothing is merged down.
        """
        if not layer or not cmds.objExists(layer):
            return False
        if cmds.nodeType(layer) != "animLayer":
            raise ValueError(f"remove_preview_layer: {layer!r} is not an animLayer")
        cmds.delete(layer)
        return True


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
