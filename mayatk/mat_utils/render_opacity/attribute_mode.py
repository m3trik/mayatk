# !/usr/bin/python
# coding=utf-8
import re
from typing import Dict, List, Optional, Tuple
import pythontk as ptk

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None  # type: ignore[assignment]

from mayatk.node_utils.attributes._attributes import Attributes


class OpacityAttributeMode(ptk.LoggingMixin):
    """
    Implements the 'attribute' mode for RenderOpacity.

    This mode adds a custom ``opacity`` float attribute (0-1) to each object's
    transform.  Recommended for per-object control in Game Engines.

    Visibility is managed through **direct keyframe mirroring** rather than
    a condition-node driver.  When opacity is keyed (either manually or via
    the behavior system), a matching visibility keyframe is set at the same
    time.  This produces real animation curves on both channels that FBX can
    export and game engines can read natively.

    The behavior system in ``shots.behaviors`` performs this mirroring
    automatically: when a template targets ``visibility`` and the object
    has an ``opacity`` attribute, the behavior keys ``opacity`` and mirrors
    the value to ``visibility`` at the same time.
    """

    ATTR_NAME = "opacity"
    """Custom attribute name used in ``"attribute"`` mode."""

    _VIS_DRIVER_RE = re.compile(r"_VisDriver\d*$")
    """Matches legacy condition node names including Maya auto-incremented variants."""

    @classmethod
    def create(cls, objects) -> Dict[str, Dict]:
        """Add 'opacity' attribute on each transform (no keyframes).

        No condition-node driver is created.  Visibility mirroring is
        handled by the behavior system or by calling
        :meth:`sync_visibility_from_opacity` explicitly after keying.
        """
        results = {}

        Attributes.apply_preset("opacity", objects)

        for obj in cmds.ls(objects):
            # Remove any legacy condition-node driver left from older versions
            cls._remove_legacy_vis_driver(obj)

            short = obj.split("|")[-1].split(":")[-1]
            results[short] = {"attrs_created": [f"{short}.{cls.ATTR_NAME}"]}
            cls.logger.info(f"Verified {cls.ATTR_NAME} on {obj}")

        return results

    @classmethod
    def key_fade(
        cls,
        objects,
        start: float,
        end: float,
        direction: str = "in",
        auto_create: bool = True,
        tangent: str = "linear",
    ) -> List[Tuple[str, str]]:
        """Key an opacity fade and mirror to visibility.

        Creates two keyframes on ``opacity`` (smooth channel) and two
        matching keyframes on ``visibility`` (stepped binary) so that
        FBX export produces native tracks for both channels.

        Parameters:
            objects: Maya nodes to key.
            start: First frame of the fade.
            end: Last frame of the fade.
            direction: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"``
                (detect from the last keyed opacity value).
            auto_create: When ``True``, create the ``opacity`` attribute
                on objects that lack it before keying.
            tangent: Tangent type for the opacity keys (default ``"linear"``).

        Returns:
            List of ``(object_name, "in"|"out")`` for each keyed object.
        """
        objects = cmds.ls(objects)
        if not objects:
            return []

        if auto_create:
            missing = [o for o in objects if not cmds.attributeQuery(cls.ATTR_NAME, node=o, exists=True)]
            if missing:
                cls.create(missing)

        keyed: List[Tuple[str, str]] = []
        for obj in objects:
            if not cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
                continue

            # Resolve direction
            if direction == "auto":
                fade_in = cls._resolve_auto_fade(obj, start)
            else:
                fade_in = direction == "in"

            start_val, end_val = (0.0, 1.0) if fade_in else (1.0, 0.0)

            # Maya's inTangentType doesn't accept "step"; use "stepnext".
            itt = "stepnext" if tangent == "step" else tangent

            # Key opacity (smooth channel)
            # Use explicit plug path to target the transform only —
            # the kwarg form (attribute=) also hits the shape node.
            opacity_plug = f"{(cmds.ls(obj, long=True) or [obj])[0]}.{cls.ATTR_NAME}"
            cmds.setKeyframe(
                opacity_plug,
                time=start,
                value=start_val,
                inTangentType=itt,
                outTangentType=tangent,
            )
            cmds.setKeyframe(
                opacity_plug,
                time=end,
                value=end_val,
                inTangentType=itt,
                outTangentType=tangent,
            )

            # Mirror to visibility (stepped binary)
            # Use longName to unambiguously target the transform —
            # short names also match the shape node.
            vis_plug = f"{(cmds.ls(obj, long=True) or [obj])[0]}.visibility"
            for t, v in ((start, start_val), (end, end_val)):
                cmds.setKeyframe(
                    vis_plug,
                    time=t,
                    value=1.0 if v > 0 else 0.0,
                    inTangentType="stepnext",
                    outTangentType="step",
                )
                cmds.keyTangent(
                    vis_plug,
                    edit=True,
                    time=(t, t),
                    inTangentType="stepnext",
                    outTangentType="step",
                )

            keyed.append((obj.split("|")[-1].split(":")[-1], "in" if fade_in else "out"))

        return keyed

    @staticmethod
    def _resolve_auto_fade(obj, reference_time: float) -> bool:
        """Return ``True`` for fade-in, ``False`` for fade-out.

        Inspects the most recent opacity key at or before *reference_time*.
        If its value is >= 0.5 (opaque), the object needs a fade-out.
        Defaults to fade-in when no previous key exists.
        """
        key_times = (
            cmds.keyframe(obj, attribute="opacity", query=True, timeChange=True) or []
        )
        prev_time = None
        for t in sorted(key_times):
            if t <= reference_time:
                prev_time = t
            else:
                break
        if prev_time is None:
            return True
        vals = cmds.keyframe(
            obj,
            attribute="opacity",
            query=True,
            time=(prev_time, prev_time),
            valueChange=True,
        )
        if vals:
            return vals[0] < 0.5
        return True

    @classmethod
    def _remove_legacy_vis_driver(cls, obj):
        """Disconnect and delete any condition-node visibility driver.

        Handles condition nodes created by older versions of this module
        so that visibility is free for direct keyframing.
        """
        vis_plug = f"{obj}.visibility"
        if cmds.getAttr(vis_plug, lock=True):
            return

        inputs = (
            cmds.listConnections(vis_plug, source=True, destination=False) or []
        )
        if (
            inputs
            and cmds.objectType(inputs[0]) == "condition"
            and cls._VIS_DRIVER_RE.search(inputs[0].split("|")[-1].split(":")[-1])
        ):
            cmds.delete(inputs[0])
            try:
                cmds.setAttr(vis_plug, True)
            except Exception:
                pass

        # Also clean orphaned VisDrivers still connected via opacity
        if cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
            conds = cmds.listConnections(f"{obj}.{cls.ATTR_NAME}", type="condition") or []
            for c in conds:
                if cls._VIS_DRIVER_RE.search(c.split("|")[-1].split(":")[-1]):
                    cmds.delete(c)

    @classmethod
    def sync_visibility_from_opacity(cls, objects) -> None:
        """Create visibility keyframes that mirror the opacity animation curve.

        For each keyframe on ``opacity``, a matching keyframe is set on
        ``visibility`` with the same value and tangent type.  This produces
        real animation curves that FBX can export and game engines can read
        natively — unlike the deprecated condition-node approach which was
        Maya-only.

        Safe to call repeatedly; existing visibility keys are cleared and
        rebuilt from the current opacity curve each time.

        .. warning:: This replaces **all** visibility keyframes with those
           derived from opacity.  Any hand-keyed visibility animation that
           does not correspond to an opacity key will be lost.
        """
        for obj in cmds.ls(objects):
            if not cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
                continue
            if cmds.getAttr(f"{obj}.visibility", lock=True):
                continue

            # Remove any legacy condition-node driver first
            cls._remove_legacy_vis_driver(obj)

            times = cmds.keyframe(obj, attribute=cls.ATTR_NAME, q=True, tc=True)
            if not times:
                continue

            values = cmds.keyframe(obj, attribute=cls.ATTR_NAME, q=True, vc=True)
            in_tans = cmds.keyTangent(
                obj, attribute=cls.ATTR_NAME, q=True, inTangentType=True
            )
            out_tans = cmds.keyTangent(
                obj, attribute=cls.ATTR_NAME, q=True, outTangentType=True
            )

            # Clear existing visibility keys so repeated calls don't
            # accumulate duplicates.  Use full DAG path to target the
            # transform only — short names also match the shape.
            vis_attr = f"{(cmds.ls(obj, long=True) or [obj])[0]}.visibility"
            cmds.cutKey(vis_attr, clear=True)

            for t, v, it, ot in zip(times, values, in_tans, out_tans):
                cmds.setKeyframe(
                    vis_attr,
                    time=t,
                    value=1.0 if v > 0 else 0.0,
                    inTangentType="step",
                    outTangentType="step",
                )

    @classmethod
    def ensure_connections(cls, objects) -> None:
        """Ensure opacity → visibility mirroring for objects that already
        have the ``opacity`` attribute (e.g. after a duplicate operation).

        Removes any legacy condition-node drivers and syncs visibility
        keyframes from opacity keyframes.
        """
        # sync_visibility_from_opacity already calls _remove_legacy_vis_driver
        # per-object, so no separate loop is needed here.
        cls.sync_visibility_from_opacity(objects)

    @classmethod
    def remove(cls, objects):
        for obj in objects:
            obj = str(obj)
            if not cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
                continue

            # Clean up any legacy condition-node visibility driver
            cls._remove_legacy_vis_driver(obj)

            # Delete anim curves first (deleteAttr errors on connected attrs)
            curves = cmds.listConnections(f"{obj}.{cls.ATTR_NAME}", type="animCurve")
            if curves:
                cmds.delete(curves)
            cmds.deleteAttr(f"{obj}.{cls.ATTR_NAME}")
            cls.logger.info(f"Removed {cls.ATTR_NAME} from {obj}")
